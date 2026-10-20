"""날짜와 거래번호로 정렬하는 디스크 B+Tree.

파일을 같은 크기의 페이지로 나누고, page 0에 실제 root의 위치를 기록한다.
leaf에는 (날짜 일련번호, 거래번호)를, internal에는 자식으로 가는 길을 저장한다.
leaf의 앞/뒤 연결을 따라가면 전체 정렬이나 역순 범위를 차례대로 읽을 수 있다.

WAL(Write-Ahead Log, 선행 기록 로그)에 변경 후 페이지를 먼저 확정한 뒤
본 파일을 고친다. 중간에 종료되면 WAL의 바이트를 다시 써서 마무리한다.
필드 이름·크기·저장 순서의 이유는 doc/bplustree-report.md에 설명했다.
기존 프로그램처럼 같은 data-dir의 동시 프로세스 실행은 지원하지 않는다.
"""

from __future__ import annotations

import hashlib
import heapq
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

DateEntry = tuple[int, int]  # (날짜 ordinal, 거래 id). 먼저 날짜, 같으면 id를 비교한다.
PAGE_SIZE = 4096  # 기본 4KiB. page_id * page_size로 파일 안의 위치를 구한다.
MAGIC = b"DATEBPT1"  # DATE + B+Tree(BPT) + 형식 계열 1: 파일 종류를 알리는 8바이트.
VERSION = 1  # 프로그램 버전이 아니라 디스크 배치의 버전이다.

# struct는 Python 값을 정해진 바이너리 배치로 바꾼다. '<'는 little-endian이고
# 자동 정렬 패딩을 넣지 않는다. I=부호 없는 4바이트, Q=부호 없는 8바이트다.
# 8s/16s는 각각 바이트 문자열 한 항목이다. META는 I가 두 개이며 총 80바이트다.
META = struct.Struct("<8sII16sQQQQQQ")  # magic, 버전, 크기, UUID, root/페이지/키/첫/끝/세대
PAGE_HEADER = struct.Struct("<4sBBHQQQ")  # 32바이트: magic, 종류, root 여부, 키 수, prev/next/합계
KEY = struct.Struct("<IQ")  # 12바이트: 날짜 ordinal 4 + 거래번호 8
CHILD = struct.Struct("<QQ")  # 16바이트: 첫 자식 페이지번호 + 그 자식 아래 키 수
BRANCH = struct.Struct("<IQQQ")  # 28바이트: 분기 키(IQ) + 오른쪽 자식 번호/키 수(QQ)
CRC = struct.Struct("<I")  # 페이지 끝의 CRC32(32비트 순환 중복 검사) 4바이트
WAL_HEADER = struct.Struct("<8sI16sQQ")  # 44바이트: 서명, 페이지 크기, UUID, 목표 페이지/이미지 수
PAGE_ID = struct.Struct("<Q")  # WAL 이미지 앞에 붙이는 대상 페이지번호 8바이트
LEAF, INTERNAL = 1, 2
# merge 가 메모리에 들고 있을 변경 페이지 수의 상한(4096바이트 페이지면 약 1MiB, 객체로는 약 8MB). 넘으면 그때까지를
# 먼저 확정한다. 상한이 없으면 큰 import 가 바뀐 페이지를 모두 들고 있어 100만 건에 수백 MB 를 썼다.
# 확정은 삽입이 끝난 뒤에 확인하므로, 삽입 한 번이 바꾸는 페이지(트리 높이에 비례)만큼은 넘을 수 있다.
MAX_DIRTY_PAGES = 256
MAX_ID = (1 << 64) - 1  # Q의 최댓값. Python 정수여도 디스크 필드 범위는 넘을 수 없다.
MAX_ORDINAL = date.max.toordinal()


def _error(detail: str) -> StorageError:
    """바이너리 손상을 CLI에서 읽기 쉬운 오류와 복구 힌트로 바꾼다."""
    return StorageError(f"날짜 인덱스가 손상되었습니다: {detail}", "compact 를 실행해 원본에서 다시 만드세요.")


def _checksum(payload: bytes, size: int) -> bytes:
    """본문을 페이지 크기에 맞춰 0으로 채운 뒤 마지막 4바이트에 CRC를 붙인다."""
    if len(payload) > size - CRC.size:
        raise _error("페이지 용량 초과")
    body = payload.ljust(size - CRC.size, b"\0")  # 패딩까지 검사해 일부 쓰기 손상을 찾는다.
    return body + CRC.pack(zlib.crc32(body))


