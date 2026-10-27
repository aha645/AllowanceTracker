"""거래 저장 엔진: append-only 거래 데이터 파일(JSONL) + 위치 기반 고정폭 이진 인덱스 + 날짜 정렬 인덱스.

파일 구성
    transactions.jsonl     거래 원본. append 전용(수정도 새 버전을 끝에 덧붙인다).
    transactions.idx       id -> 현재 유효 byte 범위 매핑. 슬롯당 16바이트 고정폭.
    transactions.date.idx  (거래일자, id) 를 날짜순으로 정렬해 둔 보조 인덱스. 항목당 12바이트 고정폭.

인덱스 슬롯
    슬롯 위치가 곧 id 다. id N 의 슬롯은 (N-1)*16 바이트 위치에 있고
    `struct.pack("<QQ", start, end)` 로 기록된다. start == end == 0 이면 삭제된 슬롯이다
    (실제 레코드는 항상 end > start 이므로 오프셋 0에서 시작하는 TX-1 과 혼동되지 않는다).

날짜 인덱스
    id 순서(= 등록 순서)와 거래일자 순서는 다를 수 있다(예: 옛 날짜 CSV 를 import).
    그래서 최신순 조회는 id 슬롯이 아니라 이 정렬 인덱스를 맨 끝부터 역순으로 읽는다.
    항목은 `struct.pack("<IQ", date.toordinal(), id)` 이며 (날짜, id) 오름차순으로 정렬돼 있다.
    update 로 날짜가 바뀌면 새 항목을 끼워 넣고 옛 항목은 지우지 않는다(읽을 때 건너뜀).
    삭제된 거래의 항목도 마찬가지다. 이 파일은 transactions.jsonl/idx 에서 언제든 다시 만들 수 있다.

쓰기 순서
    jsonl(내용) → date.idx(날짜 항목) → idx(위치 슬롯). idx 슬롯 fsync 후 undo 기록의 삭제를 확정하면 "저장 완료"다.
    중간에 꺼지면 date.idx 에 아직 idx 에 없는 id 의 항목이 남을 수 있는데, 읽을 때 슬롯 범위 밖이라
    건너뛴다. 중단된 슬롯 쓰기는 .slot-undo로 되돌린다. 반대 순서였다면 저장된 거래가 날짜 색인에만 빠져
    list/summary 에서 조용히 사라졌다.
"""

from __future__ import annotations

import os
import shutil
import struct
import sys
from array import array
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from itertools import chain
from typing import BinaryIO, Iterable, Iterator

from .durability import SlotUndo, fsync_dir
from .models import NotFoundError, StorageError, Transaction

SLOT_SIZE = 16
SLOT_STRUCT = struct.Struct("<QQ")
DELETED_SLOT = (0, 0)

DATE_ENTRY_STRUCT = struct.Struct("<IQ")
DATE_ENTRY_SIZE = DATE_ENTRY_STRUCT.size  # 12
MERGE_CHUNK_ENTRIES = 4096

#: (날짜 순번, 거래 id). 날짜 순번은 `date.toordinal()` 이다.
DateEntry = tuple[int, int]

_ID_BITS = 64
_ID_MASK = (1 << _ID_BITS) - 1


def entry_key(entry: DateEntry) -> int:
    """(날짜 순번, id) 를 정렬 순서가 같은 정수 하나로 합친다.

    Python 정수로 날짜와 unsigned 64-bit id를 손실 없이 담는다. 많은 키를 담을 때는 이 정수 대신
    날짜·id 배열을 따로 쓴다(정수 객체는 건당 수십 바이트).
    """
    ordinal, tx_id = entry
    return ordinal << _ID_BITS | tx_id


def _pack_key(key: int) -> bytes:
    return DATE_ENTRY_STRUCT.pack(key >> _ID_BITS, key & _ID_MASK)


def _unique(sorted_keys: Iterable[int]) -> Iterator[int]:
    """오름차순 키에서 연속 중복을 뺀다. 순서가 어긋나면 병합 결과가 틀리므로 오류로 멈춘다."""
    prev: int | None = None
    for key in sorted_keys:
        if prev is not None and key < prev:
            raise ValueError("날짜 키는 오름차순으로 넘겨야 합니다")
        if key != prev:
            yield key
            prev = key


def _sorted_keys(ordinals: array[int], ids: array[int]) -> Iterator[DateEntry]:
    """id 오름차순으로 모은 (날짜, id) 를 정렬 순서로 하나씩 낸다(import 와 날짜 인덱스 재생성 용).

    날짜 인덱스의 병합(DateIndex.merge_keys)과 재생성(DateIndex.rebuild)은 정렬된 키를 요구한다.
    sorted() 로 정렬하면 키마다 파이썬 정수 객체와 리스트 칸이 생겨(건당 수십 바이트) 100만 건 import 가
    92MB, compact 가 350MB 를 썼다. id 는 이미 오름차순이고 날짜 종류는 많아야 수천 개라, 날짜별로
    나누기만 하면 정렬이 끝난다(계수 정렬, 추가 메모리는 id 배열 하나).
    """
    counts: dict[int, int] = {}
    for ordinal in ordinals:
        counts[ordinal] = counts.get(ordinal, 0) + 1
    starts: dict[int, int] = {}
    position = 0
    for ordinal in sorted(counts):
        starts[ordinal] = position
        position += counts[ordinal]
    placed = array("Q", bytes(8 * len(ids)))
    cursor = dict(starts)
    for ordinal, tx_id in zip(ordinals, ids):
        placed[cursor[ordinal]] = tx_id
        cursor[ordinal] += 1
    for ordinal in sorted(counts):
        start = starts[ordinal]
        for i in range(start, start + counts[ordinal]):
            yield ordinal, placed[i]


