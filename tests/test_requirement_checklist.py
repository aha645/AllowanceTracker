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
import os
import re
import shlex
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

from budget_app import transaction_store
from budget_app.cli import main
from budget_app.commands.transaction import cmd_add
from budget_app.console import prompt_until_valid
from budget_app.stores import CategoryStore, RecurringStore
from budget_app.transaction_store import DATE_ENTRY_STRUCT, DateIndex, TransactionStore
from budget_app.validators import validate_type


# 셸이 따로 해석하지 않는 글자만으로 된 값(한글 포함)은 따옴표 없이 보여준다.
_SHELL_SAFE = re.compile(r"[\w@%+=:,./-]+")


def _shell_quote(value: str) -> str:
    """로그에 보여줄 값을, 셸에 붙여 넣어도 값 하나로 전달되도록 필요할 때만 따옴표로 감싼다."""
    return value if _SHELL_SAFE.fullmatch(value) else shlex.quote(value)


class _FakeTerminal(io.StringIO):
    """출력을 모으면서 터미널인지 여부(isatty)를 정해 둘 수 있는 가짜 stdout."""

    def __init__(self, is_terminal: bool) -> None:
        super().__init__()
        self._is_terminal = is_terminal

    def isatty(self) -> bool:
        return self._is_terminal


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

    def _group(self, pattern: str, text: str, flags: int = 0) -> str:
        """정규식의 첫 번째 그룹을 돌려준다. 못 찾으면 출력과 함께 테스트를 실패시킨다."""
        found = re.search(pattern, text, flags)
        if found is None:
            self.fail(f"출력에서 {pattern!r} 를 찾지 못했습니다:\n{text}")
        return found.group(1)

    def _category_counts(self) -> tuple[dict[str, int], dict[str, int]]:
        """(categories.jsonl 에 기록된 사용 건수, 거래를 실제로 센 건수)."""
        saved = {c.name: c.count for c in CategoryStore(self.data_dir).list_all()}
        actual = TransactionStore(self.data_dir).category_counts()
        return saved, {name: actual.get(name, 0) for name in saved}

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        """CLI 를 실행하고 (종료 코드, stdout, stderr) 를 돌려준다(콘솔에도 그대로 보여준다)."""
        console = sys.stdout
        console.write(f"\n$ python -m budget_app {' '.join(map(_shell_quote, argv))}\n")
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

    def _listed_row(self, tx_id: str) -> list[str]:
        """`list --all` 표에서 그 거래의 칸(id·날짜·타입·카테고리·금액·메모·태그)을 돌려준다.

        칸 사이는 공백 두 칸 이상이고 메모 안의 띄어쓰기는 한 칸이므로 그 기준으로 나눈다.
        빈 메모·태그는 줄 끝에서 빠진다.
        """
        return self._row_in(self.run_cli("list", "--all")[1], tx_id)

    def _row_in(self, out: str, tx_id: str) -> list[str]:
        """거래 표 출력(list·search·recurring apply)에서 그 거래 줄의 칸을 돌려준다."""
        line = self._group(rf"^(\s*\d+\s+{re.escape(tx_id)}\s.*)$", out, re.M)
        return re.split(r"\s{2,}", line.strip())[1:]

    # ==================================================================
    # 사전: 데이터 초기화
    # ==================================================================
    def test_000_reset_data(self) -> None:
        """tests/data 를 비운다(수동 초기화). 다른 테스트는 데이터를 지우지 않는다."""
        shutil.rmtree(self.data_dir, ignore_errors=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.ids.clear()
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

        # 등록한 4개가 실제로 목록에 있다
        code, out, _ = self.run_cli("category", "list")
        self.assertEqual(code, 0)
        for name in ("식비", "교통", "월급", "월세"):
            self.assertIn(name, out)
        self.assertIn("[완료] 4개", out)

        # 대화형 등록: 생성된 id 가 출력되고, 입력한 값 그대로 저장된다
        with self.fake_input(["2024-01-05", "expense", "식비", "12000", "점심", "외식"]):
            code, out, _ = self.run_cli("add")
        self.assertEqual(code, 0)
        self.assertIn("[저장 완료] id=TX-", out)
        self.ids["a"] = self._extract_id(out)
        self.assertEqual(self._listed_row(self._id("a")),
                         [self._id("a"), "2024-01-05", "expense", "식비", "-12,000", "점심", "외식"])

        # 엔터로 기본값을 고르면 터미널에서는 입력 줄을 `프롬프트 + 기본값` 으로 다시 써서 보여주고,
        # 파이프·리다이렉트 입출력에서는 제어 문자를 섞지 않는다
        label = "타입 (income/expense, 엔터=expense): "
        for is_terminal, expected in ((True, f"\033[F\033[K{label}expense\n"), (False, "")):
            with self.subTest(터미널=is_terminal):
                shown = _FakeTerminal(is_terminal)
                with mock.patch("builtins.input", return_value=""), \
                        mock.patch.object(sys.stdin, "isatty", return_value=is_terminal), redirect_stdout(shown):
                    value = prompt_until_valid(label, validate_type, default="expense", allow_blank=True)
                self.assertEqual(value, "expense")
                self.assertEqual(shown.getvalue(), expected)

        # 옵션 방식 등록: 띄어쓰기가 든 메모도 한 값으로 저장되고, 금액은 천 단위 쉼표를 붙여도 된다(d)
        self._add("b", "2024-01-10", "expense", "교통", "20000", "지하철")
        self._add("c", "2024-01-25", "income", "월급", "3000000", "1월 급여")
        self._add("d", "2024-01-31", "expense", "월세", "500,000", "1월 월세")
        self.assertEqual(len({self._id(k) for k in "abcd"}), 4)
        expected_rows = {
            "b": ["2024-01-10", "expense", "교통", "-20,000", "지하철"],
            "c": ["2024-01-25", "income", "월급", "+3,000,000", "1월 급여"],
            "d": ["2024-01-31", "expense", "월세", "-500,000", "1월 월세"],
        }
        for key, row in expected_rows.items():
            with self.subTest(key):
                self.assertEqual(self._listed_row(self._id(key)), [self._id(key), *row])

        # 잘못된 값은 저장되지 않고 거부된다(사유가 맞게 나오고, 거래 수도 그대로)
        listed_before = self._listed_ids()
        self.assertEqual(len(listed_before), 4)
        cases = {
            "날짜 형식 오류": (("2024-13-40", "expense", "식비", "1000"), "날짜 형식이 올바르지 않습니다"),
            "음수 금액": (("2024-01-01", "expense", "식비", "-500"), "금액은 0보다 커야 합니다: -500"),
            "0 금액": (("2024-01-01", "expense", "식비", "0"), "금액은 0보다 커야 합니다: 0"),
            "쉼표가 세 자리마다가 아님": (("2024-01-01", "expense", "식비", "1,2,3"),
                                   "금액의 쉼표 위치가 올바르지 않습니다: '1,2,3'"),
            "쉼표 뒤 자릿수 부족": (("2024-01-01", "expense", "식비", "600,00"),
                              "금액의 쉼표 위치가 올바르지 않습니다: '600,00'"),
            "허용되지 않은 type": (("2024-01-01", "transfer", "식비", "1000"), None),
            "미등록 카테고리": (("2024-01-01", "expense", "없는카테고리", "1000"), "등록되지 않은 카테고리입니다"),
        }
        for label, ((date, type_, category, amount), message) in cases.items():
            with self.subTest(label):
                try:
                    code, out, err = self.run_cli(
                        "add", "--date", date, "--type", type_, "--category", category, "--amount", amount
                    )
                except SystemExit as exc:  # argparse 가 허용되지 않은 --type 을 직접 거부
                    self.assertEqual(exc.code, 2)
                    continue
                self.assertEqual(code, 1)
                self.assertNotIn("[저장 완료]", out)
                self.assertIn(f"[오류] {message}", err)
        self.assertEqual(self._listed_ids(), listed_before)

        # 대화형에서는 잘못 입력해도 다시 묻는다. 이 거래(e)는 시나리오에 남긴다(다른 달이라 집계 영향 없음).
        answers = ["2024-13-40", "2024-03-01", "expense", "없는것", "교통", "-1", "1500", "", ""]
        with self.fake_input(answers):
            code, out, err = self.run_cli("add")
        self.assertEqual(code, 0)
        self.assertEqual(err.count("[오류]"), 3)  # 날짜 / 카테고리 / 금액 각각 한 번씩 거부 후 재입력
        self.ids["e"] = self._extract_id(out)
        # 거부된 값이 아니라 다시 입력한 값으로 저장된다(메모·태그는 비어 있음)
        self.assertEqual(self._listed_row(self._id("e")), [self._id("e"), "2024-03-01", "expense", "교통", "-1,500"])

        # 카테고리별 사용 건수가 실제 거래 수와 같다(교통 = b, e)
        saved, actual = self._category_counts()
        self.assertEqual(saved, actual)
        self.assertEqual(saved, {"식비": 1, "교통": 2, "월급": 1, "월세": 1})

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
        code, out, _ = self.run_cli("budget", "set", "--month", "2024-01", "--amount", "600,000")  # 천 단위 쉼표 허용
        self.assertEqual(code, 0)
        self.assertIn("[저장 완료] 2024-01 예산 600,000원", out)
        _, out, _ = self.run_cli("summary", "--month", "2024-01")
        self.assertIn("88.7%", out)  # 532,000 / 600,000
        self.assertNotIn("[경고]", out)
        self.assertEqual(self._budget_rows(), [["2024-01", "600,000원", "532,000원", "88.7%"]])

        code, out, _ = self.run_cli("budget", "set", "--month", "2024-01", "--amount", "500000")
        self.assertIn("덮어썼습니다", out)  # 같은 달은 덮어쓰기
        _, out, _ = self.run_cli("summary", "--month", "2024-01")
        self.assertIn("106.4%", out)  # 532,000 / 500,000
        self.assertIn("[경고]", out)
        self.assertIn("32,000원 초과", out)
        overwritten = [["2024-01", "500,000원", "532,000원", "106.4%", "초과"]]  # 한 줄로 바뀌었다(추가 아님)
        self.assertEqual(self._budget_rows(), overwritten)

        code, _, err = self.run_cli("budget", "set", "--month", "2024-01", "--amount", "0")
        self.assertEqual(code, 1)
        self.assertIn("[오류] 금액은 0보다 커야 합니다: 0", err)
        self.assertEqual(self._budget_rows(), overwritten)  # 거부된 뒤에도 기존 예산 그대로

    def _budget_rows(self) -> list[list[str]]:
        """`budget list` 표의 줄마다 칸(월·예산·사용·사용률·상태)을 돌려준다."""
        code, out, _ = self.run_cli("budget", "list")
        self.assertEqual(code, 0)
        return [re.split(r"\s{2,}", line.strip()) for line in re.findall(r"^\d{4}-\d{2}\s.*$", out, re.M)]

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
        self.assertEqual(self._category_names(), ["식비", "교통", "월급", "월세", "여가"])  # 식비는 남아 있다
        code, out, _ = self.run_cli("category", "remove", "--name", "여가")  # 미사용
        self.assertEqual(code, 0)
        self.assertIn("[삭제 완료]", out)
        self.assertEqual(self._category_names(), ["식비", "교통", "월급", "월세"])  # 여가만 빠졌다
        code, _, err = self.run_cli("category", "remove", "--name", "없는카테고리")
        self.assertEqual(code, 1)
        self.assertIn("찾을 수 없습니다", err)
        self.assertEqual(self._category_names(), ["식비", "교통", "월급", "월세"])

        # 카테고리마다 사용 건수가 기록되고, 실제 거래 수와 같다
        saved, actual = self._category_counts()
        self.assertEqual(saved, actual)
        self.assertGreater(saved["식비"], 0)

        # 건수가 0 이면 거래를 훑지 않고 바로 삭제한다(10만 건이어도 즉시)
        self.assertEqual(self.run_cli("category", "add", "--name", "미사용")[0], 0)
        with mock.patch.object(TransactionStore, "iter_latest_transactions",
                               side_effect=AssertionError("훑으면 안 됨")):
            self.assertEqual(self.run_cli("category", "remove", "--name", "미사용")[0], 0)
        self.assertNotIn("미사용", self._category_names())

    def _category_names(self) -> list[str]:
        """`category list` 표의 카테고리 이름을 위에서 아래 순서로 돌려준다."""
        code, out, _ = self.run_cli("category", "list")
        self.assertEqual(code, 0)
        return re.findall(r"^\d+\s+(\S+)\s", out, re.M)

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
        # 터미널에서 --yes 없이 실행하면 확인을 묻고, N 이면 지우지 않는다
        listed_before = self._listed_ids()
        with mock.patch.object(sys.stdin, "isatty", return_value=True), self.fake_input(["N"]):
            code, out, _ = self.run_cli("delete", "--id", self._id("b"))
        self.assertEqual(code, 0)
        self.assertIn("[안내] 삭제를 취소했습니다.", out)
        self.assertEqual(self._listed_ids(), listed_before)

        self.assertEqual(self._category_counts()[0]["교통"], 2)  # b, e
        code, out, _ = self.run_cli("delete", "--id", self._id("b"), "--yes")
        self.assertEqual(code, 0)
        self.assertIn("[삭제 완료]", out)
        _, out, _ = self.run_cli("list")
        self.assertFalse(self._has(out, self._id("b")))
        self.assertEqual(self._listed_ids(), [i for i in listed_before if i != self._id("b")])  # b 만 빠졌다
        _, out, _ = self.run_cli("summary", "--month", "2024-01")
        self.assertIn("총 지출  515,000원", out)  # 교통 20,000 제외
        saved, actual = self._category_counts()  # 삭제한 만큼 사용 건수도 준다
        self.assertEqual(saved, actual)
        self.assertEqual(saved["교통"], 1)

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
        listed_before = self._listed_ids()
        code, out, _ = self.run_cli("import", "--from", str(source))
        self.assertEqual(code, 0)
        self.assertIn("imported=2, skipped=2", out)
        self.assertRegex(out, r"(?m)^\s*4\s+등록되지 않은 카테고리입니다: '없는카테고리'$")  # 건너뛴 행 번호와 사유
        self.assertRegex(out, r"(?m)^\s*5\s+금액은 0보다 커야 합니다: -100$")

        # 정상 2행만 들어왔고, 쉼표가 든 메모·여러 태그도 그대로다
        imported = [i for i in self._listed_ids() if i not in listed_before]
        self.assertEqual(len(imported), 2)
        rows = sorted(self._listed_row(i)[1:] for i in imported)
        self.assertEqual(rows, [
            ["2024-02-05", "expense", "식비", "-8,000", "점심, 회사 근처", "외식,점심"],
            ["2024-02-25", "income", "월급", "+3,000,000", "2월 급여"],
        ])

        _, out, _ = self.run_cli("summary", "--month", "2024-02")  # 가져온 데이터가 집계에 반영
        self.assertIn("총 수입  3,000,000원", out)
        self.assertIn("총 지출  8,000원", out)
        listed_before = self._listed_ids()

        # 파일 자체를 못 읽으면(뒤쪽에 UTF-8 이 아닌 바이트 / 닫히지 않은 따옴표) 앞쪽 정상 행도 저장하지 않는다
        valid_rows = "".join(f"2024-02-{d:02d},expense,식비,1000,,\n" for d in range(1, 29)) * 20
        broken = self.data_dir / "broken.csv"
        broken.write_bytes(("date,type,category,amount,memo,tags\n" + valid_rows).encode("utf-8") + b"\xff\xfe\n")
        unclosed = self.data_dir / "unclosed.csv"
        unclosed.write_text(
            "date,type,category,amount,memo,tags\n" + valid_rows + '2024-02-01,expense,식비,1000,"닫히지 않은 메모'
            + "x" * 140_000 + "\n",  # csv 모듈의 필드 길이 한도(131072자)를 넘긴다
            encoding="utf-8",
        )
        for path in (broken, unclosed):
            code, _, err = self.run_cli("import", "--from", str(path))
            self.assertEqual(code, 1)
            self.assertIn("[오류]", err)
            self.assertIn("[힌트]", err)
            self.assertNotIn("Traceback", err)
        _, out, _ = self.run_cli("summary", "--month", "2024-02")
        self.assertIn("총 지출  8,000원", out)  # 그대로

        # 잘못된 행이 많아도 사유는 앞쪽 20건만 담고 나머지는 건수만 센다
        many_bad = self.data_dir / "many_bad.csv"
        many_bad.write_text(
            "date,type,category,amount,memo,tags\n" + "2024-13-01,expense,식비,1000,,\n" * 25, encoding="utf-8"
        )
        code, out, _ = self.run_cli("import", "--from", str(many_bad))
        self.assertEqual(code, 0)
        self.assertIn("imported=0, skipped=25", out)
        self.assertIn("... 외 5건", out)
        self.assertEqual(self._listed_ids(), listed_before)  # 읽지 못한 파일·잘못된 행만 있는 파일은 아무것도 추가하지 않음

        # 메모리 부족도 스택트레이스 없이 [오류]/[힌트] 로 알린다
        with mock.patch.object(TransactionStore, "append_transactions", side_effect=MemoryError):
            code, _, err = self.run_cli("import", "--from", str(source))
        self.assertEqual(code, 1)
        self.assertIn("[오류] 메모리가 부족", err)
        self.assertIn("[힌트]", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(self._listed_ids(), listed_before)  # 거래는 하나도 저장되지 않음

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

        # 이미 있는 파일에 내보내다 중간에 멈춰도 원래 파일은 그대로이고 임시 파일도 남지 않는다
        original = target.read_text(encoding="utf-8")
        with mock.patch("budget_app.file_services.format_tags", side_effect=KeyboardInterrupt):
            code, _, _ = self.run_cli("export", "--out", str(target), "--month", "2024-02")
        self.assertEqual(code, 130)
        self.assertEqual(target.read_text(encoding="utf-8"), original)
        self.assertFalse((self.data_dir / ".export_range.csv.tmp").exists())

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

        # 카테고리 사용 건수는 실제보다 적은 적이 없다. 090 의 "메모리 부족" import 가 건수를 저장한 뒤
        # 거래 저장 전에 멈췄으므로 그만큼은 많다(안전한 쪽으로 틀림)
        saved, actual = self._category_counts()
        self.assertTrue(all(saved[name] >= actual[name] for name in saved), (saved, actual))
        self.assertNotEqual(saved, actual)

        # 건수를 저장한 뒤 거래를 쓰기 전에 꺼지면 건수만 많아진다 → 삭제 때 확인해 보고 허용
        self.assertEqual(self.run_cli("category", "add", "--name", "중단_카테고리")[0], 0)
        with mock.patch.object(TransactionStore, "append_transaction", side_effect=KeyboardInterrupt):
            code, _, _ = self.run_cli("add", "--date", "2024-04-02", "--type", "expense",
                                      "--category", "중단_카테고리", "--amount", "1000")
        self.assertEqual(code, 130)
        self.assertEqual(CategoryStore(self.data_dir).count_of("중단_카테고리"), 1)  # 실제로는 0건
        code, out, _ = self.run_cli("category", "remove", "--name", "중단_카테고리")
        self.assertEqual(code, 0)
        self.assertIn("[삭제 완료]", out)

        # 기록이 실제보다 적어(직접 편집 등) 줄이다가 0 아래로 내려가면, 알아채고 처음부터 다시 센다
        self._add("tmp2", "2024-04-03", "expense", "식비", "1000")
        categories = self.data_dir / "categories.jsonl"
        rows = [json.loads(line) for line in categories.read_text(encoding="utf-8").splitlines()]
        for row in rows:
            row["count"] = 0
        categories.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        self.assertEqual(self.run_cli("delete", "--id", self.ids["tmp2"], "--yes")[0], 0)  # 식비 0 → -1
        saved, actual = self._category_counts()
        self.assertEqual(saved, actual)

        # 건수 기록 이전 형식(count 없음)이면 다음 실행 때 한 번 세어 채운다
        for row in rows:
            del row["count"]
        categories.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        self.assertEqual(self.run_cli("category", "list")[0], 0)
        saved, actual = self._category_counts()
        self.assertEqual(saved, actual)

        # compact 는 건수를 처음부터 다시 맞춘다
        CategoryStore(self.data_dir).set_counts({"식비": 999})
        self.assertEqual(self.run_cli("compact")[0], 0)
        saved, actual = self._category_counts()
        self.assertEqual(saved, actual)

        # 위치 인덱스 끝에 덜 써진 슬롯 조각(add/import 가 슬롯을 쓰다 꺼짐)은 잘라 내고 계속한다
        listing = self.run_cli("list", "--all")[1]
        idx.write_bytes(idx.read_bytes() + b"\x01" * 8)
        code, out, _ = self.run_cli("list", "--all")
        self.assertEqual(code, 0)
        self.assertIn("[안내] 끝나지 않은 마지막 저장", out)
        self.assertEqual(idx.stat().st_size % 16, 0)
        self.assertEqual(self.run_cli("list", "--all")[1], listing)  # 저장된 거래는 그대로

        # compact 가 위치 인덱스를 고친 뒤 로그 교체 전에 꺼지면, 다음 실행 때 compact 전 상태로 되돌린다
        for amount in ("15001", "15000"):  # 바꿨다 되돌려 내용은 같고 정리할 옛 줄만 생기게 한다
            with self.fake_input(["", "", "", amount, "", ""]):
                self.assertEqual(self.run_cli("update", "--id", self._id("a"))[0], 0)
        before_idx = idx.read_bytes()
        real_replace = os.replace

        def stop_at_log_replace(src, dst):  # type: ignore[no-untyped-def]
            if Path(dst) == log:
                raise KeyboardInterrupt  # 강제 종료 흉내
            return real_replace(src, dst)

        with mock.patch("budget_app.transaction_store.os.replace", side_effect=stop_at_log_replace):
            self.assertEqual(self.run_cli("compact")[0], 130)
        self.assertNotEqual(idx.read_bytes(), before_idx)  # 슬롯은 새 로그 위치로 바뀌었는데 로그는 옛것
        code, out, _ = self.run_cli("list", "--all")
        self.assertEqual(code, 0)
        self.assertIn("[안내] 지난 compact 가 끝나지 않아 compact 전 상태로 되돌렸습니다", out)
        self.assertEqual(idx.read_bytes(), before_idx)
        self.assertEqual(self.run_cli("list", "--all")[1], listing)
        self.assertFalse((self.data_dir / "transactions.idx.compact-backup").exists())
        self.assertFalse((self.data_dir / "transactions.jsonl.tmp").exists())

        # 로그 교체는 끝나고 복사본을 지우기 전에 꺼지면, 이미 짝이 맞으므로 복사본만 지운다
        calls: list[int] = []
        real_fsync_dir = transaction_store._fsync_dir

        def stop_after_log_replace(path):  # type: ignore[no-untyped-def]
            calls.append(1)
            if len(calls) == 2:  # 1번째: 복사본 이름 확정, 2번째: 로그 교체 확정
                raise KeyboardInterrupt
            return real_fsync_dir(path)

        with mock.patch("budget_app.transaction_store._fsync_dir", side_effect=stop_after_log_replace):
            self.assertEqual(self.run_cli("compact")[0], 130)
        self.assertTrue((self.data_dir / "transactions.idx.compact-backup").exists())
        code, out, _ = self.run_cli("list", "--all")
        self.assertIn("[안내] 지난 compact 의 마무리", out)
        self.assertFalse((self.data_dir / "transactions.idx.compact-backup").exists())
        self.assertEqual(self.run_cli("list", "--all")[1], listing)

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
        self.ids["rule"] = self._group(r"id=(RC-\d+)", out)
        rule_line = self._group(rf"^({re.escape(self._id('rule'))}\s.*)$", self.run_cli("recurring", "list")[1], re.M)
        self.assertEqual(re.split(r"\s{2,}", rule_line.strip()),
                         [self._id("rule"), "매월 31일", "income", "월급", "3,000,000원", "월급(반복)", "0"])

        code, out, _ = self.run_cli("recurring", "apply", "--month", "2024-02")
        self.assertIn("생성 1건", out)
        self.ids["applied"] = self._group(r"^\s*\d+\s+(TX-\d+)\s", out, re.M)
        # 31일 규칙 → 윤년 2월 말일로 보정된 거래가 실제로 저장되어 목록에 나온다
        self.assertEqual(self._listed_row(self._id("applied")),
                         [self._id("applied"), "2024-02-29", "income", "월급", "+3,000,000", "월급(반복)"])

        listed_before = self._listed_ids()
        code, out, _ = self.run_cli("recurring", "apply", "--month", "2024-02")  # 같은 달 재적용은 건너뜀
        self.assertIn("생성 0건", out)
        self.assertIn("건너뜀 1건", out)
        self.assertEqual(self._listed_ids(), listed_before)

        # 거래를 저장한 뒤 규칙에 "적용함"을 기록하기 전에 꺼져도, 다시 apply 할 때 중복으로 만들지 않는다
        def april_ids() -> list[str]:
            out = self.run_cli("search", "--month", "2024-04", "--all")[1]
            return re.findall(r"^\s*\d+\s+(TX-\d+)\s", out, re.M)

        with mock.patch.object(RecurringStore, "save", side_effect=KeyboardInterrupt):
            self.assertEqual(self.run_cli("recurring", "apply", "--month", "2024-04")[0], 130)
        self.assertEqual(len(april_ids()), 1)  # 거래는 저장됨
        code, out, _ = self.run_cli("recurring", "apply", "--month", "2024-04")
        self.assertIn("생성 0건", out)
        self.assertIn("건너뜀 1건", out)
        self.assertEqual(len(april_ids()), 1)  # 한 건 그대로
        self.assertIn("2024-04", RecurringStore(self.data_dir).get(int(self._id("rule")[3:])).applied_months)
        # 뒤 테스트(150)는 2024-03-01 거래가 가장 최신이라고 가정하므로 이 달 거래는 지운다
        self.assertEqual(self.run_cli("delete", "--id", april_ids()[0], "--yes")[0], 0)

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
        old = self._group(r"^\s*\d+\s+(TX-\d+)\s", out, re.M)
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
        original = TransactionStore._read_at

        def counting(repo, fp, start, end):  # type: ignore[no-untyped-def]
            reads.append(start)
            return original(repo, fp, start, end)

        with mock.patch.object(TransactionStore, "_read_at", counting):
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

        # 날짜 항목을 쓴 직후(슬롯을 쓰기 전) 중단돼도 목록과 id 조회가 서로 맞는다
        def stop_after(original):  # type: ignore[no-untyped-def]
            def wrapper(index, entries, *args, **kwargs):  # type: ignore[no-untyped-def]
                original(index, entries, *args, **kwargs)
                raise KeyboardInterrupt  # 강제 종료 흉내
            return wrapper

        with mock.patch.object(DateIndex, "merge", stop_after(DateIndex.merge)):
            code, _, _ = self.run_cli("add", "--date", "2024-01-15", "--type", "expense",
                                      "--category", "식비", "--amount", "1000", "--memo", "중단된_추가")
        self.assertEqual(code, 130)
        self.assertEqual(self._listed_ids(), before)  # 반쯤 저장된 거래는 어디에도 보이지 않는다
        self._add("after_stop", "2024-01-16", "expense", "식비", "1000", "중단_뒤_추가")
        order = self._listed_ids()
        self.assertEqual(order.count(self.ids["after_stop"]), 1)  # 같은 id 를 다시 받아도 한 번만 나온다
        self.assertEqual(len(order), len(before) + 1)

        with mock.patch.object(DateIndex, "merge_keys", stop_after(DateIndex.merge_keys)):
            self.assertEqual(self.run_cli("import", "--from", str(source))[0], 130)
        self.assertEqual(self._listed_ids(), order)

        # 슬롯 묶음을 다 썼어도 완료 기록을 지우기 전에 멈추면, 다음 실행 때 그 import 전체를 취소한다
        two_rows = self.data_dir / "two_rows.csv"
        two_rows.write_text("date,type,category,amount,memo,tags\n2024-01-11,expense,식비,100,,\n"
                            "2024-01-12,expense,식비,200,,\n", encoding="utf-8")
        with mock.patch.object(TransactionStore, "_commit_import", side_effect=KeyboardInterrupt):
            self.assertEqual(self.run_cli("import", "--from", str(two_rows))[0], 130)
        code, out, _ = self.run_cli("list", "--all")
        self.assertIn("[안내] 지난 import 가 끝나지 않아 그 import 전체를 취소했습니다", out)
        self.assertEqual(self._listed_ids(), order)
        self.assertFalse((self.data_dir / "transactions.idx.import-pending").exists())

        # 슬롯 묶음을 쓰다 멈춰 일부(1개 반)만 써진 경우도 일부 행만 들어가지 않고 전체가 취소된다
        idx_path = self.data_dir / "transactions.idx"
        size_before = idx_path.stat().st_size
        (self.data_dir / "transactions.idx.import-pending").write_text(str(size_before), encoding="utf-8")
        idx_path.write_bytes(idx_path.read_bytes() + b"\x01" * 24)
        self.assertEqual(self._listed_ids(), order)
        self.assertEqual(idx_path.stat().st_size, size_before)

        with mock.patch.object(DateIndex, "merge", stop_after(DateIndex.merge)), \
                self.fake_input(["2023-01-01", "", "", "", "", ""]):
            self.assertEqual(self.run_cli("update", "--id", self.ids["after_stop"])[0], 130)
        self.assertEqual(self._listed_ids(), order)  # 수정 전 날짜 위치에 그대로 한 번

        # id 0 항목처럼 생길 수 없는 손상은 조용히 건너뛰지 않고 compact 를 안내한다
        good = date_idx.read_bytes()
        ordinal, _ = DATE_ENTRY_STRUCT.unpack(good[-12:])
        date_idx.write_bytes(good[:-12] + DATE_ENTRY_STRUCT.pack(ordinal, 0))
        code, _, err = self.run_cli("list", "--all")
        self.assertEqual(code, 1)
        self.assertIn("[오류] 날짜 인덱스", err)
        self.assertIn("compact", err)
        self.assertEqual(self.run_cli("compact")[0], 0)
        self.assertEqual(self._listed_ids(), order)

        # 날짜 항목이 다른 거래를 가리키게 손상되면(읽는 중엔 옛 항목과 구분 불가) list 가 건수로 알아챈다
        good = date_idx.read_bytes()
        last_ordinal, _ = DATE_ENTRY_STRUCT.unpack(good[-12:])
        _, other_id = DATE_ENTRY_STRUCT.unpack(good[:12])
        date_idx.write_bytes(good[:-12] + DATE_ENTRY_STRUCT.pack(last_ordinal, other_id))
        code, out, _ = self.run_cli("list", "--all")
        self.assertEqual(code, 0)
        self.assertIn("[경고] 날짜 인덱스와 저장된 거래 수가 맞지 않습니다", out)
        self.assertIn("compact", out)
        self.assertNotIn("[경고]", self.run_cli("list", "--limit", "1")[1])  # limit 에서 멈추면 판단하지 않는다
        self.assertEqual(self.run_cli("compact")[0], 0)
        self.assertNotIn("[경고]", self.run_cli("list", "--all")[1])
        self.assertEqual(self._listed_ids(), order)


if __name__ == "__main__":
    unittest.main()
