"""Compare the original Sorted Array and B+Tree with real fsync enabled.

python tools/bench_date_index.py --sizes 10000 100000 --samples 15 --out doc/bplustree-benchmark.json

The original complete repository module is loaded from the fixed Git commit,
not a rewritten approximation. Full repository timings include JSONL, slots,
and the new slot-undo durability work; index-only timings isolate the index.
"""

from __future__ import annotations

import argparse
import json
import platform
import random
import statistics
import subprocess
import sys
import tempfile
import time
import types
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from budget_app.bplustree import DateIndex
from budget_app.models import Transaction
from budget_app.repository import TransactionRepository

BASE_COMMIT = "4713a5bc9406816477d8ffd4b2e0e0fb498b91aa"


def original_module() -> types.ModuleType:
    source = subprocess.check_output(["git", "show", f"{BASE_COMMIT}:budget_app/repository.py"], cwd=ROOT)
    module = types.ModuleType("budget_app._sorted_array_baseline")
    module.__package__ = "budget_app"
    sys.modules[module.__name__] = module
    exec(compile(source, f"git:{BASE_COMMIT}:budget_app/repository.py", "exec"), module.__dict__)
    return module


def measure(operation: Callable[[int], object], samples: int) -> dict[str, float | int]:
    durations = []
    for i in range(samples):
        start = time.perf_counter_ns()
        operation(i)
        durations.append((time.perf_counter_ns() - start) / 1_000_000)
    return {"median_ms": round(statistics.median(durations), 6),
            "min_ms": round(min(durations), 6), "max_ms": round(max(durations), 6),
            "samples": samples}


def run(size: int, samples: int, baseline: types.ModuleType, folder: Path) -> dict:
    rng = random.Random(645)
    origin = date(2020, 1, 1)
    days = [(origin + timedelta(days=rng.randrange(1827))).isoformat() for _ in range(size)]
    keys = [(date.fromisoformat(day).toordinal(), i + 1) for i, day in enumerate(days)]
    low, high = date(2022, 6, 10).toordinal(), date(2022, 6, 13).toordinal()
    expected_keys = sorted((key for key in keys if low <= key[0] < high), reverse=True)
    result: dict = {"n": size, "range": ["2022-06-10", "2022-06-12"],
                    "range_results": len(expected_keys), "repository": {}, "index_only": {}}
    for label, repo_type, index_type in [("sorted_array", baseline.TransactionRepository, baseline.DateIndex),
                                         ("bplustree", TransactionRepository, DateIndex)]:
        repo = repo_type(folder / f"{label}-repository")
        start = time.perf_counter()
        repo.append_transactions(Transaction(0, day, "expense", "식비", i + 1)
                                 for i, day in enumerate(days))
        bulk_seconds = time.perf_counter() - start
        latest = lambda i: repo.append_transaction(Transaction(0, "2030-01-01", "expense", "식비", 100))
        historic = lambda i: repo.append_transaction(Transaction(0, "1900-01-01", "expense", "식비", 100))
        def update(i: int) -> None:
            old = repo.get_transaction(i + 1)
            repo.update_transaction(old.replace_fields(date="1950-01-01"))
        def query(i: int) -> None:
            actual = [(date.fromisoformat(item.date).toordinal(), item.id)
                      for item in repo.iter_latest_transactions("2022-06-10", "2022-06-12")]
            # Updates use early ids, so compute the expected post-update result.
            self_expected = [key for key in expected_keys if key[1] > samples]
            if actual != self_expected:
                raise AssertionError((label, "repository range results differ"))
        result["repository"][label] = {
            "bulk_add_seconds": round(bulk_seconds, 6),
            "add_latest": measure(latest, samples),
            "insert_historical": measure(historic, samples),
            "update_date": measure(update, samples),
            "date_range": measure(query, samples),
            "date_index_bytes": repo.date_idx_path.stat().st_size,
        }
        index = index_type(folder / f"{label}-only.idx")
        index.rebuild(sorted(keys))
        def index_query(i: int) -> None:
            if label == "bplustree":
                actual = list(index.iter_range((low, 0), (high, 0), reverse=True))
            else:
                actual = list(index.iter_desc(index.bisect_left((low, 0)), index.bisect_left((high, 0))))
            if actual != expected_keys:
                raise AssertionError((label, "index range results differ"))
        result["index_only"][label] = {
            "add_latest": measure(lambda i: index.merge([(800000, size + i + 1)]), samples),
            "insert_historical": measure(lambda i: index.merge([(600000, size + samples + i + 1)]), samples),
            # Existing date updates retain the old key, matching repository policy.
            "update_date_key": measure(lambda i: index.merge([(650000, i + 1)]), samples),
            "date_range": measure(index_query, samples),
        }
        if label == "bplustree":
            result["tree"] = index.validate()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sizes", type=int, nargs="+", default=[10000, 100000])
    parser.add_argument("--samples", type=int, default=15)
    parser.add_argument("--out", type=Path, default=ROOT / "doc/bplustree-benchmark.json")
    # 측정 파일을 둘 폴더. fsync 비용은 파일시스템마다 다르므로 비교할 때는 같은 폴더를 쓴다.
    parser.add_argument("--tmp-dir", type=Path, default=None, help="임시 측정 폴더(기본: 시스템 임시 폴더)")
    args = parser.parse_args()
    if args.samples < 1 or any(size < args.samples for size in args.sizes):
        parser.error("sizes must be >= samples >= 1")
    baseline = original_module()
    report = {"base_commit": BASE_COMMIT, "python": platform.python_version(),
              "platform": platform.platform(), "seed": 645, "fsync": "enabled, no mocks",
              "timing": "in-process wall time, median, real files; no Python startup/CLI rendering",
              "runs": []}
    runs: list[dict] = []
    report["runs"] = runs
    with tempfile.TemporaryDirectory(prefix="allowance-compare-", dir=args.tmp_dir) as temp:
        for size in args.sizes:
            folder = Path(temp) / str(size)
            folder.mkdir()
            result = run(size, args.samples, baseline, folder)
            runs.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
