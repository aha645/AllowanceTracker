"""저장소: 10만 건, 날짜 인덱스 재생성, 파일 간 일관성과 위치 칸 되돌리기(.slot-undo) 복구."""

from __future__ import annotations

import csv
import os
import random
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from budget_app.context import AppContext
from budget_app.durability import SlotUndo
from budget_app.models import StorageError, Transaction
from budget_app.repository import SLOT_STRUCT, DateIndex, TransactionRepository, date_ordinal
from budget_app.services import SearchCriteria

ROOT = Path(__file__).resolve().parents[1]


def tx(day: str = "2024-01-15", amount: int = 100) -> Transaction:
    return Transaction(id=0, date=day, type="expense", category="식비", amount=amount, memo="한글 메모")


REBUILT = "날짜 인덱스가 손상되어 거래 파일에서 다시 만들었습니다."


class RepositoryRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.repo = self.open_repo()

    def open_repo(self) -> TransactionRepository:
        repo = TransactionRepository(self.folder)
        repo.ensure_files()
        return repo

    def listing(self, repo: TransactionRepository | None = None) -> list[tuple[str, int, int]]:
        return [(item.date, item.id, item.amount) for item in (repo or self.open_repo()).iter_latest_transactions()]

    def test_100000_transactions_ranges_updates_deletes_and_compact(self) -> None:
        rng = random.Random(645)
        first = date(2020, 1, 1)
        days = [(first + timedelta(days=rng.randrange(1827))).isoformat() for _ in range(100000)]
        self.assertEqual(self.repo.append_transactions(tx(day, i + 1) for i, day in enumerate(days)), 100000)
        oracle = sorted([(day, i + 1, i + 1) for i, day in enumerate(days)], reverse=True)
        self.assertEqual(self.listing(), oracle)
        self.assertEqual(self.repo.index.next_id(), 100001)
        self.assertEqual(self.repo.date_index.count(), 100000)
        # Query independently computed boundaries, including a full month and no results.
        for low, high in [("2022-02-01", "2022-02-28"), ("2020-01-01", "2020-01-01"),
                          ("2010-01-01", "2010-01-31"), ("2024-12-31", "9999-12-31")]:
            self.assertEqual([(item.date, item.id, item.amount)
                              for item in self.repo.iter_latest_transactions(low, high)],
                             [row for row in oracle if low <= row[0] <= high])
        added = self.repo.append_transaction(tx("1900-01-01", 123))
        self.assertEqual(added.id, 100001)
        oracle.append((added.date, added.id, added.amount))
        old = self.repo.get_transaction(27)
        self.repo.update_transaction(old.replace_fields(date="2099-12-31", amount=777))
        self.repo.delete_transaction(51)
        oracle = [row for row in oracle if row[1] not in (27, 51)] + [("2099-12-31", 27, 777)]
        oracle.sort(reverse=True)
        self.assertEqual(self.listing(), oracle)
        next_id = self.repo.index.next_id()
        self.repo.compact()
        self.assertEqual(self.repo.index.next_id(), next_id)
        self.assertIsNone(self.repo.read_transaction(51))
        self.assertEqual(self.listing(), oracle)
        self.assertEqual(self.repo.date_index.count(), len(oracle))  # compact 가 옛 항목을 정리했다

    def test_same_date_ties_updates_back_and_forth_no_duplicates(self) -> None:
        for _ in range(400):
            self.repo.append_transaction(tx())
        self.assertEqual([row[1] for row in self.listing()], list(range(400, 0, -1)))
        old = self.repo.get_transaction(17)
        for day in ["2000-01-01", "2099-12-31", old.date, old.date]:
            self.repo.update_transaction(old.replace_fields(date=day))
            rows = self.listing()
            self.assertEqual(len(rows), 400)
            self.assertEqual(sum(row[1] == 17 for row in rows), 1)
            self.assertEqual(rows, sorted(rows, reverse=True))
        self.repo.delete_transaction(400)
        self.repo.compact()
        self.assertEqual(self.repo.append_transaction(tx()).id, 401)

    def damage_date_index(self) -> None:
        """마지막 항목이 덜 써진 것처럼 5바이트를 잘라 크기를 12의 배수가 아니게 만든다."""
        raw = self.repo.date_idx_path.read_bytes()
        self.repo.date_idx_path.write_bytes(raw[:-5])

    def test_damaged_index_rebuilt_keeps_ids_and_deleted_slots(self) -> None:
        self.repo.append_transactions([tx("2024-03-01"), tx("2023-01-01"), tx("2024-03-01")])
        self.repo.update_transaction(self.repo.get_transaction(2).replace_fields(date="2025-01-01"))
        self.repo.delete_transaction(3)
        before = self.listing(self.repo)
        log_before, idx_before = self.repo.log_path.read_bytes(), self.repo.idx_path.read_bytes()
        self.assertEqual(self.repo.date_index.count(), 4)  # update 로 남은 옛 날짜 항목 포함
        self.damage_date_index()
        reopened = self.open_repo()
        self.assertEqual(reopened.recovered, [REBUILT])
        self.assertEqual(self.listing(reopened), before)
        self.assertEqual(reopened.log_path.read_bytes(), log_before)
        self.assertEqual(reopened.idx_path.read_bytes(), idx_before)
        self.assertEqual(sorted(path.name for path in self.folder.glob("transactions.date.idx*")),
                         ["transactions.date.idx"])  # 백업하지 않는다
        self.assertEqual(reopened.date_index.count(), 2)  # 살아있는 거래만 다시 담는다
        self.assertEqual(reopened.append_transaction(tx()).id, 4)
        self.assertEqual(self.open_repo().recovered, [])  # 다시 만든 뒤에는 안내가 없다

    def test_rebuild_interrupted_before_and_after_replace(self) -> None:
        self.repo.append_transactions([tx(), tx("2023-01-01")])
        expected = self.listing(self.repo)
        for when in ["before", "after"]:
            with self.subTest(when=when):
                self.damage_date_index()
                real_replace = os.replace
                def stop(src, dst):
                    if Path(src).name == "transactions.date.idx.tmp":
                        if when == "after":
                            real_replace(src, dst)
                        raise KeyboardInterrupt
                    return real_replace(src, dst)
                with mock.patch("budget_app.repository.os.replace", side_effect=stop), \
                     self.assertRaises(KeyboardInterrupt):
                    self.open_repo()
                self.assertEqual(self.listing(), expected)

    def test_import_date_merge_interrupted_before_and_after_replace(self) -> None:
        # 기존 마지막 날짜(2023-01-01)보다 이른 행이 있으면 병합해 임시 파일로 교체한다. 그 앞뒤 어디서 멈춰도 import 전 그대로다.
        self.repo.append_transactions([tx("2024-01-15"), tx("2023-01-01")])
        self.repo.update_transaction(self.repo.get_transaction(1).replace_fields(date="2022-06-01"))
        expected = self.listing(self.repo)
        rows = [tx(f"2022-{1 + i % 12:02d}-{1 + i % 28:02d}", i + 1) for i in range(400)]
        for when in ["before", "after"]:
            with self.subTest(when=when):
                real_replace = os.replace
                def stop(src, dst):
                    if Path(src).name == "transactions.date.idx.tmp":
                        if when == "after":
                            real_replace(src, dst)  # 교체는 됐지만 슬롯을 쓰기 전에 멈춘 경우
                        raise KeyboardInterrupt
                    return real_replace(src, dst)
                repo = self.open_repo()
                with mock.patch("budget_app.repository.os.replace", side_effect=stop), \
                     self.assertRaises(KeyboardInterrupt):
                    repo.append_transactions(rows)
                self.assertEqual(self.listing(), expected)
        repo = self.open_repo()
        self.assertEqual(repo.append_transactions(rows), 400)
        self.assertEqual(self.listing(repo), sorted(expected + [(row.date, 3 + i, i + 1) for i, row in enumerate(rows)],
                                                    reverse=True))
        # 살아있는 2개 + 새 400개(예전 날짜 update 가 옛 항목을 지웠다). 중단된 import 가 남긴 항목은 같은 id 로 다시 들어와 겹친다.
        self.assertEqual(repo.date_index.count(), 402)

    def date_entries(self, repo: TransactionRepository) -> list[tuple[int, int]]:
        index = repo.date_index
        return sorted(index.iter_desc(0, index.count()))

    def test_older_date_update_removes_old_entry_newer_date_keeps_it(self) -> None:
        for day in ["2024-01-10", "2024-01-20", "2024-01-30"]:
            self.repo.append_transaction(tx(day))
        live = lambda: sorted((date_ordinal(day), tx_id) for day, tx_id, _ in self.listing(self.repo))  # noqa: E731
        # 더 예전 날짜로: 날짜 인덱스를 다시 쓰면서 그 id 의 옛 항목도 뺀다 → 살아있는 항목만 남는다
        self.repo.update_transaction(self.repo.get_transaction(3).replace_fields(date="2024-01-05"))
        self.assertEqual(self.date_entries(self.repo), live())
        # 더 최신 날짜로: 끝에 덧붙이기만 하고 옛 항목은 남긴다(목록에는 한 번만 나온다)
        self.repo.update_transaction(self.repo.get_transaction(1).replace_fields(date="2025-01-01"))
        self.assertEqual(len(self.date_entries(self.repo)), 4)
        self.assertIn((date_ordinal("2024-01-10"), 1), self.date_entries(self.repo))
        self.assertEqual([row[1] for row in self.listing(self.repo)], [1, 2, 3])
        # 다시 예전 날짜로 바꾸면 그 id 의 남은 옛 항목까지 함께 빠진다
        self.repo.update_transaction(self.repo.get_transaction(1).replace_fields(date="2024-01-01"))
        self.assertEqual(self.date_entries(self.repo), live())
        self.assertEqual([row[1] for row in self.listing(self.repo)], [2, 3, 1])

    def test_crash_after_date_index_rewrite_before_slot_write_restores_both(self) -> None:
        # 옛 항목을 지운 날짜 인덱스를 쓴 뒤 위치 칸을 쓰기 전에 꺼지면, 위치 칸은 아직 수정 전(옛 날짜)이다.
        # 다음 실행이 되돌리기 기록을 보고 위치 칸을 되돌리고 날짜 인덱스도 다시 만들어 거래가 빠지지 않는다.
        self.repo.append_transaction(tx("2024-01-10"))
        self.repo.append_transaction(tx("2024-01-20"))
        expected = self.listing(self.repo)
        script = '''
import os, sys
from pathlib import Path
from budget_app.repository import TransactionIndex, TransactionRepository
repo = TransactionRepository(Path(sys.argv[1]))
repo.ensure_files()
TransactionIndex.write_slot = lambda self, *args: os._exit(76)
repo.update_transaction(repo.get_transaction(2).replace_fields(date="2023-01-01"))
'''
        proc = subprocess.run([sys.executable, "-c", script, str(self.folder)], cwd=ROOT,
                              capture_output=True, timeout=15)
        self.assertEqual(proc.returncode, 76, proc.stderr.decode())
        # 꺼진 직후 날짜 인덱스에는 2번의 옛 항목(2024-01-20)이 없다
        self.assertNotIn((date_ordinal("2024-01-20"), 2), sorted(DateIndex(self.repo.date_idx_path).iter_desc(0, 2)))
        reopened = self.open_repo()
        self.assertEqual(reopened.recovered, ["끝나지 않은 거래 저장의 위치 슬롯을 이전 상태로 복구했습니다."])
        self.assertEqual(self.listing(reopened), expected)
        self.assertEqual(self.date_entries(reopened), [(date_ordinal("2024-01-10"), 1), (date_ordinal("2024-01-20"), 2)])

    def test_missing_and_truncated_index_rebuilt_from_authority(self) -> None:
        self.repo.append_transactions([tx(), tx("2023-01-01"), tx("2025-01-01")])
        self.repo.delete_transaction(2)
        expected = self.listing(self.repo)
        self.repo.date_idx_path.unlink()
        reopened = self.open_repo()
        self.assertEqual(reopened.recovered, [])  # 없던 파일은 조용히 만든다
        self.assertEqual(self.listing(reopened), expected)
        raw = self.repo.date_idx_path.read_bytes()
        self.repo.date_idx_path.write_bytes(raw[:-5])  # 마지막 항목이 덜 써진 손상(12의 배수가 아님)
        reopened = self.open_repo()
        self.assertEqual(reopened.recovered, [REBUILT])
        self.assertEqual(self.listing(reopened), expected)
        self.assertIsNone(reopened.read_transaction(2))  # 삭제한 거래를 JSONL 에서 되살리지 않는다

    def test_jsonl_then_date_failure_leaves_uncommitted_orphan(self) -> None:
        old = self.repo.append_transaction(tx())
        expected = self.listing(self.repo)
        size = self.repo.log_path.stat().st_size
        with mock.patch.object(DateIndex, "merge", side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.repo.append_transaction(tx("2020-01-01"))
        self.assertGreater(self.repo.log_path.stat().st_size, size)
        self.assertEqual(self.listing(), expected)
        with mock.patch.object(DateIndex, "merge", side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.repo.update_transaction(old.replace_fields(date="2020-01-01"))
        self.assertEqual(self.listing(), expected)
        self.assertEqual(self.open_repo().append_transaction(tx()).id, 2)

    def test_torn_slot_add_update_delete_undo(self) -> None:
        old = self.repo.append_transaction(tx())
        for operation in ["add", "update", "delete"]:
            with self.subTest(operation=operation):
                repo = self.open_repo()
                before = repo.idx_path.read_bytes()
                expected = self.listing(repo)
                def torn_append(start, end):
                    with repo.idx_path.open("ab") as fp:
                        fp.write(SLOT_STRUCT.pack(start, end)[:9])
                    raise KeyboardInterrupt
                def torn_write(tx_id, start, end):
                    with repo.idx_path.open("r+b") as fp:
                        fp.seek((tx_id - 1) * 16)
                        fp.write(SLOT_STRUCT.pack(start, end)[:9])
                    raise KeyboardInterrupt
                with mock.patch.object(repo.index, "append_slot", side_effect=torn_append), \
                     mock.patch.object(repo.index, "write_slot", side_effect=torn_write), \
                     self.assertRaises(KeyboardInterrupt):
                    if operation == "add":
                        repo.append_transaction(tx("2022-01-01"))
                    elif operation == "update":
                        repo.update_transaction(old.replace_fields(date="2022-01-01", amount=88888))
                    else:
                        repo.delete_transaction(old.id)
                reopened = self.open_repo()
                self.assertEqual(reopened.idx_path.read_bytes(), before)
                self.assertEqual(self.listing(reopened), expected)
                self.assertTrue(reopened.recovered)
                self.assertFalse(reopened.slot_undo.path.exists())

    def test_slot_recovery_itself_can_be_interrupted(self) -> None:
        old = self.repo.append_transaction(tx())
        before = self.repo.idx_path.read_bytes()
        with mock.patch.object(SlotUndo, "commit", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.repo.update_transaction(old.replace_fields(amount=9999))
        with mock.patch.object(SlotUndo, "commit", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.open_repo()
        self.assertTrue(self.repo.slot_undo.path.exists())
        self.assertEqual(self.open_repo().get_transaction(old.id), old)
        self.assertEqual(self.repo.idx_path.read_bytes(), before)

    def test_corrupt_slot_journal_fails_closed(self) -> None:
        self.repo.append_transaction(tx())
        before = self.repo.idx_path.read_bytes()
        self.repo.slot_undo.path.write_bytes(b"bad undo")
        with self.assertRaises(StorageError):
            self.open_repo()
        self.assertEqual(self.repo.idx_path.read_bytes(), before)

    def test_real_process_crash_before_slot_commit(self) -> None:
        old = self.repo.append_transaction(tx())
        script = '''
import os, sys
from pathlib import Path
from budget_app.repository import TransactionRepository
from budget_app.durability import SlotUndo
repo = TransactionRepository(Path(sys.argv[1]))
repo.ensure_files()
SlotUndo.commit = lambda self: os._exit(74)
repo.update_transaction(repo.get_transaction(1).replace_fields(date="1990-01-01", amount=999))
'''
        proc = subprocess.run([sys.executable, "-c", script, str(self.folder)], cwd=ROOT,
                              capture_output=True, timeout=15)
        self.assertEqual(proc.returncode, 74, proc.stderr.decode())
        reopened = self.open_repo()
        self.assertEqual(reopened.get_transaction(1), old)
        self.assertEqual(self.listing(reopened), [(old.date, 1, old.amount)])

    def test_csv_export_import_recurring_and_backup(self) -> None:
        ctx = AppContext.create(self.folder)
        ctx.categories.add("식비")
        source = self.folder / "input.csv"
        with source.open("w", newline="", encoding="utf-8") as fp:
            writer = csv.writer(fp)
            writer.writerow(["date", "type", "category", "amount", "memo", "tags"])
            for i in range(1200):
                writer.writerow([f"2024-02-{i % 29 + 1:02}", "expense", "식비", i + 1, "한글,CSV", "a,b"])
            writer.writerow(["invalid", "expense", "식비", 1, "", ""])
        report = ctx.csv_service.import_csv(source)
        self.assertEqual((report.imported, report.skipped), (1200, 1))
        out = self.folder / "export.csv"
        criteria = SearchCriteria(month="2024-02")
        self.assertEqual(ctx.csv_service.export_csv(out, ctx.transactions.search(criteria)), 1200)
        ctx.recurring_service.add(31, "expense", "식비", 123)
        self.assertEqual(len(ctx.recurring_service.apply_month("2024-02")[0]), 1)
        self.assertEqual(len(ctx.recurring_service.apply_month("2024-02")[0]), 0)
        expected = self.listing(ctx.repo)
        ctx.repo.compact()
        self.assertEqual(self.listing(), expected)
        backup, names = ctx.backup_service.create()
        self.assertIn("transactions.date.idx", names)
        self.assertEqual((backup / "transactions.date.idx").read_bytes(), ctx.repo.date_idx_path.read_bytes())
        roundtrip = AppContext.create(self.folder / "roundtrip")
        roundtrip.categories.add("식비")
        self.assertEqual(roundtrip.csv_service.import_csv(out).imported, 1200)
        self.assertEqual(sorted((item.date, item.amount, item.memo, item.tags)
                                for item in roundtrip.repo.iter_latest_transactions()),
                         sorted((item.date, item.amount, item.memo, item.tags)
                                for item in ctx.repo.iter_latest_transactions() if not item.source))


if __name__ == "__main__":
    unittest.main()
