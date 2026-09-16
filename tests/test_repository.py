"""저장 엔진(append-only 로그 + 고정폭 이진 인덱스) 단위 테스트."""

from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

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

    def test_files_created_on_first_run(self) -> None:
        self.assertTrue(self.repo.log_path.exists())
        self.assertTrue(self.repo.idx_path.exists())
        self.assertEqual(self.repo.index.slot_count(), 0)
        self.assertEqual(self.repo.index.next_id(), 1)

    def test_ids_start_at_one_and_increase(self) -> None:
        ids = [self.repo.append_transaction(make_tx(d)).id for d in range(1, 4)]
        self.assertEqual(ids, [1, 2, 3])
        self.assertEqual(self.repo.idx_path.stat().st_size, 3 * SLOT_SIZE)

    def test_iter_latest_is_newest_first(self) -> None:
        for day in range(1, 4):
            self.repo.append_transaction(make_tx(day))
        self.assertEqual([tx.id for tx in self.repo.iter_latest_transactions()], [3, 2, 1])

    def test_update_appends_and_rewrites_slot(self) -> None:
        tx = self.repo.append_transaction(make_tx(1))
        size_before = self.repo.log_path.stat().st_size
        self.repo.update_transaction(tx.replace_fields(amount=7777))

        self.assertGreater(self.repo.log_path.stat().st_size, size_before)  # 로그는 append-only
        self.assertEqual(self.repo.idx_path.stat().st_size, SLOT_SIZE)  # 슬롯 수는 그대로
        self.assertEqual(self.repo.get_transaction(1).amount, 7777)

    def test_update_missing_id_raises(self) -> None:
        with self.assertRaises(NotFoundError):
            self.repo.update_transaction(make_tx(1).replace_fields(id=42))

    def test_delete_zeroes_slot_and_keeps_log(self) -> None:
        self.repo.append_transaction(make_tx(1))
        self.repo.append_transaction(make_tx(2))
        log_size = self.repo.log_path.stat().st_size

        self.repo.delete_transaction(1)

        self.assertEqual(self.repo.log_path.stat().st_size, log_size)  # 로그는 건드리지 않음
        with self.repo.idx_path.open("rb") as fp:
            self.assertEqual(struct.unpack("<QQ", fp.read(SLOT_SIZE)), (0, 0))
        self.assertIsNone(self.repo.read_transaction(1))
        self.assertEqual([tx.id for tx in self.repo.iter_latest_transactions()], [2])

    def test_delete_twice_raises(self) -> None:
        self.repo.append_transaction(make_tx(1))
        self.repo.delete_transaction(1)
        with self.assertRaises(NotFoundError):
            self.repo.delete_transaction(1)

    def test_ids_are_never_reused_after_delete(self) -> None:
        for day in range(1, 4):
            self.repo.append_transaction(make_tx(day))
        self.repo.delete_transaction(2)
        self.assertEqual(self.repo.append_transaction(make_tx(4)).id, 4)
        self.assertEqual([tx.id for tx in self.repo.iter_latest_transactions()], [4, 3, 1])

    def test_tx1_at_offset_zero_is_not_confused_with_deleted(self) -> None:
        """TX-1 은 offset 0 에서 시작하지만 (0, 0) sentinel 과 구분되어야 한다."""
        self.repo.append_transaction(make_tx(1))
        self.assertEqual(self.repo.index.read_slot(1)[0], 0)
        self.assertIsNotNone(self.repo.read_transaction(1))

    def test_compact_keeps_ids_and_data(self) -> None:
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

    def test_compact_on_empty_repo(self) -> None:
        self.repo.compact()
        self.assertEqual(self.repo.stats().live_count, 0)

    def test_unicode_roundtrip(self) -> None:
        tx = self.repo.append_transaction(
            Transaction(0, "2024-01-01", "expense", "식비", 1000, memo="김밥 🍙, 콤마", tags=["외식", "점심"])
        )
        loaded = self.repo.get_transaction(tx.id)
        self.assertEqual(loaded.memo, "김밥 🍙, 콤마")
        self.assertEqual(loaded.tags, ["외식", "점심"])


if __name__ == "__main__":
    unittest.main()
