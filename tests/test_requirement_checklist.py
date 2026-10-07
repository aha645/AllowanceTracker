"""요구사항의 10대 기능과 응용 기능을 순서대로 하나씩 검증하는 인수 테스트(총 17개).

기능 하나당 테스트 하나이며, 앞 테스트가 만든 데이터를 뒤 테스트가 이어받는다.

    [사전]  000  데이터 초기화
    [10대 기능]
            010  add        카테고리 없으면 차단 → 카테고리 등록 → 대화형/옵션 등록, 잘못된 입력 거부, 재입력
            020  list       거래일자 최신순, --limit, --limit/--all 동시 사용 거부
            030  search     기간/카테고리/타입/메모/태그, AND 결합, 조건 없음 거부
            040  summary    수입/지출/잔액, 지출 TOP N, 데이터 없는 달
            050  budget     설정 → 사용률 → 덮어쓰기 → 초과 경고
            060  category   중복 차단, 목록, 사용 중 카테고리 삭제 차단
            070  update     대화형 부분 수정(엔터=유지, -=비우기), summary 반영, 없는 id
            080  delete     삭제 → list/summary 반영, 없는 id
            090  import     CSV 가져오기(잘못된 행 skip) → summary 반영
            095  export     월/기간 내보내기, 고정 스키마, 조건 필수, 저장 파일 3종
    [응용 기능]
            100  로그·종료 코드 (--verbose, 0/1/130, 스택트레이스 없음)
            110  표 포맷 (구분선, 부호, 천 단위, 예산 막대)
            120  저장 원자성 (임시파일 교체 실패 시 원본 보존, 로그 append 전용 + 인덱스 제자리 갱신)
            130  백업
            140  반복 내역
            150  날짜 인덱스 (옛 날짜 거래여도 거래일자 최신순, 조기 종료, compact/삭제 후 복구)

시나리오 데이터 (정상적인 수입/지출 조합):
    a  2024-01-05  expense  식비    12,000   점심      #외식      (대화형 add)
    b  2024-01-10  expense  교통    20,000   지하철
    c  2024-01-25  income   월급  3,000,000  1월 급여
    d  2024-01-31  expense  월세   500,000   1월 월세
    e  2024-03-01  expense  교통     1,500             (재입력 테스트로 등록, 다른 달이라 1월/2월 집계에 영향 없음)

모든 테스트가 tests/data 폴더 하나를 공유하며 번호 순서대로 실행된다. 데이터는
test_000_reset_data 에서만 지운다. 파일 전체를 실행하면 000 이 먼저 초기화하므로 몇 번을
실행해도 같은 결과이고, 개별 실행은 직전 실행이 남긴 데이터 위에서 동작한다(처음부터 다시
하려면 000 → 010 순서로 실행).

```bash
python -m unittest tests.test_requirement_checklist -v
```
"""

from __future__ import annotations

import csv
import io
import json
import re
import shutil
import sys
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Iterable, TextIO
from unittest import mock

# `python tests/test_requirement_checklist.py` 로 직접 실행할 때도 budget_app 을
# 찾을 수 있도록 프로젝트 루트를 sys.path 에 넣는다.
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from budget_app.cli import main
from budget_app.commands.transaction import cmd_add
from budget_app.repository import TransactionRepository
from budget_app.stores import RecurringStore


class _TeeIO(io.StringIO):
    """StringIO 로 캡처하면서 같은 내용을 실시간으로 콘솔에도 써서 보여준다."""

    def __init__(self, mirror: TextIO) -> None:
        super().__init__()
        self._mirror = mirror

    def write(self, s: str) -> int:
        self._mirror.write(s)
        return super().write(s)


