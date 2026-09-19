"""검증/검색/요약/예산/CSV 서비스 단위 테스트."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

# `python tests/test_services.py` 로 직접 실행할 때도 budget_app 을 찾을 수 있도록
# 프로젝트 루트를 sys.path 에 넣는다 (자세한 이유는 test_repository.py 상단 주석 참고).
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

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
    # 번호는 실행 순서 강제가 아니라 가독성을 위한 정렬이다(각 테스트는 독립적).
    # 두 자리 0-padding 은 문자열 정렬 시 "10"이 "2"보다 앞에 오는 걸 막기 위함.

    # 기능: validate_date — 앞뒤 공백 제거 및 정규화, 달력상 불가능한
    # 날짜(2월 30일 등)·형식 어긋난 날짜를 모두 거부하는지 검증
    def test_00_validate_date(self) -> None:
        self.assertEqual(validate_date(" 2024-01-05 "), "2024-01-05")
        for bad in ("2024-13-01", "2024-02-30", "24-01-01", "2024/01/01", "2024-1-5", ""):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                validate_date(bad)

    # 기능: validate_month — YYYY-MM 형식 정규화 및 자릿수 어긋난 값(2024-1)
    # 거부를 검증
    def test_01_validate_month(self) -> None:
        self.assertEqual(validate_month("2024-01"), "2024-01")
        with self.assertRaises(ValidationError):
            validate_month("2024-1")

    # 기능: validate_type — 대소문자 무관하게 income/expense 로 정규화되고,
    # 그 외 값은 거부되는지 검증
    def test_02_validate_type(self) -> None:
        self.assertEqual(validate_type("INCOME"), "income")
        with self.assertRaises(ValidationError):
            validate_type("transfer")

    # 기능: validate_amount — 콤마 포함 숫자 문자열을 정수로 변환하고,
    # 0/음수/숫자아님/소수는 모두 거부되는지 검증
    def test_03_validate_amount(self) -> None:
        self.assertEqual(validate_amount("12,000"), 12000)
        for bad in ("0", "-1", "abc", "1.5"):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                validate_amount(bad)

    # 기능: parse_tags/format_tags — 공백 제거·중복 제거된 태그 리스트
    # 파싱과, 리스트를 다시 쉼표 문자열로 합치는 왕복 변환을 검증
    def test_04_tags_roundtrip(self) -> None:
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

    # 번호는 실행 순서 강제가 아니라 가독성을 위한 정렬이다. setUp() 이 테스트마다
    # 새 임시 폴더 + 카테고리 3종을 준비해주므로 각 테스트는 서로 완전히 독립적이다.
    # (카테고리 등록이 안 된 상태에서 add 가 막히는 것을 보고 싶다면 test_00 처럼
    #  같은 테스트 안에서 별도의 "빈" 서비스를 직접 만들어 검증한다.)

    # 기능: TransactionService.build_transaction — 카테고리가 하나도 등록
    # 되지 않은 서비스에서는 거래 생성 자체가 ValidationError 로 막히는지 검증
    def test_00_blocks_add_without_categories(self) -> None:
        empty_dir = Path(tempfile.mkdtemp())
        service = TransactionService(TransactionRepository(empty_dir), CategoryStore(empty_dir))
        with self.assertRaises(ValidationError) as ctx:
            service.build_transaction("2024-01-01", "expense", "식비", 1000)
        self.assertIn("카테고리", ctx.exception.message)

    # 기능: TransactionService.validate_category — 등록되지 않은 카테고리
    # 이름은 거부되는지 검증
    def test_01_unknown_category_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self.service.build_transaction("2024-01-01", "expense", "없는것", 1000)

    # 기능: CategoryStore.add — 이미 등록된 이름을 다시 추가하면
    # DuplicateError 가 발생하는지 검증
    def test_02_duplicate_category_rejected(self) -> None:
        with self.assertRaises(DuplicateError):
            self.categories.add("식비")

    # 기능: TransactionService.search — month/category/query/tag/date 조건이
    # 각각 올바르게 필터링되고, 여러 결과는 최신순으로 정렬되는지 검증
    def test_03_search_combines_conditions_with_and(self) -> None:
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

    # 기능: TransactionService.search/iter_latest — limit 을 지정하면
    # 제너레이터가 그 건수만큼만 소비하고 조기 종료되는지 검증
    def test_04_search_limit_stops_early(self) -> None:
        for day in range(1, 6):
            self.add(f"2024-01-{day:02d}", "expense", "식비", 1000)
        self.assertEqual(len(list(self.service.search(SearchCriteria(type="expense"), limit=2))), 2)
        self.assertEqual(len(list(self.service.iter_latest(3))), 3)

    # 기능: TransactionService.update — 지정한 필드(amount)만 바뀌고 나머지
    # 필드(memo/date/tags)는 그대로 유지되는지 검증
    def test_05_update_keeps_unspecified_fields(self) -> None:
        tx = self.add("2024-01-05", "expense", "식비", 12000, "점심", "외식")
        before, after = self.service.update(tx.id, amount=5000)
        self.assertEqual(after.amount, 5000)
        self.assertEqual(after.memo, before.memo)
        self.assertEqual(after.date, before.date)
        self.assertEqual(after.tags, ["외식"])

    # 기능: TransactionService.update — 새로 지정하는 값도 add 와 동일한
    # 검증 규칙(금액/날짜/타입/카테고리, 빈 변경사항)이 적용되는지 검증
    def test_06_update_validates_new_values(self) -> None:
        tx = self.add("2024-01-05", "expense", "식비", 12000)
        for kwargs in ({"amount": "-1"}, {"date": "2024-99-99"}, {"type_": "x"}, {"category": "없음"}, {}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError):
                self.service.update(tx.id, **kwargs)

    # 기능: TransactionService.update/delete — 존재하지 않는 id 를 대상으로
    # 하면 둘 다 NotFoundError 를 발생시키는지 검증
    def test_07_update_and_delete_missing_id(self) -> None:
        with self.assertRaises(NotFoundError):
            self.service.update(99, amount=100)
        with self.assertRaises(NotFoundError):
            self.service.delete(99)

    # 기능: TransactionService.summarize_month — 총수입/총지출/잔액/거래
    # 건수와 카테고리별 지출 TOP N 집계가 정확한지 검증
    def test_08_summary_totals_and_top_n(self) -> None:
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

    # 기능: TransactionService.summarize_month — 데이터가 없는 달은
    # is_empty=True, total_expense=0 으로 집계되는지 검증
    def test_09_summary_empty_month(self) -> None:
        summary = self.service.summarize_month("2030-01", top=3)
        self.assertTrue(summary.is_empty)
        self.assertEqual(summary.total_expense, 0)

    # 기능: BudgetService.usage — 예산 대비 사용률/초과여부/잔여액 계산과,
    # 예산 미설정 달은 None 을 반환하는지 검증
    def test_10_budget_usage_and_overrun(self) -> None:
        self.budget_service.set_budget("2024-01", 10000)
        usage = self.budget_service.usage("2024-01", 5000)
        self.assertEqual(usage.percent, 50.0)
        self.assertFalse(usage.is_over)
        self.assertEqual(usage.remaining, 5000)

        over = self.budget_service.usage("2024-01", 15000)
        self.assertTrue(over.is_over)
        self.assertEqual(over.remaining, -5000)
        self.assertIsNone(self.budget_service.usage("2030-01", 0))

    # 기능: BudgetService.set_budget — 같은 달을 다시 설정하면 덮어쓰고,
    # 이전 예산을 반환값으로 돌려주며 목록에는 1건만 남는지 검증
    def test_11_budget_overwrite_same_month(self) -> None:
        self.budget_service.set_budget("2024-01", 10000)
        budget, previous = self.budget_service.set_budget("2024-01", 20000)
        self.assertEqual(budget.amount, 20000)
        self.assertEqual(previous.amount, 10000)
        self.assertEqual(len(self.budget_service.list_all()), 1)

    # 기능: BudgetService.set_budget — 잘못된 월 형식과 음수 금액이 모두
    # 거부되는지 검증
    def test_12_budget_rejects_bad_input(self) -> None:
        with self.assertRaises(ValidationError):
            self.budget_service.set_budget("2024-1", 1000)
        with self.assertRaises(ValidationError):
            self.budget_service.set_budget("2024-01", -100)

    # 기능: TransactionService.is_category_in_use — 사용 중인/사용하지 않는
    # 카테고리를 정확히 구분해 감지하는지 검증
    def test_13_category_in_use_detection(self) -> None:
        self.add("2024-01-05", "expense", "식비", 12000)
        self.assertIsNotNone(self.service.is_category_in_use("식비"))
        self.assertIsNone(self.service.is_category_in_use("교통"))

    # 기능: CsvService.import_csv — 잘못된 행(날짜/카테고리/금액 오류)은
    # 건너뛰고, 콤마가 포함된 따옴표 메모/태그는 손실 없이 파싱되는지 검증
    def test_14_csv_import_skips_invalid_rows(self) -> None:
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

    # 기능: CsvService — export 로 내보낸 CSV 를 다시 import 했을 때
    # 콤마 포함 메모까지 손실 없이 왕복되는지 검증
    def test_15_csv_roundtrip(self) -> None:
        self.add("2024-03-01", "expense", "식비", 8000, "점심, 회사", "외식,점심")
        out = self.data_dir / "out.csv"
        csv_service = CsvService(self.service)
        count = csv_service.export_csv(out, self.service.search(SearchCriteria(month="2024-03")))
        self.assertEqual(count, 1)

        report = csv_service.import_csv(out)
        self.assertEqual(report.imported, 1)
        self.assertEqual(self.repo.get_transaction(2).memo, "점심, 회사")

    # 기능: CsvService.import_csv — 파일이 없거나 필수 헤더가 없는 CSV 는
    # ValidationError 로 처리되는지 검증
    def test_16_csv_import_missing_file_and_header(self) -> None:
        csv_service = CsvService(self.service)
        with self.assertRaises(ValidationError):
            csv_service.import_csv(self.data_dir / "nope.csv")
        bad = self.data_dir / "bad.csv"
        bad.write_text("a,b\n1,2\n", encoding="utf-8")
        with self.assertRaises(ValidationError):
            csv_service.import_csv(bad)

    # 기능: RecurringService.apply_month — 윤년 2월처럼 규칙의 일자(31일)가
    # 해당 월에 없으면 말일(29일)로 보정되고, 같은 달 재적용은 멱등적으로
    # 건너뛰는지 검증
    def test_17_recurring_apply_is_idempotent(self) -> None:
        recurring = RecurringService(RecurringStore(self.data_dir), self.service)
        recurring.add(day=31, type_="income", category="월급", amount=3000000, memo="급여")
        created, skipped = recurring.apply_month("2024-02")
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].date, "2024-02-29")  # 윤년 말일로 보정
        created, skipped = recurring.apply_month("2024-02")
        self.assertEqual((len(created), len(skipped)), (0, 1))


if __name__ == "__main__":
    unittest.main()
