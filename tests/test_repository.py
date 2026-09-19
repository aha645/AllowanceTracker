"""저장 엔진(append-only 로그 + 고정폭 이진 인덱스) 단위 테스트."""

from __future__ import annotations

import struct
import sys
import tempfile
import unittest
from pathlib import Path

# `python tests/test_repository.py` 로 직접 실행하면 sys.path[0] 이 tests/ 가 되어
# budget_app 을 찾지 못한다. `python -m unittest tests.test_repository` 로 실행할 때는
# 이미 프로젝트 루트가 sys.path 에 있으므로 중복 삽입만 막아주면 된다.
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from budget_app.models import NotFoundError, Transaction
from budget_app.repository import SLOT_SIZE, TransactionRepository


def make_tx(day: int, amount: int = 1000, category: str = "식비") -> Transaction:
    return Transaction(id=0, date=f"2024-01-{day:02d}", type="expense", category=category, amount=amount)


class RepositoryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        self.repo = TransactionRepository(self.data_dir)
        self.repo.ensure_files()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # 아래 번호는 실행 순서를 강제하기 위한 것이 아니라(각 테스트는 setUp 에서 매번
    # 새 임시 폴더를 받으므로 서로 독립적이다), 기능이 쌓이는 순서대로 읽기 쉽게
    # 정렬해 둔 것이다. 두 자리로 0-padding 한 이유는 unittest 가 메서드 이름을
    # 문자열로 정렬해서 실행하기 때문 — 패딩이 없으면 "10"이 "2"보다 앞에 온다.

    # 기능: ensure_files — 최초 실행 시 로그/인덱스 파일이 생성되고, 슬롯이
    # 0개인 빈 상태에서 next_id 가 1인지 검증
    def test_00_files_created_on_first_run(self) -> None:
        self.assertTrue(self.repo.log_path.exists())
        self.assertTrue(self.repo.idx_path.exists())
        self.assertEqual(self.repo.index.slot_count(), 0)
        self.assertEqual(self.repo.index.next_id(), 1)

    # 기능: append_transaction — id 가 1부터 시작해 순차 증가하고, 인덱스
    # 파일 크기가 슬롯 수 * SLOT_SIZE 만큼 늘어나는지 검증
    def test_01_ids_start_at_one_and_increase(self) -> None:
        ids = [self.repo.append_transaction(make_tx(d)).id for d in range(1, 4)]
        self.assertEqual(ids, [1, 2, 3])
        self.assertEqual(self.repo.idx_path.stat().st_size, 3 * SLOT_SIZE)

    # 기능: 인덱스 슬롯 — offset 0 에서 시작하는 TX-1 이 삭제 sentinel
    # (0, 0) 과 혼동되지 않고 정상 조회되는지 검증
    def test_02_tx1_at_offset_zero_is_not_confused_with_deleted(self) -> None:
        """TX-1 은 offset 0 에서 시작하지만 (0, 0) sentinel 과 구분되어야 한다."""
        self.repo.append_transaction(make_tx(1))
        self.assertEqual(self.repo.index.read_slot(1)[0], 0)
        self.assertIsNotNone(self.repo.read_transaction(1))

    # 기능: iter_latest_transactions — 인덱스를 역순으로 순회해 최신(id 큰
    # 순)부터 스트리밍되는지 검증
    def test_03_iter_latest_is_newest_first(self) -> None:
        for day in range(1, 4):
            self.repo.append_transaction(make_tx(day))
        self.assertEqual([tx.id for tx in self.repo.iter_latest_transactions()], [3, 2, 1])

    # 기능: update_transaction — 로그에는 새 레코드를 append 만 하고(로그
    # 크기 증가), 인덱스는 슬롯 수 변화 없이 offset 만 갱신되는지 검증
    def test_04_update_appends_and_rewrites_slot(self) -> None:
        tx = self.repo.append_transaction(make_tx(1))
        size_before = self.repo.log_path.stat().st_size
        self.repo.update_transaction(tx.replace_fields(amount=7777))

        self.assertGreater(self.repo.log_path.stat().st_size, size_before)  # 로그는 append-only
        self.assertEqual(self.repo.idx_path.stat().st_size, SLOT_SIZE)  # 슬롯 수는 그대로
        self.assertEqual(self.repo.get_transaction(1).amount, 7777)

    # 기능: update_transaction — 존재하지 않는 id 를 수정하려 하면
    # NotFoundError 를 발생시키는지 검증
    def test_05_update_missing_id_raises(self) -> None:
        with self.assertRaises(NotFoundError):
            self.repo.update_transaction(make_tx(1).replace_fields(id=42))

    # 기능: delete_transaction — 인덱스 슬롯 16바이트를 (0, 0) 으로 초기화할
    # 뿐 로그 파일 크기는 변하지 않고, 삭제된 거래는 조회/순회에서 제외되는지 검증
    def test_06_delete_zeroes_slot_and_keeps_log(self) -> None:
        self.repo.append_transaction(make_tx(1))
        self.repo.append_transaction(make_tx(2))
        log_size = self.repo.log_path.stat().st_size

        self.repo.delete_transaction(1)

        self.assertEqual(self.repo.log_path.stat().st_size, log_size)  # 로그는 건드리지 않음
        with self.repo.idx_path.open("rb") as fp:
            self.assertEqual(struct.unpack("<QQ", fp.read(SLOT_SIZE)), (0, 0))
        self.assertIsNone(self.repo.read_transaction(1))
        self.assertEqual([tx.id for tx in self.repo.iter_latest_transactions()], [2])

    # 기능: delete_transaction — 이미 삭제된 id 를 다시 삭제하면
    # NotFoundError 를 발생시키는지 검증
    def test_07_delete_twice_raises(self) -> None:
        self.repo.append_transaction(make_tx(1))
        self.repo.delete_transaction(1)
        with self.assertRaises(NotFoundError):
            self.repo.delete_transaction(1)

    # 기능: append_transaction — 중간 id 를 삭제해도 다음에 발급되는 id 는
    # 재사용되지 않고 이어서 증가하는지 검증
    def test_08_ids_are_never_reused_after_delete(self) -> None:
        for day in range(1, 4):
            self.repo.append_transaction(make_tx(day))
        self.repo.delete_transaction(2)
        self.assertEqual(self.repo.append_transaction(make_tx(4)).id, 4)
        self.assertEqual([tx.id for tx in self.repo.iter_latest_transactions()], [4, 3, 1])

    # 기능: compact — 살아있는 레코드의 id/값은 그대로 유지한 채 로그 크기만
    # 줄이고, 고아 데이터가 0이 되며, 슬롯 수(id 매핑)는 바뀌지 않는지 검증
    def test_09_compact_keeps_ids_and_data(self) -> None:
        for day in range(1, 6):
            self.repo.append_transaction(make_tx(day, amount=day * 100))
        self.repo.update_transaction(self.repo.get_transaction(2).replace_fields(amount=9999))
        self.repo.delete_transaction(4)
        before = [(tx.id, tx.amount) for tx in self.repo.iter_latest_transactions()]

        stats_before = self.repo.stats()
        self.repo.compact()
        stats_after = self.repo.stats()

        self.assertEqual(before, [(tx.id, tx.amount) for tx in self.repo.iter_latest_transactions()])
        self.assertLess(stats_after.log_size, stats_before.log_size)
        self.assertEqual(stats_after.orphan_size, 0)
        self.assertEqual(stats_after.slot_count, stats_before.slot_count)  # id 재매핑 없음
        # compact 이후에도 새 id 는 이어서 발급된다
        self.assertEqual(self.repo.append_transaction(make_tx(6)).id, 6)

    # 기능: compact — 거래가 하나도 없는 저장소에서 실행해도 예외 없이
    # 안전하게 끝나는지 검증
    def test_10_compact_on_empty_repo(self) -> None:
        self.repo.compact()
        self.assertEqual(self.repo.stats().live_count, 0)

    # 기능: 저장 엔진 — 이모지/한글이 섞인 메모·태그가 저장 후 읽어도
    # 손실 없이 그대로 복원되는지(unicode round-trip) 검증
    def test_11_unicode_roundtrip(self) -> None:
        tx = self.repo.append_transaction(
            Transaction(0, "2024-01-01", "expense", "식비", 1000, memo="김밥 🍙, 콤마", tags=["외식", "점심"])
        )
        loaded = self.repo.get_transaction(tx.id)
        self.assertEqual(loaded.memo, "김밥 🍙, 콤마")
        self.assertEqual(loaded.tags, ["외식", "점심"])


if __name__ == "__main__":
    unittest.main()
