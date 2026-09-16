"""검증/검색/요약/예산/CSV 서비스 단위 테스트."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from budget_app.models import DuplicateError, NotFoundError, Transaction, ValidationError
from budget_app.repository import TransactionRepository
from budget_app.services import (
    BudgetService,
    CsvService,
    RecurringService,
    SearchCriteria,
    TransactionService,
    format_tags,
    parse_tags,
    validate_amount,
    validate_date,
    validate_month,
    validate_type,
)
from budget_app.stores import BudgetStore, CategoryStore, RecurringStore


class ValidationTestCase(unittest.TestCase):
    def test_validate_date(self) -> None:
        self.assertEqual(validate_date(" 2024-01-05 "), "2024-01-05")
        for bad in ("2024-13-01", "2024-02-30", "24-01-01", "2024/01/01", "2024-1-5", ""):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                validate_date(bad)

    def test_validate_month(self) -> None:
        self.assertEqual(validate_month("2024-01"), "2024-01")
        with self.assertRaises(ValidationError):
            validate_month("2024-1")

    def test_validate_type(self) -> None:
        self.assertEqual(validate_type("INCOME"), "income")
        with self.assertRaises(ValidationError):
            validate_type("transfer")

    def test_validate_amount(self) -> None:
        self.assertEqual(validate_amount("12,000"), 12000)
        for bad in ("0", "-1", "abc", "1.5"):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                validate_amount(bad)

    def test_tags_roundtrip(self) -> None:
        self.assertEqual(parse_tags(" 외식 , 점심 ,, 외식 "), ["외식", "점심"])
        self.assertEqual(format_tags(["외식", "점심"]), "외식,점심")
        self.assertEqual(parse_tags(None), [])


class ServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        self.repo = TransactionRepository(self.data_dir)
        self.repo.ensure_files()
        self.categories = CategoryStore(self.data_dir)
        self.budgets = BudgetStore(self.data_dir)
        self.service = TransactionService(self.repo, self.categories)
        self.budget_service = BudgetService(self.budgets)
        for name in ("식비", "교통", "월급"):
            self.categories.add(name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def add(self, date: str, type_: str, category: str, amount: int, memo: str = "", tags: str = "") -> Transaction:
        return self.service.add(
            self.service.build_transaction(date, type_, category, amount, memo, tags)
        )

    def test_blocks_add_without_categories(self) -> None:
        empty_dir = Path(tempfile.mkdtemp())
        service = TransactionService(TransactionRepository(empty_dir), CategoryStore(empty_dir))
        with self.assertRaises(ValidationError) as ctx:
            service.build_transaction("2024-01-01", "expense", "식비", 1000)
        self.assertIn("카테고리", ctx.exception.message)

    def test_unknown_category_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self.service.build_transaction("2024-01-01", "expense", "없는것", 1000)

    def test_duplicate_category_rejected(self) -> None:
        with self.assertRaises(DuplicateError):
            self.categories.add("식비")

    def test_search_combines_conditions_with_and(self) -> None:
        self.add("2024-01-05", "expense", "식비", 12000, "점심 김밥", "외식,점심")
        self.add("2024-01-06", "expense", "교통", 1250, "버스")
        self.add("2024-02-01", "expense", "식비", 30000, "저녁 김밥", "외식")

        result = list(self.service.search(SearchCriteria(month="2024-01", category="식비")))
        self.assertEqual([tx.memo for tx in result], ["점심 김밥"])

        result = list(self.service.search(SearchCriteria(query="김밥")))
        self.assertEqual(len(result), 2)
        self.assertEqual([tx.id for tx in result], [3, 1])  # 최신순

        result = list(self.service.search(SearchCriteria(tag="점심")))
        self.assertEqual(len(result), 1)

        result = list(self.service.search(SearchCriteria(date_from="2024-01-06", date_to="2024-02-01")))
        self.assertEqual(len(result), 2)

    def test_search_limit_stops_early(self) -> None:
        for day in range(1, 6):
            self.add(f"2024-01-{day:02d}", "expense", "식비", 1000)
        self.assertEqual(len(list(self.service.search(SearchCriteria(type="expense"), limit=2))), 2)
        self.assertEqual(len(list(self.service.iter_latest(3))), 3)

    def test_update_keeps_unspecified_fields(self) -> None:
        tx = self.add("2024-01-05", "expense", "식비", 12000, "점심", "외식")
        before, after = self.service.update(tx.id, amount=5000)
        self.assertEqual(after.amount, 5000)
        self.assertEqual(after.memo, before.memo)
        self.assertEqual(after.date, before.date)
        self.assertEqual(after.tags, ["외식"])

    def test_update_validates_new_values(self) -> None:
        tx = self.add("2024-01-05", "expense", "식비", 12000)
        for kwargs in ({"amount": "-1"}, {"date": "2024-99-99"}, {"type_": "x"}, {"category": "없음"}, {}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError):
                self.service.update(tx.id, **kwargs)

    def test_update_and_delete_missing_id(self) -> None:
        with self.assertRaises(NotFoundError):
            self.service.update(99, amount=100)
        with self.assertRaises(NotFoundError):
            self.service.delete(99)

    def test_summary_totals_and_top_n(self) -> None:
        self.add("2024-01-05", "expense", "식비", 12000)
        self.add("2024-01-06", "expense", "식비", 8000)
        self.add("2024-01-07", "expense", "교통", 1250)
        self.add("2024-01-25", "income", "월급", 3000000)
        self.add("2024-02-01", "expense", "식비", 5000)

        summary = self.service.summarize_month("2024-01", top=1)
        self.assertEqual(summary.total_income, 3000000)
        self.assertEqual(summary.total_expense, 21250)
        self.assertEqual(summary.balance, 2978750)
        self.assertEqual(summary.count, 4)
        self.assertEqual(summary.expense_by_category, [("식비", 20000)])

    def test_summary_empty_month(self) -> None:
        summary = self.service.summarize_month("2030-01", top=3)
        self.assertTrue(summary.is_empty)
        self.assertEqual(summary.total_expense, 0)

    def test_budget_usage_and_overrun(self) -> None:
        self.budget_service.set_budget("2024-01", 10000)
        usage = self.budget_service.usage("2024-01", 5000)
        self.assertEqual(usage.percent, 50.0)
        self.assertFalse(usage.is_over)
        self.assertEqual(usage.remaining, 5000)

        over = self.budget_service.usage("2024-01", 15000)
        self.assertTrue(over.is_over)
        self.assertEqual(over.remaining, -5000)
        self.assertIsNone(self.budget_service.usage("2030-01", 0))

    def test_budget_overwrite_same_month(self) -> None:
        self.budget_service.set_budget("2024-01", 10000)
        budget, previous = self.budget_service.set_budget("2024-01", 20000)
        self.assertEqual(budget.amount, 20000)
        self.assertEqual(previous.amount, 10000)
        self.assertEqual(len(self.budget_service.list_all()), 1)

    def test_budget_rejects_bad_input(self) -> None:
        with self.assertRaises(ValidationError):
            self.budget_service.set_budget("2024-1", 1000)
        with self.assertRaises(ValidationError):
            self.budget_service.set_budget("2024-01", -100)

    def test_category_in_use_detection(self) -> None:
        self.add("2024-01-05", "expense", "식비", 12000)
        self.assertIsNotNone(self.service.is_category_in_use("식비"))
        self.assertIsNone(self.service.is_category_in_use("교통"))

    def test_csv_import_skips_invalid_rows(self) -> None:
        csv_path = self.data_dir / "in.csv"
        csv_path.write_text(
            "date,type,category,amount,memo,tags\n"
            "2024-03-01,expense,식비,8000,\"점심, 회사\",\"외식,점심\"\n"
            "2024-13-40,expense,식비,1000,bad date,\n"
            "2024-03-02,expense,없는것,1000,bad category,\n"
            "2024-03-03,expense,식비,0,bad amount,\n",
            encoding="utf-8",
        )
        report = CsvService(self.service).import_csv(csv_path)
        self.assertEqual(report.imported, 1)
        self.assertEqual(report.skipped, 3)
        self.assertEqual(len(report.errors), 3)
        tx = self.repo.get_transaction(1)
        self.assertEqual(tx.memo, "점심, 회사")
        self.assertEqual(tx.tags, ["외식", "점심"])

    def test_csv_roundtrip(self) -> None:
        self.add("2024-03-01", "expense", "식비", 8000, "점심, 회사", "외식,점심")
        out = self.data_dir / "out.csv"
        csv_service = CsvService(self.service)
        count = csv_service.export_csv(out, self.service.search(SearchCriteria(month="2024-03")))
        self.assertEqual(count, 1)

        report = csv_service.import_csv(out)
        self.assertEqual(report.imported, 1)
        self.assertEqual(self.repo.get_transaction(2).memo, "점심, 회사")

    def test_csv_import_missing_file_and_header(self) -> None:
        csv_service = CsvService(self.service)
        with self.assertRaises(ValidationError):
            csv_service.import_csv(self.data_dir / "nope.csv")
        bad = self.data_dir / "bad.csv"
        bad.write_text("a,b\n1,2\n", encoding="utf-8")
        with self.assertRaises(ValidationError):
            csv_service.import_csv(bad)

    def test_recurring_apply_is_idempotent(self) -> None:
        recurring = RecurringService(RecurringStore(self.data_dir), self.service)
        recurring.add(day=31, type_="income", category="월급", amount=3000000, memo="급여")
        created, skipped = recurring.apply_month("2024-02")
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].date, "2024-02-29")  # 윤년 말일로 보정
        created, skipped = recurring.apply_month("2024-02")
        self.assertEqual((len(created), len(skipped)), (0, 1))


if __name__ == "__main__":
    unittest.main()
