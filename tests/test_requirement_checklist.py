"""요구사항 문서(파싱: 10대 기능 + 보너스 6종)를 사용자가 지정한 테스트 케이스
번호 체계로 그대로 매핑한 인수(acceptance) 테스트.

번호 체계 (그룹 내 순번은 두 자리, 그룹 접두어는 요구사항 문서 순서를 따름):
    000            카테고리를 하나도 등록하지 않은 상태에서 add 를 시도 → 오류 안내
    001~003        category add / list / remove
    01x (010~014)  add — 대화형/옵션 방식, 모든 옵션 조합
    02x (020~024)  list — --limit/--all, 최신순, 빈 목록
    03x (030~034)  update — 옵션 기반 부분 수정, 없는 id, 값 검증
    04x (040~044)  delete — 성공/없는 id/이미 삭제/확인 프롬프트
    05x (050~057)  search — --from/--to/--category/--type/--q/--tag 옵션별 + AND 결합
    06x (060~066)  budget — set/show/list/remove/사용률/초과 경고
    07x (070~075)  summary — 총수입/총지출/잔액/TOP N/데이터없음/예산 반영
    08x (080~083)  import — 정상/일부 실패/파일없음/헤더불일치/카테고리 미등록
    09x (090~095)  export — month/range 조건/스키마 고정/빈 결과
    10x (100~102)  데코레이터 — --verbose 로그, 메타데이터 보존
    11x (110~113)  예외 처리 및 종료 코드 — 0/1/130, 스택트레이스 미노출
    12x (120~122)  backup — 타임스탬프 폴더, 데이터 파일 복사
    13x (130~135)  recurring — 등록/목록/적용/중복 방지/삭제/말일 보정
    14x (140~143)  출력 포맷 — 콘솔 표/막대 그래프가 CLI 출력에 실제로 반영되는지
    15x (150~153)  저장 원자성 — categories/budgets 임시파일+rename, transactions
                   append-only 로그 + 인덱스 제자리 수정/삭제

각 번호는 실행 순서를 강제하지 않는다(테스트별로 독립된 임시 --data-dir 사용).
숫자 접두어는 오직 "요구사항 문서의 어느 항목을 검증하는 테스트인지"를 한눈에
찾기 위한 인덱스다.
"""

from __future__ import annotations

import io
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest import mock

# `python tests/test_requirement_checklist.py` 로 직접 실행할 때도 budget_app 을
# 찾을 수 있도록 프로젝트 루트를 sys.path 에 넣는다.
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from budget_app import cli
from budget_app.cli import main


class RequirementChecklistTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name) / "data"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        """CLI 를 실행하고 (종료 코드, stdout, stderr) 를 돌려준다."""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["--data-dir", str(self.data_dir), *argv])
        return code, out.getvalue(), err.getvalue()

    # ==================================================================
    # 000: 카테고리 미등록 상태에서 add 시도 → 반드시 오류 + 힌트
    # ==================================================================
    # 기능: add — 카테고리가 하나도 없으면 대화형 입력 진입 전에 즉시 차단하고
    # "[오류]/[힌트]" 를 출력하는지 검증
    def test_000_add_before_category_registered_shows_error(self) -> None:
        code, out, err = self.run_cli("add")
        self.assertEqual(code, 1)
        self.assertIn("[오류] 등록된 카테고리가 없습니다.", err)
        self.assertIn("[힌트]", err)
        self.assertEqual(out, "")  # 오류 시 표준출력은 비어 있어야 함

    # 기능: add(옵션 방식) — 옵션을 모두 채워 실행해도 카테고리 미등록이면
    # 동일하게 차단되는지 검증
    def test_000b_add_option_mode_before_category_registered_shows_error(self) -> None:
        code, _, err = self.run_cli(
            "add", "--date", "2024-01-01", "--type", "expense", "--category", "식비", "--amount", "1000"
        )
        self.assertEqual(code, 1)
        self.assertIn("[오류] 등록된 카테고리가 없습니다.", err)
        self.assertIn("category add", err)

    # ==================================================================
    # 001~003: category add / list / remove
    # ==================================================================
    # 기능: category add — 등록 성공 메시지 출력 및 중복 이름 등록 차단
    def test_001_category_add(self) -> None:
        code, out, _ = self.run_cli("category", "add", "--name", "식비")
        self.assertEqual(code, 0)
        self.assertIn("[저장 완료]", out)
        self.assertIn("식비", out)

        # 중복 등록은 막혀야 한다
        code, _, err = self.run_cli("category", "add", "--name", "식비")
        self.assertEqual(code, 1)
        self.assertIn("이미 등록", err)

    # 기능: category list — 빈 목록 안내 및 등록된 카테고리 전체 목록/개수 출력
    def test_002_category_list(self) -> None:
        code, out, _ = self.run_cli("category", "list")
        self.assertEqual(code, 0)
        self.assertIn("등록된 카테고리가 없습니다", out)

        self.run_cli("category", "add", "--name", "식비")
        self.run_cli("category", "add", "--name", "교통")
        code, out, _ = self.run_cli("category", "list")
        self.assertEqual(code, 0)
        self.assertIn("식비", out)
        self.assertIn("교통", out)
        self.assertIn("2개", out)

    # 기능: category remove — 사용 중인 카테고리 삭제 차단, 미사용 카테고리
    # 정상 삭제, 존재하지 않는 카테고리 삭제 시도 시 오류
    def test_003_category_remove(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        self.run_cli("add", "--date", "2024-01-01", "--type", "expense", "--category", "식비", "--amount", "1000")

        # 사용 중인 카테고리는 삭제 차단
        code, _, err = self.run_cli("category", "remove", "--name", "식비")
        self.assertEqual(code, 1)
        self.assertIn("사용 중", err)

        # 사용하지 않는 카테고리는 정상 삭제
        self.run_cli("category", "add", "--name", "미사용")
        code, out, _ = self.run_cli("category", "remove", "--name", "미사용")
        self.assertEqual(code, 0)
        self.assertIn("[삭제 완료]", out)

        # 존재하지 않는 카테고리 삭제 시도
        code, _, err = self.run_cli("category", "remove", "--name", "없는카테고리")
        self.assertEqual(code, 1)
        self.assertIn("찾을 수 없습니다", err)

    # ==================================================================
    # 01x: 거래 추가 (add) — 대화형/옵션, 모든 옵션 조합
    # ==================================================================
    # 기능: add(옵션 방식) — date/type/category/amount/memo/tags 를 전부 지정해
    # 저장하고 생성된 id 및 입력값이 그대로 조회되는지 검증
    def test_010_add_option_mode_with_all_fields(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        code, out, _ = self.run_cli(
            "add", "--date", "2024-05-01", "--type", "expense",
            "--category", "식비", "--amount", "7000", "--memo", "저녁", "--tags", "외식,저녁",
        )
        self.assertEqual(code, 0)
        self.assertIn("[저장 완료] id=TX-1", out)

        code, out, _ = self.run_cli("list")
        self.assertIn("2024-05-01", out)
        self.assertIn("7,000", out)
        self.assertIn("저녁", out)
        self.assertIn("외식,저녁", out)

    # 기능: add(대화형 방식) — 날짜/타입/카테고리(번호 선택)/금액/메모/태그를
    # 순차 입력받아 저장하는지 검증
    def test_011_add_interactive_mode_all_fields(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        answers = iter(["2024-01-07", "expense", "1", "5500", "커피", "카페,간식"])
        with mock.patch("builtins.input", lambda *a: next(answers)):
            code, out, _ = self.run_cli("add")
        self.assertEqual(code, 0)
        self.assertIn("[저장 완료] id=TX-1", out)

        code, out, _ = self.run_cli("search", "--tag", "간식")
        self.assertIn("5,500", out)

    # 기능: add(대화형 방식) — 날짜/타입을 엔터만 치면 오늘 날짜/expense 기본값이
    # 적용되는지 검증
    def test_012_add_interactive_mode_uses_defaults_on_blank_input(self) -> None:
        """날짜/타입을 엔터만 치면 오늘 날짜/expense 기본값이 적용돼야 한다."""
        self.run_cli("category", "add", "--name", "식비")
        answers = iter(["", "", "1", "3000", "", ""])
        with mock.patch("builtins.input", lambda *a: next(answers)):
            code, out, _ = self.run_cli("add")
        self.assertEqual(code, 0)
        today = datetime.now().strftime("%Y-%m-%d")

        code, out, _ = self.run_cli("list")
        self.assertIn(today, out)
        self.assertIn("expense", out)

    # 기능: add(옵션 방식) — date/type/category/amount 중 일부만 준 옵션 모드는
    # 누락된 필수 옵션 이름을 모두 나열하며 오류를 내는지 검증
    def test_013_add_option_mode_missing_required_fields(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        code, _, err = self.run_cli("add", "--date", "2024-05-01", "--type", "expense")
        self.assertEqual(code, 1)
        self.assertIn("--category", err)
        self.assertIn("--amount", err)

    # 기능: add — 등록되지 않은 카테고리는 거부하고, 실패한 시도는 id 발급에
    # 영향을 주지 않고 연속된 id(TX-1, TX-2, ...)가 부여되는지 검증
    def test_014_add_rejects_unregistered_category_and_assigns_sequential_ids(self) -> None:
        self.run_cli("category", "add", "--name", "식비")

        code, _, err = self.run_cli(
            "add", "--date", "2024-05-01", "--type", "expense",
            "--category", "없는카테고리", "--amount", "1000",
        )
        self.assertEqual(code, 1)
        self.assertIn("등록되지 않은 카테고리", err)

        for day in range(1, 4):
            code, out, _ = self.run_cli(
                "add", "--date", f"2024-05-0{day}", "--type", "expense",
                "--category", "식비", "--amount", "1000",
            )
            self.assertIn(f"id=TX-{day}", out)  # id 는 실패한 시도를 세지 않고 연속 발급

    # ==================================================================
    # 02x: 거래 목록 (list) — --limit/--all, 최신순, 스트리밍, 빈 목록
    # ==================================================================
    def _seed_transactions(self, count: int, *, category: str = "식비") -> None:
        self.run_cli("category", "add", "--name", category)
        for day in range(1, count + 1):
            self.run_cli(
                "add", "--date", f"2024-01-{day:02d}", "--type", "expense",
                "--category", category, "--amount", str(day * 100),
            )

    # 기능: list — --limit 옵션 없이 실행하면 기본값(20건)까지만 출력하고
    # "N건 출력 / 전체 M건" 안내가 붙는지 검증
    def test_020_list_default_limit(self) -> None:
        self._seed_transactions(22)
        code, out, _ = self.run_cli("list")  # --limit 옵션 없이 기본값 사용
        self.assertEqual(code, 0)
        self.assertIn("TX-22", out)
        self.assertNotIn("TX-2 ", out)  # 20건까지만: 3~22 가 보이고 1~2 는 안 보임
        self.assertIn("20건 출력", out)
        self.assertIn("전체 22건", out)

    # 기능: list — --limit N 옵션으로 출력 건수를 제한할 수 있는지 검증
    def test_021_list_limit_option(self) -> None:
        self._seed_transactions(5)
        code, out, _ = self.run_cli("list", "--limit", "2")
        self.assertEqual(code, 0)
        self.assertIn("TX-5", out)
        self.assertIn("TX-4", out)
        self.assertNotIn("TX-3", out)

    # 기능: list — --all 옵션을 주면 --limit 을 무시하고 전체를 출력하는지 검증
    def test_022_list_all_option_ignores_limit(self) -> None:
        self._seed_transactions(25)
        code, out, _ = self.run_cli("list", "--limit", "5", "--all")
        self.assertEqual(code, 0)
        self.assertIn("TX-25", out)
        self.assertIn("TX-1 ", out)
        self.assertIn("25건 출력", out)
        self.assertNotIn("전체", out)  # --all 이면 "N건 출력 / 전체 M건" 접미사가 붙지 않음

    # 기능: list — 거래가 최신순(id 내림차순)으로 정렬되어 출력되는지 검증
    def test_023_list_is_newest_first(self) -> None:
        self._seed_transactions(3)
        code, out, _ = self.run_cli("list")
        self.assertLess(out.index("TX-3"), out.index("TX-2"))
        self.assertLess(out.index("TX-2"), out.index("TX-1"))

    # 기능: list — 등록된 거래가 없을 때 "등록된 거래가 없습니다" 안내와
    # add 유도 힌트를 출력하는지 검증
    def test_024_list_empty_shows_guidance(self) -> None:
        code, out, _ = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertIn("[안내] 등록된 거래가 없습니다.", out)
        self.assertIn("[힌트]", out)

    # ==================================================================
    # 03x: 거래 수정 (update) — 옵션 기반, 부분 수정, 없는 id, 값 검증
    # ==================================================================
    # 기능: update — 지정한 필드(amount) 한 개만 바꾸고 나머지(memo 등)는
    # 그대로 유지되는지 검증
    def test_030_update_single_field_keeps_other_fields(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        self.run_cli(
            "add", "--date", "2024-01-05", "--type", "expense",
            "--category", "식비", "--amount", "12000", "--memo", "점심",
        )
        code, out, _ = self.run_cli("update", "--id", "TX-1", "--amount", "5000")
        self.assertEqual(code, 0)
        self.assertIn("[수정 완료] id=TX-1", out)

        code, out, _ = self.run_cli("list")
        self.assertIn("5,000", out)
        self.assertIn("점심", out)  # amount 만 바꿨으므로 memo 유지

    # 기능: update — date/category/amount/tags 등 여러 필드를 한 번에 수정할
    # 수 있는지 검증
    def test_031_update_multiple_fields_at_once(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        self.run_cli("category", "add", "--name", "교통")
        self.run_cli(
            "add", "--date", "2024-01-05", "--type", "expense",
            "--category", "식비", "--amount", "12000",
        )
        code, out, _ = self.run_cli(
            "update", "--id", "TX-1", "--date", "2024-02-01",
            "--category", "교통", "--amount", "1250", "--tags", "버스",
        )
        self.assertEqual(code, 0)
        code, out, _ = self.run_cli("list")
        self.assertIn("2024-02-01", out)
        self.assertIn("교통", out)
        self.assertIn("1,250", out)
        self.assertIn("버스", out)

    # 기능: update — 존재하지 않는 id 를 수정하려 하면 오류로 처리되는지 검증
    def test_032_update_missing_id_returns_error(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        code, _, err = self.run_cli("update", "--id", "TX-999", "--amount", "1")
        self.assertEqual(code, 1)
        self.assertIn("TX-999", err)

    # 기능: update — 수정할 필드를 하나도 지정하지 않으면 오류로 처리되는지 검증
    def test_033_update_no_fields_specified_returns_error(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        self.run_cli("add", "--date", "2024-01-05", "--type", "expense", "--category", "식비", "--amount", "1000")
        code, _, err = self.run_cli("update", "--id", "TX-1")
        self.assertEqual(code, 1)
        self.assertIn("수정할 항목이 지정되지 않았습니다", err)

    # 기능: update — 잘못된 값(숫자가 아닌 금액)은 거부되고, 실패 시 기존
    # 데이터가 손상되지 않고 그대로 유지되는지 검증
    def test_034_update_invalid_value_is_rejected_and_leaves_data_untouched(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        self.run_cli("add", "--date", "2024-01-05", "--type", "expense", "--category", "식비", "--amount", "1000")
        code, _, err = self.run_cli("update", "--id", "TX-1", "--amount", "abc")
        self.assertEqual(code, 1)
        self.assertIn("[오류]", err)

        code, out, _ = self.run_cli("list")
        self.assertIn("1,000", out)  # 검증 실패 시 원래 값 유지

    # ==================================================================
    # 04x: 거래 삭제 (delete) — 성공/없는 id/이미 삭제/확인 프롬프트
    # ==================================================================
    # 기능: delete — --id 로 거래를 삭제하면 목록에서 사라지는지 검증
    def test_040_delete_success(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        self.run_cli("add", "--date", "2024-01-05", "--type", "expense", "--category", "식비", "--amount", "1000")
        code, out, _ = self.run_cli("delete", "--id", "TX-1", "--yes")
        self.assertEqual(code, 0)
        self.assertIn("[삭제 완료]", out)
        code, out, _ = self.run_cli("list")
        self.assertNotIn("TX-1", out)

    # 기능: delete — 존재하지 않는 id 를 삭제하려 하면 오류로 처리되는지 검증
    def test_041_delete_missing_id_returns_error(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        code, _, err = self.run_cli("delete", "--id", "TX-999", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("TX-999", err)

    # 기능: delete — 이미 삭제된 id 를 다시 삭제하려 하면 "없는 데이터"로
    # 처리되는지 검증
    def test_042_delete_already_deleted_id_returns_error(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        self.run_cli("add", "--date", "2024-01-05", "--type", "expense", "--category", "식비", "--amount", "1000")
        self.run_cli("delete", "--id", "TX-1", "--yes")
        code, _, err = self.run_cli("delete", "--id", "TX-1", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("TX-1", err)

    # 기능: delete — 대화형 터미널에서 --yes 없이 삭제 시 확인 프롬프트가
    # 뜨고, "아니오"를 선택하면 삭제가 취소되고 거래가 남아있는지 검증
    def test_043_delete_confirmation_prompt_cancel_keeps_transaction(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        self.run_cli("add", "--date", "2024-01-05", "--type", "expense", "--category", "식비", "--amount", "1000")
        with mock.patch("sys.stdin.isatty", return_value=True), mock.patch("builtins.input", return_value="n"):
            code, out, _ = self.run_cli("delete", "--id", "TX-1")
        self.assertEqual(code, 0)
        self.assertIn("취소", out)

        code, out, _ = self.run_cli("list")
        self.assertIn("TX-1", out)

    # 기능: delete — --yes 플래그를 주면 대화형 터미널이어도 확인 프롬프트
    # 없이 바로 삭제되는지 검증
    def test_044_delete_yes_flag_skips_confirmation_prompt(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        self.run_cli("add", "--date", "2024-01-05", "--type", "expense", "--category", "식비", "--amount", "1000")
        with mock.patch("sys.stdin.isatty", return_value=True), mock.patch(
            "builtins.input", side_effect=AssertionError("--yes 인데 확인 프롬프트가 호출되면 안 됨")
        ):
            code, out, _ = self.run_cli("delete", "--id", "TX-1", "--yes")
        self.assertEqual(code, 0)
        self.assertIn("[삭제 완료]", out)

    # ==================================================================
    # 05x: 거래 검색 (search) — 옵션별 + AND 결합, 최신순
    # ==================================================================
    def _seed_search_fixture(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        self.run_cli("category", "add", "--name", "교통")
        self.run_cli(
            "add", "--date", "2024-01-05", "--type", "expense",
            "--category", "식비", "--amount", "12000", "--memo", "점심 김밥", "--tags", "외식,점심",
        )  # TX-1
        self.run_cli(
            "add", "--date", "2024-01-06", "--type", "expense",
            "--category", "교통", "--amount", "1250", "--memo", "버스",
        )  # TX-2
        self.run_cli(
            "add", "--date", "2024-02-01", "--type", "income",
            "--category", "식비", "--amount", "30000", "--memo", "저녁 김밥", "--tags", "외식",
        )  # TX-3

    # 기능: search --from/--to — 지정한 기간 안에 있는 거래만 검색되는지 검증
    def test_050_search_by_date_range(self) -> None:
        self._seed_search_fixture()
        code, out, _ = self.run_cli("search", "--from", "2024-01-01", "--to", "2024-01-31")
        self.assertEqual(code, 0)
        self.assertIn("TX-1", out)
        self.assertIn("TX-2", out)
        self.assertNotIn("TX-3", out)

    # 기능: search --category — 지정한 카테고리 거래만 검색되는지 검증
    def test_051_search_by_category(self) -> None:
        self._seed_search_fixture()
        code, out, _ = self.run_cli("search", "--category", "교통")
        self.assertEqual(code, 0)
        self.assertIn("TX-2", out)
        self.assertNotIn("TX-1", out)
        self.assertNotIn("TX-3", out)

    # 기능: search --type — income/expense 타입으로 필터링되는지 검증
    def test_052_search_by_type(self) -> None:
        self._seed_search_fixture()
        code, out, _ = self.run_cli("search", "--type", "income")
        self.assertEqual(code, 0)
        self.assertIn("TX-3", out)
        self.assertNotIn("TX-1", out)
        self.assertNotIn("TX-2", out)

    # 기능: search --q — 메모 안의 키워드로 검색되는지 검증
    def test_053_search_by_memo_keyword(self) -> None:
        self._seed_search_fixture()
        code, out, _ = self.run_cli("search", "--q", "김밥")
        self.assertEqual(code, 0)
        self.assertIn("TX-1", out)
        self.assertIn("TX-3", out)
        self.assertNotIn("TX-2", out)

    # 기능: search --tag — 지정한 태그를 가진 거래만 검색되는지 검증
    def test_054_search_by_tag(self) -> None:
        self._seed_search_fixture()
        code, out, _ = self.run_cli("search", "--tag", "점심")
        self.assertEqual(code, 0)
        self.assertIn("TX-1", out)
        self.assertNotIn("TX-2", out)
        self.assertNotIn("TX-3", out)

    # 기능: search — --from/--to/--category/--type/--q/--tag 를 동시에 지정하면
    # AND 조건으로 결합되는지 검증
    def test_055_search_combines_all_filters_with_and(self) -> None:
        self._seed_search_fixture()
        code, out, _ = self.run_cli(
            "search",
            "--from", "2024-01-01", "--to", "2024-01-31",
            "--category", "식비", "--type", "expense",
            "--q", "김밥", "--tag", "점심",
        )
        self.assertEqual(code, 0)
        self.assertIn("TX-1", out)
        self.assertNotIn("TX-2", out)
        self.assertNotIn("TX-3", out)

    # 기능: search — 조건을 하나도 지정하지 않으면 오류로 처리되는지 검증
    def test_056_search_requires_at_least_one_condition(self) -> None:
        self._seed_search_fixture()
        code, _, err = self.run_cli("search")
        self.assertEqual(code, 1)
        self.assertIn("검색 조건이 하나도 지정되지 않았습니다", err)

    # 기능: search — 검색 결과도 list 와 마찬가지로 최신순으로 정렬되는지 검증
    def test_057_search_results_are_newest_first(self) -> None:
        self._seed_search_fixture()
        code, out, _ = self.run_cli("search", "--category", "식비")
        self.assertLess(out.index("TX-3"), out.index("TX-1"))

    # ==================================================================
    # 06x: 예산 설정/조회 (budget)
    # ==================================================================
    # 기능: budget set/show — 월 예산을 저장하고 조회 시 그대로 반영되는지 검증
    def test_060_budget_set_and_show(self) -> None:
        code, out, _ = self.run_cli("budget", "set", "--month", "2024-01", "--amount", "500000")
        self.assertEqual(code, 0)
        self.assertIn("[저장 완료]", out)
        self.assertIn("500,000", out)

        code, out, _ = self.run_cli("budget", "show", "--month", "2024-01")
        self.assertEqual(code, 0)
        self.assertIn("500,000", out)

    # 기능: budget set — 같은 달에 다시 설정하면 기존 값을 덮어쓰는지 검증
    def test_061_budget_set_overwrites_same_month(self) -> None:
        self.run_cli("budget", "set", "--month", "2024-01", "--amount", "500000")
        code, out, _ = self.run_cli("budget", "set", "--month", "2024-01", "--amount", "700000")
        self.assertEqual(code, 0)
        self.assertIn("기존 예산 500,000원을 덮어썼습니다", out)

        code, out, _ = self.run_cli("budget", "show", "--month", "2024-01")
        self.assertIn("700,000", out)

    # 기능: budget list — 설정된 모든 월의 예산 목록/개수가 출력되는지 검증
    def test_062_budget_list_shows_all_months(self) -> None:
        self.run_cli("budget", "set", "--month", "2024-01", "--amount", "500000")
        self.run_cli("budget", "set", "--month", "2024-02", "--amount", "300000")
        code, out, _ = self.run_cli("budget", "list")
        self.assertEqual(code, 0)
        self.assertIn("2024-01", out)
        self.assertIn("2024-02", out)
        self.assertIn("2개", out)

    # 기능: budget remove — 예산을 삭제하면 이후 조회 시 "설정되지 않음"으로
    # 나오고, 이미 삭제된 월을 다시 삭제하면 오류가 나는지 검증
    def test_063_budget_remove(self) -> None:
        self.run_cli("budget", "set", "--month", "2024-01", "--amount", "500000")
        code, out, _ = self.run_cli("budget", "remove", "--month", "2024-01")
        self.assertEqual(code, 0)
        self.assertIn("[삭제 완료]", out)

        code, out, _ = self.run_cli("budget", "show", "--month", "2024-01")
        self.assertIn("설정되지 않았습니다", out)

        code, _, err = self.run_cli("budget", "remove", "--month", "2024-01")
        self.assertEqual(code, 1)

    # 기능: budget set — 금액이 0 이하면 검증에서 거부되는지 검증
    def test_064_budget_invalid_amount_is_rejected(self) -> None:
        code, _, err = self.run_cli("budget", "set", "--month", "2024-01", "--amount", "0")
        self.assertEqual(code, 1)
        self.assertIn("금액은 0보다 커야 합니다", err)

    # 기능: budget + summary — 예산 대비 사용률(%)이 초과 없이 계산되어
    # summary 에 반영되는지 검증
    def test_065_budget_usage_percent_without_overrun(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        self.run_cli("budget", "set", "--month", "2024-01", "--amount", "10000")
        self.run_cli("add", "--date", "2024-01-05", "--type", "expense", "--category", "식비", "--amount", "5000")
        code, out, _ = self.run_cli("summary", "--month", "2024-01")
        self.assertIn("50.0%", out)
        self.assertNotIn("[경고]", out)

    # 기능: budget + summary — 지출이 예산을 초과하면 초과 금액과 함께
    # 경고 문구가 출력되는지 검증
    def test_066_budget_usage_overrun_warning(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        self.run_cli("budget", "set", "--month", "2024-01", "--amount", "10000")
        self.run_cli("add", "--date", "2024-01-05", "--type", "expense", "--category", "식비", "--amount", "13000")
        code, out, _ = self.run_cli("summary", "--month", "2024-01")
        self.assertIn("[경고]", out)
        self.assertIn("3,000원 초과", out)

    # ==================================================================
    # 07x: 월별 요약 (summary)
    # ==================================================================
    # 기능: summary — 총 수입/총 지출/잔액(수입-지출)이 올바르게 계산/출력되는지 검증
    def test_070_summary_totals_and_balance(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        self.run_cli("category", "add", "--name", "월급")
        self.run_cli("add", "--date", "2024-01-05", "--type", "expense", "--category", "식비", "--amount", "12000")
        self.run_cli("add", "--date", "2024-01-25", "--type", "income", "--category", "월급", "--amount", "3000000")

        code, out, _ = self.run_cli("summary", "--month", "2024-01")
        self.assertEqual(code, 0)
        self.assertIn("총 수입", out)
        self.assertIn("3,000,000", out)
        self.assertIn("총 지출", out)
        self.assertIn("12,000", out)
        self.assertIn("잔액", out)
        self.assertIn("2,988,000", out)

    # 기능: summary --top — 카테고리별 지출 합계를 TOP N 개로 잘라 출력하는지 검증
    def test_071_summary_top_n_expense_categories(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        self.run_cli("category", "add", "--name", "교통")
        self.run_cli("add", "--date", "2024-01-05", "--type", "expense", "--category", "식비", "--amount", "12000")
        self.run_cli("add", "--date", "2024-01-06", "--type", "expense", "--category", "교통", "--amount", "1250")

        code, out, _ = self.run_cli("summary", "--month", "2024-01", "--top", "1")
        self.assertEqual(code, 0)
        self.assertIn("TOP 1", out)
        self.assertIn("식비", out)
        self.assertNotIn("교통", out)

    # 기능: summary — 데이터가 없는 달은 0원이 아니라 "데이터 없음"으로
    # 명확히 구분되어 출력되는지 검증
    def test_072_summary_reports_no_data_distinctly(self) -> None:
        code, out, _ = self.run_cli("summary", "--month", "2030-01")
        self.assertEqual(code, 0)
        self.assertIn("데이터 없음", out)

    # 기능: summary — 예산이 설정된 달이면 예산 사용률(%)이 함께 출력되는지 검증
    def test_073_summary_reflects_budget_usage(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        self.run_cli("budget", "set", "--month", "2024-01", "--amount", "20000")
        self.run_cli("add", "--date", "2024-01-05", "--type", "expense", "--category", "식비", "--amount", "12000")

        code, out, _ = self.run_cli("summary", "--month", "2024-01")
        self.assertIn("예산", out)
        self.assertIn("60.0%", out)

    # 기능: summary — 예산이 설정되지 않은 달은 "설정되지 않음"으로
    # 명확히 표시되는지 검증
    def test_074_summary_without_budget_shows_not_set(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        self.run_cli("add", "--date", "2024-01-05", "--type", "expense", "--category", "식비", "--amount", "1000")
        code, out, _ = self.run_cli("summary", "--month", "2024-01")
        self.assertIn("설정되지 않음", out)

    # 기능: summary — --month 를 지정하지 않으면 실행 자체가 거부되는지 검증
    def test_075_summary_requires_month_option(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            self.run_cli("summary")
        self.assertEqual(ctx.exception.code, 2)  # argparse 필수 옵션 누락

    # ==================================================================
    # 08x: 가져오기 (import)
    # ==================================================================
    # 기능: import — 유효한 CSV 행들이 모두 거래로 등록되고 처리 건수가
    # 출력되는지 검증
    def test_080_import_valid_rows_are_registered(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        source = Path(self._tmp.name) / "in.csv"
        source.write_text(
            "date,type,category,amount,memo,tags\n"
            "2024-03-01,expense,식비,8000,점심,외식\n"
            "2024-03-02,income,식비,100000,용돈,\n",
            encoding="utf-8",
        )
        code, out, _ = self.run_cli("import", "--from", str(source))
        self.assertEqual(code, 0)
        self.assertIn("imported=2, skipped=0", out)

    # 기능: import — 잘못된 행(형식 오류/미등록 카테고리)은 건너뛰고, 처리
    # 건수와 함께 건너뛴 사유가 출력되는지 검증
    def test_081_import_skips_invalid_rows_and_reports_reason(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        source = Path(self._tmp.name) / "in.csv"
        source.write_text(
            "date,type,category,amount,memo,tags\n"
            "2024-03-01,expense,식비,8000,점심,외식\n"
            "2024-13-40,expense,식비,1000,잘못된 날짜,\n"
            "2024-03-03,expense,없는카테고리,1000,,\n",
            encoding="utf-8",
        )
        code, out, _ = self.run_cli("import", "--from", str(source))
        self.assertEqual(code, 0)
        self.assertIn("imported=1, skipped=2", out)
        self.assertIn("[건너뛴 행]", out)

    # 기능: import — 존재하지 않는 CSV 경로를 지정하면 오류로 처리되는지 검증
    def test_082_import_missing_file_returns_error(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        code, _, err = self.run_cli("import", "--from", str(Path(self._tmp.name) / "없는파일.csv"))
        self.assertEqual(code, 1)
        self.assertIn("[오류]", err)

    # 기능: import — CSV 헤더에 필수 컬럼(amount 등)이 빠지면 오류로 처리되는지 검증
    def test_083_import_missing_required_header_column(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        source = Path(self._tmp.name) / "bad_header.csv"
        source.write_text("date,type,category,memo,tags\n2024-03-01,expense,식비,점심,외식\n", encoding="utf-8")
        code, _, err = self.run_cli("import", "--from", str(source))
        self.assertEqual(code, 1)
        self.assertIn("amount", err)

    # 기능: import — 카테고리가 하나도 등록되지 않은 상태면 CSV 내용과 무관하게
    # 가져오기 자체가 차단되는지 검증
    def test_084_import_blocked_without_category_registered(self) -> None:
        source = Path(self._tmp.name) / "in.csv"
        source.write_text("date,type,category,amount,memo,tags\n2024-03-01,expense,식비,8000,,\n", encoding="utf-8")
        code, _, err = self.run_cli("import", "--from", str(source))
        self.assertEqual(code, 1)
        self.assertIn("등록된 카테고리가 없습니다", err)

    # ==================================================================
    # 09x: 내보내기 (export)
    # ==================================================================
    # 기능: export --month — 지정한 월의 거래만 CSV 로 내보내는지 검증
    def test_090_export_by_month(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        self.run_cli("add", "--date", "2024-03-01", "--type", "expense", "--category", "식비", "--amount", "8000")
        target = Path(self._tmp.name) / "out.csv"
        code, out, _ = self.run_cli("export", "--out", str(target), "--month", "2024-03")
        self.assertEqual(code, 0)
        self.assertIn("(1 records)", out)

    # 기능: export --from/--to — 지정한 날짜 범위의 거래만 CSV 로 내보내는지 검증
    def test_091_export_by_date_range(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        self.run_cli("add", "--date", "2024-03-01", "--type", "expense", "--category", "식비", "--amount", "8000")
        self.run_cli("add", "--date", "2024-04-01", "--type", "expense", "--category", "식비", "--amount", "9000")
        target = Path(self._tmp.name) / "out.csv"
        code, out, _ = self.run_cli(
            "export", "--out", str(target), "--from", "2024-03-01", "--to", "2024-03-31"
        )
        self.assertEqual(code, 0)
        self.assertIn("(1 records)", out)

    # 기능: export — --month 도 --from/--to 도 지정하지 않으면 오류로
    # 처리되는지 검증(조건 최소 1개 필수)
    def test_092_export_requires_month_or_range(self) -> None:
        code, _, err = self.run_cli("export", "--out", str(Path(self._tmp.name) / "x.csv"))
        self.assertEqual(code, 1)
        self.assertIn("--month", err)

    # 기능: export — --month 와 --from/--to 를 동시에 지정하면 오류로
    # 처리되는지 검증(두 방식은 함께 쓸 수 없음)
    def test_093_export_month_and_range_are_mutually_exclusive(self) -> None:
        code, _, err = self.run_cli(
            "export", "--out", str(Path(self._tmp.name) / "x.csv"),
            "--month", "2024-03", "--from", "2024-03-01", "--to", "2024-03-31",
        )
        self.assertEqual(code, 1)
        self.assertIn("함께 쓸 수 없습니다", err)

    # 기능: export — CSV 스키마(date,type,category,amount,memo,tags)가
    # 고정되어 헤더/데이터 행이 그대로 기록되는지 검증
    def test_094_export_csv_schema_is_fixed(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        self.run_cli(
            "add", "--date", "2024-03-01", "--type", "expense",
            "--category", "식비", "--amount", "8000", "--memo", "점심", "--tags", "외식",
        )
        target = Path(self._tmp.name) / "out.csv"
        self.run_cli("export", "--out", str(target), "--month", "2024-03")
        lines = target.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "date,type,category,amount,memo,tags")
        self.assertIn("2024-03-01,expense,식비,8000,점심,외식", lines[1])

    # 기능: export — 조건에 맞는 거래가 없으면 헤더만 기록하고 그 사실을
    # 안내하는지 검증
    def test_095_export_with_no_matches_writes_header_only(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        target = Path(self._tmp.name) / "out.csv"
        code, out, _ = self.run_cli("export", "--out", str(target), "--month", "2030-01")
        self.assertEqual(code, 0)
        self.assertIn("(0 records)", out)
        self.assertIn("헤더만 기록", out)
        lines = target.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines, ["date,type,category,amount,memo,tags"])

    # ==================================================================
    # 10x: 데코레이터 — 공통 관심사(예외 처리/로그/시간 측정)
    # ==================================================================
    # 기능: 데코레이터(@log_call/@timeit) — --verbose 를 주면 실행 로그와
    # 실행 시간 로그가 stderr 로 출력되는지 검증
    def test_100_verbose_flag_enables_debug_logs(self) -> None:
        code, _, err = self.run_cli("--verbose", "category", "add", "--name", "식비")
        self.assertEqual(code, 0)
        self.assertIn("[log]", err)
        self.assertIn("실행 시간", err)

    # 기능: 데코레이터 — --verbose 없이 실행하면 디버그 로그가 전혀 찍히지
    # 않는지 검증
    def test_101_without_verbose_no_debug_logs(self) -> None:
        code, _, err = self.run_cli("category", "add", "--name", "식비")
        self.assertEqual(code, 0)
        self.assertNotIn("[log]", err)

    # 기능: 데코레이터(@command, functools.wraps) — 여러 데코레이터를 겹쳐
    # 씌워도 원본 함수의 __name__/__doc__ 메타데이터가 보존되는지 검증
    def test_102_command_decorator_preserves_function_metadata(self) -> None:
        self.assertEqual(cli.cmd_add.__name__, "cmd_add")
        self.assertIsNotNone(cli.cmd_add.__doc__)
        self.assertIn("거래 추가", cli.cmd_add.__doc__)

    # ==================================================================
    # 11x: 예외 처리 및 종료 코드
    # ==================================================================
    # 기능: 종료 코드 — 정상 처리된 명령은 종료 코드 0 을 반환하는지 검증
    def test_110_normal_exit_code_is_zero(self) -> None:
        code, _, _ = self.run_cli("category", "list")
        self.assertEqual(code, 0)

    # 기능: 종료 코드 — 검증 오류(ValidationError)가 발생하면 종료 코드
    # 1 을 반환하는지 검증
    def test_111_validation_error_exit_code_is_one(self) -> None:
        code, _, _ = self.run_cli("add")  # 카테고리 미등록
        self.assertEqual(code, 1)

    # 기능: 예외 처리(@handle_errors) — 오류 메시지가 파이썬 스택트레이스가
    # 아니라 "[오류] 원인" 형태로만 출력되는지 검증
    def test_112_error_output_has_cause_and_hint_not_stacktrace(self) -> None:
        code, _, err = self.run_cli("update", "--id", "TX-999", "--amount", "1")
        self.assertEqual(code, 1)
        self.assertIn("[오류]", err)
        self.assertNotIn("Traceback", err)
        self.assertNotIn(".py", err)  # 파일 경로가 찍히는 스택트레이스가 없어야 함

    # 기능: 종료 코드 — 대화형 입력 중 Ctrl+C(KeyboardInterrupt) 로 취소하면
    # 종료 코드 130 과 "[중단]" 메시지를 반환하는지 검증
    def test_113_interactive_cancel_returns_130(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        with mock.patch("builtins.input", side_effect=KeyboardInterrupt):
            code, _, err = self.run_cli("add")
        self.assertEqual(code, 130)
        self.assertIn("[중단]", err)

    # ==================================================================
    # 12x: 백업 기능
    # ==================================================================
    # 기능: backup — 실행 시 타임스탬프(YYYYmmdd_HHMMSS) 이름의 백업 폴더가
    # 정확히 하나 생성되는지 검증
    def test_120_backup_creates_timestamped_folder(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        code, out, _ = self.run_cli("backup")
        self.assertEqual(code, 0)
        backups = list((self.data_dir / "backup").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertTrue(re.fullmatch(r"\d{8}_\d{6}", backups[0].name), backups[0].name)

    # 기능: backup — transactions/categories/budgets 데이터 파일이 모두
    # 백업 폴더로 복사되는지 검증
    def test_121_backup_copies_all_data_files(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        self.run_cli("budget", "set", "--month", "2024-01", "--amount", "1000")
        self.run_cli("add", "--date", "2024-01-01", "--type", "expense", "--category", "식비", "--amount", "500")
        code, out, _ = self.run_cli("backup")
        self.assertEqual(code, 0)
        backup_dir = next((self.data_dir / "backup").iterdir())
        for name in ("transactions.jsonl", "transactions.idx", "categories.jsonl", "budgets.jsonl"):
            self.assertTrue((backup_dir / name).exists(), name)

    # 기능: backup — 완료 메시지에 백업된 파일 이름 목록이 함께 출력되는지 검증
    def test_122_backup_output_lists_copied_file_names(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        code, out, _ = self.run_cli("backup")
        self.assertEqual(code, 0)
        self.assertIn("[완료] 백업 생성:", out)
        self.assertIn("categories.jsonl", out)

    # ==================================================================
    # 13x: 반복 내역 기능 (recurring)
    # ==================================================================
    # 기능: recurring add/list — 반복 규칙 등록 성공 메시지와 id, 목록 조회를 검증
    def test_130_recurring_add_and_list(self) -> None:
        self.run_cli("category", "add", "--name", "월급")
        code, out, _ = self.run_cli(
            "recurring", "add", "--day", "25", "--type", "income",
            "--category", "월급", "--amount", "3000000", "--memo", "정기 급여",
        )
        self.assertEqual(code, 0)
        self.assertIn("[저장 완료] id=RC-1", out)

        code, out, _ = self.run_cli("recurring", "list")
        self.assertIn("RC-1", out)
        self.assertIn("월급", out)

    # 기능: recurring apply — 등록된 규칙으로 지정한 월에 거래가 자동
    # 생성되는지 검증
    def test_131_recurring_apply_creates_transaction_for_month(self) -> None:
        self.run_cli("category", "add", "--name", "월급")
        self.run_cli(
            "recurring", "add", "--day", "25", "--type", "income",
            "--category", "월급", "--amount", "3000000",
        )
        code, out, _ = self.run_cli("recurring", "apply", "--month", "2024-04")
        self.assertEqual(code, 0)
        self.assertIn("생성 1건", out)

        code, out, _ = self.run_cli("list")
        self.assertIn("2024-04-25", out)
        self.assertIn("3,000,000", out)

    # 기능: recurring apply — 같은 달에 이미 적용한 규칙을 다시 적용하면
    # 중복 생성되지 않고 "건너뜀"으로 처리되는지 검증
    def test_132_recurring_apply_skips_already_applied_month(self) -> None:
        self.run_cli("category", "add", "--name", "월급")
        self.run_cli(
            "recurring", "add", "--day", "25", "--type", "income",
            "--category", "월급", "--amount", "3000000",
        )
        self.run_cli("recurring", "apply", "--month", "2024-04")
        code, out, _ = self.run_cli("recurring", "apply", "--month", "2024-04")
        self.assertEqual(code, 0)
        self.assertIn("생성 0건", out)
        self.assertIn("건너뜀 1건", out)

    # 기능: recurring remove — 반복 규칙을 삭제하면 목록에서 사라지는지 검증
    def test_133_recurring_remove(self) -> None:
        self.run_cli("category", "add", "--name", "월급")
        self.run_cli(
            "recurring", "add", "--day", "25", "--type", "income",
            "--category", "월급", "--amount", "3000000",
        )
        code, out, _ = self.run_cli("recurring", "remove", "--id", "RC-1")
        self.assertEqual(code, 0)
        self.assertIn("[삭제 완료] id=RC-1", out)

        code, out, _ = self.run_cli("recurring", "list")
        self.assertIn("등록된 반복 규칙이 없습니다", out)

    # 기능: recurring apply — 해당 월에 존재하지 않는 일자(예: 31일)는
    # 말일로 보정되어 생성되는지 검증
    def test_134_recurring_apply_clamps_day_to_month_end(self) -> None:
        self.run_cli("category", "add", "--name", "월세")
        self.run_cli(
            "recurring", "add", "--day", "31", "--type", "expense",
            "--category", "월세", "--amount", "500000",
        )
        code, out, _ = self.run_cli("recurring", "apply", "--month", "2024-04")  # 4월은 30일까지
        self.assertEqual(code, 0)
        code, out, _ = self.run_cli("list")
        self.assertIn("2024-04-30", out)

    # 기능: recurring add — 카테고리가 하나도 등록되지 않은 상태에서는
    # 반복 규칙 등록도 차단되는지 검증
    def test_135_recurring_add_blocked_without_category(self) -> None:
        code, _, err = self.run_cli(
            "recurring", "add", "--day", "25", "--type", "income",
            "--category", "월급", "--amount", "3000000",
        )
        self.assertEqual(code, 1)
        self.assertIn("등록된 카테고리가 없습니다", err)

    # ==================================================================
    # 14x: 출력 포맷 — 표/막대 그래프가 실제 CLI 출력에 반영되는지
    # ==================================================================
    # 기능: 출력 포맷(표) — list 출력이 헤더/구분선(대시)을 갖춘 정렬된
    # 표 형태로 렌더링되는지 검증
    def test_140_list_output_uses_dashed_separator_table(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        self.run_cli("add", "--date", "2024-01-01", "--type", "expense", "--category", "식비", "--amount", "1000")
        code, out, _ = self.run_cli("list")
        lines = [line for line in out.splitlines() if line.strip()]
        header, separator = lines[0], lines[1]
        for column in ("id", "날짜", "타입", "카테고리", "금액"):
            self.assertIn(column, header)
        self.assertTrue(set(separator) <= {"-", " "})

    # 기능: 출력 포맷(전각 문자 폭) — 한글 카테고리 이름이 섞여도 category
    # list 표가 깨지지 않고 정렬되는지 검증
    def test_141_category_list_table_handles_korean_width(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        self.run_cli("category", "add", "--name", "교통비지출")
        code, out, _ = self.run_cli("category", "list")
        self.assertEqual(code, 0)
        lines = [line for line in out.splitlines() if line.strip()]
        self.assertGreaterEqual(len(lines), 4)  # 헤더+구분선+2행+완료메시지
        self.assertTrue(set(lines[1]) <= {"-", " "})

    # 기능: 출력 포맷(막대 그래프) — summary 의 예산 사용률 막대가 '#'(채움)과
    # '.'(빈칸) 문자로 실제 렌더링되는지 검증
    def test_142_summary_budget_bar_uses_hash_and_dot_chars(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        self.run_cli("budget", "set", "--month", "2024-01", "--amount", "10000")
        self.run_cli("add", "--date", "2024-01-01", "--type", "expense", "--category", "식비", "--amount", "5000")
        code, out, _ = self.run_cli("summary", "--month", "2024-01")
        self.assertIn("[", out)
        self.assertIn("#", out)
        self.assertIn(".", out)  # 50% 사용 → 절반은 #, 절반은 . 이어야 함

    # 기능: 출력 포맷(금액) — 큰 금액도 천 단위 콤마 구분으로 표시되는지 검증
    def test_143_amounts_are_formatted_with_thousand_separators(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        self.run_cli("add", "--date", "2024-01-01", "--type", "income", "--category", "식비", "--amount", "1234000")
        code, out, _ = self.run_cli("list")
        self.assertIn("1,234,000", out)

    # ==================================================================
    # 15x: 저장 원자성 강화
    # ==================================================================
    # 기능: 저장 원자성(categories.jsonl) — os.replace 실패 시 임시파일
    # 교체가 이루어지지 않아 원본 파일이 손상되지 않는지 검증
    def test_150_category_store_rewrite_is_atomic_via_tmp_and_replace(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        path = self.data_dir / "categories.jsonl"
        original = path.read_text(encoding="utf-8")

        with mock.patch("budget_app.stores.os.replace", side_effect=OSError("simulated disk failure")):
            code, _, err = self.run_cli("category", "add", "--name", "교통")
        self.assertEqual(code, 1)
        self.assertIn("입출력 오류", err)
        # os.replace 가 실패했으므로 원본 파일은 절대 손상되지 않아야 한다(임시파일에만 썼다가 교체 직전에 실패).
        self.assertEqual(path.read_text(encoding="utf-8"), original)

    # 기능: 저장 원자성(budgets.jsonl) — os.replace 실패 시 원본 예산 파일이
    # 손상되지 않는지 검증
    def test_151_budget_store_rewrite_is_atomic_via_tmp_and_replace(self) -> None:
        self.run_cli("budget", "set", "--month", "2024-01", "--amount", "10000")
        path = self.data_dir / "budgets.jsonl"
        original = path.read_text(encoding="utf-8")

        with mock.patch("budget_app.stores.os.replace", side_effect=OSError("simulated disk failure")):
            code, _, err = self.run_cli("budget", "set", "--month", "2024-02", "--amount", "20000")
        self.assertEqual(code, 1)
        self.assertEqual(path.read_text(encoding="utf-8"), original)

    # 기능: 저장 원자성(transactions) — update 가 로그에는 새 버전을 append
    # 만 하고(기존 줄 보존), 인덱스는 슬롯 개수 증가 없이 제자리에서만
    # 갱신되는지 검증
    def test_152_update_appends_to_log_and_overwrites_index_slot_in_place(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        self.run_cli("add", "--date", "2024-01-01", "--type", "expense", "--category", "식비", "--amount", "1000")

        log_path = self.data_dir / "transactions.jsonl"
        idx_path = self.data_dir / "transactions.idx"
        lines_before = log_path.read_text(encoding="utf-8").strip().splitlines()
        idx_size_before = idx_path.stat().st_size
        self.assertEqual(len(lines_before), 1)

        self.run_cli("update", "--id", "TX-1", "--amount", "2000")

        lines_after = log_path.read_text(encoding="utf-8").strip().splitlines()
        idx_size_after = idx_path.stat().st_size
        self.assertEqual(len(lines_after), 2)  # 옛 버전은 그대로 남고 새 버전이 끝에 append 됨
        self.assertIn('"amount": 1000', lines_after[0])
        self.assertIn('"amount": 2000', lines_after[1])
        self.assertEqual(idx_size_before, idx_size_after)  # 슬롯 개수는 안 늘어남(제자리 덮어쓰기)

        code, out, _ = self.run_cli("list")
        self.assertIn("2,000", out)
        self.assertNotIn("1,000", out)  # 조회는 항상 최신 버전만 보여줘야 함

    # 기능: 저장 원자성(transactions) — delete 는 인덱스 슬롯만 초기화할 뿐
    # transactions.jsonl 로그 파일 자체는 전혀 건드리지 않는지 검증
    def test_153_delete_only_clears_index_slot_log_untouched(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        self.run_cli("add", "--date", "2024-01-01", "--type", "expense", "--category", "식비", "--amount", "1000")

        log_path = self.data_dir / "transactions.jsonl"
        log_size_before = log_path.stat().st_size

        self.run_cli("delete", "--id", "TX-1", "--yes")

        log_size_after = log_path.stat().st_size
        self.assertEqual(log_size_before, log_size_after)  # delete 는 로그 파일을 건드리지 않음

        code, out, _ = self.run_cli("list")
        self.assertNotIn("TX-1", out)


if __name__ == "__main__":
    unittest.main()
