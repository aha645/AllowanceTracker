"""비즈니스 로직: 거래 CRUD·검색·월별 요약, 카테고리, 예산 계산, 반복 거래 규칙.

저장 방식(파일 포맷)은 transaction_store/stores 가, 사용자 입출력은 cli 가 맡는다.
이 모듈은 그 사이에서 "규칙"만 담당한다. 입력 검증은 validators, CSV/백업은 file_services.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Iterator, Mapping

from .models import (
    TYPE_INCOME,
    Budget,
    Category,
    MonthlySummary,
    RecurringRule,
    Transaction,
    ValidationError,
)
from .stores import BudgetStore, CategoryStore, RecurringStore
from .transaction_store import StorageStats, TransactionStore
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

    def date_bounds(self) -> tuple[str | None, str | None]:
        """조건에서 뽑은 거래일자 범위(포함). --month 와 --from/--to 는 교집합으로 합친다.

        저장소가 날짜 인덱스를 이진 탐색해 이 범위만 읽도록 넘기는 용도이며, 범위 밖 거래는 읽지 않는다.
        """
        low, high = self.date_from, self.date_to
        if self.month:
            year, mon = int(self.month[:4]), int(self.month[5:7])
            first = f"{self.month}-01"
            last = f"{self.month}-{calendar.monthrange(year, mon)[1]:02d}"
            low = max(low, first) if low else first
            high = min(high, last) if high else last
        return low, high

    @property
    def is_empty(self) -> bool:
        return not any(
            (self.date_from, self.date_to, self.month, self.category, self.type, self.query, self.tag)
        )


class CategoryService:
    """카테고리 등록·삭제·검증과 카테고리별 사용 건수 관리."""

    def __init__(self, store: CategoryStore, transactions: TransactionStore) -> None:
        self.store: CategoryStore = store
        self.transactions: TransactionStore = transactions

    # ------------------------------------------------------------- 등록·조회·삭제
    def add(self, name: str) -> Category:
        return self.store.add(name)

    def list_all(self) -> list[Category]:
        return self.store.list_all()

    def names(self) -> list[str]:
        return self.store.names()

    def remove(self, name: str) -> Category:
        """카테고리를 삭제한다. 없는 이름이거나 거래에서 쓰는 중이면 거부한다."""
        name = name.strip()
        if not self.store.exists(name):
            raise ValidationError(
                f"카테고리 '{name}' 을(를) 찾을 수 없습니다.", "category list 로 목록을 확인하세요."
            )
        in_use = self.is_in_use(name)
        if in_use is not None:
            raise ValidationError(
                f"카테고리 '{name}' 은(는) 사용 중이라 삭제할 수 없습니다 (예: {in_use.display_id} {in_use.date}).",
                f'search --category "{name}" 로 확인 후, 해당 거래를 update/delete 한 뒤 다시 시도하세요.',
            )
        return self.store.remove(name)

    def is_in_use(self, name: str) -> Transaction | None:
        """해당 카테고리를 쓰는 거래를 하나라도 찾으면 그 거래를 돌려준다.

        기록된 건수가 0 이면 거래를 훑지 않고 바로 None(미사용). 건수는 실제보다 적게 틀리지 않도록
        갱신하므로 0 은 믿을 수 있다. 0 이 아니면 최신 거래부터 훑어 확인한다. 쓰는 카테고리는 대개
        최근 거래에서 금방 찾고, 끝까지 없으면 기록이 많게 틀린 것(저장 도중 중단 등)이라 미사용으로 본다.
        """
        if self.store.count_of(name) == 0:
            return None
        for tx in self.transactions.iter_latest_transactions():
            if tx.category == name:
                return tx
        return None

    # ------------------------------------------------------------- 검증
    def ensure_registered(self) -> None:
        """카테고리가 하나도 없으면 거래 등록 자체를 막는다(정책 안 B)."""
        if self.store.is_empty():
            raise ValidationError(
                "등록된 카테고리가 없습니다.",
                'category add 로 먼저 등록하세요. 예: python -m budget_app category add --name "식비"',
            )

    def validate(self, value: str, known: set[str] | None = None) -> str:
        """등록된 카테고리인지 확인한다(자동 생성하지 않음).

        `known` 을 주면 파일을 다시 읽지 않고 그 집합으로 확인한다(import 처럼 행이 많을 때).
        """
        name = (value or "").strip()
        if not name:
            raise ValidationError(
                "카테고리를 입력해야 합니다.",
                "category list 로 등록된 카테고리를 확인하세요.",
            )
        if name not in (known if known is not None else set(self.store.names())):
            registered = ", ".join(self.store.names()) or "(없음)"
            raise ValidationError(
                f"등록되지 않은 카테고리입니다: '{name}'",
                f"등록된 카테고리: {registered} / 새로 만들려면 category add 를 사용하세요.",
            )
        return name

    # ------------------------------------------------------------- 사용 건수
    # 거래 파일과 categories.jsonl 은 따로 저장돼 중간에 꺼지면 어긋날 수 있다. 그래서 건수는
    # "늘릴 때는 거래보다 먼저, 줄일 때는 거래보다 나중에" 바꿔 실제보다 많게만 틀리게 한다(TransactionService).
    # 많게 틀리면 안 쓰는 카테고리 삭제가 확인 한 번 더 거치는 정도지만, 적게 틀리면 사용 중인
    # 카테고리를 지울 수 있기 때문이다.
    def adjust_counts(self, deltas: Mapping[str, int]) -> None:
        if not self.store.adjust_counts(deltas):
            self.recount()  # 기록이 실제보다 적었다(직접 편집 등) → 처음부터 다시 센다

    def recount(self) -> None:
        """거래를 처음부터 세어 카테고리별 사용 건수를 다시 맞춘다(compact, 기록이 어긋났을 때)."""
        self.store.set_counts(self.transactions.category_counts())

    def ensure_counts(self) -> None:
        """건수 기록 이전에 만든 categories.jsonl 이면 한 번 세어 채운다(없으면 0 으로 읽혀 위험)."""
        if not self.store.has_counts():
            self.recount()


class TransactionService:
    """거래 관련 비즈니스 로직(검증 + 저장소 호출 + 집계)."""

    def __init__(self, store: TransactionStore, categories: CategoryService) -> None:
        self.store: TransactionStore = store
        self.categories: CategoryService = categories

    def build_transaction(
        self,
        date: str,
        type_: str,
        category: str,
        amount: str | int,
        memo: str = "",
        tags: str | list[str] | None = None,
        known_categories: set[str] | None = None,
    ) -> Transaction:
        """원시 입력값을 검증해 (id 미부여) Transaction 으로 만든다.

        `known_categories` 를 주면 호출자가 카테고리 존재 여부를 이미 확인했다고 보고,
        categories.jsonl 을 행마다 다시 읽지 않는다.
        """
        if known_categories is None:
            self.categories.ensure_registered()
        tag_list = parse_tags(tags if tags is None or isinstance(tags, str) else format_tags(tags))
        return Transaction(
            id=0,
            date=validate_date(date),
            type=validate_type(type_),
            category=self.categories.validate(category, known_categories),
            amount=validate_amount(amount),
            memo=(memo or "").strip(),
            tags=tag_list,
        )

    # ------------------------------------------------------------- CRUD
    def add(self, tx: Transaction) -> Transaction:
        self.categories.adjust_counts({tx.category: 1})  # 거래보다 먼저(CategoryService 설명)
        return self.store.append_transaction(tx)

    def add_many(self, transactions: Iterable[Transaction], category_counts: Mapping[str, int]) -> int:
        """여러 거래를 한 번에 저장하고 건수를 돌려준다(import 용: 날짜 인덱스를 한 번만 병합).

        category_counts 는 저장할 거래의 카테고리별 건수다. 행마다 categories.jsonl 을 다시 쓰지 않도록
        호출자가 미리 합산해 넘기고, 여기서 거래보다 먼저 한 번만 반영한다.
        """
        self.categories.adjust_counts(category_counts)
        return self.store.append_transactions(transactions)

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
        current = self.store.get_transaction(tx_id)
        changes: dict[str, object] = {}
        if date is not None:
            changes["date"] = validate_date(date)
        if type_ is not None:
            changes["type"] = validate_type(type_)
        if category is not None:
            changes["category"] = self.categories.validate(category)
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
        moved = updated.category != current.category
        if moved:
            self.categories.adjust_counts({updated.category: 1})  # 새 카테고리는 거래보다 먼저 늘리고
        self.store.update_transaction(updated)
        if moved:
            self.categories.adjust_counts({current.category: -1})  # 옛 카테고리는 거래 다음에 줄인다
        return current, updated

    def delete(self, tx_id: int) -> Transaction:
        deleted = self.store.delete_transaction(tx_id)
        self.categories.adjust_counts({deleted.category: -1})  # 거래보다 나중(CategoryService 설명)
        return deleted

    # ------------------------------------------------------------- 저장소 상태·정리
    def get(self, tx_id: int) -> Transaction:
        """id 로 거래 하나를 읽는다(없거나 삭제됐으면 NotFoundError)."""
        return self.store.get_transaction(tx_id)

    def stats(self) -> StorageStats:
        return self.store.stats()

    def live_count(self) -> int:
        return self.store.live_count()

    def needs_compact(self, stats: StorageStats | None = None) -> bool:
        return self.store.needs_compact(stats)

    def compact(self) -> StorageStats:
        """옛 줄을 정리하고, 어긋났을 수 있는 카테고리 사용 건수도 다시 센다. 정리 전 상태를 돌려준다."""
        before = self.store.compact()
        self.categories.recount()
        return before

    # ------------------------------------------------------------- 조회
    def iter_latest(self, limit: int | None = None) -> Iterator[Transaction]:
        """최신순 거래를 limit 건까지 스트리밍한다."""
        if limit is not None and limit <= 0:
            return
        for count, tx in enumerate(self.store.iter_latest_transactions(), start=1):
            yield tx
            if limit is not None and count >= limit:
                return  # limit 번째를 내보낸 직후 멈춘다(다음 레코드는 읽지 않는다)

    def search(self, criteria: SearchCriteria, limit: int | None = None) -> Iterator[Transaction]:
        """조건에 맞는 거래를 거래일자 최신순으로 스트리밍한다(AND 결합, 기간 조건은 인덱스 범위로 제한)."""
        found = 0
        date_from, date_to = criteria.date_bounds()
        for tx in self.store.iter_latest_transactions(date_from, date_to):
            if not criteria.matches(tx):
                continue
            yield tx
            found += 1
            if limit is not None and found >= limit:
                return

    def summarize_month(self, month: str, top: int) -> MonthlySummary:
        """월별 수입/지출/잔액과 카테고리별 지출 TOP N 을 집계한다."""
        total_income = 0
        total_expense = 0
        count = 0
        by_category: dict[str, int] = {}
        bounds = SearchCriteria(month=month).date_bounds()
        for tx in self.store.iter_latest_transactions(*bounds):  # 그 달 구간만 읽는다
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

    def __init__(self, store: RecurringStore, transactions: TransactionService, categories: CategoryService) -> None:
        self.store: RecurringStore = store
        self.transactions: TransactionService = transactions
        self.categories: CategoryService = categories

    def add(
        self,
        day: str | int,
        type_: str,
        category: str,
        amount: str | int,
        memo: str = "",
        tags: str | None = None,
    ) -> RecurringRule:
        self.categories.ensure_registered()
        rule = RecurringRule(
            id=0,
            day=validate_day(day),
            type=validate_type(type_),
            category=self.categories.validate(category),
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
        # 거래 저장과 규칙의 "이번 달 적용함" 기록은 다른 파일이라 그 사이에 꺼질 수 있다. 예전에는
        # 그러면 다시 apply 할 때 같은 거래가 또 생겼다. 그래서 만든 거래에 규칙/월 표시(source)를 남기고,
        # 그 달에 이미 그 표시의 거래가 있으면 새로 만들지 않고 기록만 마저 한다. 그 달 구간만 읽는다.
        made = {tx.source for tx in self.transactions.search(SearchCriteria(month=month)) if tx.source}
        for rule in self.store.list_all():
            if month in rule.applied_months:
                skipped.append(rule)
                continue
            source = f"{rule.display_id}/{month}"
            if source in made:  # 거래는 저장됐는데 기록 전에 중단됐던 규칙
                skipped.append(rule)
            else:
                tx = self.transactions.build_transaction(
                    date=self._clamp_day(month, rule.day),
                    type_=rule.type,
                    category=rule.category,
                    amount=rule.amount,
                    memo=rule.memo,
                    tags=format_tags(rule.tags),
                )
                created.append(self.transactions.add(tx.replace_fields(source=source)))
            rule.applied_months.append(month)
            self.store.save(rule)
        return created, skipped
