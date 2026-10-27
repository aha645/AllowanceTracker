"""커맨드 실행에 필요한 서비스를 한 번에 조립하는 컨텍스트."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .file_services import BackupService, CsvService
from .services import BudgetService, CategoryService, RecurringService, TransactionService
from .stores import BudgetStore, CategoryStore, RecurringStore
from .transaction_store import TransactionStore


@dataclass(slots=True)
class AppContext:
    """커맨드가 쓰는 서비스 묶음.

    저장소(store)는 여기서 한 번만 만들어 서비스들이 나눠 쓰고, 커맨드에는 노출하지 않는다.
    커맨드는 서비스만 거쳐 데이터를 다룬다.
    """

    data_dir: Path
    recovered: list[str]  # 시작할 때 지난 실행의 중단을 자동으로 바로잡은 내용(커맨드 실행 전에 안내)
    transactions: TransactionService
    categories: CategoryService
    budgets: BudgetService
    recurring: RecurringService
    csv: CsvService
    backup: BackupService

    @classmethod
    def create(cls, data_dir: Path) -> "AppContext":
        """저장 폴더를 준비하고(없으면 생성) 저장소와 서비스를 조립한다."""
        data_dir = Path(data_dir).expanduser()
        transaction_store = TransactionStore(data_dir)
        category_store = CategoryStore(data_dir)
        budget_store = BudgetStore(data_dir)
        recurring_store = RecurringStore(data_dir)
        transaction_store.ensure_files()  # 지난 실행이 중간에 꺼졌으면 여기서 복구한다
        category_store.ensure_file()
        budget_store.ensure_file()
        recurring_store.ensure_file()
        categories = CategoryService(category_store, transaction_store, recurring_store)
        categories.ensure_counts()
        transactions = TransactionService(transaction_store, categories)
        return cls(
            data_dir=data_dir,
            recovered=list(transaction_store.recovered),
            transactions=transactions,
            categories=categories,
            budgets=BudgetService(budget_store),
            recurring=RecurringService(recurring_store, transactions, categories),
            csv=CsvService(transactions, data_dir),
            backup=BackupService(data_dir),
        )
