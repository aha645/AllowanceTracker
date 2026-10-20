"""거래 저장 엔진: append-only 로그(JSONL) + 위치 기반 고정폭 이진 인덱스 + 디스크 B+Tree 날짜 인덱스.

파일 구성
    transactions.jsonl     거래 원본. append 전용(수정도 새 버전을 끝에 덧붙인다).
    transactions.idx       id -> 현재 유효 byte 범위 매핑. 슬롯당 16바이트 고정폭.
    transactions.date.idx  4096바이트 페이지 B+Tree + 페이지 after-image WAL.

인덱스 슬롯
    슬롯 위치가 곧 id 다. id N 의 슬롯은 (N-1)*16 바이트 위치에 있고
    `struct.pack("<QQ", start, end)` 로 기록된다. start == end == 0 이면 삭제된 슬롯이다
    (실제 레코드는 항상 end > start 이므로 오프셋 0에서 시작하는 TX-1 과 혼동되지 않는다).

날짜 인덱스
    id 순서(= 등록 순서)와 거래일자 순서는 다를 수 있다(예: 옛 날짜 CSV 를 import).
    그래서 최신순 조회는 id 슬롯이 아니라 이 정렬 인덱스를 맨 끝부터 역순으로 읽는다.
    leaf 키는 `struct.pack("<IQ", date.toordinal(), id)` 이며 (날짜, id) 순으로 정렬돼 있다.
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
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import BinaryIO, Iterable, Iterator

from .bplustree import DateIndex
from .durability import SlotUndo
from .models import NotFoundError, StorageError, Transaction

SLOT_SIZE = 16
SLOT_STRUCT = struct.Struct("<QQ")
DELETED_SLOT = (0, 0)

DATE_ENTRY_STRUCT = struct.Struct("<IQ")
DATE_ENTRY_SIZE = DATE_ENTRY_STRUCT.size  # 12-byte composite key inside leaf pages

#: (날짜 순번, 거래 id). 날짜 순번은 `date.toordinal()` 이다.
DateEntry = tuple[int, int]

_ID_BITS = 64


def entry_key(entry: DateEntry) -> int:
    """(날짜 순번, id) 를 정렬 순서가 같은 정수 하나로 합친다.

    Python 정수로 날짜와 unsigned 64-bit id를 손실 없이 담는다.
    """
    ordinal, tx_id = entry
    return ordinal << _ID_BITS | tx_id


def _fsync_dir(path: Path) -> None:
    """폴더의 이름 바꾸기/삭제를 디스크에 확정한다. 안 하면 os.replace 가 꺼진 뒤 되돌아갈 수 있다."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


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
    DATE_IDX_NAME = "transactions.date.idx"
    #: 고아 데이터 비율이 이 값을 넘으면 compact 안내를 띄운다.
    COMPACT_HINT_RATIO = 0.5

    def __init__(self, data_dir: Path) -> None:
        self.data_dir: Path = Path(data_dir)
        self.log_path: Path = self.data_dir / self.LOG_NAME
        self.idx_path: Path = self.data_dir / self.IDX_NAME
        self.index: TransactionIndex = TransactionIndex(self.idx_path)
        self.date_idx_path: Path = self.data_dir / self.DATE_IDX_NAME
        self.date_index: DateIndex = DateIndex(self.date_idx_path)
        # compact 가 위치 인덱스를 고치기 전에 만들어 두는 복사본. 이 파일이 있으면 compact 가 끝나지 않은 것이다.
        self.compact_backup_path: Path = self.data_dir / (self.IDX_NAME + ".compact-backup")
        self.compact_tmp_log_path: Path = self.log_path.with_suffix(".jsonl.tmp")
        # import 가 위치 슬롯 묶음을 쓰기 전의 위치 파일 크기를 적어 두는 파일. 있으면 import 가 끝나지 않은 것이다.
        self.import_journal_path: Path = self.data_dir / (self.IDX_NAME + ".import-pending")
        self.slot_undo = SlotUndo(self.idx_path)
        #: 이번 실행에서 자동으로 바로잡은 일(사용자에게 [안내] 로 알린다).
        self.recovered: list[str] = []

    # ------------------------------------------------------------------ 초기화
    def ensure_files(self) -> None:
        """최초 실행 시 빈 로그/인덱스 파일을 만들어 둔다."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.log_path.exists():
            self.log_path.touch()
        self._recover_interrupted_compact()  # 위치 인덱스를 통째로 되돌릴 수 있으므로 가장 먼저
        self.index.ensure()
        self._recover_interrupted_import()  # 조각 정리보다 먼저: import 의 조각은 묶음째 잘라 낸다
        if self.slot_undo.recover():
            self.recovered.append("끝나지 않은 거래 저장의 위치 슬롯을 이전 상태로 복구했습니다.")
        if self.index.drop_partial_slot():
            self.recovered.append("끝나지 않은 마지막 저장(add)의 조각을 정리했습니다. 그 거래는 저장되지 않았습니다.")
        # 날짜 인덱스가 없거나 손상됐으면(예: 이 인덱스가 생기기 전의 데이터) 원본에서 자동 재생성한다.
        if not self.date_index.is_valid():
            if self.date_index.is_legacy():
                self._backup_legacy_date_index()
                self.recovered.append("기존 날짜 인덱스를 B+Tree로 마이그레이션했습니다. 원본은 .legacy.bak에 보존했습니다.")
            self.rebuild_date_index()

    def _backup_legacy_date_index(self) -> None:
        """Preserve the exact old array before any atomic migration replacement."""
        backup = self.date_idx_path.with_name(self.DATE_IDX_NAME + ".legacy.bak")
        if backup.exists():
            return
        tmp = backup.with_name(backup.name + ".tmp")
        shutil.copyfile(self.date_idx_path, tmp)
        with tmp.open("rb") as fp:
            os.fsync(fp.fileno())
        os.replace(tmp, backup)
        _fsync_dir(self.data_dir)

    def _live_date_entries(self) -> Iterator[DateEntry]:
        """살아있는 모든 거래의 (날짜 순번, id) 를 id 순서로 yield 한다."""
        for tx in self._iter_live_by_id():
            yield date_ordinal(tx.date), tx.id

    def _iter_live_by_id(self) -> Iterator[Transaction]:
        """id 오름차순으로 살아있는 거래를 하나씩 yield 한다(날짜 인덱스를 쓰지 않는다)."""
        total = self.index.slot_count()
        if total == 0 or not self.log_path.exists():
            return
        with self.idx_path.open("rb") as idx, self.log_path.open("rb") as log:
            for slot in range(total):
                idx.seek(slot * SLOT_SIZE)
                raw = idx.read(SLOT_SIZE)
                if len(raw) != SLOT_SIZE:
                    break
                start, end = SLOT_STRUCT.unpack(raw)
                if (start, end) == DELETED_SLOT:
                    continue
                yield self._read_at(log, start, end)

    def category_counts(self) -> dict[str, int]:
        """살아있는 거래를 처음부터 세어 카테고리별 건수를 돌려준다(O(n), 기록을 다시 맞출 때만 쓴다)."""
        counts: dict[str, int] = {}
        for tx in self._iter_live_by_id():
            counts[tx.category] = counts.get(tx.category, 0) + 1
        return counts

    def rebuild_date_index(self) -> None:
        """transactions.jsonl/idx 로부터 날짜 인덱스를 처음부터 다시 만든다."""
        self.date_index.rebuild(self._live_date_entries())

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
        # 날짜 항목을 슬롯보다 먼저 쓴다. 슬롯을 먼저 쓰고 그 사이에 꺼지면 id 로는 찾히는 거래가
        # 날짜 색인에만 빠져 list/summary 에서 조용히 사라진다. 이 순서면 꺼져도 남는 건 슬롯 범위 밖
        # id 의 날짜 항목뿐이라 읽을 때 건너뛰고, 그 id 는 다음 add 때 다시 발급된다.
        self.date_index.merge([(date_ordinal(stored.date), stored.id)])
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

        로그 레코드와 인덱스 슬롯은 각각 한 번에 이어 쓰고(fsync 도 파일당 한 번), 날짜 인덱스는 모아서
        한 번에 병합한다. 옛 날짜가 섞여 있어도 행마다 파일을 다시 쓰지 않는다.
        거래 객체는 쓰고 나면 버리므로(제너레이터를 넘기면) 메모리가 건수만큼 늘지 않는다.
        """
        self.ensure_files()
        next_id = self.index.next_id()
        count = 0
        # 위치 슬롯은 bytearray, 날짜 항목은 64-bit id를 보존하는 Python 정수 목록에 모은다.
        slots = bytearray()
        keys: list[int] = []
        with self.log_path.open("ab") as log:
            log.seek(0, os.SEEK_END)
            offset = log.tell()
            for tx in transactions:
                stored = tx.replace_fields(id=next_id + count)
                payload = stored.to_json().encode("utf-8")
                log.write(payload + b"\n")
                slots += SLOT_STRUCT.pack(offset, offset + len(payload))
                offset += len(payload) + 1
                count += 1
                keys.append(entry_key((date_ordinal(stored.date), stored.id)))
            log.flush()
            os.fsync(log.fileno())
        if not count:
            return 0
        # append_transaction() 과 같은 이유로 날짜 항목을 슬롯보다 먼저 쓴다. 슬롯 묶음을 쓰기 전에
        # 꺼지면 import 한 거래 전체가 슬롯 범위 밖이 되어, 반쯤 보이는 상태 없이 통째로 없던 일이 된다.
        self.date_index.merge_keys(keys)
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
        _fsync_dir(self.data_dir)  # 기록이 확정된 뒤에 슬롯을 써야 꺼져도 되돌릴 근거가 남는다

    def _commit_import(self) -> None:
        """기록을 지워 import 를 완료한다(이 순간 이후로는 되돌리지 않는다)."""
        self.import_journal_path.unlink()
        _fsync_dir(self.data_dir)

    def _recover_interrupted_import(self) -> None:
        """끝나지 않은 import 가 쓴 슬롯을 기록된 크기로 잘라 그 import 전체를 취소한다.

        슬롯을 다 썼지만 기록을 지우기 전에 꺼진 경우도 취소한다(완료 시점은 기록 삭제). 남는 jsonl 줄과
        날짜 항목은 슬롯 범위 밖이라 읽을 때 건너뛰고 compact 때 정리되며, 카테고리 건수는 많게만 틀린다.
        """
        tmp = self.import_journal_path.with_name(self.import_journal_path.name + ".tmp")
        tmp.unlink(missing_ok=True)  # 기록을 만드는 중에 멈춤. 슬롯은 아직 쓰지 않았다
        if not self.import_journal_path.exists():
            return
        idx_size = int(self.import_journal_path.read_text(encoding="utf-8"))
        if self.index.size() > idx_size:
            with self.idx_path.open("r+b") as fp:
                fp.truncate(idx_size)
                fp.flush()
                os.fsync(fp.fileno())
        self.import_journal_path.unlink()
        _fsync_dir(self.data_dir)
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
        # 날짜가 바뀌면 새 위치에 항목을 끼워 넣는다(옛 항목은 읽을 때 건너뜀). 슬롯보다 먼저 쓰는
        # 이유: 그 사이에 꺼져도 슬롯은 아직 수정 전 레코드를 가리키므로, 새 항목은 날짜 불일치로
        # 건너뛰고 거래는 수정 전 날짜에 한 번만 나온다. 반대 순서면 슬롯은 새 날짜인데 새 날짜 항목이
        # 없어, 수정한 거래가 목록에서 사라진다.
        if old.date != tx.date:
            self.date_index.merge([(date_ordinal(tx.date), tx.id)])
        self.slot_undo.prepare(tx.id)
        self.index.write_slot(tx.id, start, end)
        self.slot_undo.commit()
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

    def iter_latest_transactions(
        self, date_from: str | None = None, date_to: str | None = None
    ) -> Iterator[Transaction]:
        """거래일자 최신순(같은 날짜면 id 큰 순)으로 거래를 하나씩 yield 하는 제너레이터.

        날짜 인덱스를 맨 끝부터 역순으로 읽고, 항목마다 해당 id 의 레코드 byte 범위만 seek 해서 읽는다.
        그래서 전체를 메모리에 올리지 않고, 호출자가 limit 에서 멈추면 그 뒤는 읽지 않는다
        (옛 날짜를 import 해도 id 순서와 무관하게 날짜순이 유지된다).
        `date_from`/`date_to`(YYYY-MM-DD, 포함)를 주면 B+Tree에서 구간 시작 leaf만 찾아 읽는다.
        list/search/summary/export/category remove 가 모두 이 제너레이터 하나를 소비한다.
        """
        if not self.log_path.exists():
            return
        lower = (date_ordinal(date_from), 0) if date_from else None
        upper = (date_ordinal(date_to) + 1, 0) if date_to else None
        # 날짜 항목의 id 를 슬롯 수와 비교해, 중단된 저장의 흔적(건너뜀)과 손상(오류)을 구분한다.
        # 예전에는 둘 다 슬롯을 읽다 실패하면 조용히 건너뛰어서, 손상돼도 거래가 빠진 결과가
        # 종료 코드 0 으로 나왔고 id 0 은 음수 seek 로 엉뚱한 입출력 오류가 났다.
        slot_count = self.index.slot_count()
        with self.idx_path.open("rb") as idx, self.log_path.open("rb") as log:
            for ordinal, tx_id in self.date_index.iter_range(lower, upper, reverse=True):
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
                tx = self._read_at(log, start, end)
                if date_ordinal(tx.date) != ordinal:  # 날짜가 바뀌기 전의 옛 항목
                    continue
                yield tx

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
        tmp_path = self.compact_tmp_log_path
        # 아래 반복문은 위치 인덱스의 슬롯을 하나씩 제자리에서 고쳐서, 도중에 꺼지면 일부 슬롯만 새 로그
        # 위치를 가리킨 채 옛 로그와 짝지어진다(엉뚱한 곳을 읽음). 그래서 고치기 전에 복사본을 만들고,
        # 다음 실행 때 _recover_interrupted_compact() 가 복사본으로 되돌린다. 임시 이름으로 다 쓰고
        # 확정한 뒤 이름을 바꾸므로, 복사본 이름의 파일이 있으면 그 복사본은 항상 완전하다.
        backup_tmp = self.compact_backup_path.with_name(self.compact_backup_path.name + ".tmp")
        shutil.copyfile(self.idx_path, backup_tmp)
        with backup_tmp.open("rb") as fp:
            os.fsync(fp.fileno())
        os.replace(backup_tmp, self.compact_backup_path)
        _fsync_dir(self.data_dir)
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
        os.replace(tmp_path, self.log_path)  # 이 순간이 완료. 이후로는 새 로그와 새 위치 인덱스가 짝이다
        _fsync_dir(self.data_dir)  # 교체를 확정한 뒤에 복사본을 지워야, 꺼져도 옛 로그+새 인덱스가 되지 않는다
        self.compact_backup_path.unlink()
        _fsync_dir(self.data_dir)
        self.rebuild_date_index()  # 삭제/옛 날짜 항목을 정리하고 새 로그 기준으로 다시 만든다
        return before

    def _recover_interrupted_compact(self) -> None:
        """끝나지 않은 compact 를 정리한다. 복사본이 있으면 임시 로그가 남았는지로 어디서 멈췄는지 판단한다.

        - 임시 로그가 남음: 로그 교체 전에 멈춤 → 위치 인덱스를 복사본으로 되돌리고 임시 로그를 지운다
          (compact 전 상태. 옛 줄 정리만 안 된 것이라 잃는 데이터는 없다).
        - 임시 로그가 없음: 교체는 끝나고 복사본 삭제 전에 멈춤 → 새 로그와 새 인덱스가 이미 짝이라 복사본만 지운다.
        """
        backup_tmp = self.compact_backup_path.with_name(self.compact_backup_path.name + ".tmp")
        if backup_tmp.exists():  # 복사본을 만드는 중에 멈춤. 위치 인덱스는 아직 그대로라 지우기만 하면 된다
            backup_tmp.unlink()
        if self.compact_backup_path.exists():
            if self.compact_tmp_log_path.exists():
                os.replace(self.compact_backup_path, self.idx_path)
                self.compact_tmp_log_path.unlink()
                self.recovered.append("지난 compact 가 끝나지 않아 compact 전 상태로 되돌렸습니다. 다시 실행하세요.")
            else:
                self.compact_backup_path.unlink()
                self.recovered.append("지난 compact 의 마무리(복사본 정리)를 끝냈습니다.")
            _fsync_dir(self.data_dir)
        elif self.compact_tmp_log_path.exists():  # 복사본 없이 남은 임시 로그는 쓰이지 않은 찌꺼기다
            self.compact_tmp_log_path.unlink()