def _check(raw: bytes, size: int) -> None:
    """길이와 CRC가 모두 맞아야 페이지 필드를 해석할 수 있다."""
    if len(raw) != size or CRC.unpack(raw[-4:])[0] != zlib.crc32(raw[:-4]):
        raise _error("페이지 길이 또는 CRC32 불일치")


def _valid_key(key: DateEntry) -> None:
    """날짜 범위와 unsigned 64-bit 거래번호를 검사한다. 거래 id 0은 실제 키가 아니다."""
    if not 1 <= key[0] <= MAX_ORDINAL or not 1 <= key[1] <= MAX_ID:
        raise _error("날짜 또는 거래 id 범위 오류 (id 0은 허용되지 않음)")


@dataclass
class _Meta:
    """page 0의 안내 정보. 실제 검색 root 페이지와는 구분한다."""

    page_size: int  # 한 페이지의 바이트 수. 헤더 80바이트만의 크기가 아니다.
    identity: bytes  # UUID 16바이트: 같은 이름으로 다시 만든 파일과 구별한다.
    root: int = 1  # 검색 시작 페이지. 처음에는 page 1의 빈 leaf다.
    pages: int = 2  # 처음에는 메타데이터 + root leaf, 총 2페이지다.
    count: int = 0  # stale 항목을 포함한 키 수. 살아있는 거래 수와는 다르다.
    first: int = 1  # 오름차순 조회를 바로 시작할 첫 leaf
    last: int = 1  # 역순 조회를 바로 시작할 마지막 leaf
    generation: int = 0  # WAL 갱신 세대 기록. 현재 오래된 WAL 판정에 비교하지는 않는다.

    def encode(self) -> bytes:
        """필드 순서대로 pack하고 패딩·CRC를 붙여 페이지 하나를 만든다."""
        return _checksum(META.pack(MAGIC, VERSION, self.page_size, self.identity,
                                  self.root, self.pages, self.count, self.first,
                                  self.last, self.generation), self.page_size)

    @classmethod
    def decode(cls, raw: bytes) -> _Meta:
        """바이트를 메타데이터로 복원하되, 길이·형식·주소를 먼저 검사한다."""
        if len(raw) < META.size:
            raise _error("메타데이터 없음")
        magic, version, size, identity, root, pages, count, first, last, gen = META.unpack_from(raw)
        # 양수 x에서 x & (x-1)이 0이면 2의 거듭제곱이다. 크기 범위도 함께 제한한다.
        if magic != MAGIC or version != VERSION or not 256 <= size <= 65536 or size & (size - 1):
            raise _error("지원하지 않는 파일 포맷")
        _check(raw, size)
        if pages < 2 or not all(0 < p < pages for p in (root, first, last)):
            raise _error("root/leaf 페이지 주소 오류")
        return cls(size, identity, root, pages, count, first, last, gen)


