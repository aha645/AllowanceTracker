"""요구사항 문서의 9개 기능을 문서 순서대로 "하나의 시나리오"로 이어서 검증하는 인수 테스트.

필수 검증 항목만 남기고, 앞 단계에서 만든 데이터를 뒤 단계가 그대로 이어받는다.

    00x  사전 준비   데이터 초기화(수동) → 카테고리 없이 add 차단 → 기본 카테고리 등록
    01x  add         대화형/옵션 등록 → 거래 5건 생성, 잘못된 입력 거부
    02x  list        최신순, --limit, --limit/--all 동시 사용 거부
    03x  search      기간/카테고리/타입/메모/태그, AND 결합, 조건 없음 거부
    04x  summary     수입/지출/잔액/TOP N, 데이터 없는 달
    05x  budget      설정 → 사용률 → 덮어쓰기 → 초과 경고
    06x  category    중복 add 차단, list, 사용 중 카테고리 삭제 차단
    07x  update      대화형 수정(엔터=유지, -=비우기), 없는 id → summary 반영
    08x  delete      삭제 → list/summary 반영, 없는 id
    09x  import/export  CSV 가져오기(일부 행 skip) → summary 반영 → 내보내기, 저장 파일 3종
    10x  데코레이터  --verbose 로그/시간 측정, 함수 메타데이터 보존
    11x  종료 코드   0 / 1(원인+힌트, 스택트레이스 없음) / 130(Ctrl+C)
    12x  표 포맷     list 표·부호·천 단위 구분, 예산 막대
    13x  저장 원자성 임시파일+교체 실패 시 원본 보존, 거래 로그 append 전용 + 인덱스 제자리 갱신
    14x  [보너스] backup     타임스탬프 폴더로 데이터 파일 복사
    15x  [보너스] recurring  반복 규칙 등록 → 월별 적용(말일 보정, 중복 방지) → 삭제

시나리오 데이터 (모두 2024-01, 정상적인 수입/지출 조합):
    a  2024-01-05  expense  식비    12,000   점심      #외식      (대화형 add)
    b  2024-01-10  expense  교통    20,000   지하철
    c  2024-01-25  income   월급  3,000,000  1월 급여
    d  2024-01-31  expense  월세   500,000   1월 월세
    e  2024-03-01  expense  교통     1,500             (대화형 재입력 테스트로 등록, 다른 달이라 집계에 영향 없음)

모든 테스트가 tests/data 폴더 하나를 공유하며 번호 순서대로 실행된다. 데이터는
test_000_reset_data 에서만 지우고(수동 초기화), 나머지 테스트는 앞 단계가 남긴 데이터를
그대로 이어받는다. 따라서
    - 파일 전체 실행: 000 이 먼저 초기화하므로 몇 번을 다시 실행해도 같은 결과
    - 개별 테스트 실행: 직전 실행이 남긴 데이터 위에서 동작(다시 처음부터 하려면
      test_000_reset_data 를 먼저 실행). 앞 단계의 결과가 필요한데 없으면 실패한다.
끝난 뒤 tests/data 에서 시나리오 결과를 그대로 확인할 수 있다.

## 실제 콘솔처럼 화면 보기

그냥 실행하면 된다. 각 테스트가 호출하는 `python -m budget_app ...` 명령, 대화형으로
"입력한" 값, 실제 화면 출력, 종료 코드가 실제 터미널 세션처럼 콘솔에 보인다.

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
    #: 시나리오 거래 a~d 를 저장 파일에서 다시 찾기 위한 식별 정보(date, category).
    #: 개별 테스트만 따로 실행해도 앞선 실행이 남긴 데이터에서 id 를 복원할 수 있게 한다.
    SCENARIO_KEYS = {"a": ("2024-01-05", "식비"), "b": ("2024-01-10", "교통"),
                     "c": ("2024-01-25", "월급"), "d": ("2024-01-31", "월세"),
                     "e": ("2024-03-01", "교통")}

    def setUp(self) -> None:
        sys.stdout.write(f"\n{'=' * 72}\n[{self._testMethodName}]\n{'=' * 72}\n")

    # ------------------------------------------------------------------ 공용 보조
    def _id(self, key: str) -> str:
        """시나리오 거래(a~d)/반복 규칙(rule)의 id. 이번 실행에서 모르면 저장 파일에서 복원한다.

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
            self.fail(f"'{key}' 의 id 를 알 수 없습니다. 앞 단계(add 그룹 등)를 먼저 실행하세요.")
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

    # ==================================================================
    # 00x: 사전 준비 — 데이터 초기화 → 카테고리 없으면 add 차단(정책 안 B) → 기본 카테고리 등록
    # ==================================================================
    # 수동 초기화: tests/data 를 비운다. 파일 전체를 실행하면 맨 먼저 돌아 항상 같은 상태에서 시작하고,
    # 처음부터 다시 하고 싶을 때는 이 테스트만 단독 실행하면 된다(다른 곳에서는 데이터를 지우지 않는다).
    def test_000_reset_data(self) -> None:
        shutil.rmtree(self.data_dir, ignore_errors=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        type(self).ids = {}
        self.assertEqual(list(self.data_dir.iterdir()), [])

    def test_001_add_is_blocked_without_category(self) -> None:
        code, out, err = self.run_cli("add")
        self.assertEqual(code, 1)
        self.assertIn("[오류] 등록된 카테고리가 없습니다.", err)
        self.assertIn("[힌트]", err)
        self.assertEqual(out, "")  # 입력 프롬프트로 진입하지 않고 즉시 차단

    # add 는 카테고리가 먼저 있어야 하므로 category 그룹(06x)보다 앞서 기본 카테고리를 등록한다.
    def test_002_prepare_default_categories(self) -> None:
        for name in ("식비", "교통", "월급", "월세"):
            code, out, _ = self.run_cli("category", "add", "--name", name)
            self.assertEqual(code, 0)
            self.assertIn("[저장 완료]", out)

    # ==================================================================
    # 01x: add — 대화형 + 옵션 방식으로 거래 5건 등록, 잘못된 입력 거부
    # ==================================================================
    def test_010_add_interactive(self) -> None:
        with self.fake_input(["2024-01-05", "expense", "식비", "12000", "점심", "외식"]):
            code, out, _ = self.run_cli("add")
        self.assertEqual(code, 0)
        self.assertIn("[저장 완료] id=TX-", out)  # 생성된 id 출력
        self.ids["a"] = self._extract_id(out)

    def test_011_add_option_mode(self) -> None:
        self._add("b", "2024-01-10", "expense", "교통", "20000", "지하철")
        self._add("c", "2024-01-25", "income", "월급", "3000000", "1월 급여")
        self._add("d", "2024-01-31", "expense", "월세", "500000", "1월 월세")
        self.assertEqual(len({self._id(k) for k in "abcd"}), 4)  # id 는 서로 달라야 함

    def test_012_add_rejects_invalid_values(self) -> None:
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

    def test_013_add_interactive_reprompts_on_invalid_input(self) -> None:
        # 이 거래(e)는 삭제하지 않고 시나리오에 남긴다. 다른 달(2024-03)이라 1월/2월 집계에는 영향이 없다.
        answers = ["2024-13-40", "2024-03-01", "expense", "없는것", "교통", "-1", "1500", "", ""]
        with self.fake_input(answers):
            code, out, err = self.run_cli("add")
        self.assertEqual(code, 0)
        self.assertEqual(err.count("[오류]"), 3)  # 날짜 / 카테고리 / 금액 각각 한 번씩 거부 후 재입력
        self.ids["e"] = self._extract_id(out)

    # ==================================================================
    # 02x: list — 최신순, --limit, --limit/--all 동시 사용 거부
    # ==================================================================
    def test_020_list_is_newest_first(self) -> None:
        code, out, _ = self.run_cli("list")
        self.assertEqual(code, 0)
        positions = [out.index(self._id(k)) for k in ("e", "d", "c", "b", "a")]  # 나중에 등록한 것이 먼저
        self.assertEqual(positions, sorted(positions))

    def test_021_list_limit(self) -> None:
        code, out, _ = self.run_cli("list", "--limit", "2")
        self.assertEqual(code, 0)
        # 가장 최근에 추가한 2건(e, d)만 나오고 그 이전(c, b, a)은 나오지 않는다
        self.assertTrue(self._has(out, self._id("e")) and self._has(out, self._id("d")))
        for key in ("c", "b", "a"):
            self.assertFalse(self._has(out, self._id(key)))

    def test_022_limit_and_all_cannot_be_combined(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            self.run_cli("list", "--limit", "5", "--all")
        self.assertEqual(raised.exception.code, 2)

    # ==================================================================
    # 03x: search — 조건별 필터, AND 결합, 조건 없음 거부
    # ==================================================================
    def test_030_search_each_filter(self) -> None:
        cases = {
            "기간": (["--from", "2024-01-10", "--to", "2024-01-25"], {"b", "c"}),
            "카테고리": (["--category", "식비"], {"a"}),
            "타입": (["--type", "income"], {"c"}),
            "메모 키워드": (["--q", "지하철"], {"b"}),
            "태그": (["--tag", "외식"], {"a"}),
        }
        for label, (argv, expected) in cases.items():
            with self.subTest(label):
                code, out, _ = self.run_cli("search", *argv)
                self.assertEqual(code, 0)
                for key in "abcd":
                    self.assertEqual(self._has(out, self._id(key)), key in expected, f"{label}: {key}")

    def test_031_search_combines_conditions_with_and(self) -> None:
        code, out, _ = self.run_cli(
            "search", "--type", "expense", "--from", "2024-01-01", "--to", "2024-01-31", "--category", "월세"
        )
        self.assertEqual(code, 0)
        self.assertTrue(self._has(out, self._id("d")))
        for key in ("a", "b", "c"):
            self.assertFalse(self._has(out, self._id(key)))

    def test_032_search_requires_a_condition(self) -> None:
        code, _, err = self.run_cli("search")
        self.assertEqual(code, 1)
        self.assertIn("[오류]", err)

    # ==================================================================
    # 04x: summary — 수입/지출/잔액, 지출 TOP N, 데이터 없는 달
    # ==================================================================
    def test_040_summary_totals_and_top_n(self) -> None:
        code, out, _ = self.run_cli("summary", "--month", "2024-01", "--top", "2")
        self.assertEqual(code, 0)
        self.assertIn("총 수입  3,000,000원", out)
        self.assertIn("총 지출  532,000원", out)  # 12,000 + 20,000 + 500,000
        self.assertIn("잔액     2,468,000원", out)
        self.assertLess(out.index("월세"), out.index("교통"))  # 지출 큰 순서
        self.assertNotIn("식비", out)  # TOP 2 에 들지 못함

    def test_041_summary_reports_no_data(self) -> None:
        code, out, _ = self.run_cli("summary", "--month", "2023-12")
        self.assertEqual(code, 0)
        self.assertIn("데이터 없음", out)

    # ==================================================================
    # 05x: budget — 설정 → 사용률 → 덮어쓰기 → 초과 경고
    # ==================================================================
    def test_050_budget_set_shows_usage_in_summary(self) -> None:
        code, out, _ = self.run_cli("budget", "set", "--month", "2024-01", "--amount", "600000")
        self.assertEqual(code, 0)
        self.assertIn("[저장 완료] 2024-01 예산 600,000원", out)
        _, out, _ = self.run_cli("summary", "--month", "2024-01")
        self.assertIn("88.7%", out)  # 532,000 / 600,000
        self.assertNotIn("[경고]", out)

    def test_051_budget_overwrite_triggers_overrun_warning(self) -> None:
        code, out, _ = self.run_cli("budget", "set", "--month", "2024-01", "--amount", "500000")
        self.assertEqual(code, 0)
        self.assertIn("덮어썼습니다", out)  # 같은 달은 덮어쓰기
        _, out, _ = self.run_cli("summary", "--month", "2024-01")
        self.assertIn("106.4%", out)  # 532,000 / 500,000
        self.assertIn("[경고]", out)
        self.assertIn("32,000원 초과", out)

    def test_052_budget_rejects_invalid_amount(self) -> None:
        code, _, err = self.run_cli("budget", "set", "--month", "2024-01", "--amount", "0")
        self.assertEqual(code, 1)
        self.assertIn("[오류]", err)

    # ==================================================================
    # 06x: category — 중복 add 차단, list, 사용 중 카테고리 삭제 차단
    # ==================================================================
    def test_060_category_add_blocks_duplicate(self) -> None:
        code, _, _ = self.run_cli("category", "add", "--name", "여가")
        self.assertEqual(code, 0)
        code, _, err = self.run_cli("category", "add", "--name", "식비")
        self.assertEqual(code, 1)
        self.assertIn("이미 등록", err)

    def test_061_category_list(self) -> None:
        code, out, _ = self.run_cli("category", "list")
        self.assertEqual(code, 0)
        for name in ("식비", "교통", "월급", "월세", "여가"):
            self.assertIn(name, out)
        self.assertIn("[완료] 5개", out)

    def test_062_category_remove(self) -> None:
        code, _, err = self.run_cli("category", "remove", "--name", "식비")  # 거래에서 사용 중
        self.assertEqual(code, 1)
        self.assertIn("사용 중", err)
        code, out, _ = self.run_cli("category", "remove", "--name", "여가")  # 미사용
        self.assertEqual(code, 0)
        self.assertIn("[삭제 완료]", out)
        code, _, err = self.run_cli("category", "remove", "--name", "없는카테고리")
        self.assertEqual(code, 1)
        self.assertIn("찾을 수 없습니다", err)

    # ==================================================================
    # 07x: update — 대화형 수정(엔터=유지, -=비우기), 없는 id, summary 반영
    # ==================================================================
    def test_070_update_changes_only_entered_fields(self) -> None:
        # 날짜/타입/카테고리는 엔터(유지), 금액 12,000 → 15,000, 메모는 '-'(비움), 태그는 엔터(유지)
        with self.fake_input(["", "", "", "15000", "-", ""]):
            code, out, _ = self.run_cli("update", "--id", self._id("a"))
        self.assertEqual(code, 0)
        self.assertIn(f"[수정 완료] id={self._id('a')}", out)

        _, out, _ = self.run_cli("search", "--category", "식비")
        self.assertIn("15,000", out)
        self.assertNotIn("점심", out)  # 메모 비움
        self.assertIn("외식", out)  # 태그 유지

    def test_071_update_is_reflected_in_summary(self) -> None:
        _, out, _ = self.run_cli("summary", "--month", "2024-01")
        self.assertIn("총 지출  535,000원", out)  # 15,000 + 20,000 + 500,000

    def test_072_update_missing_id_returns_error(self) -> None:
        code, _, err = self.run_cli("update", "--id", "TX-999999")
        self.assertEqual(code, 1)
        self.assertIn("TX-999999", err)

    # ==================================================================
    # 08x: delete — 삭제 → list/summary 반영, 없는 id
    # ==================================================================
    def test_080_delete_removes_from_list_and_summary(self) -> None:
        code, out, _ = self.run_cli("delete", "--id", self._id("b"), "--yes")
        self.assertEqual(code, 0)
        self.assertIn("[삭제 완료]", out)
        _, out, _ = self.run_cli("list")
        self.assertFalse(self._has(out, self._id("b")))
        _, out, _ = self.run_cli("summary", "--month", "2024-01")
        self.assertIn("총 지출  515,000원", out)  # 교통 20,000 제외

    def test_081_delete_missing_id_returns_error(self) -> None:
        for target in (self._id("b"), "TX-999999"):  # 이미 삭제한 id, 존재한 적 없는 id
            with self.subTest(target):
                code, _, err = self.run_cli("delete", "--id", target, "--yes")
                self.assertEqual(code, 1)
                self.assertIn("[오류]", err)

    # ==================================================================
    # 09x: import/export — 가져오기(일부 행 skip) → summary 반영 → 내보내기, 저장 파일 3종
    # ==================================================================
    def test_090_import_registers_valid_rows_and_skips_invalid(self) -> None:
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

    def test_091_import_is_reflected_in_summary(self) -> None:
        _, out, _ = self.run_cli("summary", "--month", "2024-02")
        self.assertIn("총 수입  3,000,000원", out)
        self.assertIn("총 지출  8,000원", out)

    def test_092_export_month_writes_fixed_schema_csv(self) -> None:
        target = self.data_dir / "export_2024_02.csv"
        code, out, _ = self.run_cli("export", "--out", str(target), "--month", "2024-02")
        self.assertEqual(code, 0)
        self.assertIn("(2 records)", out)
        with target.open(encoding="utf-8", newline="") as fp:
            header = fp.readline().strip()
            fp.seek(0)
            rows = list(csv.DictReader(fp))
        self.assertEqual(header, "date,type,category,amount,memo,tags")
        lunch = next(r for r in rows if r["category"] == "식비")
        self.assertEqual(lunch["memo"], "점심, 회사 근처")  # 쉼표가 든 메모도 왕복 보존
        self.assertEqual(lunch["tags"], "외식,점심")

    def test_093_export_date_range_reflects_update_and_delete(self) -> None:
        target = self.data_dir / "export_range.csv"
        code, _, _ = self.run_cli(
            "export", "--out", str(target), "--from", "2024-01-01", "--to", "2024-01-31"
        )
        self.assertEqual(code, 0)
        with target.open(encoding="utf-8", newline="") as fp:
            rows = list(csv.DictReader(fp))
        # 식비(수정됨) / 월급 / 월세 만 남고, 삭제한 교통은 빠져 있어야 한다
        self.assertEqual({r["category"] for r in rows}, {"식비", "월급", "월세"})
        self.assertEqual(next(r for r in rows if r["category"] == "식비")["amount"], "15000")

    def test_094_export_requires_month_or_range(self) -> None:
        code, _, err = self.run_cli("export", "--out", str(self.data_dir / "none.csv"))
        self.assertEqual(code, 1)
        self.assertIn("[오류]", err)

    def test_095_data_is_persisted_in_three_files(self) -> None:
        for name in ("transactions.jsonl", "categories.jsonl", "budgets.jsonl"):
            path = self.data_dir / name
            self.assertTrue(path.exists(), name)
            self.assertGreater(path.stat().st_size, 0, name)

    # ==================================================================
    # 10x: 데코레이터 — @command(예외 처리 + 로그 + 시간 측정)가 실제 커맨드에 적용됨
    # ==================================================================
    def test_100_verbose_shows_log_and_timing(self) -> None:
        code, _, err = self.run_cli("--verbose", "list")
        self.assertEqual(code, 0)
        self.assertIn("[log] -> cmd_list 시작", err)
        self.assertIn("cmd_list 실행 시간", err)

    def test_101_no_logs_without_verbose(self) -> None:
        _, _, err = self.run_cli("list")
        self.assertNotIn("[log]", err)

    def test_102_decorator_preserves_function_metadata(self) -> None:
        self.assertEqual(cmd_add.__name__, "cmd_add")  # functools.wraps 덕분에 원본 이름/설명 유지
        self.assertIn("거래 추가", cmd_add.__doc__ or "")

    # ==================================================================
    # 11x: 종료 코드 — 0 정상 / 1 오류(원인+힌트, 스택트레이스 없음) / 130 사용자 취소
    # ==================================================================
    def test_110_exit_codes(self) -> None:
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

    # ==================================================================
    # 12x: 표 포맷 — 외부 라이브러리 없는 문자열 정렬이 실제 출력에 반영됨
    # ==================================================================
    def test_120_list_table_format(self) -> None:
        _, out, _ = self.run_cli("list")
        lines = out.splitlines()
        self.assertIn("카테고리", lines[0])  # 헤더
        self.assertRegex(lines[1], r"^-[- ]+$")  # 구분선
        self.assertIn("+3,000,000", out)  # 수입은 + 부호, 천 단위 구분
        self.assertIn("-500,000", out)  # 지출은 - 부호

    def test_121_summary_budget_bar(self) -> None:
        _, out, _ = self.run_cli("summary", "--month", "2024-01")
        self.assertRegex(out, r"\[#+\]")  # 예산 초과이므로 막대가 전부 채워짐

    # ==================================================================
    # 13x: 저장 원자성 — categories/budgets 는 임시파일 + os.replace, transactions 는 append + 인덱스 제자리 갱신
    # ==================================================================
    def test_130_store_rewrite_failure_keeps_original_file(self) -> None:
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
                self.assertEqual(path.read_text(encoding="utf-8"), original)  # 반쯤 쓰인 파일이 남지 않음

    def test_131_transactions_log_is_append_only_and_index_updated_in_place(self) -> None:
        self._add("tmp", "2024-04-01", "expense", "식비", "1000", "원자성 테스트")
        log, idx = self.data_dir / "transactions.jsonl", self.data_dir / "transactions.idx"

        def log_lines() -> list[str]:
            return log.read_text(encoding="utf-8").strip().splitlines()

        lines_before, idx_size = len(log_lines()), idx.stat().st_size
        with self.fake_input(["", "", "", "2000", "", ""]):  # update: 금액만 1,000 → 2,000
            self.assertEqual(self.run_cli("update", "--id", self._id("tmp"))[0], 0)
        self.assertEqual(len(log_lines()), lines_before + 1)  # 새 버전이 끝에 append, 옛 줄은 보존
        self.assertIn('"amount": 1000', log_lines()[-2])
        self.assertIn('"amount": 2000', log_lines()[-1])
        self.assertEqual(idx.stat().st_size, idx_size)  # 인덱스 슬롯 수는 그대로(제자리 덮어쓰기)

        log_size = log.stat().st_size
        self.assertEqual(self.run_cli("delete", "--id", self._id("tmp"), "--yes")[0], 0)
        self.assertEqual(log.stat().st_size, log_size)  # delete 는 로그를 건드리지 않음
        self.assertEqual(idx.stat().st_size, idx_size)  # 슬롯만 0 으로 초기화

    # ==================================================================
    # 14x: [보너스] backup — 타임스탬프 폴더로 데이터 파일 복사
    # ==================================================================
    def test_140_backup_copies_data_files_to_timestamp_folder(self) -> None:
        code, out, _ = self.run_cli("backup")
        self.assertEqual(code, 0)
        folders = [p for p in (self.data_dir / "backup").iterdir() if p.is_dir()]
        self.assertEqual(len(folders), 1)
        self.assertRegex(folders[0].name, r"^\d{8}_\d{6}$")  # YYYYmmdd_HHMMSS
        for name in ("transactions.jsonl", "categories.jsonl", "budgets.jsonl"):
            self.assertEqual((folders[0] / name).read_bytes(), (self.data_dir / name).read_bytes())
            self.assertIn(name, out)

    # ==================================================================
    # 15x: [보너스] recurring — 등록 → 월별 적용(말일 보정, 중복 방지) → 삭제
    # ==================================================================
    def test_150_recurring_add_and_list(self) -> None:
        code, out, _ = self.run_cli(
            "recurring", "add", "--day", "31", "--type", "income",
            "--category", "월급", "--amount", "3000000", "--memo", "월급(반복)",
        )
        self.assertEqual(code, 0)
        self.assertIn("[저장 완료]", out)
        self.ids["rule"] = re.search(r"id=(RC-\d+)", out).group(1)
        _, out, _ = self.run_cli("recurring", "list")
        self.assertIn(self._id("rule"), out)

    def test_151_recurring_apply_clamps_day_to_month_end(self) -> None:
        code, out, _ = self.run_cli("recurring", "apply", "--month", "2024-02")
        self.assertEqual(code, 0)
        self.assertIn("생성 1건", out)
        self.assertIn("2024-02-29", out)  # 31일 규칙 → 윤년 2월 말일로 보정

    def test_152_recurring_apply_is_not_duplicated(self) -> None:
        code, out, _ = self.run_cli("recurring", "apply", "--month", "2024-02")
        self.assertEqual(code, 0)
        self.assertIn("생성 0건", out)
        self.assertIn("건너뜀 1건", out)

    def test_153_recurring_remove(self) -> None:
        code, _, _ = self.run_cli("recurring", "remove", "--id", self._id("rule"))
        self.assertEqual(code, 0)
        _, out, _ = self.run_cli("recurring", "list")
        self.assertNotIn(self._id("rule"), out)


if __name__ == "__main__":
    unittest.main()
