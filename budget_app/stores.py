"""카테고리·예산·반복규칙 저장소.

데이터 양이 적으므로 전체를 메모리에 읽고, 변경 시에는 임시 파일에 전부 다시 쓴 뒤
`os.replace` 로 원자적 교체한다(쓰다가 죽어도 원본이 반쯤 망가지지 않는다).
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .models import (
    Budget,
    Category,
    DuplicateError,
    NotFoundError,
    RecurringRule,
    StorageError,
    ValidationError,
)


class JsonlStore:
    """JSONL 파일 하나를 통째로 읽고 통째로 다시 쓰는 저장소의 공통 기반."""

    def __init__(self, path: Path) -> None:
        self.path: Path = Path(path)

    def ensure_file(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()

    def _iter_raw(self) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as fp:
            for lineno, line in enumerate(fp, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise StorageError(
                        f"{self.path.name} {lineno}번째 줄을 읽을 수 없습니다: {exc}",
                        "해당 줄을 수정하거나 backup 명령으로 만든 백업본에서 복구하세요.",
                    ) from exc

    def _rewrite(self, rows: list[dict[str, Any]]) -> None:
        """임시 파일에 전부 쓰고 os.replace 로 원자적 교체."""
        self.ensure_file()
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as fp:
            for row in rows:
                fp.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
                fp.write("\n")
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp_path, self.path)


class CategoryStore(JsonlStore):
    """`categories.jsonl` — 등록된 카테고리 목록."""

    FILE_NAME = "categories.jsonl"

    def __init__(self, data_dir: Path) -> None:
        super().__init__(Path(data_dir) / self.FILE_NAME)

    def list_all(self) -> list[Category]:
        """등록 순서대로 카테고리를 돌려준다."""
        return [Category.from_dict(row) for row in self._iter_raw()]

    def names(self) -> list[str]:
        return [c.name for c in self.list_all()]

    def is_empty(self) -> bool:
        return not self.names()

    def exists(self, name: str) -> bool:
        return name in set(self.names())

    def add(self, name: str) -> Category:
        clean = name.strip()
        if not clean:
            raise ValidationError(
                "카테고리 이름이 비어 있습니다.",
                '예: python -m budget_app category add --name "식비"',
            )
        categories = self.list_all()
        if any(c.name == clean for c in categories):
            raise DuplicateError(
                f"카테고리 '{clean}' 은(는) 이미 등록되어 있습니다.",
                "category list 로 등록된 목록을 확인하세요.",
            )
        category = Category(name=clean, created_at=datetime.now().isoformat(timespec="seconds"))
        categories.append(category)
        self._rewrite([c.to_dict() for c in categories])
        return category

    def remove(self, name: str) -> Category:
        clean = name.strip()
        categories = self.list_all()
        target = next((c for c in categories if c.name == clean), None)
        if target is None:
            raise NotFoundError(
                f"카테고리 '{clean}' 을(를) 찾을 수 없습니다.",
                "category list 로 등록된 목록을 확인하세요.",
            )
        self._rewrite([c.to_dict() for c in categories if c.name != clean])
        return target


class BudgetStore(JsonlStore):
    """`budgets.jsonl` — 월별 예산. 같은 달을 다시 설정하면 덮어쓴다."""

    FILE_NAME = "budgets.jsonl"

    def __init__(self, data_dir: Path) -> None:
        super().__init__(Path(data_dir) / self.FILE_NAME)

    def list_all(self) -> list[Budget]:
        """월 오름차순으로 예산을 돌려준다."""
        budgets = [Budget.from_dict(row) for row in self._iter_raw()]
        return sorted(budgets, key=lambda b: b.month)

    def get(self, month: str) -> Budget | None:
        return next((b for b in self.list_all() if b.month == month), None)

    def set_budget(self, month: str, amount: int) -> tuple[Budget, Budget | None]:
        """예산을 저장하고 (새 예산, 덮어쓰기 전 예산 or None) 을 돌려준다."""
        budgets = self.list_all()
        previous = next((b for b in budgets if b.month == month), None)
        budget = Budget(
            month=month, amount=amount, updated_at=datetime.now().isoformat(timespec="seconds")
        )
        rows = [b for b in budgets if b.month != month] + [budget]
        rows.sort(key=lambda b: b.month)
        self._rewrite([b.to_dict() for b in rows])
        return budget, previous

    def remove(self, month: str) -> Budget:
        budgets = self.list_all()
        target = next((b for b in budgets if b.month == month), None)
        if target is None:
            raise NotFoundError(
                f"{month} 예산이 설정되어 있지 않습니다.",
                "budget list 로 설정된 달을 확인하세요.",
            )
        self._rewrite([b.to_dict() for b in budgets if b.month != month])
        return target


class RecurringStore(JsonlStore):
    """`recurring.jsonl` — 매월 반복 거래 규칙(보너스 기능)."""

    FILE_NAME = "recurring.jsonl"

    def __init__(self, data_dir: Path) -> None:
        super().__init__(Path(data_dir) / self.FILE_NAME)

    def list_all(self) -> list[RecurringRule]:
        rules = [RecurringRule.from_dict(row) for row in self._iter_raw()]
        return sorted(rules, key=lambda r: r.id)

    def get(self, rule_id: int) -> RecurringRule:
        rule = next((r for r in self.list_all() if r.id == rule_id), None)
        if rule is None:
            raise NotFoundError(
                f"RC-{rule_id} 반복 규칙을 찾을 수 없습니다.",
                "recurring list 로 등록된 규칙을 확인하세요.",
            )
        return rule

    def add(self, rule: RecurringRule) -> RecurringRule:
        rules = self.list_all()
        new_id = max((r.id for r in rules), default=0) + 1
        stored = RecurringRule(
            id=new_id,
            day=rule.day,
            type=rule.type,
            category=rule.category,
            amount=rule.amount,
            memo=rule.memo,
            tags=list(rule.tags),
            applied_months=[],
        )
        rules.append(stored)
        self._rewrite([r.to_dict() for r in rules])
        return stored

    def save(self, rule: RecurringRule) -> RecurringRule:
        """기존 규칙을 통째로 교체 저장한다."""
        rules = self.list_all()
        if not any(r.id == rule.id for r in rules):
            raise NotFoundError(
                f"RC-{rule.id} 반복 규칙을 찾을 수 없습니다.",
                "recurring list 로 등록된 규칙을 확인하세요.",
            )
        rows = [rule.to_dict() if r.id == rule.id else r.to_dict() for r in rules]
        self._rewrite(rows)
        return rule

    def remove(self, rule_id: int) -> RecurringRule:
        rules = self.list_all()
        target = next((r for r in rules if r.id == rule_id), None)
        if target is None:
            raise NotFoundError(
                f"RC-{rule_id} 반복 규칙을 찾을 수 없습니다.",
                "recurring list 로 등록된 규칙을 확인하세요.",
            )
        self._rewrite([r.to_dict() for r in rules if r.id != rule_id])
        return target