@dataclass
class _Page:
    """노드 한 개의 메모리 모습. encode/decode할 때 디스크 바이트로 바뀐다."""

    page_id: int
    leaf: bool
    keys: list[DateEntry] = field(default_factory=list)
    children: list[int] = field(default_factory=list)  # internal 전용. 키 K개이면 자식 K+1개다.
    counts: list[int] = field(default_factory=list)  # children과 같은 순서의 자식별 키 수
    prev: int = 0  # leaf 전용. 0이면 이전 leaf가 없다.
    next: int = 0  # leaf 전용. 0이면 다음 leaf가 없다.

    @property
    def count(self) -> int:
        """leaf는 자기 키 수, internal은 모든 자식 아래 키 수의 합을 반환한다."""
        return len(self.keys) if self.leaf else sum(self.counts)

    def encode(self, meta: _Meta) -> bytes:
        """공통 헤더 뒤에 종류별 본문을 붙인다. root 여부는 현재 meta.root로 계산한다."""
        payload = PAGE_HEADER.pack(b"BPTP", LEAF if self.leaf else INTERNAL,
                                   int(self.page_id == meta.root), len(self.keys),
                                   self.prev, self.next, self.count)
        if self.leaf:
            # leaf에는 키만 차례대로 쓴다. 거래 내용은 JSONL에 있으므로 복사하지 않는다.
            payload += b"".join(KEY.pack(*key) for key in self.keys)
        else:
            # 첫 자식에는 앞쪽 분기 키가 없다. 이후는 (분기 키, 오른쪽 자식) 묶음이다.
            payload += CHILD.pack(self.children[0], self.counts[0])
            payload += b"".join(BRANCH.pack(*key, child, count)
                                for key, child, count in zip(self.keys, self.children[1:], self.counts[1:]))
        return _checksum(payload, meta.page_size)

    @classmethod
    def decode(cls, page_id: int, raw: bytes, meta: _Meta) -> _Page:
        """검증한 페이지를 노드로 읽는다. 이상한 주소나 순서를 조용히 사용하지 않는다."""
        _check(raw, meta.page_size)
        magic, kind, flags, count, prev, nxt, total = PAGE_HEADER.unpack_from(raw)
        if magic != b"BPTP" or kind not in (LEAF, INTERNAL) or flags not in (0, 1):
            raise _error("페이지 종류 오류")
        if bool(flags) != (page_id == meta.root):
            raise _error("root 플래그 오류")
        page = cls(page_id, kind == LEAF, prev=prev, next=nxt)
        offset = PAGE_HEADER.size
        # 공통 헤더·CRC를 빼고 몇 개 키가 들어가는지 계산한다.
        # internal은 첫 자식 16바이트도 먼저 빼야 한다.
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
        # CRC가 맞아도 id=0이나 역전된 키 같은 의미상 손상은 별도로 거부한다.
        for key in page.keys:
            _valid_key(key)
        if any(a >= b for a, b in zip(page.keys, page.keys[1:])) or page.count != total:
            raise _error("키 순서 또는 자식 항목 수 오류")
        return page


