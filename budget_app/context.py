"""커맨드 실행에 필요한 저장소/서비스를 한 번에 조립하는 컨텍스트."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .file_services import BackupService, CsvService
from .repository import TransactionRepository
from .services import BudgetService, RecurringService, TransactionService
from .stores import BudgetStore, CategoryStore, RecurringStore


@dataclass(slots=True)
class AppContext:
    """커맨드 실행에 필요한 저장소/서비스 묶음."""

    data_dir: Path
    repo: TransactionRepository
    categories: CategoryStore
    budgets: BudgetStore
    recurring_store: RecurringStore
    transactions: TransactionService
    budget_service: BudgetService
    csv_service: CsvService
    backup_service: BackupService
    recurring_service: RecurringService

    @classmethod
    def create(cls, data_dir: Path) -> "AppContext":
        """저장 폴더를 준비하고(없으면 생성) 서비스 객체들을 조립한다."""
        data_dir = Path(data_dir).expanduser()
        repo = TransactionRepository(data_dir)
        categories = CategoryStore(data_dir)
        budgets = BudgetStore(data_dir)
        recurring_store = RecurringStore(data_dir)
        repo.ensure_files()
        categories.ensure_file()
        budgets.ensure_file()
        recurring_store.ensure_file()
        tx_service = TransactionService(repo, categories)
        return cls(
            data_dir=data_dir,
            repo=repo,
            categories=categories,
            budgets=budgets,
            recurring_store=recurring_store,
            transactions=tx_service,
            budget_service=BudgetService(budgets),
            csv_service=CsvService(tx_service),
            backup_service=BackupService(data_dir),
            recurring_service=RecurringService(recurring_store, tx_service),
        )
