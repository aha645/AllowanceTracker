"""CLI 계층 단위테스트: 인자 파싱/오류 처리/종료 코드/대화형 입력 로직 등 "배관" 검증.

"10대 기능 + 보너스가 요구사항대로 동작하는가"를 항목별로 확인하는 **기능 테스트는
`test_features.py`** 에 있다. 이 파일은 그 기능들을 구현하는 CLI 계층 자체가
튼튼한지(도움말, 잘못된 입력, 없는 id, 대화형 재입력 루프, 취소 처리, 엔드투엔드
회귀 시나리오)를 보는 단위테스트에 가깝다.
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

# `python tests/test_cli.py` 로 직접 실행할 때도 budget_app 을 찾을 수 있도록
# 프로젝트 루트를 sys.path 에 넣는다 (자세한 이유는 test_repository.py 상단 주석 참고).
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from budget_app.cli import main


class CliTestCase(unittest.TestCase):
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

    # 번호는 실행 순서 강제가 아니라 가독성을 위한 정렬이다(각 테스트는 setUp 에서 매번
    # 새 임시 --data-dir 을 받으므로 서로 독립적). 두 자리 0-padding 은 문자열 정렬 시
    # "10"이 "2"보다 앞에 오는 걸 막기 위함이다.

    # ---------------------------------------------------------------- 기본 동작
    def test_00_creates_data_files_on_first_run(self) -> None:
        code, _, _ = self.run_cli("list")
        self.assertEqual(code, 0)
        for name in ("transactions.jsonl", "transactions.idx", "categories.jsonl", "budgets.jsonl"):
            self.assertTrue((self.data_dir / name).exists(), name)

    def test_01_no_command_prints_help(self) -> None:
        code, out, _ = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("usage:", out)

    def test_02_help_available_for_every_subcommand(self) -> None:
        for cmd in (
            "add", "list", "search", "summary", "budget", "category",
            "update", "delete", "import", "export", "compact", "backup", "recurring",
        ):
            with self.subTest(cmd=cmd), self.assertRaises(SystemExit) as ctx:
                self.run_cli(cmd, "--help")
            self.assertEqual(ctx.exception.code, 0)

    # ---------------------------------------------------------------- 오류 처리
    def test_03_add_blocked_without_category(self) -> None:
        code, _, err = self.run_cli(
            "add", "--date", "2024-01-01", "--type", "expense", "--category", "식비", "--amount", "1000"
        )
        self.assertEqual(code, 1)
        self.assertIn("[오류] 등록된 카테고리가 없습니다.", err)
        self.assertIn("[힌트]", err)

    def test_04_missing_subcommand_returns_error(self) -> None:
        code, _, err = self.run_cli("category")
        self.assertEqual(code, 1)
        self.assertIn("[힌트]", err)

    def test_05_unknown_id_returns_exit_code_1(self) -> None:
        code, _, err = self.run_cli("delete", "--id", "TX-999", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("TX-999", err)

    def test_06_bad_id_format(self) -> None:
        code, _, err = self.run_cli("update", "--id", "abc", "--amount", "100")
        self.assertEqual(code, 1)
        self.assertIn("[오류]", err)

    def test_07_export_requires_month_or_range(self) -> None:
        code, _, err = self.run_cli("export", "--out", str(self.data_dir / "x.csv"))
        self.assertEqual(code, 1)
        self.assertIn("--month", err)

    # ---------------------------------------------------------------- 시나리오
    def test_08_full_workflow(self) -> None:
        """여러 기능을 한 흐름으로 엮은 엔드투엔드 회귀 시나리오(각 기능의 독립적인
        요구사항 검증은 test_features.py 참고): add→list→search→budget→summary→
        update→delete→compact 순서로 id/최신순/예산경고가 상태 변화에 맞춰
        일관되게 이어지는지 확인한다."""
        self.assertEqual(self.run_cli("category", "add", "--name", "식비")[0], 0)
        self.assertEqual(self.run_cli("category", "add", "--name", "월급")[0], 0)

        code, out, _ = self.run_cli(
            "add", "--date", "2024-01-05", "--type", "expense",
            "--category", "식비", "--amount", "12000", "--memo", "점심", "--tags", "외식,점심",
        )
        self.assertEqual(code, 0)
        self.assertIn("[저장 완료] id=TX-1", out)

        self.run_cli("add", "--date", "2024-01-25", "--type", "income", "--category", "월급", "--amount", "3000000")

        code, out, _ = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertIn("TX-2", out)
        self.assertIn("TX-1", out)
        self.assertLess(out.index("TX-2"), out.index("TX-1"))  # 최신순

        code, out, _ = self.run_cli("search", "--month", "2024-01", "--type", "expense")
        self.assertIn("TX-1", out)
        self.assertNotIn("TX-2", out)

        self.run_cli("budget", "set", "--month", "2024-01", "--amount", "10000")
        code, out, _ = self.run_cli("summary", "--month", "2024-01")
        self.assertEqual(code, 0)
        self.assertIn("총 수입", out)
        self.assertIn("[경고]", out)  # 12,000 > 10,000 예산 초과

        code, out, _ = self.run_cli("update", "--id", "TX-1", "--amount", "5000")
        self.assertIn("[수정 완료] id=TX-1", out)

        code, out, _ = self.run_cli("summary", "--month", "2024-01")
        self.assertNotIn("[경고]", out)

        code, out, _ = self.run_cli("delete", "--id", "TX-1", "--yes")
        self.assertIn("[삭제 완료]", out)

        code, out, _ = self.run_cli("compact")
        self.assertEqual(code, 0)

        code, out, _ = self.run_cli("list")
        self.assertIn("TX-2", out)
        self.assertNotIn("TX-1", out)

    def test_09_summary_reports_no_data(self) -> None:
        code, out, _ = self.run_cli("summary", "--month", "2030-01")
        self.assertEqual(code, 0)
        self.assertIn("데이터 없음", out)

    def test_10_category_remove_blocked_when_in_use(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        self.run_cli("add", "--date", "2024-01-05", "--type", "expense", "--category", "식비", "--amount", "1000")
        code, _, err = self.run_cli("category", "remove", "--name", "식비")
        self.assertEqual(code, 1)
        self.assertIn("사용 중", err)

    def test_11_import_export_roundtrip(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        source = Path(self._tmp.name) / "in.csv"
        source.write_text(
            "date,type,category,amount,memo,tags\n"
            "2024-03-01,expense,식비,8000,점심,외식\n"
            "2024-03-02,expense,없는것,1000,bad,\n",
            encoding="utf-8",
        )
        code, out, _ = self.run_cli("import", "--from", str(source))
        self.assertEqual(code, 0)
        self.assertIn("imported=1, skipped=1", out)

        target = Path(self._tmp.name) / "out.csv"
        code, out, _ = self.run_cli("export", "--out", str(target), "--month", "2024-03")
        self.assertEqual(code, 0)
        self.assertIn("(1 records)", out)
        self.assertEqual(
            target.read_text(encoding="utf-8").splitlines()[0], "date,type,category,amount,memo,tags"
        )

    def test_12_backup_creates_timestamped_folder(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        code, out, _ = self.run_cli("backup")
        self.assertEqual(code, 0)
        backups = list((self.data_dir / "backup").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertTrue((backups[0] / "categories.jsonl").exists())

    # ---------------------------------------------------------------- 대화형
    def test_13_interactive_add_reprompts_on_invalid_input(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        answers = iter([
            "2024-13-99",   # 잘못된 날짜 → 재입력
            "2024-01-07",
            "foo",          # 잘못된 타입 → 재입력
            "expense",
            "없는것",        # 잘못된 카테고리 → 재입력
            "1",            # 번호로 선택
            "-500",         # 잘못된 금액 → 재입력
            "5500",
            "커피",
            "카페,간식",
        ])
        with mock.patch("builtins.input", lambda *a: next(answers)):
            code, out, _ = self.run_cli("add")
        self.assertEqual(code, 0)
        self.assertIn("[저장 완료] id=TX-1", out)

        code, out, _ = self.run_cli("search", "--tag", "간식")
        self.assertIn("5,500", out)

    def test_14_interactive_add_cancelled_returns_130(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        with mock.patch("builtins.input", side_effect=KeyboardInterrupt):
            code, _, err = self.run_cli("add")
        self.assertEqual(code, 130)
        self.assertIn("[중단]", err)

    def test_15_delete_hints_compact_when_orphan_ratio_high(self) -> None:
        # delete 는 로그(transactions.jsonl)를 건드리지 않고 인덱스 슬롯만 0으로
        # 만들기 때문에, delete 가 쌓여도 update 와 마찬가지로 고아 데이터가 늘어난다.
        # 4건 중 2건(50%)을 지우기 전까지는 안내가 없다가, 50%를 넘는 순간부터 뜬다.
        self.run_cli("category", "add", "--name", "식비")
        for day in range(1, 5):
            self.run_cli(
                "add", "--date", f"2024-01-0{day}", "--type", "expense",
                "--category", "식비", "--amount", "1000",
            )

        _, out, _ = self.run_cli("delete", "--id", "TX-1", "--yes")
        self.assertNotIn("compact", out)  # 아직 25% → compact 안내 없음

        _, out, _ = self.run_cli("delete", "--id", "TX-2", "--yes")
        self.assertIn("compact", out)  # 정확히 50% 도달 → compact 안내 등장


if __name__ == "__main__":
    unittest.main()
