"""CLI 통합 테스트: 종료 코드, 오류 출력 형식, 대화형 재입력."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

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

    # ---------------------------------------------------------------- 기본 동작
    def test_creates_data_files_on_first_run(self) -> None:
        code, _, _ = self.run_cli("list")
        self.assertEqual(code, 0)
        for name in ("transactions.jsonl", "transactions.idx", "categories.jsonl", "budgets.jsonl"):
            self.assertTrue((self.data_dir / name).exists(), name)

    def test_no_command_prints_help(self) -> None:
        code, out, _ = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("usage:", out)

    def test_help_available_for_every_subcommand(self) -> None:
        for cmd in (
            "add", "list", "search", "summary", "budget", "category",
            "update", "delete", "import", "export", "compact", "backup", "recurring",
        ):
            with self.subTest(cmd=cmd), self.assertRaises(SystemExit) as ctx:
                self.run_cli(cmd, "--help")
            self.assertEqual(ctx.exception.code, 0)

    # ---------------------------------------------------------------- 오류 처리
    def test_add_blocked_without_category(self) -> None:
        code, _, err = self.run_cli(
            "add", "--date", "2024-01-01", "--type", "expense", "--category", "식비", "--amount", "1000"
        )
        self.assertEqual(code, 1)
        self.assertIn("[오류] 등록된 카테고리가 없습니다.", err)
        self.assertIn("[힌트]", err)

    def test_missing_subcommand_returns_error(self) -> None:
        code, _, err = self.run_cli("category")
        self.assertEqual(code, 1)
        self.assertIn("[힌트]", err)

    def test_unknown_id_returns_exit_code_1(self) -> None:
        code, _, err = self.run_cli("delete", "--id", "TX-999", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("TX-999", err)

    def test_bad_id_format(self) -> None:
        code, _, err = self.run_cli("update", "--id", "abc", "--amount", "100")
        self.assertEqual(code, 1)
        self.assertIn("[오류]", err)

    def test_export_requires_month_or_range(self) -> None:
        code, _, err = self.run_cli("export", "--out", str(self.data_dir / "x.csv"))
        self.assertEqual(code, 1)
        self.assertIn("--month", err)

    # ---------------------------------------------------------------- 시나리오
    def test_full_workflow(self) -> None:
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

    def test_summary_reports_no_data(self) -> None:
        code, out, _ = self.run_cli("summary", "--month", "2030-01")
        self.assertEqual(code, 0)
        self.assertIn("데이터 없음", out)

    def test_category_remove_blocked_when_in_use(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        self.run_cli("add", "--date", "2024-01-05", "--type", "expense", "--category", "식비", "--amount", "1000")
        code, _, err = self.run_cli("category", "remove", "--name", "식비")
        self.assertEqual(code, 1)
        self.assertIn("사용 중", err)

    def test_import_export_roundtrip(self) -> None:
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

    def test_backup_creates_timestamped_folder(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        code, out, _ = self.run_cli("backup")
        self.assertEqual(code, 0)
        backups = list((self.data_dir / "backup").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertTrue((backups[0] / "categories.jsonl").exists())

    # ---------------------------------------------------------------- 대화형
    def test_interactive_add_reprompts_on_invalid_input(self) -> None:
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

    def test_interactive_add_cancelled_returns_130(self) -> None:
        self.run_cli("category", "add", "--name", "식비")
        with mock.patch("builtins.input", side_effect=KeyboardInterrupt):
            code, _, err = self.run_cli("add")
        self.assertEqual(code, 130)
        self.assertIn("[중단]", err)


if __name__ == "__main__":
    unittest.main()
