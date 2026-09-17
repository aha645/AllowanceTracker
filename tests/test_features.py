"""기능(인수) 테스트 — `doc/request.md`의 10대 기능 + 보너스 체크리스트를 그대로 따라간다.

`test_repository.py`/`test_services.py`/`test_cli.py`가 "구현이 내부적으로 올바른가"를
계층별로 검증하는 **단위테스트**라면, 이 파일은 "최종 결과물이 요구사항대로 동작하는가"를
검증하는 **기능(인수) 테스트**다. CLI를 블랙박스로 호출해서(`main(argv)`), 사람이 실제로
치는 명령과 화면에 보이는 출력만으로 판단한다 — 내부 클래스/함수를 직접 부르지 않는다.

테스트 메서드 이름과 순서는 요구사항 문서의 "1. 기능 요구사항 체크리스트 (10대 기능)"
순서를 그대로 따른다. 그래서 "이 요구사항을 검증하는 테스트가 있는가?"라는 질문에는
이 파일의 메서드 이름을 그대로 보여주는 것으로 답할 수 있다.
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

# `python tests/test_features.py` 로 직접 실행할 때도 budget_app 을 찾을 수 있도록
# 프로젝트 루트를 sys.path 에 넣는다 (자세한 이유는 test_repository.py 상단 주석 참고).
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from budget_app.cli import main


class FeatureAcceptanceTestCase(unittest.TestCase):
    """요구사항 문서 1번 섹션(10대 기능)을 항목별로 하나씩 독립 검증한다."""

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

    # ---------------------------------------------------------- 1-1. 거래 추가 (add)
    def test_01_add_interactive_saves_transaction_and_reports_generated_id(self) -> None:
        """요구사항: 대화형 입력(날짜/타입/카테고리/금액/메모/태그) → 저장 성공 시 생성 id 출력."""
        self.run_cli("category", "add", "--name", "식비")
        answers = iter(["2024-01-05", "expense", "식비", "12000", "점심", "외식,점심"])
        with mock.patch("builtins.input", lambda *a: next(answers)):
            code, out, _ = self.run_cli("add")
        self.assertEqual(code, 0)
        self.assertIn("[저장 완료] id=TX-1", out)  # 생성된 id 가 사용자에게 출력됨

        code, out, _ = self.run_cli("list")
        self.assertIn("2024-01-05", out)
        self.assertIn("12,000", out)
        self.assertIn("외식,점심", out)

    def test_01b_add_blocked_when_no_category_registered(self) -> None:
        """요구사항: 카테고리 파일이 비어있으면 add 를 막고 안내한다(정책 안 B)."""
        code, _, err = self.run_cli(
            "add", "--date", "2024-01-01", "--type", "expense", "--category", "식비", "--amount", "1000"
        )
        self.assertEqual(code, 1)
        self.assertIn("[오류] 등록된 카테고리가 없습니다.", err)
        self.assertIn("[힌트]", err)

    # ---------------------------------------------------------- 1-2. 거래 목록 (list)
    def test_02_list_supports_limit_and_returns_newest_first(self) -> None:
        """요구사항: --limit N 지원, 최신순(id 내림차순) 정렬 출력."""
        self.run_cli("category", "add", "--name", "식비")
        for day in range(1, 6):
            self.run_cli(
                "add", "--date", f"2024-01-0{day}", "--type", "expense",
                "--category", "식비", "--amount", str(day * 1000),
            )

        code, out, _ = self.run_cli("list", "--limit", "2")
        self.assertEqual(code, 0)
        self.assertIn("TX-5", out)
        self.assertIn("TX-4", out)
        self.assertNotIn("TX-3", out)  # limit 2 → 3건째는 안 보여야 함
        self.assertLess(out.index("TX-5"), out.index("TX-4"))  # 최신순

    # ---------------------------------------------------------- 1-3. 거래 검색 (search)
    def test_03_search_combines_all_filters_with_and_returns_newest_first(self) -> None:
        """요구사항: --from/--to, --category, --type, --q, --tag 를 AND 로 결합, 최신순 출력."""
        self.run_cli("category", "add", "--name", "식비")
        self.run_cli("category", "add", "--name", "교통")
        self.run_cli(
            "add", "--date", "2024-01-05", "--type", "expense",
            "--category", "식비", "--amount", "12000", "--memo", "점심 김밥", "--tags", "외식,점심",
        )
        self.run_cli(
            "add", "--date", "2024-01-06", "--type", "expense",
            "--category", "교통", "--amount", "1250", "--memo", "버스",
        )
        self.run_cli(
            "add", "--date", "2024-02-01", "--type", "expense",
            "--category", "식비", "--amount", "30000", "--memo", "저녁 김밥", "--tags", "외식",
        )

        # 기간 + 카테고리 + 타입 + 메모키워드 + 태그를 모두 동시에 지정 → 1건만 남아야 함
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

    # ---------------------------------------------------------- 1-4. 월별 요약 (summary)
    def test_04_summary_shows_totals_and_top_n_expense_categories(self) -> None:
        """요구사항: 총 수입/총 지출/잔액 + 카테고리별 지출 TOP N."""
        self.run_cli("category", "add", "--name", "식비")
        self.run_cli("category", "add", "--name", "교통")
        self.run_cli("category", "add", "--name", "월급")
        self.run_cli("add", "--date", "2024-01-05", "--type", "expense", "--category", "식비", "--amount", "12000")
        self.run_cli("add", "--date", "2024-01-06", "--type", "expense", "--category", "교통", "--amount", "1250")
        self.run_cli("add", "--date", "2024-01-25", "--type", "income", "--category", "월급", "--amount", "3000000")

        code, out, _ = self.run_cli("summary", "--month", "2024-01", "--top", "1")
        self.assertEqual(code, 0)
        self.assertIn("총 수입", out)
        self.assertIn("3,000,000", out)
        self.assertIn("총 지출", out)
        self.assertIn("13,250", out)
        self.assertIn("잔액", out)
        self.assertIn("식비", out)      # TOP 1 은 식비(12,000) 여야 함
        self.assertNotIn("교통", out)   # top=1 이므로 2위는 안 보여야 함

    def test_04b_summary_reports_no_data_distinctly_from_zero(self) -> None:
        """요구사항: 해당 월 데이터가 없으면 "데이터 없음"을 0원과 구분해 출력."""
        code, out, _ = self.run_cli("summary", "--month", "2030-01")
        self.assertEqual(code, 0)
        self.assertIn("데이터 없음", out)

    # ---------------------------------------------------------- 1-5. 예산 설정/조회 (budget)
    def test_05_budget_set_reflected_in_summary_as_usage_and_overrun_warning(self) -> None:
        """요구사항: budget set 저장 + summary 실행 시 사용률(%)/초과 경고 반영."""
        self.run_cli("category", "add", "--name", "식비")

        code, out, _ = self.run_cli("budget", "set", "--month", "2024-01", "--amount", "10000")
        self.assertEqual(code, 0)
        self.assertIn("[저장 완료]", out)
        self.assertIn("10,000", out)

        self.run_cli("add", "--date", "2024-01-05", "--type", "expense", "--category", "식비", "--amount", "5000")
        code, out, _ = self.run_cli("summary", "--month", "2024-01")
        self.assertIn("50.0%", out)   # 5,000 / 10,000 = 50%
        self.assertNotIn("[경고]", out)

        self.run_cli("add", "--date", "2024-01-06", "--type", "expense", "--category", "식비", "--amount", "8000")
        code, out, _ = self.run_cli("summary", "--month", "2024-01")
        self.assertIn("[경고]", out)  # 13,000 > 10,000 예산 초과

    # ---------------------------------------------------------- 1-6. 카테고리 관리 (category)
    def test_06_category_add_list_remove_and_block_when_in_use(self) -> None:
        """요구사항: add/list/remove, 사용 중인 카테고리는 삭제 차단."""
        code, out, _ = self.run_cli("category", "add", "--name", "식비")
        self.assertEqual(code, 0)
        self.assertIn("[저장 완료]", out)

        code, _, err = self.run_cli("category", "add", "--name", "식비")  # 중복
        self.assertEqual(code, 1)
        self.assertIn("[오류]", err)

        code, out, _ = self.run_cli("category", "list")
        self.assertIn("식비", out)

        self.run_cli("add", "--date", "2024-01-05", "--type", "expense", "--category", "식비", "--amount", "1000")
        code, _, err = self.run_cli("category", "remove", "--name", "식비")
        self.assertEqual(code, 1)  # 사용 중이라 삭제 차단
        self.assertIn("사용 중", err)

        self.run_cli("category", "add", "--name", "미사용")
        code, out, _ = self.run_cli("category", "remove", "--name", "미사용")
        self.assertEqual(code, 0)
        self.assertIn("[삭제 완료]", out)

    # ---------------------------------------------------------- 1-7. 거래 수정 (update)
    def test_07_update_changes_only_specified_fields_and_rejects_missing_id(self) -> None:
        """요구사항: --id 옵션 기반, 지정한 필드만 수정, 없는 id 는 오류 메시지."""
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
        self.assertIn("점심", out)  # amount 만 바꿨으니 memo 는 그대로 유지돼야 함

        code, _, err = self.run_cli("update", "--id", "TX-999", "--amount", "1")
        self.assertEqual(code, 1)
        self.assertIn("TX-999", err)

    # ---------------------------------------------------------- 1-8. 거래 삭제 (delete)
    def test_08_delete_removes_transaction_and_rejects_missing_id(self) -> None:
        """요구사항: delete --id <id>, 없는 id 는 오류 메시지."""
        self.run_cli("category", "add", "--name", "식비")
        self.run_cli("add", "--date", "2024-01-05", "--type", "expense", "--category", "식비", "--amount", "1000")

        code, out, _ = self.run_cli("delete", "--id", "TX-1", "--yes")
        self.assertEqual(code, 0)
        self.assertIn("[삭제 완료]", out)

        code, out, _ = self.run_cli("list")
        self.assertNotIn("TX-1", out)

        code, _, err = self.run_cli("delete", "--id", "TX-1", "--yes")  # 이미 삭제됨
        self.assertEqual(code, 1)
        self.assertIn("TX-1", err)

    # ---------------------------------------------------------- 1-9. 가져오기/내보내기
    def test_09_import_export_report_counts_and_use_fixed_csv_schema(self) -> None:
        """요구사항: import/export 처리 건수 출력, CSV 스키마 date,type,category,amount,memo,tags 고정."""
        self.run_cli("category", "add", "--name", "식비")
        source = Path(self._tmp.name) / "in.csv"
        source.write_text(
            "date,type,category,amount,memo,tags\n"
            "2024-03-01,expense,식비,8000,점심,외식\n"
            "2024-13-40,expense,식비,1000,잘못된 날짜,\n",  # 검증 실패 → skip 되어야 함
            encoding="utf-8",
        )
        code, out, _ = self.run_cli("import", "--from", str(source))
        self.assertEqual(code, 0)
        self.assertIn("imported=1, skipped=1", out)

        # export 는 --month 또는 --from+--to 중 최소 하나가 필수
        code, _, err = self.run_cli("export", "--out", str(Path(self._tmp.name) / "x.csv"))
        self.assertEqual(code, 1)
        self.assertIn("--month", err)

        target = Path(self._tmp.name) / "out.csv"
        code, out, _ = self.run_cli("export", "--out", str(target), "--month", "2024-03")
        self.assertEqual(code, 0)
        self.assertIn("(1 records)", out)
        lines = target.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "date,type,category,amount,memo,tags")  # 스키마 고정
        self.assertIn("2024-03-01,expense,식비,8000,점심,외식", lines[1])

    # ---------------------------------------------------------- 보너스: 백업
    def test_10_bonus_backup_creates_timestamped_snapshot_of_data_files(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        code, out, _ = self.run_cli("backup")
        self.assertEqual(code, 0)
        self.assertIn("[완료]", out)
        backups = list((self.data_dir / "backup").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertTrue((backups[0] / "categories.jsonl").exists())

    # ---------------------------------------------------------- 보너스: 반복 내역
    def test_11_bonus_recurring_registers_rule_and_applies_it_monthly(self) -> None:
        """요구사항: 반복 규칙 등록 + 특정 월 자동 생성, 같은 달 중복 적용 방지."""
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

        code, out, _ = self.run_cli("recurring", "apply", "--month", "2024-04")
        self.assertEqual(code, 0)
        self.assertIn("생성 1건", out)
        code, out, _ = self.run_cli("list")
        self.assertIn("2024-04-25", out)
        self.assertIn("3,000,000", out)

        # 같은 달을 다시 적용하면 새로 생성되지 않고 "건너뜀" 처리돼야 함
        code, out, _ = self.run_cli("recurring", "apply", "--month", "2024-04")
        self.assertIn("생성 0건", out)
        self.assertIn("건너뜀 1건", out)

    # ---------------------------------------------------------- 유지보수: compact
    def test_12_compact_shrinks_log_without_changing_transaction_ids(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        self.run_cli("add", "--date", "2024-01-01", "--type", "expense", "--category", "식비", "--amount", "1000")
        self.run_cli("update", "--id", "TX-1", "--amount", "9999")  # 고아 레코드 발생

        code, out, _ = self.run_cli("compact")
        self.assertEqual(code, 0)
        self.assertIn("[완료] compact", out)

        code, out, _ = self.run_cli("list")
        self.assertIn("TX-1", out)      # id 는 그대로 유지
        self.assertIn("9,999", out)     # 최신 값도 그대로 유지


if __name__ == "__main__":
    unittest.main()