def date_ordinal(value: str) -> int:
    """`YYYY-MM-DD` 문자열을 날짜 순번(정수)으로 바꾼다."""
    try:
        return date.fromisoformat(value).toordinal()
    except ValueError as exc:
        raise StorageError(
            f"저장된 거래 날짜를 해석할 수 없습니다: '{value}'",
            "transactions.jsonl 이 손상되었을 수 있습니다. backup 명령으로 백업본을 확인하세요.",
        ) from exc


class TransactionIndex:
    """`transactions.idx` 한 파일을 다루는 얇은 래퍼."""

    def __init__(self, path: Path) -> None:
        self.path: Path = path

    def ensure(self) -> None:
        """파일이 없으면 빈 파일로 생성한다."""
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.touch()

    def drop_partial_slot(self) -> int:
        """끝에 덜 써진 슬롯 조각이 있으면 잘라 내고, 잘라 낸 바이트 수를 돌려준다.

        슬롯은 끝에만 덧붙이고 슬롯을 쓰는 순간이 저장 완료이므로(모듈 설명), 16바이트가 안 되는
        끝 조각은 add 가 슬롯을 쓰다 꺼진 "완료되지 않은 저장"이다(import 는 기록 파일로 묶음째 되돌린다). 잘라 내면 그 저장은 없던
        일이 되고, 남은 jsonl 줄과 날짜 항목은 읽을 때 건너뛰다 compact 때 정리된다. 예전에는 이 상태에서
        모든 명령이 "백업본으로 복구하세요" 오류로 멈췄다.
        """
        if not self.path.exists():
            return 0
        extra = self.size() % SLOT_SIZE
        if extra:
            with self.path.open("r+b") as fp:
                fp.truncate(self.size() - extra)
                fp.flush()
                os.fsync(fp.fileno())
        return extra

    def size(self) -> int:
        try:
            return self.path.stat().st_size
        except FileNotFoundError:
            return 0

    def slot_count(self) -> int:
        """슬롯 개수(= 지금까지 발급된 최대 id)."""
        size = self.size()
        if size % SLOT_SIZE:
            raise StorageError(
                f"인덱스 파일 크기({size}B)가 슬롯 크기 {SLOT_SIZE}B 의 배수가 아닙니다.",
                f"{self.path} 가 손상되었습니다. 백업본으로 복구하세요.",
            )
        return size // SLOT_SIZE

    def iter_slot_arrays(self) -> Iterator[tuple[array[int], array[int]]]:
        """슬롯을 MERGE_CHUNK_ENTRIES 개씩 읽어 (start 배열, end 배열) 로 돌려준다.

        16바이트씩 struct 로 풀면 100만 건에 0.4초가 걸려, 묶음째 array 에 담아 세기·합을 C 쪽에 맡긴다.
        """
        if not self.path.exists():
            return
        with self.path.open("rb") as fp:
            while raw := fp.read(MERGE_CHUNK_ENTRIES * SLOT_SIZE):
                values = array("Q")
                values.frombytes(raw[: len(raw) - len(raw) % SLOT_SIZE])
                if sys.byteorder == "big":  # 파일은 리틀 엔디언("<QQ")이다
                    values.byteswap()
                yield values[0::2], values[1::2]

    def live_count(self) -> int:
        """살아있는 슬롯 수. 삭제된 슬롯은 (0, 0) 이고 살아있는 슬롯은 항상 end > 0 이다."""
        return sum(len(ends) - ends.count(0) for _, ends in self.iter_slot_arrays())

    def next_id(self) -> int:
        """다음에 발급될 거래 id."""
        return self.slot_count() + 1

    def read_slot(self, tx_id: int) -> tuple[int, int] | None:
        """id 의 (start, end) 를 돌려준다. 없는 id 이거나 삭제된 슬롯이면 None."""
        if tx_id < 1 or tx_id > self.slot_count():
            return None
        with self.path.open("rb") as fp:
            fp.seek((tx_id - 1) * SLOT_SIZE)
            raw = fp.read(SLOT_SIZE)
        if len(raw) != SLOT_SIZE:
            return None
        start, end = SLOT_STRUCT.unpack(raw)
        if (start, end) == DELETED_SLOT:
            return None
        return start, end

    def append_slot(self, start: int, end: int) -> int:
        """새 슬롯을 파일 끝에 덧붙이고 부여된 id 를 돌려준다."""
        self.ensure()
        new_id = self.next_id()
        with self.path.open("ab") as fp:
            fp.write(SLOT_STRUCT.pack(start, end))
            fp.flush()
            os.fsync(fp.fileno())
        return new_id

    def write_slot(self, tx_id: int, start: int, end: int) -> None:
        """기존 슬롯을 제자리(in-place) 덮어쓴다. 파일 전체 재작성 없음."""
        with self.path.open("r+b") as fp:
            fp.seek((tx_id - 1) * SLOT_SIZE)
            fp.write(SLOT_STRUCT.pack(start, end))
            fp.flush()
            os.fsync(fp.fileno())

    def clear_slot(self, tx_id: int) -> None:
        """슬롯 16바이트를 전부 0으로 만들어 삭제 표시한다."""
        self.write_slot(tx_id, 0, 0)