class _Session:
    """한 번의 읽기/삽입 작업 동안 페이지를 관리한다.

    clean cache는 최대 64페이지다. 변경된 dirty 페이지는 확정 전까지 따로 둔다.
    따라서 단건 삽입은 전체 인덱스 대신 경로와 분할 이웃만 메모리에서 다룬다.
    큰 배치는 변경 페이지가 많으므로 dirty 메모리도 배치 크기에 따라 늘어난다.
    """

    def __init__(self, fp: BinaryIO, meta: _Meta) -> None:
        self.fp, self.meta = fp, meta
        self.dirty: dict[int, _Page] = {}
        self.cache: OrderedDict[int, _Page] = OrderedDict()
        self.reads = 0

    def read(self, page_id: int) -> _Page:
        """메모리의 최신 변경본 → 읽기 캐시 → 디스크 순서로 페이지를 찾는다."""
        if page_id in self.dirty:
            return self.dirty[page_id]
        if page_id not in self.cache:
            if not 0 < page_id < self.meta.pages:
                raise _error("페이지 주소 범위 오류")
            self.fp.seek(page_id * self.meta.page_size)
            self.cache[page_id] = _Page.decode(page_id, self.fp.read(self.meta.page_size), self.meta)
            self.reads += 1
            if len(self.cache) > 64:
                self.cache.popitem(last=False)  # 가장 오래 참조하지 않은 clean 페이지를 버린다.
        self.cache.move_to_end(page_id)
        return self.cache[page_id]

    def save(self, page: _Page) -> None:
        """변경 표시만 한다. 이 시점에는 본 파일에 쓰지 않고 나중에 WAL로 확정한다."""
        self.dirty[page.page_id] = page

    def new(self, leaf: bool) -> _Page:
        """현재 페이지 수를 새 번호로 발급한다. 이 트리는 파일 끝에 페이지를 할당한다."""
        page = _Page(self.meta.pages, leaf)
        self.meta.pages += 1
        self.save(page)
        return page

    def find(self, key: DateEntry) -> tuple[_Page, list[tuple[_Page, int]], int]:
        """키가 들어갈 leaf, 부모 경로, 그 leaf보다 앞선 전체 키 수를 찾는다."""
        page = self.read(self.meta.root)
        path: list[tuple[_Page, int]] = []
        rank = 0
        while not page.leaf:
            # separator와 같은 키는 오른쪽 자식에 있다. 따라서 bisect_right를 사용한다.
            pos = bisect_right(page.keys, key)
            rank += sum(page.counts[:pos])
            path.append((page, pos))  # 삽입 후 부모 수 갱신과 split 전파 때 되짚을 경로
            if len(path) >= self.meta.pages:
                raise _error("페이지 순환 참조")
            page = self.read(page.children[pos])
        return page, path, rank

    def select(self, rank: int) -> tuple[_Page, int]:
        """전체 정렬 순서의 0부터 세는 rank번째 키가 있는 leaf와 내부 위치를 찾는다."""
        page = self.read(self.meta.root)
        depth = 0
        while not page.leaf:
            for pos, count in enumerate(page.counts):
                if rank < count:
                    break
                rank -= count  # 앞 자식 전체를 건너뛰고 다음 자식 안의 상대 순위를 구한다.
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
    """기존 DateIndex 호출 방식을 유지하는 B+Tree 파일 입출력 창구."""

    def __init__(self, path: Path, page_size: int = PAGE_SIZE) -> None:
        if not 256 <= page_size <= 65536 or page_size & (page_size - 1):
            raise ValueError("page_size must be a power of two between 256 and 65536")
        self.path = Path(path)
        self.page_size = page_size
        self.wal_path = self.path.with_name(self.path.name + ".wal")
        self.last_page_reads = 0

    def size(self) -> int:
        """파일의 바이트 크기다. 키 수를 알려 주는 count()와 구별한다."""
        return self.path.stat().st_size if self.path.exists() else 0

    def _checkpoint(self, stage: str) -> None:
        """테스트에서만 종료를 주입하는 경계다. 정상 실행에서는 아무 일도 하지 않는다."""

    def _header(self, fp: BinaryIO) -> _Meta:
        """고정 80바이트에서 페이지 크기를 알아낸 다음 page 0 전체의 CRC를 확인한다."""
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
        """헤더를 조회하기 전에 WAL을 검증·재적용한다. 실제 복구했으면 True다."""
        # .tmp만 있으면 publish 전에 멈춘 것이다. 본 파일은 바꾸기 전이므로 폐기한다.
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
        # 모든 이미지를 검사한 뒤에만 파일을 수정한다. 앞쪽 몇 개만 믿고 적용하지 않는다.
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
            # UUID는 메타데이터의 16~31번 바이트다. 새 파일에 남은 옛 WAL을 막는다.
            if len(prefix) < 32 or prefix[:8] != MAGIC or prefix[16:32] != identity:
                raise _error("다른 인덱스의 WAL")
            self._apply(fp, images, meta)
        # 파일 fsync를 마쳤으므로 이제 복구 기록을 지워도 된다.
        self.wal_path.unlink()
        fsync_dir(self.path.parent)
        self.page_size = size
        return True

    def is_valid(self) -> bool:
        """WAL 복구와 메타데이터 검사를 한다. 매번 모든 노드를 순회하지는 않는다."""
        try:
            self.recover()
            with self.path.open("rb") as fp:
                self._header(fp)
            return True
        except (FileNotFoundError, StorageError):
            return False

    def is_legacy(self) -> bool:
        """12바이트 배열 크기이고 B+Tree magic이 없으면 기존 형식 후보로 본다."""
        if not self.path.exists() or self.size() % KEY.size:
            return False
        with self.path.open("rb") as fp:
            return fp.read(8) != MAGIC

    def _open(self) -> tuple[BinaryIO, _Session]:
        """남은 WAL을 먼저 처리하고 새 작업을 연다. 실패하면 파일도 닫는다."""
        self.recover()
        fp = self.path.open("rb")
        try:
            return fp, _Session(fp, self._header(fp))
        except BaseException:
            fp.close()
            raise

    def count(self) -> int:
        """메타데이터에서 키 수를 바로 읽는다. 삭제·옛 날짜 키도 포함될 수 있다."""
        fp, session = self._open()
        with fp:
            return session.meta.count

    def bisect_left(self, key: DateEntry) -> int:
        """key 이상인 첫 항목의 전체 순위: 앞 자식들의 키 수 + leaf 안의 순위."""
        fp, session = self._open()
        with fp:
            leaf, _, rank = session.find(key)
            result = rank + bisect_left(leaf.keys, key)
            self.last_page_reads = session.reads
            return result

    def _iter_ranks(self, lo: int, hi: int, reverse: bool) -> Iterator[DateEntry]:
        """순위 구간 [lo, hi)를 스트리밍한다. 시작 leaf를 찾은 뒤 이웃으로 이동한다."""
        fp, session = self._open()
        with fp:
            lo, hi = max(lo, 0), min(hi, session.meta.count)
            if lo >= hi:
                return
            page, pos = session.select(hi - 1 if reverse else lo)
            remaining = hi - lo
            steps = 0  # 파일의 페이지 수 이상 이동하면 잘못된 순환 링크로 판단한다.
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
        """기존 호출과 같은 순위 구간을 큰 키부터 반환한다."""
        return self._iter_ranks(lo, hi, True)

    def iter_asc(self, lo: int = 0, hi: int | None = None) -> Iterator[DateEntry]:
        """순위 구간을 작은 키부터 반환한다. hi가 없으면 끝까지 읽는다."""
        return self._iter_ranks(lo, self.count() if hi is None else hi, False)

    def iter_range(self, lower: DateEntry | None = None, upper: DateEntry | None = None,
                   *, reverse: bool = False) -> Iterator[DateEntry]:
        """키 범위 [lower, upper)를 정방향/역방향으로 읽는다. upper는 포함하지 않는다."""
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
                # 정방향은 lower 이상 첫 키, 역방향은 upper 미만 마지막 키에서 시작한다.
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
                    yield key  # 호출자가 limit에서 멈추면 나머지 페이지는 읽지 않는다.
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
        """트리 경로와 leaf 이진 탐색으로 복합 키 하나의 존재를 확인한다."""
        fp, session = self._open()
        with fp:
            page, _, _ = session.find(key)
            pos = bisect_left(page.keys, key)
            self.last_page_reads = session.reads
            return pos < len(page.keys) and page.keys[pos] == key

    def _insert(self, session: _Session, key: DateEntry) -> None:
        """leaf에 키를 넣고, 공간이 넘치면 부모 방향으로 분할을 전파한다.

        여기서는 메모리의 dirty 페이지만 바꾼다. 실제 파일 저장은 _commit이 한다.
        """
        _valid_key(key)
        page, path, _ = session.find(key)
        pos = bisect_left(page.keys, key)
        if pos < len(page.keys) and page.keys[pos] == key:
            # 같은 날짜와 같은 거래번호 조합은 한 번만 둔다. 다른 id면 같은 날짜도 허용한다.
            return
        page.keys.insert(pos, key)
        session.save(page)
        session.meta.count += 1
        # 4096바이트 기준 leaf 338키, internal 144분기 키가 상한이다.
        # 헤더와 CRC를 먼저 빼고, internal은 첫 자식 16바이트도 뺀다.
        leaf_cap = (session.meta.page_size - PAGE_HEADER.size - 4) // KEY.size
        branch_cap = (session.meta.page_size - PAGE_HEADER.size - 4 - CHILD.size) // BRANCH.size
        split: tuple[DateEntry, _Page] | None = None
        if len(page.keys) > leaf_cap:
            # leaf split: 보통은 왼쪽이 작은 절반, 새 오른쪽이 큰 절반을 보관한다.
            # 맨 끝에 붙인 키로 넘쳤으면(최신 날짜 add, 정렬해서 넣는 import) 왼쪽은 꽉 찬 채로 두고
            # 새 키만 옮긴다. 반반으로 나누면 다음 키도 오른쪽에만 쌓여 왼쪽 절반이 영영 비어 있다.
            right = session.new(True)
            mid = len(page.keys) - 1 if pos == len(page.keys) - 1 else len(page.keys) // 2
            right.keys, page.keys = page.keys[mid:], page.keys[:mid]
            right.prev, right.next = page.page_id, page.next
            if page.next:
                # A -> B를 A -> 새 leaf -> B로 바꾸므로 B의 뒤쪽 참조도 고쳐야 한다.
                neighbor = session.read(page.next)
                neighbor.prev = right.page_id
                session.save(neighbor)
            else:
                session.meta.last = right.page_id
            page.next = right.page_id
            split = (right.keys[0], right)  # leaf의 최소 키를 부모에 '복사'하고 leaf에도 남긴다.
            self._checkpoint("leaf_split")
        while path:
            # 가장 가까운 부모부터 거슬러 올라가 자식 키 수와 새 분기를 반영한다.
            parent, child_pos = path.pop()
            parent.counts[child_pos] = page.count
            # 맨 오른쪽 자식이 나뉘었는지. leaf 와 같은 이유로 이때는 부모도 한쪽으로 몰아 나눈다.
            tail = split is not None and child_pos == len(parent.children) - 1
            if split is not None:
                separator, right = split
                parent.keys.insert(child_pos, separator)
                parent.children.insert(child_pos + 1, right.page_id)
                parent.counts.insert(child_pos + 1, right.count)
            session.save(parent)
            split = None
            if len(parent.keys) > branch_cap:
                # internal split: 가운데 키는 부모로 '승격'하고 좌우 노드에서는 뺀다.
                # 키를 나눌 때는 자식이 항상 키보다 하나 많다는 관계도 함께 유지한다.
                # 맨 끝으로 몰 때도 오른쪽에 분기 키 하나(자식 둘)는 남긴다. 분기 키 0개인 internal 은 없다.
                mid = len(parent.keys) - 2 if tail else len(parent.keys) // 2
                separator = parent.keys[mid]
                right = session.new(False)
                right.keys, parent.keys = parent.keys[mid + 1:], parent.keys[:mid]
                right.children, parent.children = parent.children[mid + 1:], parent.children[:mid + 1]
                right.counts, parent.counts = parent.counts[mid + 1:], parent.counts[:mid + 1]
                split = (separator, right)
                self._checkpoint("internal_split")
            page = parent
        if split is not None:
            # 이전 root 위로 더 올라갈 부모가 없으므로 새 root를 만들어 높이를 1 늘린다.
            separator, right = split
            root = session.new(False)
            root.keys = [separator]
            root.children = [page.page_id, right.page_id]
            root.counts = [page.count, right.count]
            session.meta.root = root.page_id
            self._checkpoint("root_split")

    def _apply(self, fp: BinaryIO, images: dict[int, bytes], meta: _Meta) -> None:
        """검증된 페이지 이미지를 정해진 위치에 쓰고 파일 길이까지 확정한다."""
        # 노드들을 먼저 쓴 뒤 root 위치가 든 메타데이터를 마지막에 쓴다.
        # 이 순서만으로 원자적 저장이 되는 것은 아니며, 중단 시 남은 WAL이 전체를 복구한다.
        for page_id, image in images.items():
            if page_id:
                fp.seek(page_id * meta.page_size)
                fp.write(image)
                self._checkpoint("page_written")
        fp.seek(0)
        fp.write(images[0])
        self._checkpoint("root_written")
        fp.truncate(meta.pages * meta.page_size)  # split 후 늘어난 페이지를 포함한 정확한 길이
        fp.flush()
        os.fsync(fp.fileno())
        self._checkpoint("data_synced")

    def _commit(self, session: _Session) -> None:
        """페이지 after-image WAL을 먼저 확정하고 본 파일을 갱신한다.

        이 완료는 날짜 인덱스의 완료다. 거래의 슬롯 완료 여부는 저장소가 별도로 정한다.
        """
        if not session.dirty:
            return
        meta = session.meta
        meta.generation += 1
        # 1. 변경한 노드와 메타데이터의 완성본을 만든다. 전체 인덱스를 복사하지 않는다.
        images = {page_id: page.encode(meta) for page_id, page in session.dirty.items()}
        images[0] = meta.encode()
        body = WAL_HEADER.pack(b"BPTWAL01", meta.page_size, meta.identity, meta.pages, len(images))
        body += b"".join(PAGE_ID.pack(page_id) + raw for page_id, raw in images.items())
        # 2. 임시 WAL 전체와 SHA-256 digest(원시 32바이트)를 저장·fsync한다.
        tmp = self.wal_path.with_name(self.wal_path.name + ".tmp")
        with tmp.open("wb") as fp:
            fp.write(body + hashlib.sha256(body).digest())
            fp.flush()
            os.fsync(fp.fileno())
        self._checkpoint("wal_prepared")
        # 3. 정식 WAL 이름을 디렉터리까지 확정한다. 이 전에는 본 파일을 쓰지 않는다.
        os.replace(tmp, self.wal_path)
        fsync_dir(self.path.parent)
        self._checkpoint("wal_published")
        # 4. 여기서부터 종료되면 recover()가 같은 이미지를 다시 적용할 수 있다.
        with self.path.open("r+b") as fp:
            self._apply(fp, images, meta)
        # 5. 본 파일 fsync 뒤에만 WAL을 지운다. 삭제한 사실도 디렉터리에 확정한다.
        self.wal_path.unlink()
        self._checkpoint("wal_removed")
        fsync_dir(self.path.parent)

    def merge(self, entries: Iterable[DateEntry]) -> None:
        """중복을 제외해 키들을 삽입하고 바뀐 페이지만 WAL로 확정한다.

        바뀐 페이지가 MAX_DIRTY_PAGES 개를 넘으면 그때까지를 WAL 하나로 먼저 확정하고 이어서 넣는다.
        그래서 날짜 인덱스의 원자성은 그 묶음 단위다. 거래의 완료는 위치 슬롯이 정하고, 슬롯이 없는
        키는 조회에서 걸러지므로(import 전체 취소도 슬롯 기록이 맡음) 일부 묶음만 확정돼도 안전하다.
        """
        if not self.path.exists():
            self.rebuild(())
        fp, session = self._open()
        with fp:
            for key in entries:
                self._insert(session, key)
                if len(session.dirty) >= MAX_DIRTY_PAGES:
                    self._commit(session)
                    # 확정한 페이지는 디스크와 같으므로 놓아준다. 다시 필요하면 파일에서 읽는다.
                    session.dirty.clear()
            self._commit(session)
            self.last_page_reads = session.reads

    def merge_keys(self, keys: Iterable[int]) -> None:
        """저장소에서 메모리용 정수 하나로 합쳐 넘긴 날짜·거래번호를 다시 분리한다."""
        # 위쪽 비트는 날짜, 아래 64비트는 거래번호다. 디스크 키는 여전히 <IQ> 12바이트다.
        self.merge((key >> 64, key & MAX_ID) for key in keys)

    def rebuild(self, entries: Iterable[DateEntry]) -> None:
        """정렬된 키로 옆의 임시 파일에 새 트리를 완성·fsync한 뒤 원래 이름으로 교체한다.

        entries 는 오름차순이어야 한다(같은 키는 한 번만 둔다). 키를 하나씩 삽입하지 않고 leaf 를 왼쪽부터
        꽉 채워 쓴 다음 그 위층을 아래에서부터 쌓는다(_bulk_load). 중간에 실패하면 원래 인덱스는 유지된다.
        legacy 원본 백업은 저장소가 먼저 한다. 재생성한 파일에는 새 UUID를 부여해 이전 파일의 WAL과 구별한다.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".build")
        meta = _Meta(self.page_size, uuid.uuid4().bytes)
        with tmp.open("w+b") as fp:
            # 임시 파일은 아직 사용자 인덱스가 아니다. 완성 전까지 기존 파일을 교체하지
            # 않으므로, 이 빌드 자체는 별도 WAL 없이 임시 파일 전체를 완성하면 된다.
            self._bulk_load(fp, meta, entries)
            fp.seek(0)
            fp.write(meta.encode())
            fp.truncate(meta.pages * meta.page_size)
            fp.flush()
            os.fsync(fp.fileno())
        self._checkpoint("rebuild_synced")
        os.replace(tmp, self.path)
        fsync_dir(self.path.parent)
        # 새 UUID의 파일로 교체를 확정했으므로 이전 인덱스의 WAL은 폐기한다.
        self.wal_path.unlink(missing_ok=True)
        self.wal_path.with_name(self.wal_path.name + ".tmp").unlink(missing_ok=True)
        fsync_dir(self.path.parent)

    def rebuild_merged(self, entries: Iterable[DateEntry]) -> None:
        """지금 들어 있는 키와 정렬된 entries 를 합쳐 rebuild 한다(큰 import 용).

        넣을 키가 많으면 하나씩 삽입해 WAL 로 확정하는 것보다, 두 정렬 목록을 한 번 훑어 새 파일을 만드는
        편이 빠르고 leaf 도 꽉 찬다. 교체 전에 꺼지면 원래 인덱스가 그대로 남는다.
        """
        existing = self.iter_range() if self.path.exists() else iter(())
        self.rebuild(heapq.merge(existing, entries))

    def _bulk_load(self, fp: BinaryIO, meta: _Meta, entries: Iterable[DateEntry]) -> None:
        """leaf 를 왼쪽부터 꽉 채워 쓰고, 각 층의 (첫 키, 페이지 번호, 키 수)로 그 위층을 만든다.

        페이지 번호는 leaf 가 1부터 차례로, 그다음 internal 이 아래층부터 붙는다. leaf 의 앞뒤 연결은
        번호가 연달아 있으므로 바로 정해진다. 메모리에는 쓰고 있는 leaf 하나와 위층 재료만 둔다.
        """
        size = meta.page_size
        leaf_cap = (size - PAGE_HEADER.size - 4) // KEY.size
        fanout = (size - PAGE_HEADER.size - 4 - CHILD.size) // BRANCH.size + 1  # internal 의 최대 자식 수
        meta.pages, meta.root, meta.count = 1, 0, 0  # root 는 마지막 층을 만들 때 정해진다
        level: list[tuple[DateEntry, int, int]] = []  # 위층 재료. leaf 338개당 한 항목이라 작다
        keys: list[DateEntry] = []
        previous: DateEntry | None = None

        def write_leaf(last: bool) -> None:
            page_id = meta.pages
            meta.pages += 1
            if last and not level:
                meta.root = page_id  # leaf 가 하나뿐이면 그 leaf 가 root 다
            page = _Page(page_id, True, keys, prev=page_id - 1 if level else 0, next=0 if last else page_id + 1)
            fp.seek(page_id * size)
            fp.write(page.encode(meta))
            level.append((keys[0] if keys else (0, 0), page_id, len(keys)))

        for key in entries:
            if previous is not None and key <= previous:
                if key == previous:
                    continue  # 같은 날짜와 같은 거래번호 조합은 한 번만 둔다
                raise ValueError("rebuild 에는 오름차순으로 정렬된 키를 넘겨야 합니다")
            _valid_key(key)
            previous = key
            if len(keys) == leaf_cap:
                write_leaf(last=False)  # 다음 키가 있으므로 이 leaf 는 마지막이 아니다
                keys = []
            keys.append(key)
            meta.count += 1
        write_leaf(last=True)  # 키가 없으면 빈 root leaf 하나가 된다
        meta.first, meta.last = 1, meta.pages - 1
        while len(level) > 1:
            groups = [level[i:i + fanout] for i in range(0, len(level), fanout)]
            if len(groups[-1]) == 1:
                # 자식 하나짜리 internal 은 분기 키가 0개라 허용되지 않는다. 앞 묶음에서 하나 가져온다.
                groups[-1].insert(0, groups[-2].pop())
            upper: list[tuple[DateEntry, int, int]] = []
            for group in groups:
                page = _Page(meta.pages, False, keys=[item[0] for item in group[1:]],
                             children=[item[1] for item in group], counts=[item[2] for item in group])
                meta.pages += 1
                if len(groups) == 1:
                    meta.root = page.page_id
                fp.seek(page.page_id * size)
                fp.write(page.encode(meta))
                upper.append((group[0][0], page.page_id, page.count))
            level = upper

    def validate(self) -> dict[str, int]:
        """전체 구조를 검사한다. O(N) 점검이므로 평소 단건 검색 때마다 호출하지 않는다."""
        fp, session = self._open()
        with fp:
            visited: set[int] = set()
            leaves: list[_Page] = []

            def walk(page_id: int) -> tuple[DateEntry | None, DateEntry | None, int, int]:
                """아래로 순회해 (최소 키, 최대 키, 키 수, 높이)를 부모에 알려 준다."""
                if page_id in visited:
                    raise _error("중복/순환 자식 페이지")
                visited.add(page_id)
                page = session.read(page_id)
                if page.leaf:
                    leaves.append(page)
                    return (page.keys[0] if page.keys else None, page.keys[-1] if page.keys else None,
                            len(page.keys), 1)
                children = [walk(child) for child in page.children]
                # B+Tree의 모든 leaf는 같은 깊이에 있어야 검색 경로 길이가 고르게 유지된다.
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
            # 트리 경로로 찾은 leaf 순서와 prev/next 연결 순서가 서로 같아야 한다.
            for i, leaf in enumerate(leaves):
                if (leaf.prev, leaf.next) != (leaves[i - 1].page_id if i else 0,
                                             leaves[i + 1].page_id if i + 1 < len(leaves) else 0):
                    raise _error("leaf 연결 순서 불일치")
            return {"entries": count, "height": height, "pages": session.meta.pages,
                    "leaves": len(leaves), "page_size": session.meta.page_size}
