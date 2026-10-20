"""Disk B+Tree date index with checksummed pages and redo WAL.

Page zero contains root metadata. Leaf keys are (date ordinal, uint64 id).
Internal child counts preserve the old rank-based DateIndex interface as well
as O(log N + K) key-range scans. See doc/bplustree-design.md for commit rules.
One writer per data directory, as in the original repository.
"""

from __future__ import annotations

import hashlib
import os
import struct
import uuid
import zlib
from bisect import bisect_left, bisect_right
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import BinaryIO, Iterable, Iterator

from .durability import fsync_dir
from .models import StorageError

DateEntry = tuple[int, int]
PAGE_SIZE = 4096
MAGIC = b"DATEBPT1"
VERSION = 1
META = struct.Struct("<8sII16sQQQQQQ")
PAGE_HEADER = struct.Struct("<4sBBHQQQ")
KEY = struct.Struct("<IQ")
CHILD = struct.Struct("<QQ")
BRANCH = struct.Struct("<IQQQ")
CRC = struct.Struct("<I")
WAL_HEADER = struct.Struct("<8sI16sQQ")
PAGE_ID = struct.Struct("<Q")
LEAF, INTERNAL = 1, 2
MAX_ID = (1 << 64) - 1
MAX_ORDINAL = date.max.toordinal()


def _error(detail: str) -> StorageError:
    return StorageError(f"날짜 인덱스가 손상되었습니다: {detail}", "compact 를 실행해 원본에서 다시 만드세요.")


def _checksum(payload: bytes, size: int) -> bytes:
    if len(payload) > size - CRC.size:
        raise _error("페이지 용량 초과")
    body = payload.ljust(size - CRC.size, b"\0")
    return body + CRC.pack(zlib.crc32(body))


def _check(raw: bytes, size: int) -> None:
    if len(raw) != size or CRC.unpack(raw[-4:])[0] != zlib.crc32(raw[:-4]):
        raise _error("페이지 길이 또는 CRC32 불일치")


def _valid_key(key: DateEntry) -> None:
    if not 1 <= key[0] <= MAX_ORDINAL or not 1 <= key[1] <= MAX_ID:
        raise _error("날짜 또는 거래 id 범위 오류 (id 0은 허용되지 않음)")


@dataclass
class _Meta:
    page_size: int
    identity: bytes
    root: int = 1
    pages: int = 2
    count: int = 0
    first: int = 1
    last: int = 1
    generation: int = 0

    def encode(self) -> bytes:
        return _checksum(META.pack(MAGIC, VERSION, self.page_size, self.identity,
                                  self.root, self.pages, self.count, self.first,
                                  self.last, self.generation), self.page_size)

    @classmethod
    def decode(cls, raw: bytes) -> _Meta:
        if len(raw) < META.size:
            raise _error("메타데이터 없음")
        magic, version, size, identity, root, pages, count, first, last, gen = META.unpack_from(raw)
        if magic != MAGIC or version != VERSION or not 256 <= size <= 65536 or size & (size - 1):
            raise _error("지원하지 않는 파일 포맷")
        _check(raw, size)
        if pages < 2 or not all(0 < p < pages for p in (root, first, last)):
            raise _error("root/leaf 페이지 주소 오류")
        return cls(size, identity, root, pages, count, first, last, gen)


