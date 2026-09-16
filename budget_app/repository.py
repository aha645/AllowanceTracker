"""거래 저장 엔진: append-only 로그(JSONL) + 위치 기반 고정폭 이진 인덱스.

파일 구성
    transactions.jsonl  거래 원본. append 전용(수정도 새 버전을 끝에 덧붙인다).
    transactions.idx    id -> 현재 유효 byte 범위 매핑. 슬롯당 16바이트 고정폭.

인덱스 슬롯
    슬롯 위치가 곧 id 다. id N 의 슬롯은 (N-1)*16 바이트 위치에 있고
    `struct.pack("<QQ", start, end)` 로 기록된다. start == end == 0 이면 삭제된 슬롯이다
    (실제 레코드는 항상 end > start 이므로 오프셋 0에서 시작하는 TX-1 과 혼동되지 않는다).
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator

from .models import NotFoundError, StorageError, Transaction

SLOT_SIZE = 16
SLOT_STRUCT = struct.Struct("<QQ")
DELETED_SLOT = (0, 0)


class TransactionIndex:
    """`transactions.idx` 한 파일을 다루는 얇은 래퍼."""

    def __init__(self, path: Path) -> None:
        self.path: Path = path

    def ensure(self) -> None:
        """파일이 없으면 빈 파일로 생성한다."""
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.touch()

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


@dataclass(slots=True)
class StorageStats:
    """저장 파일 상태(컴팩션 필요 여부 판단용)."""

    slot_count: int
    live_count: int
    log_size: int
    live_size: int

    @property
    def orphan_size(self) -> int:
        return max(self.log_size - self.live_size, 0)

    @property
    def orphan_ratio(self) -> float:
        if self.log_size <= 0:
            return 0.0
        return self.orphan_size / self.log_size


class TransactionRepository:
    """거래 저장소. 로그 append 와 인덱스 슬롯 갱신을 함께 책임진다."""

    LOG_NAME = "transactions.jsonl"
    IDX_NAME = "transactions.idx"
    #: 고아 데이터 비율이 이 값을 넘으면 compact 안내를 띄운다.
    COMPACT_HINT_RATIO = 0.5

    def __init__(self, data_dir: Path) -> None:
        self.data_dir: Path = Path(data_dir)
        self.log_path: Path = self.data_dir / self.LOG_NAME
        self.idx_path: Path = self.data_dir / self.IDX_NAME
        self.index: TransactionIndex = TransactionIndex(self.idx_path)

    # ------------------------------------------------------------------ 초기화
    def ensure_files(self) -> None:
        """최초 실행 시 빈 로그/인덱스 파일을 만들어 둔다."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.log_path.exists():
            self.log_path.touch()
        self.index.ensure()

    # ------------------------------------------------------------------ 쓰기
    def _append_record(self, tx: Transaction) -> tuple[int, int]:
        """레코드를 로그 끝에 붙이고 (start, end) byte 범위를 돌려준다."""
        payload = tx.to_json().encode("utf-8")
        with self.log_path.open("ab") as fp:
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
        assigned = self.index.append_slot(start, end)
        if assigned != new_id:  # pragma: no cover - 동시 실행 방어
            raise StorageError(
                f"id 발급이 어긋났습니다(expected={new_id}, actual={assigned}).",
                "같은 data-dir 에 대해 여러 프로세스를 동시에 실행하지 마세요.",
            )
        return stored

    def update_transaction(self, tx: Transaction) -> Transaction:
        """수정된 전체 레코드를 끝에 append 하고 해당 id 슬롯만 덮어쓴다."""
        self.ensure_files()
        if self.index.read_slot(tx.id) is None:
            raise NotFoundError(
                f"TX-{tx.id} 거래를 찾을 수 없습니다.",
                "list 명령으로 존재하는 id 를 확인하세요.",
            )
        start, end = self._append_record(tx)
        self.index.write_slot(tx.id, start, end)
        return tx

    def delete_transaction(self, tx_id: int) -> Transaction:
        """인덱스 슬롯을 0으로 초기화해 삭제한다. 로그 파일은 건드리지 않는다."""
        self.ensure_files()
        tx = self.read_transaction(tx_id)
        if tx is None:
            raise NotFoundError(
                f"TX-{tx_id} 거래를 찾을 수 없습니다.",
                "이미 삭제되었거나 발급된 적 없는 id 입니다. list 로 확인하세요.",
            )
        self.index.clear_slot(tx_id)
        return tx

    # ------------------------------------------------------------------ 읽기
    def _read_at(self, fp: BinaryIO, start: int, end: int) -> Transaction:
        fp.seek(start)
        raw = fp.read(end - start)
        if len(raw) != end - start:
            raise StorageError(
                f"로그 파일에서 {end - start}바이트를 읽지 못했습니다(offset={start}).",
                "transactions.idx 와 transactions.jsonl 이 어긋났습니다. 백업본으로 복구하세요.",
            )
        return Transaction.from_json(raw)

    def read_transaction(self, tx_id: int) -> Transaction | None:
        """id 단건 조회. 없거나 삭제된 거래면 None."""
        slot = self.index.read_slot(tx_id)
        if slot is None:
            return None
        if not self.log_path.exists():
            return None
        with self.log_path.open("rb") as fp:
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

    def iter_latest_transactions(self) -> Iterator[Transaction]:
        """최신(id 큰 순)부터 거래를 하나씩 yield 하는 제너레이터.

        인덱스가 고정폭 이진이라 역순 순회에 UTF-8 멀티바이트 경계 문제가 없고,
        전체 데이터를 메모리에 올리지 않는다. list/search/summary/export/
        category remove 가 모두 이 제너레이터 하나를 소비한다.
        """
        total = self.index.slot_count()
        if total == 0 or not self.log_path.exists():
            return
        with self.idx_path.open("rb") as idx, self.log_path.open("rb") as log:
            for slot in range(total, 0, -1):
                idx.seek((slot - 1) * SLOT_SIZE)
                raw = idx.read(SLOT_SIZE)
                if len(raw) != SLOT_SIZE:
                    break
                start, end = SLOT_STRUCT.unpack(raw)
                if (start, end) == DELETED_SLOT:
                    continue
                yield self._read_at(log, start, end)

    # ------------------------------------------------------------------ 유지보수
    def stats(self) -> StorageStats:
        """살아있는 레코드 수/바이트와 로그 파일 크기를 집계한다."""
        total = self.index.slot_count()
        live_count = 0
        live_size = 0
        if total and self.idx_path.exists():
            with self.idx_path.open("rb") as idx:
                while True:
                    raw = idx.read(SLOT_SIZE)
                    if len(raw) != SLOT_SIZE:
                        break
                    start, end = SLOT_STRUCT.unpack(raw)
                    if (start, end) == DELETED_SLOT:
                        continue
                    live_count += 1
                    live_size += end - start + 1  # 개행 1바이트 포함
        log_size = self.log_path.stat().st_size if self.log_path.exists() else 0
        return StorageStats(
            slot_count=total, live_count=live_count, log_size=log_size, live_size=live_size
        )

    def needs_compact(self) -> bool:
        stats = self.stats()
        return stats.log_size > 0 and stats.orphan_ratio >= self.COMPACT_HINT_RATIO

    def compact(self) -> StorageStats:
        """살아있는 레코드만 새 로그로 옮겨 쓰고 원자적으로 교체한다.

        인덱스는 슬롯 개수/순서를 그대로 둔 채 offset 값만 갱신하므로
        id 재매핑이 필요 없다.
        """
        self.ensure_files()
        before = self.stats()
        tmp_path = self.log_path.with_suffix(".jsonl.tmp")
        with self.idx_path.open("r+b") as idx, self.log_path.open("rb") as src, tmp_path.open(
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
        os.replace(tmp_path, self.log_path)
        return before
