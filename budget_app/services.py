"""비즈니스 로직: 거래 CRUD·검색·월별 요약, 예산 계산, 반복 거래 규칙.

저장 방식(파일 포맷)은 repository/stores 가, 사용자 입출력은 cli 가 맡는다.
이 모듈은 그 사이에서 "규칙"만 담당한다. 입력 검증은 validators, CSV/백업은 file_services.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterator

from .models import (
    TYPE_INCOME,
    Budget,
    MonthlySummary,
    RecurringRule,
    Transaction,
    ValidationError,
)
from .repository import TransactionRepository
from .stores import BudgetStore, CategoryStore, RecurringStore
from .validators import (
    format_tags,
    parse_tags,
    validate_amount,
    validate_date,
    validate_day,
    validate_month,
    validate_type,
)


@dataclass(slots=True)
class SearchCriteria:
    """search/export 공통 필터 조건. 지정된 조건들은 AND 로 결합된다."""

    date_from: str | None = None
    date_to: str | None = None
    month: str | None = None
    category: str | None = None
    type: str | None = None
    query: str | None = None
    tag: str | None = None

    def matches(self, tx: Transaction) -> bool:
        if self.month and tx.month != self.month:
            return False
        if self.date_from and tx.date < self.date_from:
            return False
        if self.date_to and tx.date > self.date_to:
            return False
        if self.category and tx.category != self.category:
            return False
        if self.type and tx.type != self.type:
            return False
        if self.query and self.query.lower() not in tx.memo.lower():
            return False
        if self.tag and self.tag not in tx.tags:
            return False
        return True

    @property
    def is_empty(self) -> bool:
        return not any(
            (self.date_from, self.date_to, self.month, self.category, self.type, self.query, self.tag)
        )


class TransactionService:
    """거래 관련 비즈니스 로직(검증 + 저장소 호출 + 집계)."""

    def __init__(self, repo: TransactionRepository, categories: CategoryStore) -> None:
        self.repo: TransactionRepository = repo
        self.categories: CategoryStore = categories

    # ------------------------------------------------------------- 검증 보조
    def ensure_categories_exist(self) -> None:
        """카테고리가 하나도 없으면 거래 등록 자체를 막는다(정책 안 B)."""
        if self.categories.is_empty():
            raise ValidationError(
                "등록된 카테고리가 없습니다.",
                'category add 로 먼저 등록하세요. 예: python -m budget_app category add --name "식비"',
            )

    def validate_category(self, value: str) -> str:
        """등록된 카테고리인지 확인한다(자동 생성하지 않음)."""
        name = (value or "").strip()
        if not name:
            raise ValidationError(
                "카테고리를 입력해야 합니다.",
                "category list 로 등록된 카테고리를 확인하세요.",
            )
        if not self.categories.exists(name):
            known = ", ".join(self.categories.names()) or "(없음)"
            raise ValidationError(
                f"등록되지 않은 카테고리입니다: '{name}'",
                f"등록된 카테고리: {known} / 새로 만들려면 category add 를 사용하세요.",
            )
        return name

    def build_transaction(
        self,
        date: str,
        type_: str,
        category: str,
        amount: str | int,
        memo: str = "",
        tags: str | list[str] | None = None,
    ) -> Transaction:
        """원시 입력값을 검증해 (id 미부여) Transaction 으로 만든다."""
        self.ensure_categories_exist()
        tag_list = parse_tags(tags if tags is None or isinstance(tags, str) else format_tags(tags))
        return Transaction(
            id=0,
            date=validate_date(date),
            type=validate_type(type_),
            category=self.validate_category(category),
            amount=validate_amount(amount),
            memo=(memo or "").strip(),
            tags=tag_list,
        )

    # ------------------------------------------------------------- CRUD
    def add(self, tx: Transaction) -> Transaction:
        return self.repo.append_transaction(tx)

    def update(
        self,
        tx_id: int,
        *,
        date: str | None = None,
        type_: str | None = None,
        category: str | None = None,
        amount: str | int | None = None,
        memo: str | None = None,
        tags: str | None = None,
    ) -> tuple[Transaction, Transaction]:
        """지정한 필드만 바꿔 저장하고 (이전, 이후) 거래를 돌려준다."""
        current = self.repo.get_transaction(tx_id)
        changes: dict[str, object] = {}
        if date is not None:
            changes["date"] = validate_date(date)
        if type_ is not None:
            changes["type"] = validate_type(type_)
        if category is not None:
            changes["category"] = self.validate_category(category)
        if amount is not None:
            changes["amount"] = validate_amount(amount)
        if memo is not None:
            changes["memo"] = memo.strip()
        if tags is not None:
            changes["tags"] = parse_tags(tags)
        if not changes:
            raise ValidationError(
                "수정할 항목이 지정되지 않았습니다.",
                "--date/--type/--category/--amount/--memo/--tags 중 최소 하나를 지정하세요.",
            )
        updated = current.replace_fields(**changes)
        self.repo.update_transaction(updated)
        return current, updated

    def delete(self, tx_id: int) -> Transaction:
        return self.repo.delete_transaction(tx_id)

    # ------------------------------------------------------------- 조회
    def iter_latest(self, limit: int | None = None) -> Iterator[Transaction]:
        """최신순 거래를 limit 건까지 스트리밍한다."""
        for i, tx in enumerate(self.repo.iter_latest_transactions()):
            if limit is not None and i >= limit:
                return
            yield tx

    def search(self, criteria: SearchCriteria, limit: int | None = None) -> Iterator[Transaction]:
        """조건에 맞는 거래를 최신순으로 스트리밍한다(AND 결합)."""
        found = 0
        for tx in self.repo.iter_latest_transactions():
            if not criteria.matches(tx):
                continue
            yield tx
            found += 1
            if limit is not None and found >= limit:
                return

    def is_category_in_use(self, name: str) -> Transaction | None:
        """해당 카테고리를 쓰는 거래를 하나라도 찾으면 그 거래를 돌려준다(조기 종료)."""
        for tx in self.repo.iter_latest_transactions():
            if tx.category == name:
                return tx
        return None

    def summarize_month(self, month: str, top: int) -> MonthlySummary:
        """월별 수입/지출/잔액과 카테고리별 지출 TOP N 을 집계한다."""
        total_income = 0
        total_expense = 0
        count = 0
        by_category: dict[str, int] = {}
        for tx in self.repo.iter_latest_transactions():
            if tx.month != month:
                continue
            count += 1
            if tx.type == TYPE_INCOME:
                total_income += tx.amount
            else:
                total_expense += tx.amount
                by_category[tx.category] = by_category.get(tx.category, 0) + tx.amount
        ranked = sorted(by_category.items(), key=lambda item: (-item[1], item[0]))[: max(top, 0)]
        return MonthlySummary(
            month=month,
            total_income=total_income,
            total_expense=total_expense,
            count=count,
            expense_by_category=ranked,
        )


@dataclass(slots=True)
class BudgetUsage:
    """예산 대비 지출 사용률."""

    month: str
    budget: int
    spent: int

    @property
    def ratio(self) -> float:
        return self.spent / self.budget if self.budget else 0.0

    @property
    def percent(self) -> float:
        return self.ratio * 100

    @property
    def remaining(self) -> int:
        return self.budget - self.spent

    @property
    def is_over(self) -> bool:
        return self.spent > self.budget


class BudgetService:
    """예산 설정/조회 및 사용률 계산."""

    def __init__(self, store: BudgetStore) -> None:
        self.store: BudgetStore = store

    def set_budget(self, month: str, amount: str | int) -> tuple[Budget, Budget | None]:
        return self.store.set_budget(validate_month(month), validate_amount(amount))

    def get(self, month: str) -> Budget | None:
        return self.store.get(validate_month(month))

    def list_all(self) -> list[Budget]:
        return self.store.list_all()

    def remove(self, month: str) -> Budget:
        return self.store.remove(validate_month(month))

    def usage(self, month: str, spent: int) -> BudgetUsage | None:
        """예산이 설정된 달이면 사용률 객체를, 아니면 None 을 돌려준다."""
        budget = self.store.get(month)
        if budget is None:
            return None
        return BudgetUsage(month=month, budget=budget.amount, spent=spent)


class RecurringService:
    """매월 반복되는 거래 규칙 등록/적용(보너스)."""

    def __init__(self, store: RecurringStore, service: TransactionService) -> None:
        self.store: RecurringStore = store
        self.service: TransactionService = service

    def add(
        self,
        day: str | int,
        type_: str,
        category: str,
        amount: str | int,
        memo: str = "",
        tags: str | None = None,
    ) -> RecurringRule:
        self.service.ensure_categories_exist()
        rule = RecurringRule(
            id=0,
            day=validate_day(day),
            type=validate_type(type_),
            category=self.service.validate_category(category),
            amount=validate_amount(amount),
            memo=(memo or "").strip(),
            tags=parse_tags(tags),
        )
        return self.store.add(rule)

    def list_all(self) -> list[RecurringRule]:
        return self.store.list_all()

    def remove(self, rule_id: int) -> RecurringRule:
        return self.store.remove(rule_id)

    @staticmethod
    def _clamp_day(month: str, day: int) -> str:
        """해당 월에 없는 날짜(2월 31일 등)는 말일로 맞춘다."""
        year, mon = int(month[:4]), int(month[5:7])
        if mon == 12:
            next_month = datetime(year + 1, 1, 1)
        else:
            next_month = datetime(year, mon + 1, 1)
        last_day = (next_month - datetime(year, mon, 1)).days
        return f"{month}-{min(day, last_day):02d}"

    def apply_month(self, month: str) -> tuple[list[Transaction], list[RecurringRule]]:
        """해당 월에 아직 적용되지 않은 규칙들로 거래를 생성한다.

        돌려주는 값은 (생성된 거래 목록, 이미 적용돼 건너뛴 규칙 목록).
        """
        month = validate_month(month)
        created: list[Transaction] = []
        skipped: list[RecurringRule] = []
        for rule in self.store.list_all():
            if month in rule.applied_months:
                skipped.append(rule)
                continue
            tx = self.service.build_transaction(
                date=self._clamp_day(month, rule.day),
                type_=rule.type,
                category=rule.category,
                amount=rule.amount,
                memo=rule.memo,
                tags=format_tags(rule.tags),
            )
            created.append(self.service.add(tx))
            rule.applied_months.append(month)
            self.store.save(rule)
        return created, skipped
