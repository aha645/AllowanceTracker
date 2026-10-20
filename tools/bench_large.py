"""대용량(기본 10만 건) 데이터에서 주요 명령의 실행 시간과 최대 메모리를 잰다.

임시 폴더에 CSV 로 N 건을 import 한 뒤, 실제 CLI(`python -m budget_app`)를 명령마다 새 프로세스로
실행해 측정한다. 프로세스를 따로 띄우므로 파이썬 시작 비용(약 수십 ms)이 모든 수치에 포함된다.

    python tools/bench_large.py            # 10만 건
    python tools/bench_large.py --n 300000 # 건수 지정
"""

from __future__ import annotations

import argparse
import csv
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CATEGORIES = ["식비", "교통", "월세", "월급", "쇼핑", "의료", "문화", "통신"]
UNUSED_CATEGORY = "미사용"
FIRST_DAY = date(2020, 1, 1)
DAYS = (date(2024, 12, 31) - FIRST_DAY).days + 1  # 5년


def run(data_dir: Path, *args: str) -> tuple[float, float, int]:
    """명령 하나를 새 프로세스로 실행하고 (초, 최대 RSS MB, 종료 코드)를 돌려준다."""
    cmd = [sys.executable, "-m", "budget_app", "--data-dir", str(data_dir), *args]
    start = time.perf_counter()
    proc = subprocess.Popen(cmd, cwd=PROJECT_ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    # os.wait4 는 그 자식 하나의 자원 사용량을 준다(RUSAGE_CHILDREN 은 자식 전체의 최댓값이라 못 씀).
    _, status, usage = os.wait4(proc.pid, 0)
    elapsed = time.perf_counter() - start
    proc.returncode = os.waitstatus_to_exitcode(status)
    # gotcha: ru_maxrss 단위가 macOS 는 바이트, Linux 는 KB 다.
    rss_mb = usage.ru_maxrss / (1024 * 1024 if sys.platform == "darwin" else 1024)
    if proc.returncode != 0 and proc.stderr is not None:
        sys.stderr.write(proc.stderr.read().decode())
    return elapsed, rss_mb, proc.returncode


def write_csv(path: Path, n: int) -> None:
    rng = random.Random(42)  # 매번 같은 데이터
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow(["date", "type", "category", "amount", "memo", "tags"])
        for i in range(n):
            day = FIRST_DAY + timedelta(days=rng.randrange(DAYS))
            category = rng.choice(CATEGORIES)
            type_ = "income" if category == "월급" else "expense"
            writer.writerow([day.isoformat(), type_, category, rng.randint(1000, 300000), f"메모 {i}", "태그"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--n", type=int, default=100_000, help="생성할 거래 수(기본 100000)")
    args = parser.parse_args()

    work = Path(tempfile.mkdtemp(prefix="budget_bench_"))
    data_dir = work / "data"
    csv_path = work / "bulk.csv"
    try:
        base_time, base_rss, _ = run(data_dir, "category", "list")
        for name in [*CATEGORIES, UNUSED_CATEGORY]:
            run(data_dir, "category", "add", "--name", name)
        write_csv(csv_path, args.n)

        cases = [
            ("import (N건, 빈 저장소)", ["import", "--from", str(csv_path)]),
            ("list --limit 20", ["list", "--limit", "20"]),
            ("summary --month 2024-06 (한 달)", ["summary", "--month", "2024-06"]),
            ("search --month 2024-06 --category 식비", ["search", "--month", "2024-06", "--category", "식비"]),
            ("search --category 식비 --limit 20", ["search", "--category", "식비", "--limit", "20"]),
            ("search --q 없는말 (기간 없음, 전체 훑기)", ["search", "--q", "없는말"]),
            ("export 전체 기간", ["export", "--out", str(work / "out.csv"), "--from", "2020-01-01", "--to", "2024-12-31"]),
            ("add 최신 날짜 (끝에 추가)", ["add", "--date", "2025-01-01", "--type", "expense", "--category", "식비", "--amount", "1000"]),
            ("add 옛 날짜 (B+Tree 삽입)", ["add", "--date", "2020-01-01", "--type", "expense", "--category", "식비", "--amount", "1000"]),
            ("category remove 미사용", ["category", "remove", "--name", UNUSED_CATEGORY]),
            ("compact", ["compact"]),
        ]
        print(f"N = {args.n:,}건, Python {sys.version.split()[0]}, {sys.platform}")
        print(f"기준선(빈 저장소 category list): {base_time:.2f}s, {base_rss:.0f}MB\n")
        print("| 명령 | 시간 | 최대 메모리 |")
        print("|---|---:|---:|")
        for label, cmd in cases:
            elapsed, rss, code = run(data_dir, *cmd)
            mark = "" if code == 0 else f" (종료 코드 {code})"
            print(f"| `{label}` | {elapsed:.2f}s | {rss:.0f}MB{mark} |")

        print("\n| 파일 | 크기 |")
        print("|---|---:|")
        for name in ("transactions.jsonl", "transactions.idx", "transactions.date.idx"):
            size = (data_dir / name).stat().st_size
            print(f"| `{name}` | {size / 1024 / 1024:.1f}MB |")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