@dataclass
class _Page:
    page_id: int
    leaf: bool
    keys: list[DateEntry] = field(default_factory=list)
    children: list[int] = field(default_factory=list)
    counts: list[int] = field(default_factory=list)
    prev: int = 0
    next: int = 0

    @property
    def count(self) -> int:
        return len(self.keys) if self.leaf else sum(self.counts)

    def encode(self, meta: _Meta) -> bytes:
        payload = PAGE_HEADER.pack(b"BPTP", LEAF if self.leaf else INTERNAL,
                                   int(self.page_id == meta.root), len(self.keys),
                                   self.prev, self.next, self.count)
        if self.leaf:
            payload += b"".join(KEY.pack(*key) for key in self.keys)
        else:
            payload += CHILD.pack(self.children[0], self.counts[0])
            payload += b"".join(BRANCH.pack(*key, child, count)
                                for key, child, count in zip(self.keys, self.children[1:], self.counts[1:]))
        return _checksum(payload, meta.page_size)

    @classmethod
    def decode(cls, page_id: int, raw: bytes, meta: _Meta) -> _Page:
        _check(raw, meta.page_size)
        magic, kind, flags, count, prev, nxt, total = PAGE_HEADER.unpack_from(raw)
        if magic != b"BPTP" or kind not in (LEAF, INTERNAL) or flags not in (0, 1):
            raise _error("페이지 종류 오류")
        if bool(flags) != (page_id == meta.root):
            raise _error("root 플래그 오류")
        page = cls(page_id, kind == LEAF, prev=prev, next=nxt)
        offset = PAGE_HEADER.size
        capacity = ((meta.page_size - offset - 4) // KEY.size if page.leaf else
                    (meta.page_size - offset - 4 - CHILD.size) // BRANCH.size)
        if count > capacity:
            raise _error("페이지 항목 수 오류")
        if page.leaf:
            if prev >= meta.pages or nxt >= meta.pages:
                raise _error("leaf 링크 범위 오류")
            page.keys = list(KEY.iter_unpack(raw[offset:offset + count * KEY.size]))
        else:
            if prev or nxt or not count:
                raise _error("internal 페이지 구조 오류")
            child, child_count = CHILD.unpack_from(raw, offset)
            page.children.append(child)
            page.counts.append(child_count)
            offset += CHILD.size
            for ordinal, tx_id, child, child_count in BRANCH.iter_unpack(raw[offset:offset + count * BRANCH.size]):
                page.keys.append((ordinal, tx_id))
                page.children.append(child)
                page.counts.append(child_count)
            if not all(0 < child < meta.pages for child in page.children):
                raise _error("자식 페이지 범위 오류")
        for key in page.keys:
            _valid_key(key)
        if any(a >= b for a, b in zip(page.keys, page.keys[1:])) or page.count != total:
            raise _error("키 순서 또는 자식 항목 수 오류")
        return page


class _Session:
    """One operation: bounded clean cache, dirty pages retained until WAL commit."""

    def __init__(self, fp: BinaryIO, meta: _Meta) -> None:
        self.fp, self.meta = fp, meta
        self.dirty: dict[int, _Page] = {}
        self.cache: OrderedDict[int, _Page] = OrderedDict()
        self.reads = 0

    def read(self, page_id: int) -> _Page:
        if page_id in self.dirty:
            return self.dirty[page_id]
        if page_id not in self.cache:
            if not 0 < page_id < self.meta.pages:
                raise _error("페이지 주소 범위 오류")
            self.fp.seek(page_id * self.meta.page_size)
            self.cache[page_id] = _Page.decode(page_id, self.fp.read(self.meta.page_size), self.meta)
            self.reads += 1
            if len(self.cache) > 64:
                self.cache.popitem(last=False)
        self.cache.move_to_end(page_id)
        return self.cache[page_id]

    def save(self, page: _Page) -> None:
        self.dirty[page.page_id] = page

    def new(self, leaf: bool) -> _Page:
        page = _Page(self.meta.pages, leaf)
        self.meta.pages += 1
        self.save(page)
        return page

    def find(self, key: DateEntry) -> tuple[_Page, list[tuple[_Page, int]], int]:
        page = self.read(self.meta.root)
        path: list[tuple[_Page, int]] = []
        rank = 0
        while not page.leaf:
            pos = bisect_right(page.keys, key)
            rank += sum(page.counts[:pos])
            path.append((page, pos))
            if len(path) >= self.meta.pages:
                raise _error("페이지 순환 참조")
            page = self.read(page.children[pos])
        return page, path, rank

    def select(self, rank: int) -> tuple[_Page, int]:
        page = self.read(self.meta.root)
        depth = 0
        while not page.leaf:
            for pos, count in enumerate(page.counts):
                if rank < count:
                    break
                rank -= count
            else:
                raise _error("순위/자식 항목 수 불일치")
            page = self.read(page.children[pos])
            depth += 1
            if depth >= self.meta.pages:
                raise _error("페이지 순환 참조")
        if not 0 <= rank < len(page.keys):
            raise _error("leaf 순위 범위 오류")
        return page, rank


class DateIndex:
    """DateIndex API backed by a durable binary B+Tree, not a sorted array."""

    def __init__(self, path: Path, page_size: int = PAGE_SIZE) -> None:
        if not 256 <= page_size <= 65536 or page_size & (page_size - 1):
            raise ValueError("page_size must be a power of two between 256 and 65536")
        self.path = Path(path)
        self.page_size = page_size
        self.wal_path = self.path.with_name(self.path.name + ".wal")
        self.last_page_reads = 0

    def size(self) -> int:
        return self.path.stat().st_size if self.path.exists() else 0

    def _checkpoint(self, stage: str) -> None:
        """Fault-injection boundary; production has no environment-controlled exits."""

    def _header(self, fp: BinaryIO) -> _Meta:
        fp.seek(0)
        prefix = fp.read(META.size)
        if len(prefix) != META.size or prefix[:8] != MAGIC:
            raise _error("B+Tree 헤더 없음")
        size = META.unpack(prefix)[2]
        if not 256 <= size <= 65536 or size & (size - 1):
            raise _error("페이지 크기 오류")
        fp.seek(0)
        meta = _Meta.decode(fp.read(size))
        if os.fstat(fp.fileno()).st_size != meta.pages * size:
            raise _error("파일 크기와 페이지 수 불일치")
        self.page_size = size
        return meta

    def recover(self) -> bool:
        """Replay a durable WAL in full before reading *any* tree metadata."""
        self.wal_path.with_name(self.wal_path.name + ".tmp").unlink(missing_ok=True)
        if not self.wal_path.exists():
            return False
        raw = self.wal_path.read_bytes()
        if len(raw) < WAL_HEADER.size + 32 or hashlib.sha256(raw[:-32]).digest() != raw[-32:]:
            raise _error("WAL 체크섬 오류")
        magic, size, identity, pages, image_count = WAL_HEADER.unpack_from(raw)
        if magic != b"BPTWAL01" or not 256 <= size <= 65536 or size & (size - 1):
            raise _error("WAL 헤더 오류")
        if len(raw) != WAL_HEADER.size + image_count * (8 + size) + 32:
            raise _error("WAL 길이 오류")
        images: dict[int, bytes] = {}
        offset = WAL_HEADER.size
        for _ in range(image_count):
            page_id = PAGE_ID.unpack_from(raw, offset)[0]
            image = raw[offset + 8:offset + 8 + size]
            _check(image, size)
            if page_id >= pages or page_id in images:
                raise _error("WAL 페이지 주소 오류")
            images[page_id] = image
            offset += 8 + size
        if 0 not in images:
            raise _error("WAL 메타데이터 없음")
        meta = _Meta.decode(images[0])
        if (meta.identity, meta.pages, meta.page_size) != (identity, pages, size):
            raise _error("WAL 메타데이터 불일치")
        for page_id, image in images.items():
            if page_id:
                _Page.decode(page_id, image, meta)
        if not self.path.exists():
            raise _error("WAL 대상 파일 없음")
        with self.path.open("r+b") as fp:
            prefix = fp.read(32)
            if len(prefix) < 32 or prefix[:8] != MAGIC or prefix[16:32] != identity:
                raise _error("다른 인덱스의 WAL")
            self._apply(fp, images, meta)
        self.wal_path.unlink()
        fsync_dir(self.path.parent)
        self.page_size = size
        return True

    def is_valid(self) -> bool:
        try:
            self.recover()
            with self.path.open("rb") as fp:
                self._header(fp)
            return True
        except (FileNotFoundError, StorageError):
            return False

    def is_legacy(self) -> bool:
        if not self.path.exists() or self.size() % KEY.size:
            return False
        with self.path.open("rb") as fp:
            return fp.read(8) != MAGIC

    def _open(self) -> tuple[BinaryIO, _Session]:
        self.recover()
        fp = self.path.open("rb")
        try:
            return fp, _Session(fp, self._header(fp))
        except BaseException:
            fp.close()
            raise

    def count(self) -> int:
        fp, session = self._open()
        with fp:
            return session.meta.count

    def bisect_left(self, key: DateEntry) -> int:
        fp, session = self._open()
        with fp:
            leaf, _, rank = session.find(key)
            result = rank + bisect_left(leaf.keys, key)
            self.last_page_reads = session.reads
            return result

    def _iter_ranks(self, lo: int, hi: int, reverse: bool) -> Iterator[DateEntry]:
        fp, session = self._open()
        with fp:
            lo, hi = max(lo, 0), min(hi, session.meta.count)
            if lo >= hi:
                return
            page, pos = session.select(hi - 1 if reverse else lo)
            remaining = hi - lo
            steps = 0
            while remaining:
                steps += 1
                if steps >= session.meta.pages:
                    raise _error("leaf 링크 순환")
                keys = page.keys[:pos + 1][::-1] if reverse else page.keys[pos:]
                for key in keys:
                    yield key
                    remaining -= 1
                    if not remaining:
                        self.last_page_reads = session.reads
                        return
                nxt = page.prev if reverse else page.next
                if not nxt:
                    raise _error("leaf 링크 조기 종료")
                old = page.page_id
                page = session.read(nxt)
                if not page.leaf or (page.next if reverse else page.prev) != old:
                    raise _error("leaf 양방향 링크 불일치")
                pos = len(page.keys) - 1 if reverse else 0

    def iter_desc(self, lo: int, hi: int) -> Iterator[DateEntry]:
        return self._iter_ranks(lo, hi, True)

    def iter_asc(self, lo: int = 0, hi: int | None = None) -> Iterator[DateEntry]:
        return self._iter_ranks(lo, self.count() if hi is None else hi, False)

    def iter_range(self, lower: DateEntry | None = None, upper: DateEntry | None = None,
                   *, reverse: bool = False) -> Iterator[DateEntry]:
        """Stream [lower, upper) in either direction; descend once, then link-scan."""
        if lower is not None and upper is not None and lower >= upper:
            return
        fp, session = self._open()
        with fp:
            if not session.meta.count:
                return
            bound = upper if reverse else lower
            if bound is None:
                page = session.read(session.meta.last if reverse else session.meta.first)
                pos = len(page.keys) - 1 if reverse else 0
            else:
                page, _, _ = session.find(bound)
                pos = bisect_left(page.keys, bound) - 1 if reverse else bisect_left(page.keys, bound)
            steps = 0
            while True:
                steps += 1
                if not page.leaf or steps >= session.meta.pages:
                    raise _error("leaf 링크 종류 또는 순환 오류")
                positions = range(pos, -1, -1) if reverse else range(pos, len(page.keys))
                for i in positions:
                    key = page.keys[i]
                    if (lower is not None and key < lower) or (upper is not None and key >= upper):
                        self.last_page_reads = session.reads
                        return
                    yield key
                nxt = page.prev if reverse else page.next
                if not nxt:
                    self.last_page_reads = session.reads
                    return
                old = page.page_id
                page = session.read(nxt)
                if (page.next if reverse else page.prev) != old:
                    raise _error("leaf 양방향 링크 불일치")
                pos = len(page.keys) - 1 if reverse else 0

    def contains(self, key: DateEntry) -> bool:
        fp, session = self._open()
        with fp:
            page, _, _ = session.find(key)
            pos = bisect_left(page.keys, key)
            self.last_page_reads = session.reads
            return pos < len(page.keys) and page.keys[pos] == key

    def _insert(self, session: _Session, key: DateEntry) -> None:
        _valid_key(key)
        page, path, _ = session.find(key)
        pos = bisect_left(page.keys, key)
        if pos < len(page.keys) and page.keys[pos] == key:
            return
        page.keys.insert(pos, key)
        session.save(page)
        session.meta.count += 1
        leaf_cap = (session.meta.page_size - PAGE_HEADER.size - 4) // KEY.size
        branch_cap = (session.meta.page_size - PAGE_HEADER.size - 4 - CHILD.size) // BRANCH.size
        split: tuple[DateEntry, _Page] | None = None
        if len(page.keys) > leaf_cap:
            right = session.new(True)
            mid = len(page.keys) // 2
            right.keys, page.keys = page.keys[mid:], page.keys[:mid]
            right.prev, right.next = page.page_id, page.next
            if page.next:
                neighbor = session.read(page.next)
                neighbor.prev = right.page_id
                session.save(neighbor)
            else:
                session.meta.last = right.page_id
            page.next = right.page_id
            split = (right.keys[0], right)
            self._checkpoint("leaf_split")
        while path:
            parent, child_pos = path.pop()
            parent.counts[child_pos] = page.count
            if split is not None:
                separator, right = split
                parent.keys.insert(child_pos, separator)
                parent.children.insert(child_pos + 1, right.page_id)
                parent.counts.insert(child_pos + 1, right.count)
            session.save(parent)
            split = None
            if len(parent.keys) > branch_cap:
                mid = len(parent.keys) // 2
                separator = parent.keys[mid]
                right = session.new(False)
                right.keys, parent.keys = parent.keys[mid + 1:], parent.keys[:mid]
                right.children, parent.children = parent.children[mid + 1:], parent.children[:mid + 1]
                right.counts, parent.counts = parent.counts[mid + 1:], parent.counts[:mid + 1]
                split = (separator, right)
                self._checkpoint("internal_split")
            page = parent
        if split is not None:
            separator, right = split
            root = session.new(False)
            root.keys = [separator]
            root.children = [page.page_id, right.page_id]
            root.counts = [page.count, right.count]
            session.meta.root = root.page_id
            self._checkpoint("root_split")

    def _apply(self, fp: BinaryIO, images: dict[int, bytes], meta: _Meta) -> None:
        # Existing metadata still refers to the old tree until the final image.
        for page_id, image in images.items():
            if page_id:
                fp.seek(page_id * meta.page_size)
                fp.write(image)
                self._checkpoint("page_written")
        fp.seek(0)
        fp.write(images[0])
        self._checkpoint("root_written")
        fp.truncate(meta.pages * meta.page_size)
        fp.flush()
        os.fsync(fp.fileno())
        self._checkpoint("data_synced")

    def _commit(self, session: _Session) -> None:
        if not session.dirty:
            return
        meta = session.meta
        meta.generation += 1
        images = {page_id: page.encode(meta) for page_id, page in session.dirty.items()}
        images[0] = meta.encode()
        body = WAL_HEADER.pack(b"BPTWAL01", meta.page_size, meta.identity, meta.pages, len(images))
        body += b"".join(PAGE_ID.pack(page_id) + raw for page_id, raw in images.items())
        tmp = self.wal_path.with_name(self.wal_path.name + ".tmp")
        with tmp.open("wb") as fp:
            fp.write(body + hashlib.sha256(body).digest())
            fp.flush()
            os.fsync(fp.fileno())
        self._checkpoint("wal_prepared")
        os.replace(tmp, self.wal_path)
        fsync_dir(self.path.parent)
        self._checkpoint("wal_published")
        with self.path.open("r+b") as fp:
            self._apply(fp, images, meta)
        self.wal_path.unlink()
        self._checkpoint("wal_removed")
        fsync_dir(self.path.parent)

    def merge(self, entries: Iterable[DateEntry]) -> None:
        """Insert unique keys in one WAL transaction, touching only affected pages."""
        if not self.path.exists():
            self.rebuild(())
        fp, session = self._open()
        with fp:
            for key in entries:
                self._insert(session, key)
            self._commit(session)
            self.last_page_reads = session.reads

    def merge_keys(self, keys: Iterable[int]) -> None:
        # Repository's packed in-memory integers now retain all 64 id bits.
        self.merge((key >> 64, key & MAX_ID) for key in keys)

    def rebuild(self, entries: Iterable[DateEntry]) -> None:
        """Build beside the original, fsync, then atomically replace the index.

        Failure never replaces a legacy/current index with a partial new tree.
        The repository makes the durable legacy backup before invoking this.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".build")
        meta = _Meta(self.page_size, uuid.uuid4().bytes)
        with tmp.open("w+b") as fp:
            fp.write(meta.encode())
            fp.write(_Page(1, True).encode(meta))
            session = _Session(fp, meta)
            for key in entries:
                self._insert(session, key)
            # No WAL needed: the destination is not visible until replace.
            for page_id, page in session.dirty.items():
                fp.seek(page_id * meta.page_size)
                fp.write(page.encode(meta))
            fp.seek(0)
            fp.write(meta.encode())
            fp.truncate(meta.pages * meta.page_size)
            fp.flush()
            os.fsync(fp.fileno())
        self._checkpoint("rebuild_synced")
        os.replace(tmp, self.path)
        fsync_dir(self.path.parent)
        # A failed old WAL must never be replayed into the new UUID.
        self.wal_path.unlink(missing_ok=True)
        self.wal_path.with_name(self.wal_path.name + ".tmp").unlink(missing_ok=True)
        fsync_dir(self.path.parent)

    def validate(self) -> dict[str, int]:
        """Explicit full structural check (not part of O(log N) startup/lookups)."""
        fp, session = self._open()
        with fp:
            visited: set[int] = set()
            leaves: list[_Page] = []

            def walk(page_id: int) -> tuple[DateEntry | None, DateEntry | None, int, int]:
                if page_id in visited:
                    raise _error("중복/순환 자식 페이지")
                visited.add(page_id)
                page = session.read(page_id)
                if page.leaf:
                    leaves.append(page)
                    return (page.keys[0] if page.keys else None, page.keys[-1] if page.keys else None,
                            len(page.keys), 1)
                children = [walk(child) for child in page.children]
                if len({item[3] for item in children}) != 1:
                    raise _error("leaf 깊이 불일치")
                for i, (left, right) in enumerate(zip(children, children[1:])):
                    if left[1] is None or right[0] != page.keys[i] or left[1] >= page.keys[i]:
                        raise _error("internal 분기 키 불일치")
                if [item[2] for item in children] != page.counts:
                    raise _error("자식 항목 수 불일치")
                return children[0][0], children[-1][1], sum(item[2] for item in children), children[0][3] + 1

            _, _, count, height = walk(session.meta.root)
            if count != session.meta.count or len(visited) != session.meta.pages - 1:
                raise _error("전체 항목/페이지 수 불일치")
            if leaves[0].page_id != session.meta.first or leaves[-1].page_id != session.meta.last:
                raise _error("첫/마지막 leaf 불일치")
            for i, leaf in enumerate(leaves):
                if (leaf.prev, leaf.next) != (leaves[i - 1].page_id if i else 0,
                                             leaves[i + 1].page_id if i + 1 < len(leaves) else 0):
                    raise _error("leaf 연결 순서 불일치")
            return {"entries": count, "height": height, "pages": session.meta.pages,
                    "leaves": len(leaves), "page_size": session.meta.page_size}