class DateIndex:
    """`transactions.date.idx` 한 파일을 다루는 래퍼: (날짜 순번, id) 오름차순 고정폭 배열."""

    def __init__(self, path: Path) -> None:
        self.path: Path = path

    def size(self) -> int:
        try:
            return self.path.stat().st_size
        except FileNotFoundError:
            return 0

    def is_valid(self) -> bool:
        """파일이 존재하고 크기가 항목 크기의 배수인지(손상되지 않았는지)."""
        return self.path.exists() and self.size() % DATE_ENTRY_SIZE == 0

    def count(self) -> int:
        size = self.size()
        if size % DATE_ENTRY_SIZE:
            raise StorageError(
                f"날짜 인덱스 파일 크기({size}B)가 항목 크기 {DATE_ENTRY_SIZE}B 의 배수가 아닙니다.",
                f"{self.path} 를 삭제하고 다시 실행하면 자동으로 재생성됩니다(또는 compact 실행).",
            )
        return size // DATE_ENTRY_SIZE

    @staticmethod
    def _read(fp: BinaryIO, pos: int) -> DateEntry:
        fp.seek(pos * DATE_ENTRY_SIZE)
        raw = fp.read(DATE_ENTRY_SIZE)
        if len(raw) != DATE_ENTRY_SIZE:
            raise StorageError("날짜 인덱스를 읽는 도중 파일이 끝났습니다.", "compact 로 인덱스를 재생성하세요.")
        return DATE_ENTRY_STRUCT.unpack(raw)

    def bisect_left(self, key: DateEntry) -> int:
        """정렬된 항목 중 key 이상인 첫 위치(없으면 count). 이진 탐색이라 O(log n) 번만 읽는다."""
        total = self.count()
        if total == 0:
            return 0
        lo, hi = 0, total
        with self.path.open("rb") as fp:
            while lo < hi:
                mid = (lo + hi) // 2
                if self._read(fp, mid) < key:
                    lo = mid + 1
                else:
                    hi = mid
        return lo

    def iter_desc(self, lo: int, hi: int) -> Iterator[DateEntry]:
        """[lo, hi) 구간의 항목을 맨 끝(hi-1)부터 역순으로 하나씩 yield 한다(제너레이터)."""
        if lo >= hi:
            return
        with self.path.open("rb") as fp:
            for pos in range(hi - 1, lo - 1, -1):
                yield self._read(fp, pos)

    def _last(self) -> DateEntry | None:
        total = self.count()
        if total == 0:
            return None
        with self.path.open("rb") as fp:
            return self._read(fp, total - 1)

    def merge(self, entries: Iterable[DateEntry], drop_id: int | None = None) -> None:
        """새 항목 몇 개를 정렬 상태를 유지하며 끼워 넣는다(add/update 용, 이미 있는 항목은 무시)."""
        self.merge_keys(sorted({entry_key(e) for e in entries}), drop_id)

    def merge_keys(self, keys: Iterable[int], drop_id: int | None = None) -> None:
        """오름차순 `entry_key()` 정수 키를 병합한다(import 는 계수 정렬한 키를 하나씩 넘긴다).

        모두 마지막 항목보다 크면(평소 add) 끝에 append 만 한다. 그렇지 않으면(옛 날짜 거래/import)
        기존 파일을 한 번만 훑으며 병합해 임시 파일에 쓰고 os.replace 로 교체한다. O(n+m).
        `drop_id` 를 주면 파일을 다시 쓸 때 그 id 의 기존 항목을 빼고 쓴다(update 가 옛 날짜 항목을 지울 때).
        끝에 덧붙이는 경우에는 파일 전체를 다시 쓰지 않으려고 빼지 않는다(읽을 때 건너뛰고 compact 가 정리).
        """
        pending = _unique(keys)
        nxt = next(pending, None)
        if nxt is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        last = self._last() if self.path.exists() else None
        if last is None or nxt > entry_key(last):
            with self.path.open("ab") as fp:
                self._write_keys(fp, chain([nxt], pending))
                fp.flush()
                os.fsync(fp.fileno())
            return
        tmp_path = self.path.with_name(self.path.name + ".tmp")
        with self.path.open("rb") as src, tmp_path.open("wb") as dst:
            while True:
                chunk = src.read(MERGE_CHUNK_ENTRIES * DATE_ENTRY_SIZE)
                if not chunk:
                    break
                out: list[bytes] = []
                for cur in DATE_ENTRY_STRUCT.iter_unpack(chunk):
                    cur_key = entry_key(cur)
                    while nxt is not None and nxt < cur_key:
                        out.append(_pack_key(nxt))
                        nxt = next(pending, None)
                    if cur[1] == drop_id:  # 지울 항목. 같은 키를 새로 넣는 중이면 nxt 가 다음 차례에 쓴다
                        continue
                    if nxt == cur_key:  # 이미 있는 항목
                        nxt = next(pending, None)
                    out.append(DATE_ENTRY_STRUCT.pack(*cur))
                dst.write(b"".join(out))
            if nxt is not None:
                self._write_keys(dst, chain([nxt], pending))
            dst.flush()
            os.fsync(dst.fileno())
        os.replace(tmp_path, self.path)
        # 교체를 확정하지 않으면, 이 뒤에 위치 슬롯을 확정한 다음 전원이 꺼졌을 때 교체가 되돌아가
        # 저장된 거래의 날짜 항목이 없어진다(목록·요약에서 빠짐). 쓰기 순서를 지키려면 여기서 확정해야 한다.
        fsync_dir(self.path.parent)

    @staticmethod
    def _write_keys(fp: BinaryIO, keys: Iterable[int]) -> None:
        """키를 MERGE_CHUNK_ENTRIES 개씩 묶어 쓴다(한 번에 join 하면 건마다 bytes 객체가 쌓인다)."""
        out: list[bytes] = []
        for key in keys:
            out.append(_pack_key(key))
            if len(out) >= MERGE_CHUNK_ENTRIES:
                fp.write(b"".join(out))
                out.clear()
        if out:
            fp.write(b"".join(out))

    def rebuild(self, entries: Iterable[DateEntry]) -> None:
        """오름차순 항목으로 파일을 처음부터 다시 만든다(임시 파일 + os.replace)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(self.path.name + ".tmp")
        with tmp_path.open("wb") as fp:
            self._write_keys(fp, _unique(entry_key(e) for e in entries))
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp_path, self.path)
        fsync_dir(self.path.parent)  # merge_keys() 와 같은 이유로 교체를 확정한다


@dataclass(slots=True)
class StorageStats:
    """저장 파일 상태(컴팩션 필요 여부 판단용)."""

    slot_count: int
    live_count: int
    tr_size: int
    live_size: int

    @property
    def orphan_size(self) -> int:
        return max(self.tr_size - self.live_size, 0)

    @property
    def orphan_ratio(self) -> float:
        if self.tr_size <= 0:
            return 0.0
        return self.orphan_size / self.tr_size


class TransactionStore:
    """거래 저장소. 거래 데이터 append 와 인덱스 슬롯 갱신을 함께 책임진다."""

    TR_NAME = "transactions.jsonl"
    IDX_NAME = "transactions.idx"
    DATE_IDX_NAME = "transactions.date.idx"
    #: 고아 데이터 비율이 이 값을 넘으면 compact 안내를 띄운다.
    COMPACT_HINT_RATIO = 0.5

    def __init__(self, data_dir: Path) -> None:
        self.data_dir: Path = Path(data_dir)
        self.tr_path: Path = self.data_dir / self.TR_NAME
        self.idx_path: Path = self.data_dir / self.IDX_NAME
        self.index: TransactionIndex = TransactionIndex(self.idx_path)
        self.date_idx_path: Path = self.data_dir / self.DATE_IDX_NAME
        self.date_index: DateIndex = DateIndex(self.date_idx_path)
        # compact 가 위치 인덱스를 고치기 전에 만들어 두는 복사본. 이 파일이 있으면 compact 가 끝나지 않은 것이다.
        self.compact_backup_path: Path = self.data_dir / (self.IDX_NAME + ".compact-backup")
        self.compact_tmp_tr_path: Path = self.tr_path.with_suffix(".jsonl.tmp")
        # import 가 위치 슬롯 묶음을 쓰기 전의 위치 파일 크기를 적어 두는 파일. 있으면 import 가 끝나지 않은 것이다.
        self.import_journal_path: Path = self.data_dir / (self.IDX_NAME + ".import-pending")
        # 16바이트 슬롯 쓰기가 도중에 끊겨도 원래 길이/슬롯으로 되돌릴 수 있게 한다.
        self.slot_undo = SlotUndo(self.idx_path)
        #: 이번 실행에서 자동으로 바로잡은 일(사용자에게 [안내] 로 알린다).
        self.recovered: list[str] = []

    # ------------------------------------------------------------------ 초기화
    def ensure_files(self) -> None:
        """최초 실행 시 빈 거래 데이터/인덱스 파일을 만들어 둔다."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.tr_path.exists():
            self.tr_path.touch()
        self._recover_interrupted_compact()  # 위치 인덱스를 통째로 되돌릴 수 있으므로 가장 먼저
        self.index.ensure()
        self._recover_interrupted_import()  # 조각 정리보다 먼저: import 의 조각은 묶음째 잘라 낸다
        # 거래 존재 여부의 기준인 idx를 먼저 바로잡은 다음 날짜 보조 인덱스를 복구한다.
        # 되돌리기 기록이 남았으면 그 저장은 날짜 인덱스를 고친 뒤 멈췄을 수 있다. update 는 옛 날짜 항목을
        # 지운 날짜 인덱스를 위치 칸보다 먼저 쓰므로, 위치 칸만 되돌리면 그 거래가 목록에서 빠진다.
        # 그래서 이때는 날짜 인덱스도 원본에서 다시 만든다(꺼진 뒤 한 번만 드는 O(n) 비용).
        slot_recovered = self.slot_undo.recover()
        if slot_recovered:
            self.recovered.append("끝나지 않은 거래 저장의 위치 슬롯을 이전 상태로 복구했습니다.")
        if self.index.drop_partial_slot():
            self.recovered.append("끝나지 않은 마지막 저장(add)의 조각을 정리했습니다. 그 거래는 저장되지 않았습니다.")
        # 날짜 인덱스가 없거나 손상됐으면(크기가 12의 배수가 아님) 원본(idx+jsonl)에서 다시 만든다.
        # 날짜 인덱스는 언제든 다시 만들 수 있는 보조 자료라 따로 백업하지 않는다.
        if slot_recovered:
            self.rebuild_date_index()
        elif not self.date_index.is_valid():
            damaged = self.date_idx_path.exists()  # 없던 파일(첫 실행)은 조용히 만든다
            self.rebuild_date_index()
            if damaged:
                self.recovered.append("날짜 인덱스가 손상되어 거래 파일에서 다시 만들었습니다.")

    def _live_date_entries(self) -> Iterator[DateEntry]:
        """살아있는 모든 거래의 (날짜 순번, id) 를 id 순서로 yield 한다."""
        for tx in self._iter_live_by_id():
            yield date_ordinal(tx.date), tx.id

    def _iter_live_by_id(self) -> Iterator[Transaction]:
        """id 오름차순으로 살아있는 거래를 하나씩 yield 한다(날짜 인덱스를 쓰지 않는다)."""
        total = self.index.slot_count()
        if total == 0 or not self.tr_path.exists():
            return
        with self.idx_path.open("rb") as idx, self.tr_path.open("rb") as tr:
            for slot in range(total):
                idx.seek(slot * SLOT_SIZE)
                raw = idx.read(SLOT_SIZE)
                if len(raw) != SLOT_SIZE:
                    break
                start, end = SLOT_STRUCT.unpack(raw)
                if (start, end) == DELETED_SLOT:
                    continue
                yield self._read_at(tr, start, end)

    def category_counts(self) -> dict[str, int]:
        """살아있는 거래를 처음부터 세어 카테고리별 건수를 돌려준다(O(n), 기록을 다시 맞출 때만 쓴다)."""
        counts: dict[str, int] = {}
        for tx in self._iter_live_by_id():
            counts[tx.category] = counts.get(tx.category, 0) + 1
        return counts

    def rebuild_date_index(self) -> None:
        """transactions.jsonl/idx 로부터 날짜 인덱스를 처음부터 다시 만든다."""
        # 키는 import 와 같이 건당 12바이트 배열에 모아 계수 정렬한다(튜플로 모으면 100만 건에 수백 MB).
        ordinals = array("I")
        ids = array("Q")
        for ordinal, tx_id in self._live_date_entries():
            ordinals.append(ordinal)
            ids.append(tx_id)
        self.date_index.rebuild(_sorted_keys(ordinals, ids))

    # ------------------------------------------------------------------ 쓰기
    def _append_record(self, tx: Transaction) -> tuple[int, int]:
        """레코드를 거래 데이터 파일 끝에 붙이고 (start, end) byte 범위를 돌려준다."""
        payload = tx.to_json().encode("utf-8")
        with self.tr_path.open("ab") as fp:
            fp.seek(0, os.SEEK_END)
            start = fp.tell()
            fp.write(payload)
            fp.write(b"\n")
            fp.flush()
            os.fsync(fp.fileno())
        return start, start + len(payload)

    def append_transaction(self, tx: Transaction) -> Transaction:
        """새 거래를 저장하고 id 가 채워진 Transaction 을 돌려준다."""
        self.ensure_files()
        new_id = self.index.next_id()
        stored = tx.replace_fields(id=new_id)
        start, end = self._append_record(stored)
        # 날짜 항목을 슬롯보다 먼저 쓴다. 슬롯을 먼저 쓰고 그 사이에 꺼지면 id 로는 찾히는 거래가
        # 날짜 색인에만 빠져 list/summary 에서 조용히 사라진다. 이 순서면 꺼져도 남는 건 슬롯 범위 밖
        # id 의 날짜 항목뿐이라 읽을 때 건너뛰고, 그 id 는 다음 add 때 다시 발급된다.
        self.date_index.merge([(date_ordinal(stored.date), stored.id)])
        # 날짜 항목을 썼다고 거래가 완료된 것은 아니다. idx를 쓰기 전에 undo를 먼저 확정한다.
        self.slot_undo.prepare(new_id)
        assigned = self.index.append_slot(start, end)
        if assigned != new_id:  # pragma: no cover - 동시 실행 방어
            raise StorageError(
                f"id 발급이 어긋났습니다(expected={new_id}, actual={assigned}).",
                "같은 data-dir 에 대해 여러 프로세스를 동시에 실행하지 마세요.",
            )
        self.slot_undo.commit()
        return stored

    def append_transactions(self, transactions: Iterable[Transaction]) -> int:
        """여러 거래를 한 번에 저장하고 저장한 건수를 돌려준다(import 용).

        거래 데이터 레코드와 인덱스 슬롯은 각각 한 번에 이어 쓰고(fsync 도 파일당 한 번), 날짜 인덱스는 모아서
        한 번에 병합한다. 옛 날짜가 섞여 있어도 행마다 파일을 다시 쓰지 않는다.
        거래 객체는 모으지 않고, 슬롯과 날짜 키는 건당 16+12바이트 배열에 담는다.
        """
        self.ensure_files()
        next_id = self.index.next_id()
        count = 0
        # 위치 슬롯은 bytearray, 날짜 항목은 날짜(4바이트)와 id(8바이트) 배열에 따로 모은다.
        # 64-bit id 를 날짜와 합치면 64비트를 넘어 array 에 못 담고, 정수 리스트는 건당 약 40바이트라
        # 100만 건 import 가 수백 MB 를 썼다. 따로 담으면 64-bit id 를 지키면서 건당 12바이트다.
        slots = bytearray()
        ordinals = array("I")
        ids = array("Q")
        with self.tr_path.open("ab") as tr:
            tr.seek(0, os.SEEK_END)
            offset = tr.tell()
            for tx in transactions:
                stored = tx.replace_fields(id=next_id + count)
                payload = stored.to_json().encode("utf-8")
                tr.write(payload + b"\n")
                slots += SLOT_STRUCT.pack(offset, offset + len(payload))
                offset += len(payload) + 1
                count += 1
                ordinals.append(date_ordinal(stored.date))
                ids.append(stored.id)
            tr.flush()
            os.fsync(tr.fileno())
        if not count:
            return 0
        # append_transaction() 과 같은 이유로 날짜 항목을 슬롯보다 먼저 쓴다. 슬롯 묶음을 쓰기 전에
        # 꺼지면 import 한 거래 전체가 슬롯 범위 밖이 되어, 반쯤 보이는 상태 없이 통째로 없던 일이 된다.
        self.date_index.merge_keys(entry_key(key) for key in _sorted_keys(ordinals, ids))
        # 슬롯 묶음은 크기가 커서 쓰는 도중 꺼지면 앞쪽 슬롯만 남아 일부 행만 들어간 상태가 될 수 있다.
        # 그래서 쓰기 전 크기를 기록해 두고, 다 쓴 뒤 그 기록을 지우는 순간을 import 완료로 삼는다.
        # 기록이 남아 있으면 다음 실행 때 _recover_interrupted_import() 가 그 크기로 잘라 import 전체를 취소한다.
        self._write_import_journal(self.index.size())
        with self.idx_path.open("ab") as idx:
            idx.write(slots)
            idx.flush()
            os.fsync(idx.fileno())
        self._commit_import()
        return count

    def _write_import_journal(self, idx_size: int) -> None:
        """import 전 위치 파일 크기를 기록한다. 임시 이름으로 쓰고 바꿔서, 기록 파일은 없거나 완전하다."""
        tmp = self.import_journal_path.with_name(self.import_journal_path.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as fp:
            fp.write(str(idx_size))
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp, self.import_journal_path)
        fsync_dir(self.data_dir)  # 기록이 확정된 뒤에 슬롯을 써야 꺼져도 되돌릴 근거가 남는다

    def _commit_import(self) -> None:
        """기록을 지워 import 를 완료한다(이 순간 이후로는 되돌리지 않는다)."""
        self.import_journal_path.unlink()
        fsync_dir(self.data_dir)

    def _recover_interrupted_import(self) -> None:
        """끝나지 않은 import 가 쓴 슬롯을 기록된 크기로 잘라 그 import 전체를 취소한다.

        슬롯을 다 썼지만 기록을 지우기 전에 꺼진 경우도 취소한다(완료 시점은 기록 삭제). 남는 jsonl 줄과
        날짜 항목은 슬롯 범위 밖이라 읽을 때 건너뛰고 compact 때 정리되며, 카테고리 건수는 많게만 틀린다.
        """
        tmp = self.import_journal_path.with_name(self.import_journal_path.name + ".tmp")
        tmp.unlink(missing_ok=True)  # 기록을 만드는 중에 멈춤. 슬롯은 아직 쓰지 않았다
        if not self.import_journal_path.exists():
            return
        raw = self.import_journal_path.read_text(encoding="utf-8").strip()
        # 기록은 임시 이름으로 다 쓴 뒤 이름을 바꾸므로 정상적으로는 망가지지 않는다. 직접 편집·디스크 고장으로
        # 망가졌다면 어디까지 잘라야 할지 추측하지 않고 멈춘다(.slot-undo 와 같음).
        if not raw.isdigit() or int(raw) % SLOT_SIZE:
            raise StorageError(
                f"import 진행 기록({self.import_journal_path.name})이 손상되었습니다: '{raw[:20]}'",
                "백업본으로 복구하세요. 이 파일은 끝나지 않은 import 를 취소할 위치(바이트)를 담고 있습니다.",
            )
        idx_size = int(raw)
        if self.index.size() > idx_size:
            with self.idx_path.open("r+b") as fp:
                fp.truncate(idx_size)
                fp.flush()
                os.fsync(fp.fileno())
        self.import_journal_path.unlink()
        fsync_dir(self.data_dir)
        self.recovered.append("지난 import 가 끝나지 않아 그 import 전체를 취소했습니다. 같은 CSV 를 다시 가져오세요.")

    def update_transaction(self, tx: Transaction) -> Transaction:
        """수정된 전체 레코드를 끝에 append 하고 해당 id 슬롯만 덮어쓴다."""
        self.ensure_files()
        old = self.read_transaction(tx.id)
        if old is None:
            raise NotFoundError(
                f"TX-{tx.id} 거래를 찾을 수 없습니다.",
                "list 명령으로 존재하는 id 를 확인하세요.",
            )
        start, end = self._append_record(tx)
        # 날짜가 그대로인 금액 수정도 슬롯 위치는 바뀌므로 undo가 필요하다. 날짜 인덱스보다 먼저 확정해서,
        # 날짜 인덱스를 고친 뒤 위치 칸을 쓰기 전에 꺼져도 다음 실행이 이 기록을 보고 둘 다 되돌린다.
        self.slot_undo.prepare(tx.id)
        # 날짜가 바뀌면 새 날짜 항목을 넣는다(위치 칸보다 먼저 써야 수정한 거래가 목록에서 빠지지 않는다).
        # 더 예전 날짜로 바꾸면 날짜 인덱스를 어차피 다시 쓰므로 그때 이 id 의 옛 항목도 함께 뺀다.
        # 더 최신 날짜면 끝에 덧붙이기만 하고 옛 항목은 남긴다(읽을 때 날짜가 안 맞아 건너뜀, compact 가 정리).
        if old.date != tx.date:
            self.date_index.merge([(date_ordinal(tx.date), tx.id)], drop_id=tx.id)
        self.index.write_slot(tx.id, start, end)
        self.slot_undo.commit()
        return tx

    def delete_transaction(self, tx_id: int) -> Transaction:
        """인덱스 슬롯을 0으로 초기화해 삭제한다. 거래 데이터 파일은 건드리지 않는다."""
        self.ensure_files()
        tx = self.read_transaction(tx_id)
        if tx is None:
            raise NotFoundError(
                f"TX-{tx_id} 거래를 찾을 수 없습니다.",
                "이미 삭제되었거나 발급된 적 없는 id 입니다. list 로 확인하세요.",
            )
        # 삭제도 0 덮어쓰기 중간에 끊길 수 있다. 날짜 키를 남겨도 슬롯이 0이면 조회가 제외한다.
        self.slot_undo.prepare(tx_id)
        self.index.clear_slot(tx_id)
        self.slot_undo.commit()
        return tx

    # ------------------------------------------------------------------ 읽기
    def _read_at(self, fp: BinaryIO, start: int, end: int) -> Transaction:
        fp.seek(start)
        raw = fp.read(end - start)
        if len(raw) != end - start:
            raise StorageError(
                f"거래 데이터 파일에서 {end - start}바이트를 읽지 못했습니다(offset={start}).",
                "transactions.idx 와 transactions.jsonl 이 어긋났습니다. 백업본으로 복구하세요.",
            )
        return Transaction.from_json(raw)

    def read_transaction(self, tx_id: int) -> Transaction | None:
        """id 단건 조회. 없거나 삭제된 거래면 None."""
        slot = self.index.read_slot(tx_id)
        if slot is None:
            return None
        if not self.tr_path.exists():
            return None
        with self.tr_path.open("rb") as fp:
            return self._read_at(fp, slot[0], slot[1])

    def get_transaction(self, tx_id: int) -> Transaction:
        """id 단건 조회. 없으면 NotFoundError."""
        tx = self.read_transaction(tx_id)
        if tx is None:
            raise NotFoundError(
                f"TX-{tx_id} 거래를 찾을 수 없습니다.",
                "list 명령으로 존재하는 id 를 확인하세요.",
            )
        return tx

    def iter_latest_transactions(
        self, date_from: str | None = None, date_to: str | None = None
    ) -> Iterator[Transaction]:
        """거래일자 최신순(같은 날짜면 id 큰 순)으로 거래를 하나씩 yield 하는 제너레이터.

        날짜 인덱스를 맨 끝부터 역순으로 읽고, 항목마다 해당 id 의 레코드 byte 범위만 seek 해서 읽는다.
        그래서 전체를 메모리에 올리지 않고, 호출자가 limit 에서 멈추면 그 뒤는 읽지 않는다
        (옛 날짜를 import 해도 id 순서와 무관하게 날짜순이 유지된다).
        `date_from`/`date_to`(YYYY-MM-DD, 포함)를 주면 이진 탐색으로 그 구간만 읽는다.
        list/search/summary/export/category remove 가 모두 이 제너레이터 하나를 소비한다.
        """
        if not self.tr_path.exists():
            return
        lo = self.date_index.bisect_left((date_ordinal(date_from), 0)) if date_from else 0
        hi = (
            self.date_index.bisect_left((date_ordinal(date_to) + 1, 0))
            if date_to
            else self.date_index.count()
        )
        # 날짜 항목의 id 를 슬롯 수와 비교해, 중단된 저장의 흔적(건너뜀)과 손상(오류)을 구분한다.
        # 예전에는 둘 다 슬롯을 읽다 실패하면 조용히 건너뛰어서, 손상돼도 거래가 빠진 결과가
        # 종료 코드 0 으로 나왔고 id 0 은 음수 seek 로 엉뚱한 입출력 오류가 났다.
        slot_count = self.index.slot_count()
        with self.idx_path.open("rb") as idx, self.tr_path.open("rb") as tr:
            for ordinal, tx_id in self.date_index.iter_desc(lo, hi):
                if tx_id == 0:  # id 는 1부터라 정상적으로는 생길 수 없다
                    raise StorageError(
                        "날짜 인덱스(transactions.date.idx)가 손상되었습니다: id 0 항목이 있습니다.",
                        "compact 를 실행하면 원본에서 날짜 인덱스를 다시 만듭니다.",
                    )
                if tx_id > slot_count:  # 슬롯을 쓰기 전에 중단된 저장의 흔적(append_transaction 참고)
                    continue
                idx.seek((tx_id - 1) * SLOT_SIZE)
                raw = idx.read(SLOT_SIZE)
                if len(raw) != SLOT_SIZE:
                    continue
                start, end = SLOT_STRUCT.unpack(raw)
                if (start, end) == DELETED_SLOT:  # 삭제된 거래의 항목
                    continue
                tx = self._read_at(tr, start, end)
                if date_ordinal(tx.date) != ordinal:  # 날짜가 바뀌기 전의 옛 항목
                    continue
                yield tx

    # ------------------------------------------------------------------ 유지보수
    def live_count(self) -> int:
        """살아있는 거래 수(list 의 "전체 M건"). 위치 칸을 훑지만 array 로 세어 100만 건에 약 0.03초다."""
        self.index.slot_count()  # 크기가 16의 배수가 아니면(손상) 여기서 알린다
        return self.index.live_count()

    def stats(self) -> StorageStats:
        """살아있는 레코드 수/바이트와 거래 데이터 파일 크기를 집계한다."""
        total = self.index.slot_count()
        live_count = 0
        live_size = 0
        # 한 줄은 (end - start) + 개행 1바이트다. 이를 살아있는 칸마다 더한 값은 end 합 - start 합 + 살아있는 수와
        # 같고, 삭제된 칸 (0, 0) 은 두 합에 0 을 더할 뿐이라 골라내지 않고 모든 칸을 더해도 된다.
        for starts, ends in self.index.iter_slot_arrays():
            live = len(ends) - ends.count(0)
            live_count += live
            live_size += sum(ends) - sum(starts) + live
        tr_size = self.tr_path.stat().st_size if self.tr_path.exists() else 0
        return StorageStats(
            slot_count=total, live_count=live_count, tr_size=tr_size, live_size=live_size
        )

    def needs_compact(self, stats: StorageStats | None = None) -> bool:
        if stats is None:
            stats = self.stats()
        return stats.tr_size > 0 and stats.orphan_ratio >= self.COMPACT_HINT_RATIO

    def compact(self) -> StorageStats:
        """살아있는 레코드만 새 거래 데이터 파일로 옮겨 쓰고 원자적으로 교체한다.

        인덱스는 슬롯 개수/순서를 그대로 둔 채 offset 값만 갱신하므로
        id 재매핑이 필요 없다.
        """
        self.ensure_files()
        before = self.stats()
        tmp_path = self.compact_tmp_tr_path
        # 아래 반복문은 위치 인덱스의 슬롯을 하나씩 제자리에서 고쳐서, 도중에 꺼지면 일부 슬롯만 새 거래 데이터
        # 위치를 가리킨 채 옛 거래 데이터와 짝지어진다(엉뚱한 곳을 읽음). 그래서 고치기 전에 복사본을 만들고,
        # 다음 실행 때 _recover_interrupted_compact() 가 복사본으로 되돌린다. 임시 이름으로 다 쓰고
        # 확정한 뒤 이름을 바꾸므로, 복사본 이름의 파일이 있으면 그 복사본은 항상 완전하다.
        backup_tmp = self.compact_backup_path.with_name(self.compact_backup_path.name + ".tmp")
        shutil.copyfile(self.idx_path, backup_tmp)
        with backup_tmp.open("rb") as fp:
            os.fsync(fp.fileno())
        os.replace(backup_tmp, self.compact_backup_path)
        fsync_dir(self.data_dir)
        with self.idx_path.open("r+b") as idx, self.tr_path.open("rb") as src, tmp_path.open(
            "wb"
        ) as dst:
            total = self.index.slot_count()
            for slot in range(total):
                idx.seek(slot * SLOT_SIZE)
                raw = idx.read(SLOT_SIZE)
                if len(raw) != SLOT_SIZE:
                    break
                start, end = SLOT_STRUCT.unpack(raw)
                if (start, end) == DELETED_SLOT:
                    continue  # 죽은 슬롯은 그대로 0으로 남겨 둔다
                src.seek(start)
                data = src.read(end - start)
                new_start = dst.tell()
                dst.write(data)
                dst.write(b"\n")
                idx.seek(slot * SLOT_SIZE)
                idx.write(SLOT_STRUCT.pack(new_start, new_start + len(data)))
            dst.flush()
            os.fsync(dst.fileno())
            idx.flush()
            os.fsync(idx.fileno())
        os.replace(tmp_path, self.tr_path)  # 이 순간이 완료. 이후로는 새 거래 데이터와 새 위치 인덱스가 짝이다
        fsync_dir(self.data_dir)  # 교체를 확정한 뒤에 복사본을 지워야, 꺼져도 옛 거래 데이터+새 인덱스가 되지 않는다
        self.compact_backup_path.unlink()
        fsync_dir(self.data_dir)
        self.rebuild_date_index()  # 삭제/옛 날짜 항목을 정리하고 새 거래 데이터 기준으로 다시 만든다
        return before

    def _recover_interrupted_compact(self) -> None:
        """끝나지 않은 compact 를 정리한다. 복사본이 있으면 임시 거래 데이터 파일이 남았는지로 어디서 멈췄는지 판단한다.

        - 임시 거래 데이터 파일이 남음: 거래 데이터 교체 전에 멈춤 → 위치 인덱스를 복사본으로 되돌리고 임시 거래 데이터 파일을 지운다
          (compact 전 상태. 옛 줄 정리만 안 된 것이라 잃는 데이터는 없다).
        - 임시 거래 데이터 파일이 없음: 교체는 끝나고 복사본 삭제 전에 멈춤 → 새 거래 데이터와 새 인덱스가 이미 짝이라 복사본만 지운다.
        """
        backup_tmp = self.compact_backup_path.with_name(self.compact_backup_path.name + ".tmp")
        if backup_tmp.exists():  # 복사본을 만드는 중에 멈춤. 위치 인덱스는 아직 그대로라 지우기만 하면 된다
            backup_tmp.unlink()
        if self.compact_backup_path.exists():
            if self.compact_tmp_tr_path.exists():
                os.replace(self.compact_backup_path, self.idx_path)
                self.compact_tmp_tr_path.unlink()
                self.recovered.append("지난 compact 가 끝나지 않아 compact 전 상태로 되돌렸습니다. 다시 실행하세요.")
            else:
                self.compact_backup_path.unlink()
                self.recovered.append("지난 compact 의 마무리(복사본 정리)를 끝냈습니다.")
            fsync_dir(self.data_dir)
        elif self.compact_tmp_tr_path.exists():  # 복사본 없이 남은 임시 거래 데이터 파일은 쓰이지 않은 찌꺼기다
            self.compact_tmp_tr_path.unlink()
