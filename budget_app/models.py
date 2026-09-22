"""데이터 모델(dataclass)과 커스텀 예외 정의."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from typing import Any

TYPE_INCOME = "income"
TYPE_EXPENSE = "expense"
TRANSACTION_TYPES: tuple[str, ...] = (TYPE_INCOME, TYPE_EXPENSE)

DATE_FORMAT = "%Y-%m-%d"
MONTH_FORMAT = "%Y-%m"


class BudgetAppError(Exception):
    """애플리케이션 공통 예외. 원인(message)과 해결 힌트(hint)를 함께 가진다."""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.message: str = message
        self.hint: str = hint


class ValidationError(BudgetAppError):
    """입력값 검증 실패."""


class NotFoundError(BudgetAppError):
    """존재하지 않는 데이터 참조."""


class DuplicateError(BudgetAppError):
    """이미 존재하는 데이터를 중복 생성하려 할 때."""


class StorageError(BudgetAppError):
    """저장 파일 입출력/포맷 오류."""


@dataclass(slots=True)
class Transaction:
    """거래 1건.

    id 는 저장소가 부여하는 1부터 시작하는 순번이며, 삭제 후에도 재사용하지 않는다.
    """

    id: int
    date: str
    type: str
    category: str
    amount: int
    memo: str = ""
    tags: list[str] = field(default_factory=list)

    @property
    def display_id(self) -> str:
        """화면 표시용 id (`TX-3`). zero-padding 없음."""
        return f"TX-{self.id}"

    @property
    def month(self) -> str:
        """거래가 속한 달 (`YYYY-MM`)."""
        return self.date[:7]

    @property
    def signed_amount(self) -> int:
        """수입은 +, 지출은 - 로 부호를 붙인 금액."""
        return self.amount if self.type == TYPE_INCOME else -self.amount

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        """저장용 JSON 한 줄(개행 없음)."""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def replace_fields(self, **changes: Any) -> "Transaction":
        """지정한 필드만 바꾼 새 Transaction 을 돌려준다(미지정 필드는 기존 값 유지)."""
        return replace(self, **changes)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Transaction":
        try:
            return cls(
                id=int(raw["id"]),
                date=str(raw["date"]),
                type=str(raw["type"]),
                category=str(raw["category"]),
                amount=int(raw["amount"]),
                memo=str(raw.get("memo", "")),
                tags=[str(t) for t in raw.get("tags", [])],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise StorageError(
                f"거래 레코드를 해석할 수 없습니다: {exc}",
                "data/transactions.jsonl 이 손상되었을 수 있습니다. backup 명령으로 백업본을 확인하세요.",
            ) from exc

    @classmethod
    def from_json(cls, raw: bytes | str) -> "Transaction":
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise StorageError(
                f"거래 레코드 JSON 파싱 실패: {exc}",
                "transactions.idx 와 transactions.jsonl 이 어긋났을 수 있습니다. compact 또는 백업 복구를 검토하세요.",
            ) from exc
        return cls.from_dict(data)


@dataclass(slots=True)
class Category:
    """카테고리 1건."""

    name: str
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Category":
        return cls(name=str(raw["name"]), created_at=str(raw.get("created_at", "")))


@dataclass(slots=True)
class Budget:
    """월 예산 1건."""

    month: str
    amount: int
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Budget":
        return cls(
            month=str(raw["month"]),
            amount=int(raw["amount"]),
            updated_at=str(raw.get("updated_at", "")),
        )


@dataclass(slots=True)
class RecurringRule:
    """매월 반복되는 거래 규칙(보너스 기능)."""

    id: int
    day: int
    type: str
    category: str
    amount: int
    memo: str = ""
    tags: list[str] = field(default_factory=list)
    applied_months: list[str] = field(default_factory=list)

    @property
    def display_id(self) -> str:
        return f"RC-{self.id}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RecurringRule":
        return cls(
            id=int(raw["id"]),
            day=int(raw["day"]),
            type=str(raw["type"]),
            category=str(raw["category"]),
            amount=int(raw["amount"]),
            memo=str(raw.get("memo", "")),
            tags=[str(t) for t in raw.get("tags", [])],
            applied_months=[str(m) for m in raw.get("applied_months", [])],
        )


@dataclass(slots=True)
class MonthlySummary:
    """월별 요약 집계 결과."""

    month: str
    total_income: int
    total_expense: int
    count: int
    expense_by_category: list[tuple[str, int]]

    @property
    def balance(self) -> int:
        return self.total_income - self.total_expense

    @property
    def is_empty(self) -> bool:
        return self.count == 0
