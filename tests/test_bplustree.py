"""B+Tree oracle, persistence, split, I/O and real process-crash tests."""

from __future__ import annotations

import bisect
import os
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from budget_app.bplustree import DateIndex, MAX_ID, MAX_ORDINAL, PAGE_SIZE
from budget_app.models import StorageError
from budget_app.repository import entry_key

ROOT = Path(__file__).resolve().parents[1]


class BPlusTreeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "transactions.date.idx"

    def test_empty_and_key_boundaries(self) -> None:
        index = DateIndex(self.path)
        index.rebuild(())
        self.assertEqual(index.count(), 0)
        self.assertEqual(list(index.iter_range()), [])
        self.assertEqual(list(index.iter_desc(0, 0)), [])
        self.assertEqual(index.bisect_left((0, 0)), 0)
        keys = [(1, 1), (1, 2**32 + 3), (MAX_ORDINAL, MAX_ID)]
        index.merge_keys([entry_key(key) for key in keys])
        self.assertEqual(list(index.iter_range()), keys)
        self.assertEqual(list(index.iter_range((MAX_ORDINAL, 0), (MAX_ORDINAL + 1, 0))), keys[-1:])
        self.assertTrue(index.contains(keys[1]))
        self.assertFalse(index.contains((1, 2)))
        before = self.path.read_bytes()
        for key in [(0, 1), (MAX_ORDINAL + 1, 1), (1, 0), (1, MAX_ID + 1)]:
            with self.subTest(key=key), self.assertRaises(StorageError):
                index.merge([key])
            self.assertEqual(self.path.read_bytes(), before)
        with self.assertRaises(ValueError):
            DateIndex(self.path, 300)

    def test_rebuild_and_sorted_appends_fill_leaves(self) -> None:
        index = DateIndex(self.path, 256)  # leaf 18키, internal 자식 8개
        for n in [1, 18, 19, 18 * 8 + 1, 3000]:
            with self.subTest(n=n):
                keys = [(700000 + i // 5, i + 1) for i in range(n)]
                index.rebuild(keys + keys[-1:])  # 같은 키는 한 번만 둔다
                info = index.validate()
                self.assertEqual(list(index.iter_range()), keys)
                self.assertEqual(info["leaves"], -(-n // 18))
        # 날짜순으로 이어 붙이면 왼쪽 leaf 를 꽉 찬 채로 두고 나눈다(반반이면 leaf 가 두 배 가까이 된다).
        index.rebuild(())
        keys = [(700000 + i // 5, i + 1) for i in range(3000)]
        for offset in range(0, len(keys), 100):
            index.merge(keys[offset:offset + 100])
        self.assertEqual(list(index.iter_range()), keys)
        self.assertEqual(index.validate()["leaves"], -(-3000 // 18))
        index.rebuild_merged([(699999, 9000), keys[0], (800000, 9001)])
        self.assertEqual(list(index.iter_range()), [(699999, 9000), *keys, (800000, 9001)])
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            index.rebuild([(700001, 1), (700000, 2)])
        self.assertEqual(self.path.read_bytes(), before)

    def test_randomized_multilevel_splits_rank_and_links(self) -> None:
        index = DateIndex(self.path, 256)
        index.rebuild(())
        keys = [(700000 + i % 137, i + 1) for i in range(4000)]
        random.Random(42).shuffle(keys)
        oracle: list[tuple[int, int]] = []
        for offset in range(0, len(keys), 31):
            batch = keys[offset:offset + 31]
            index.merge(batch)
            oracle = sorted(oracle + batch)
            self.assertEqual(list(index.iter_range()), oracle)
            self.assertEqual(list(index.iter_range(reverse=True)), oracle[::-1])
            index.validate()
        # Repeat existing keys: no duplicate output or needless page rewrite.
        before = self.path.read_bytes()
        index.merge(keys)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertGreaterEqual(index.validate()["height"], 4)
        reopened = DateIndex(self.path)
        self.assertEqual(reopened.validate()["page_size"], 256)
        for key in keys[::19] + [(700000, 0), (700200, 0)]:
            self.assertEqual(reopened.bisect_left(key), bisect.bisect_left(oracle, key))
        for lo, hi in [(0, 10), (17, 3001), (99, 99), (3990, 5000), (5000, 6000)]:
            self.assertEqual(list(reopened.iter_desc(lo, hi)), oracle[lo:hi][::-1])
            self.assertEqual(list(reopened.iter_asc(lo, hi)), oracle[lo:hi])
        for low, high in [(700010, 700020), (699990, 700001), (700136, 700140), (700020, 700010)]:
            expected = [key for key in oracle if low <= key[0] < high]
            self.assertEqual(list(reopened.iter_range((low, 0), (high, 0))), expected)
            self.assertEqual(list(reopened.iter_range((low, 0), (high, 0), reverse=True)), expected[::-1])

    def test_large_merge_commits_in_bounded_chunks(self) -> None:
        # 변경 페이지가 상한을 넘으면 묶음마다 먼저 확정해, 큰 import 도 메모리가 묶음 크기로 묶인다
        index = DateIndex(self.path, 256)
        index.rebuild([(700000, 1)])
        rng = random.Random(7)
        keys = [(700000 + rng.randrange(400), i + 2) for i in range(3000)]
        commits: list[int] = []
        original_commit = DateIndex._commit

        def counted(this: DateIndex, session) -> None:  # type: ignore[no-untyped-def]
            commits.append(len(session.dirty))
            original_commit(this, session)

        with mock.patch("budget_app.bplustree.MAX_DIRTY_PAGES", 8), \
                mock.patch.object(DateIndex, "_commit", counted):
            index.merge(keys)
        self.assertGreater(len(commits), 1)
        # 한 번의 삽입이 leaf·새 leaf·이웃 leaf·조상과 그 분할 형제·새 root 까지 바꿀 수 있어
        # 확정 시점의 변경 페이지는 상한 + (2 x 높이 + 3) 안에 든다
        height = index.validate()["height"]
        self.assertLessEqual(max(commits), 8 + 2 * height + 3)
        self.assertEqual(list(index.iter_range()), sorted(keys + [(700000, 1)]))
        self.assertFalse(index.wal_path.exists())

    def test_100000_individual_inserts_default_pages(self) -> None:
        index = DateIndex(self.path)
        index.rebuild(())
        # No mocked fsync, WAL bypass or rebuild: 100,000 individual disk inserts.
        # Dates visit the past as well as the future; ties have independent ids.
        keys = [(700000 + (i * 7919) % 3653, i + 1) for i in range(100000)]
        for key in keys:
            index.merge([key])
        oracle = sorted(keys)
        info = index.validate()
        self.assertEqual(info["entries"], 100000)
        self.assertGreaterEqual(info["height"], 3)
        self.assertEqual(info["page_size"], PAGE_SIZE)
        self.assertEqual(self.path.stat().st_size % PAGE_SIZE, 0)
        self.assertEqual(list(index.iter_range()), oracle)
        self.assertEqual(list(index.iter_range(reverse=True)), oracle[::-1])
        self.assertEqual(DateIndex(self.path).count(), 100000)
        # Read work scales with tree height; an old-date insert doesn't scan N keys.
        index.merge([(699999, 100001)])
        self.assertLessEqual(index.last_page_reads, info["height"] + 2)
        self.assertTrue(index.contains(oracle[43210]))
        self.assertLessEqual(index.last_page_reads, info["height"] + 1)

    def test_process_crash_at_wal_page_and_root_boundaries(self) -> None:
        for stage in ["wal_prepared", "wal_published", "page_written", "root_written", "data_synced", "wal_removed"]:
            with self.subTest(stage=stage):
                index = DateIndex(self.path, 256)
                old = [(700000, i + 1) for i in range(18)]  # next insert splits leaf/root
                index.rebuild(old)
                script = '''
import os, sys
from pathlib import Path
from budget_app.bplustree import DateIndex
idx = DateIndex(Path(sys.argv[1]))
idx._checkpoint = lambda stage: os._exit(73) if stage == sys.argv[2] else None
idx.merge([(700000, 19)])
'''
                proc = subprocess.run([sys.executable, "-c", script, str(self.path), stage], cwd=ROOT,
                                      capture_output=True, timeout=15)
                self.assertEqual(proc.returncode, 73, proc.stderr.decode())
                reopened = DateIndex(self.path)
                expected = old if stage == "wal_prepared" else old + [(700000, 19)]
                self.assertEqual(list(reopened.iter_range()), expected)
                self.assertEqual(list(reopened.iter_range(reverse=True)), expected[::-1])
                reopened.validate()
                self.assertFalse(reopened.wal_path.exists())
                self.assertFalse(reopened.wal_path.with_name(reopened.wal_path.name + ".tmp").exists())

    def test_internal_split_and_interrupted_redo(self) -> None:
        index = DateIndex(self.path, 256)
        index.rebuild([(700000, i + 1) for i in range(90)])
        additions = [(699000 + i % 100, i + 1000) for i in range(500)]
        def interrupted(stage: str) -> None:
            if stage == "page_written":
                raise KeyboardInterrupt
        with mock.patch.object(index, "_checkpoint", side_effect=interrupted), self.assertRaises(KeyboardInterrupt):
            index.merge(additions)
        # Crash again during redo: the committed WAL must remain replayable.
        with mock.patch.object(index, "_checkpoint", side_effect=interrupted), self.assertRaises(KeyboardInterrupt):
            index.recover()
        self.assertTrue(index.wal_path.exists())
        reopened = DateIndex(self.path)
        expected = sorted([(700000, i + 1) for i in range(90)] + additions)
        self.assertEqual(list(reopened.iter_range()), expected)
        self.assertGreaterEqual(reopened.validate()["height"], 4)

    def test_torn_page_and_root_repaired_by_wal(self) -> None:
        for offset in [40, PAGE_SIZE + 50]:
            with self.subTest(offset=offset):
                index = DateIndex(self.path)
                index.rebuild([(700000, 1)])
                def stop(stage: str) -> None:
                    if stage == "wal_published":
                        raise KeyboardInterrupt
                with mock.patch.object(index, "_checkpoint", side_effect=stop), self.assertRaises(KeyboardInterrupt):
                    index.merge([(700001, 2)])
                with self.path.open("r+b") as fp:
                    fp.seek(offset)
                    fp.write(b"\xff" * 16)
                    fp.flush()
                    os.fsync(fp.fileno())
                reopened = DateIndex(self.path)
                self.assertEqual(list(reopened.iter_range()), [(700000, 1), (700001, 2)])
                reopened.validate()

    def test_corruption_detected_and_uncommitted_batch_has_no_effect(self) -> None:
        index = DateIndex(self.path)
        index.rebuild([(700000, 1)])
        good = self.path.read_bytes()
        def broken():
            yield (700001, 2)
            raise RuntimeError("generator failed")
        with self.assertRaises(RuntimeError):
            index.merge(broken())
        self.assertEqual(self.path.read_bytes(), good)
        bad = bytearray(good)
        bad[PAGE_SIZE + 50] ^= 1
        self.path.write_bytes(bad)
        with self.assertRaises(StorageError):
            list(index.iter_range())
        self.path.write_bytes(good)
        def stop(stage: str) -> None:
            if stage == "wal_published":
                raise KeyboardInterrupt
        with mock.patch.object(index, "_checkpoint", side_effect=stop), self.assertRaises(KeyboardInterrupt):
            index.merge([(700001, 2)])
        wal = bytearray(index.wal_path.read_bytes())
        wal[-1] ^= 1
        index.wal_path.write_bytes(wal)
        with self.assertRaises(StorageError):
            index.recover()
        self.assertEqual(self.path.read_bytes(), good)  # rejected before modifying pages

    def test_rebuild_failure_preserves_original(self) -> None:
        index = DateIndex(self.path)
        index.rebuild([(700000, 1)])
        good = self.path.read_bytes()
        def stop(stage: str) -> None:
            if stage == "rebuild_synced":
                raise KeyboardInterrupt
        with mock.patch.object(index, "_checkpoint", side_effect=stop), self.assertRaises(KeyboardInterrupt):
            index.rebuild([(700001, 2)])
        self.assertEqual(self.path.read_bytes(), good)
        self.assertEqual(list(DateIndex(self.path).iter_range()), [(700000, 1)])

    def test_wal_publish_failure_does_not_modify_tree(self) -> None:
        index = DateIndex(self.path)
        index.rebuild([(700000, 1)])
        good = self.path.read_bytes()
        with mock.patch("budget_app.bplustree.os.replace", side_effect=OSError("rename failed")), \
             self.assertRaises(OSError):
            index.merge([(700001, 2)])
        self.assertEqual(self.path.read_bytes(), good)
        self.assertEqual(list(DateIndex(self.path).iter_range()), [(700000, 1)])

    def test_multiple_internal_splits_real_process_crash(self) -> None:
        index = DateIndex(self.path, 256)
        old = [(700000, i + 1) for i in range(90)]
        index.rebuild(old)
        script = '''
import os, sys
from pathlib import Path
from budget_app.bplustree import DateIndex
idx = DateIndex(Path(sys.argv[1]))
idx._checkpoint = lambda stage: os._exit(75) if stage == "wal_published" else None
idx.merge([(699000 + i % 100, i + 1000) for i in range(500)])
'''
        proc = subprocess.run([sys.executable, "-c", script, str(self.path)], cwd=ROOT,
                              capture_output=True, timeout=15)
        self.assertEqual(proc.returncode, 75, proc.stderr.decode())
        reopened = DateIndex(self.path)
        additions = [(699000 + i % 100, i + 1000) for i in range(500)]
        self.assertEqual(list(reopened.iter_range()), sorted(old + additions))
        self.assertGreaterEqual(reopened.validate()["height"], 4)


if __name__ == "__main__":
    unittest.main()