class RequirementChecklistTestCase(unittest.TestCase):
    data_dir = Path(__file__).resolve().parent / "data"
    #: 시나리오에서 만든 거래 id (이름 → "TX-n"). 앞 테스트가 채우고 뒤 테스트가 쓴다.
    ids: dict[str, str] = {}
    #: 시나리오 거래를 저장 파일에서 다시 찾기 위한 식별 정보(date, category).
    #: 개별 테스트만 따로 실행해도 앞선 실행이 남긴 데이터에서 id 를 복원할 수 있게 한다.
    SCENARIO_KEYS = {"a": ("2024-01-05", "식비"), "b": ("2024-01-10", "교통"),
                     "c": ("2024-01-25", "월급"), "d": ("2024-01-31", "월세"),
                     "e": ("2024-03-01", "교통")}

    def setUp(self) -> None:
        sys.stdout.write(f"\n{'=' * 72}\n[{self._testMethodName}]\n{'=' * 72}\n")

    # ------------------------------------------------------------------ 공용 보조
    def _id(self, key: str) -> str:
        """시나리오 거래(a~e)/반복 규칙(rule)의 id. 이번 실행에서 모르면 저장 파일에서 복원한다.

        거래는 삭제돼도 로그(transactions.jsonl)에 기록이 남으므로 삭제된 거래(b)도 찾을 수 있다.
        """
        if key not in self.ids and key in self.SCENARIO_KEYS:
            date, category = self.SCENARIO_KEYS[key]
            log = self.data_dir / "transactions.jsonl"
            for line in (log.read_text(encoding="utf-8").splitlines() if log.exists() else []):
                row = json.loads(line)
                if (row["date"], row["category"]) == (date, category):
                    self.ids[key] = f"TX-{row['id']}"
        if key not in self.ids and key == "rule":
            rules = RecurringStore(self.data_dir).list_all()
            if rules:
                self.ids[key] = rules[-1].display_id
        if key not in self.ids:
            self.fail(f"'{key}' 의 id 를 알 수 없습니다. 앞 단계(add)를 먼저 실행하세요.")
        return self.ids[key]

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        """CLI 를 실행하고 (종료 코드, stdout, stderr) 를 돌려준다(콘솔에도 그대로 보여준다)."""
        console = sys.stdout
        console.write(f"\n$ python -m budget_app {' '.join(argv)}\n")
        out, err = _TeeIO(console), _TeeIO(console)
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["--data-dir", str(self.data_dir), *argv])
        console.write(f"[종료 코드: {code}]\n")
        return code, out.getvalue(), err.getvalue()

    @contextmanager
    def fake_input(self, answers: Iterable[str]):
        """대화형 input() 을 모의 실행한다(프롬프트 뒤에 입력값을 이어 붙여 보여준다)."""
        console = sys.stdout
        it = iter(answers)

        def _input(prompt: str = "") -> str:
            value = next(it)
            console.write(f"{prompt}{value}\n")
            return value

        with mock.patch("builtins.input", _input):
            yield

    @contextmanager
    def fake_interrupt(self):
        """대화형 입력 도중 사용자가 Ctrl+C 를 누른 상황을 모의 실행한다."""
        console = sys.stdout

        def _input(prompt: str = "") -> str:
            console.write(f"{prompt}^C\n")
            raise KeyboardInterrupt

        with mock.patch("builtins.input", _input):
            yield

    @staticmethod
    def _extract_id(out: str) -> str:
        match = re.search(r"id=(TX-\d+)", out)
        assert match is not None, f"id 를 찾을 수 없음: {out!r}"
        return match.group(1)

    @staticmethod
    def _has(out: str, tx_id: str) -> bool:
        """출력에 거래 id 가 (TX-1 vs TX-10 을 구분해서) 나타나는지."""
        return re.search(rf"{re.escape(tx_id)}(?!\d)", out) is not None

    def _add(self, key: str, date: str, type_: str, category: str, amount: str, memo: str = "") -> None:
        """옵션 방식으로 거래를 등록하고 id 를 self.ids[key] 에 기록한다."""
        argv = ["add", "--date", date, "--type", type_, "--category", category, "--amount", amount]
        if memo:
            argv += ["--memo", memo]
        code, out, _ = self.run_cli(*argv)
        self.assertEqual(code, 0)
        self.ids[key] = self._extract_id(out)

    def _listed_ids(self) -> list[str]:
        """`list --all` 표의 거래 id 를 위에서 아래(출력 순서)로 돌려준다."""
        _, out, _ = self.run_cli("list", "--all")
        return re.findall(r"^\s*\d+\s+(TX-\d+)\s", out, re.M)

    # ==================================================================
    # 사전: 데이터 초기화
    # ==================================================================
    def test_000_reset_data(self) -> None:
        """tests/data 를 비운다(수동 초기화). 다른 테스트는 데이터를 지우지 않는다."""
        shutil.rmtree(self.data_dir, ignore_errors=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        type(self).ids = {}
        self.assertEqual(list(self.data_dir.iterdir()), [])

    # ==================================================================
    # 10대 기능
    # ==================================================================
    def test_010_add(self) -> None:
        # 카테고리가 하나도 없으면 입력 프롬프트로 가지 않고 즉시 차단(정책 안 B)
        code, out, err = self.run_cli("add")
        self.assertEqual(code, 1)
        self.assertIn("[오류] 등록된 카테고리가 없습니다.", err)
        self.assertIn("[힌트]", err)
        self.assertEqual(out, "")

        # 사전 준비: 기본 카테고리 등록
        for name in ("식비", "교통", "월급", "월세"):
            code, out, _ = self.run_cli("category", "add", "--name", name)
            self.assertEqual(code, 0)
            self.assertIn("[저장 완료]", out)

        # 대화형 등록: 생성된 id 가 출력된다
        with self.fake_input(["2024-01-05", "expense", "식비", "12000", "점심", "외식"]):
            code, out, _ = self.run_cli("add")
        self.assertEqual(code, 0)
        self.assertIn("[저장 완료] id=TX-", out)
        self.ids["a"] = self._extract_id(out)

        # 옵션 방식 등록
        self._add("b", "2024-01-10", "expense", "교통", "20000", "지하철")
        self._add("c", "2024-01-25", "income", "월급", "3000000", "1월 급여")
        self._add("d", "2024-01-31", "expense", "월세", "500000", "1월 월세")
        self.assertEqual(len({self._id(k) for k in "abcd"}), 4)

        # 잘못된 값은 저장되지 않고 거부된다
        cases = {
            "날짜 형식 오류": ("2024-13-40", "expense", "식비", "1000"),
            "음수 금액": ("2024-01-01", "expense", "식비", "-500"),
            "0 금액": ("2024-01-01", "expense", "식비", "0"),
            "허용되지 않은 type": ("2024-01-01", "transfer", "식비", "1000"),
            "미등록 카테고리": ("2024-01-01", "expense", "없는카테고리", "1000"),
        }
        for label, (date, type_, category, amount) in cases.items():
            with self.subTest(label):
                try:
                    code, out, _ = self.run_cli(
                        "add", "--date", date, "--type", type_, "--category", category, "--amount", amount
                    )
                except SystemExit as exc:  # argparse 가 허용되지 않은 --type 을 직접 거부
                    self.assertNotEqual(exc.code, 0)
                    continue
                self.assertEqual(code, 1)
                self.assertNotIn("[저장 완료]", out)

        # 대화형에서는 잘못 입력해도 다시 묻는다. 이 거래(e)는 시나리오에 남긴다(다른 달이라 집계 영향 없음).
        answers = ["2024-13-40", "2024-03-01", "expense", "없는것", "교통", "-1", "1500", "", ""]
        with self.fake_input(answers):
            code, out, err = self.run_cli("add")
        self.assertEqual(code, 0)
        self.assertEqual(err.count("[오류]"), 3)  # 날짜 / 카테고리 / 금액 각각 한 번씩 거부 후 재입력
        self.ids["e"] = self._extract_id(out)

    def test_020_list(self) -> None:
        # 거래일자 최신순: 나중에 등록한 것이 위
        code, out, _ = self.run_cli("list")
        self.assertEqual(code, 0)
        positions = [out.index(self._id(k)) for k in ("e", "d", "c", "b", "a")]
        self.assertEqual(positions, sorted(positions))

        # --limit: 최근 2건(e, d)만
        _, out, _ = self.run_cli("list", "--limit", "2")
        self.assertTrue(self._has(out, self._id("e")) and self._has(out, self._id("d")))
        for key in ("c", "b", "a"):
            self.assertFalse(self._has(out, self._id(key)))

        # --limit 과 --all 은 함께 쓸 수 없다
        with self.assertRaises(SystemExit) as raised:
            self.run_cli("list", "--limit", "5", "--all")
        self.assertEqual(raised.exception.code, 2)

    def test_030_search(self) -> None:
        cases = {
            "기간": (["--from", "2024-01-10", "--to", "2024-01-25"], {"b", "c"}),
            "카테고리": (["--category", "식비"], {"a"}),
            "타입": (["--type", "income"], {"c"}),
            "메모 키워드": (["--q", "지하철"], {"b"}),
            "태그": (["--tag", "외식"], {"a"}),
            "AND 결합": (["--type", "expense", "--from", "2024-01-01", "--to", "2024-01-31",
                        "--category", "월세"], {"d"}),
        }
        for label, (argv, expected) in cases.items():
            with self.subTest(label):
                code, out, _ = self.run_cli("search", *argv)
                self.assertEqual(code, 0)
                for key in "abcd":
                    self.assertEqual(self._has(out, self._id(key)), key in expected, f"{label}: {key}")

        code, _, err = self.run_cli("search")  # 조건이 하나도 없으면 오류
        self.assertEqual(code, 1)
        self.assertIn("[오류]", err)

    def test_040_summary(self) -> None:
        code, out, _ = self.run_cli("summary", "--month", "2024-01", "--top", "2")
        self.assertEqual(code, 0)
        self.assertIn("총 수입  3,000,000원", out)
        self.assertIn("총 지출  532,000원", out)  # 12,000 + 20,000 + 500,000
        self.assertIn("잔액     2,468,000원", out)
        self.assertLess(out.index("월세"), out.index("교통"))  # 지출 큰 순서
        self.assertNotIn("식비", out)  # TOP 2 에 들지 못함

        code, out, _ = self.run_cli("summary", "--month", "2023-12")  # 거래가 없는 달
        self.assertEqual(code, 0)
        self.assertIn("데이터 없음", out)

    def test_050_budget(self) -> None:
        code, out, _ = self.run_cli("budget", "set", "--month", "2024-01", "--amount", "600000")
        self.assertEqual(code, 0)
        self.assertIn("[저장 완료] 2024-01 예산 600,000원", out)
        _, out, _ = self.run_cli("summary", "--month", "2024-01")
        self.assertIn("88.7%", out)  # 532,000 / 600,000
        self.assertNotIn("[경고]", out)

        code, out, _ = self.run_cli("budget", "set", "--month", "2024-01", "--amount", "500000")
        self.assertIn("덮어썼습니다", out)  # 같은 달은 덮어쓰기
        _, out, _ = self.run_cli("summary", "--month", "2024-01")
        self.assertIn("106.4%", out)  # 532,000 / 500,000
        self.assertIn("[경고]", out)
        self.assertIn("32,000원 초과", out)

        code, _, err = self.run_cli("budget", "set", "--month", "2024-01", "--amount", "0")
        self.assertEqual(code, 1)
        self.assertIn("[오류]", err)

    def test_060_category(self) -> None:
        self.assertEqual(self.run_cli("category", "add", "--name", "여가")[0], 0)
        code, _, err = self.run_cli("category", "add", "--name", "식비")  # 중복 등록 차단
        self.assertEqual(code, 1)
        self.assertIn("이미 등록", err)

        code, out, _ = self.run_cli("category", "list")
        self.assertEqual(code, 0)
        for name in ("식비", "교통", "월급", "월세", "여가"):
            self.assertIn(name, out)
        self.assertIn("[완료] 5개", out)

        code, _, err = self.run_cli("category", "remove", "--name", "식비")  # 거래에서 사용 중
        self.assertEqual(code, 1)
        self.assertIn("사용 중", err)
        code, out, _ = self.run_cli("category", "remove", "--name", "여가")  # 미사용
        self.assertEqual(code, 0)
        self.assertIn("[삭제 완료]", out)
        code, _, err = self.run_cli("category", "remove", "--name", "없는카테고리")
        self.assertEqual(code, 1)
        self.assertIn("찾을 수 없습니다", err)

    def test_070_update(self) -> None:
        # 날짜/타입/카테고리는 엔터(유지), 금액 12,000 → 15,000, 메모는 '-'(비움), 태그는 엔터(유지)
        with self.fake_input(["", "", "", "15000", "-", ""]):
            code, out, _ = self.run_cli("update", "--id", self._id("a"))
        self.assertEqual(code, 0)
        self.assertIn(f"[수정 완료] id={self._id('a')}", out)

        _, out, _ = self.run_cli("search", "--category", "식비")
        self.assertIn("15,000", out)
        self.assertNotIn("점심", out)  # 메모 비움
        self.assertIn("외식", out)  # 태그 유지
        _, out, _ = self.run_cli("summary", "--month", "2024-01")
        self.assertIn("총 지출  535,000원", out)  # 15,000 + 20,000 + 500,000

        code, _, err = self.run_cli("update", "--id", "TX-999999")  # 없는 id
        self.assertEqual(code, 1)
        self.assertIn("TX-999999", err)

    def test_080_delete(self) -> None:
        code, out, _ = self.run_cli("delete", "--id", self._id("b"), "--yes")
        self.assertEqual(code, 0)
        self.assertIn("[삭제 완료]", out)
        _, out, _ = self.run_cli("list")
        self.assertFalse(self._has(out, self._id("b")))
        _, out, _ = self.run_cli("summary", "--month", "2024-01")
        self.assertIn("총 지출  515,000원", out)  # 교통 20,000 제외

        for target in (self._id("b"), "TX-999999"):  # 이미 삭제한 id, 존재한 적 없는 id
            with self.subTest(target):
                code, _, err = self.run_cli("delete", "--id", target, "--yes")
                self.assertEqual(code, 1)
                self.assertIn("[오류]", err)

    def test_090_import(self) -> None:
        source = self.data_dir / "import.csv"
        source.write_text(
            "date,type,category,amount,memo,tags\n"
            '2024-02-05,expense,식비,8000,"점심, 회사 근처","외식,점심"\n'
            "2024-02-25,income,월급,3000000,2월 급여,\n"
            "2024-02-10,expense,없는카테고리,5000,,\n"  # 미등록 카테고리 → skip
            "2024-02-11,expense,식비,-100,,\n",  # 음수 금액 → skip
            encoding="utf-8",
        )
        code, out, _ = self.run_cli("import", "--from", str(source))
        self.assertEqual(code, 0)
        self.assertIn("imported=2, skipped=2", out)

        _, out, _ = self.run_cli("summary", "--month", "2024-02")  # 가져온 데이터가 집계에 반영
        self.assertIn("총 수입  3,000,000원", out)
        self.assertIn("총 지출  8,000원", out)

    def test_095_export(self) -> None:
        target = self.data_dir / "export_2024_02.csv"
        code, out, _ = self.run_cli("export", "--out", str(target), "--month", "2024-02")
        self.assertEqual(code, 0)
        self.assertIn("(2 records)", out)
        with target.open(encoding="utf-8", newline="") as fp:
            header = fp.readline().strip()
            fp.seek(0)
            rows = list(csv.DictReader(fp))
        self.assertEqual(header, "date,type,category,amount,memo,tags")  # 고정 스키마
        lunch = next(r for r in rows if r["category"] == "식비")
        self.assertEqual(lunch["memo"], "점심, 회사 근처")  # 쉼표가 든 메모도 보존
        self.assertEqual(lunch["tags"], "외식,점심")

        target = self.data_dir / "export_range.csv"
        self.assertEqual(
            self.run_cli("export", "--out", str(target), "--from", "2024-01-01", "--to", "2024-01-31")[0], 0
        )
        with target.open(encoding="utf-8", newline="") as fp:
            rows = list(csv.DictReader(fp))
        # 식비(수정됨) / 월급 / 월세 만 남고, 삭제한 교통은 빠져 있어야 한다
        self.assertEqual({r["category"] for r in rows}, {"식비", "월급", "월세"})
        self.assertEqual(next(r for r in rows if r["category"] == "식비")["amount"], "15000")

        code, _, err = self.run_cli("export", "--out", str(self.data_dir / "none.csv"))  # 조건 필수
        self.assertEqual(code, 1)
        self.assertIn("[오류]", err)

        for name in ("transactions.jsonl", "categories.jsonl", "budgets.jsonl"):  # 영구 저장 파일 3종
            path = self.data_dir / name
            self.assertTrue(path.exists(), name)
            self.assertGreater(path.stat().st_size, 0, name)

    # ==================================================================
    # 응용 기능
    # ==================================================================
    def test_100_applied_logging_and_exit_codes(self) -> None:
        # 데코레이터: --verbose 일 때만 로그/실행 시간 출력, 원본 함수 메타데이터 보존
        code, _, err = self.run_cli("--verbose", "list")
        self.assertEqual(code, 0)
        self.assertIn("[log] -> cmd_list 시작", err)
        self.assertIn("cmd_list 실행 시간", err)
        self.assertNotIn("[log]", self.run_cli("list")[2])
        self.assertEqual(cmd_add.__name__, "cmd_add")
        self.assertIn("거래 추가", cmd_add.__doc__ or "")

        # 종료 코드: 0 정상 / 1 오류(원인+힌트, 스택트레이스 없음) / 130 사용자 취소
        self.assertEqual(self.run_cli("list")[0], 0)
        code, _, err = self.run_cli("delete", "--id", "TX-999999", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("[오류]", err)
        self.assertIn("[힌트]", err)
        self.assertNotIn("Traceback", err)
        with self.fake_interrupt():
            code, _, err = self.run_cli("add")
        self.assertEqual(code, 130)
        self.assertNotIn("Traceback", err)

    def test_110_applied_table_format(self) -> None:
        _, out, _ = self.run_cli("list")
        lines = out.splitlines()
        self.assertIn("카테고리", lines[0])  # 헤더
        self.assertRegex(lines[1], r"^-[- ]+$")  # 구분선
        self.assertIn("+3,000,000", out)  # 수입은 + 부호, 천 단위 구분
        self.assertIn("-500,000", out)  # 지출은 - 부호
        _, out, _ = self.run_cli("summary", "--month", "2024-01")
        self.assertRegex(out, r"\[#+\]")  # 예산 초과이므로 막대가 전부 채워짐

    def test_120_applied_atomic_storage(self) -> None:
        # categories/budgets: 임시파일 교체가 실패해도 원본이 그대로 남는다
        for name, argv in (
            ("categories.jsonl", ("category", "add", "--name", "원자성_카테고리")),
            ("budgets.jsonl", ("budget", "set", "--month", "2024-05", "--amount", "100000")),
        ):
            with self.subTest(name):
                path = self.data_dir / name
                original = path.read_text(encoding="utf-8")
                with mock.patch("budget_app.stores.os.replace", side_effect=OSError("simulated disk failure")):
                    code, _, err = self.run_cli(*argv)
                self.assertEqual(code, 1)
                self.assertIn("[오류]", err)
                self.assertEqual(path.read_text(encoding="utf-8"), original)

        # transactions: update 는 로그에 새 버전을 append, 인덱스는 제자리 갱신. delete 는 로그를 건드리지 않음
        self._add("tmp", "2024-04-01", "expense", "식비", "1000", "원자성 테스트")
        log, idx = self.data_dir / "transactions.jsonl", self.data_dir / "transactions.idx"

        def log_lines() -> list[str]:
            return log.read_text(encoding="utf-8").strip().splitlines()

        lines_before, idx_size = len(log_lines()), idx.stat().st_size
        with self.fake_input(["", "", "", "2000", "", ""]):  # 금액만 1,000 → 2,000
            self.assertEqual(self.run_cli("update", "--id", self.ids["tmp"])[0], 0)
        self.assertEqual(len(log_lines()), lines_before + 1)  # 새 버전이 끝에 append, 옛 줄은 보존
        self.assertIn('"amount": 1000', log_lines()[-2])
        self.assertIn('"amount": 2000', log_lines()[-1])
        self.assertEqual(idx.stat().st_size, idx_size)  # 슬롯 수는 그대로(제자리 덮어쓰기)

        log_size = log.stat().st_size
        self.assertEqual(self.run_cli("delete", "--id", self.ids["tmp"], "--yes")[0], 0)
        self.assertEqual(log.stat().st_size, log_size)
        self.assertEqual(idx.stat().st_size, idx_size)

    def test_130_applied_backup(self) -> None:
        code, out, _ = self.run_cli("backup")
        self.assertEqual(code, 0)
        folders = [p for p in (self.data_dir / "backup").iterdir() if p.is_dir()]
        self.assertEqual(len(folders), 1)
        self.assertRegex(folders[0].name, r"^\d{8}_\d{6}$")  # YYYYmmdd_HHMMSS
        for name in ("transactions.jsonl", "categories.jsonl", "budgets.jsonl"):
            self.assertEqual((folders[0] / name).read_bytes(), (self.data_dir / name).read_bytes())
            self.assertIn(name, out)

    def test_140_applied_recurring(self) -> None:
        code, out, _ = self.run_cli(
            "recurring", "add", "--day", "31", "--type", "income",
            "--category", "월급", "--amount", "3000000", "--memo", "월급(반복)",
        )
        self.assertEqual(code, 0)
        self.assertIn("[저장 완료]", out)
        self.ids["rule"] = re.search(r"id=(RC-\d+)", out).group(1)
        self.assertIn(self._id("rule"), self.run_cli("recurring", "list")[1])

        code, out, _ = self.run_cli("recurring", "apply", "--month", "2024-02")
        self.assertIn("생성 1건", out)
        self.assertIn("2024-02-29", out)  # 31일 규칙 → 윤년 2월 말일로 보정

        code, out, _ = self.run_cli("recurring", "apply", "--month", "2024-02")  # 같은 달 재적용은 건너뜀
        self.assertIn("생성 0건", out)
        self.assertIn("건너뜀 1건", out)

        self.assertEqual(self.run_cli("recurring", "remove", "--id", self._id("rule"))[0], 0)
        self.assertNotIn(self._id("rule"), self.run_cli("recurring", "list")[1])

    def test_150_applied_date_index(self) -> None:
        # 옛 날짜 CSV 를 import + 중간 날짜를 add: id 는 가장 크지만 거래일자 순서에 맞는 위치로 들어간다
        source = self.data_dir / "old_import.csv"
        source.write_text(
            "date,type,category,amount,memo,tags\n2023-12-15,expense,식비,7000,날짜인덱스_옛거래,\n",
            encoding="utf-8",
        )
        self.assertEqual(self.run_cli("import", "--from", str(source))[0], 0)
        self._add("mid", "2024-01-20", "expense", "식비", "3000", "날짜인덱스_중간")
        _, out, _ = self.run_cli("search", "--q", "날짜인덱스_옛거래")
        old = re.search(r"^\s*\d+\s+(TX-\d+)\s", out, re.M).group(1)
        self.ids["old"] = old

        order = self._listed_ids()
        self.assertGreater(int(old[3:]), int(self._id("e")[3:]))  # 시나리오의 모든 거래보다 나중에 등록됨
        self.assertEqual(order[-1], old)  # 그래도 날짜가 가장 오래돼 맨 아래
        self.assertLess(order.index(self._id("c")), order.index(self.ids["mid"]))  # 2024-01-25 > 2024-01-20
        self.assertLess(order.index(self.ids["mid"]), order.index(self._id("a")))  # 2024-01-20 > 2024-01-05
        self.assertTrue(self._has(self.run_cli("list", "--limit", "1")[1], self._id("e")))  # 최신 날짜가 맨 위

        # 날짜를 바꾸면 새 위치로 옮겨지고 옛 날짜 구간에는 더 나오지 않는다(중복 출력 없음)
        with self.fake_input(["2024-06-01", "", "", "", "", ""]):
            self.assertEqual(self.run_cli("update", "--id", old)[0], 0)
        order = self._listed_ids()
        self.assertEqual(order[0], old)
        self.assertEqual(order.count(old), 1)
        self.assertFalse(self._has(self.run_cli("search", "--from", "2023-12-01", "--to", "2023-12-31")[1], old))

        # 삭제된 거래는 건너뛴다
        self.assertEqual(self.run_cli("delete", "--id", old, "--yes")[0], 0)
        self.assertNotIn(old, self._listed_ids())

        # --limit 만큼만 읽고 멈춘다(전체를 읽지 않는다)
        reads: list[int] = []
        original = TransactionRepository._read_at

        def counting(repo, fp, start, end):  # type: ignore[no-untyped-def]
            reads.append(start)
            return original(repo, fp, start, end)

        with mock.patch.object(TransactionRepository, "_read_at", counting):
            self.assertEqual(self.run_cli("list", "--limit", "2")[0], 0)
        self.assertEqual(len(reads), 2)

        # compact 후에도 순서 유지 + 날짜 인덱스 정리(항목당 12바이트)
        before = self._listed_ids()
        self.assertEqual(self.run_cli("compact")[0], 0)
        self.assertEqual(self._listed_ids(), before)
        date_idx = self.data_dir / "transactions.date.idx"
        self.assertEqual(date_idx.stat().st_size, len(before) * 12)

        # 날짜 인덱스 파일이 없어도 다음 실행에서 자동 재생성된다
        date_idx.unlink()
        self.assertEqual(self._listed_ids(), before)
        self.assertEqual(date_idx.stat().st_size, len(before) * 12)


if __name__ == "__main__":
    unittest.main()
