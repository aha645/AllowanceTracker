"""카테고리·예산·반복규칙 저장소.

데이터 양이 적으므로 전체를 메모리에 읽고, 변경 시에는 임시 파일에 전부 다시 쓴 뒤
`os.replace` 로 원자적 교체한다(쓰다가 죽어도 원본이 반쯤 망가지지 않는다).
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, TypeVar

from .models import (
    Budget,
    Category,
    DuplicateError,
    NotFoundError,
    RecurringRule,
    StorageError,
    ValidationError,
)
from .durability import fsync_dir

T = TypeVar("T")


class JsonlStore:
    """JSONL 파일 하나를 통째로 읽고 통째로 다시 쓰는 저장소의 공통 기반."""

    def __init__(self, path: Path) -> None:
        self.path: Path = Path(path)

    def ensure_file(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()

    def _iter_numbered(self) -> Iterator[tuple[int, dict[str, Any]]]:
        """(줄 번호, JSON 객체) 를 하나씩 낸다. JSON 이 아니거나 객체({...})가 아닌 줄은 StorageError."""
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as fp:
            for lineno, line in enumerate(fp, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise StorageError(
                        f"{self.path.name} {lineno}번째 줄을 읽을 수 없습니다: {exc}",
                        "해당 줄을 수정하거나 backup 명령으로 만든 백업본에서 복구하세요.",
                    ) from exc
                if not isinstance(row, dict):
                    raise self._malformed(lineno, f"JSON 객체가 아닙니다: {line[:40]}")
                yield lineno, row

    def _iter_raw(self) -> Iterator[dict[str, Any]]:
        for _, row in self._iter_numbered():
            yield row

    def _iter_parsed(self, factory: Callable[[dict[str, Any]], T]) -> Iterator[T]:
        """줄마다 factory 로 객체를 만든다. 키가 없거나 값의 형식이 틀리면 줄 번호와 함께 StorageError.

        JSON 문법은 맞아도 직접 편집 등으로 키가 빠지거나(`KeyError`) 숫자 자리에 글자가 들어가면(`ValueError`)
        예전에는 스택트레이스가 그대로 보였다.
        """
        for lineno, row in self._iter_numbered():
            try:
                item = factory(row)
            except (KeyError, TypeError, ValueError) as exc:
                raise self._malformed(lineno, f"{type(exc).__name__}: {exc}") from exc
            yield item

    def _malformed(self, lineno: int, detail: str) -> StorageError:
        return StorageError(
            f"{self.path.name} {lineno}번째 줄의 형식이 올바르지 않습니다({detail}).",
            "해당 줄을 수정하거나 backup 명령으로 만든 백업본에서 복구하세요.",
        )

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
        # 교체를 폴더까지 확정한다. 안 하면 전원이 꺼졌을 때 교체가 되돌아갈 수 있는데, 그러면 카테고리
        # 사용 건수를 거래보다 먼저 늘린 순서가 뒤집혀 건수가 실제보다 적어질 수 있다(사용 중인 카테고리 삭제 위험).
        fsync_dir(self.path.parent)


class CategoryStore(JsonlStore):
    """`categories.jsonl` — 등록된 카테고리 목록."""

    FILE_NAME = "categories.jsonl"

    def __init__(self, data_dir: Path) -> None:
        super().__init__(Path(data_dir) / self.FILE_NAME)

    def list_all(self) -> list[Category]:
        """등록 순서대로 카테고리를 돌려준다."""
        return list(self._iter_parsed(Category.from_dict))

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

    def count_of(self, name: str) -> int:
        """기록된 사용 건수(없는 카테고리는 0)."""
        return next((c.count for c in self.list_all() if c.name == name), 0)

    def has_counts(self) -> bool:
        """모든 줄에 사용 건수가 기록돼 있는지. 건수 기록 이전에 만든 파일이면 False."""
        return all("count" in row for row in self._iter_raw())

    def adjust_counts(self, deltas: Mapping[str, int]) -> bool:
        """카테고리별 사용 건수를 한꺼번에 더하고 빼서 파일을 한 번만 다시 쓴다.

        0 아래로 내려가는 카테고리가 있으면 False 를 돌려준다. 기록이 실제보다 적었다는 뜻이라
        (그대로 두면 사용 중인 카테고리를 지울 수 있음) 호출자가 거래를 다시 세어야 한다.
        """
        if not any(deltas.values()):
            return True
        categories = self.list_all()
        consistent = True
        for category in categories:
            category.count += deltas.get(category.name, 0)
            if category.count < 0:
                consistent = False
                category.count = 0
        self._rewrite([c.to_dict() for c in categories])
        return consistent

    def set_counts(self, counts: Mapping[str, int]) -> None:
        """사용 건수를 통째로 바꾼다(다시 센 결과 반영용). counts 에 없는 카테고리는 0."""
        categories = self.list_all()
        for category in categories:
            category.count = counts.get(category.name, 0)
        self._rewrite([c.to_dict() for c in categories])

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
        budgets = list(self._iter_parsed(Budget.from_dict))
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
    """`recurring.jsonl` — 매월 반복 거래 규칙(보너스 기능).

    삭제한 규칙은 지우지 않고 `"removed": true` 로 표시해 남긴다. 규칙이 만든 거래에는 `RC-1/2024-03`
    같은 표시가 남는데, 지운 RC-1 의 번호를 새 규칙이 다시 받으면 그 달에 이미 적용된 것으로 보고
    건너뛰었다. 그래서 거래 id 처럼 규칙 id 도 다시 쓰지 않는다.
    """

    FILE_NAME = "recurring.jsonl"

    def __init__(self, data_dir: Path) -> None:
        super().__init__(Path(data_dir) / self.FILE_NAME)

    def _rows(self) -> list[tuple[dict[str, Any], RecurringRule]]:
        """(원본 줄, 해석한 규칙) 목록. 삭제 표시한 줄도 포함한다(번호를 피하고 다시 쓸 때 그대로 남기려고)."""
        return list(self._iter_parsed(lambda row: (row, RecurringRule.from_dict(row))))

    def list_all(self) -> list[RecurringRule]:
        rules = [rule for row, rule in self._rows() if not row.get("removed")]
        return sorted(rules, key=lambda r: r.id)

    def _live_row(self, rows: list[tuple[dict[str, Any], RecurringRule]], rule_id: int) -> dict[str, Any]:
        row = next((row for row, rule in rows if rule.id == rule_id and not row.get("removed")), None)
        if row is None:
            raise NotFoundError(
                f"RC-{rule_id} 반복 규칙을 찾을 수 없습니다.",
                "recurring list 로 등록된 규칙을 확인하세요.",
            )
        return row

    def get(self, rule_id: int) -> RecurringRule:
        rule = next((r for r in self.list_all() if r.id == rule_id), None)
        if rule is None:
            raise NotFoundError(
                f"RC-{rule_id} 반복 규칙을 찾을 수 없습니다.",
                "recurring list 로 등록된 규칙을 확인하세요.",
            )
        return rule

    def add(self, rule: RecurringRule) -> RecurringRule:
        rows = self._rows()
        new_id = max((r.id for _, r in rows), default=0) + 1  # 삭제 표시한 규칙의 번호도 피한다
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
        self._rewrite([row for row, _ in rows] + [stored.to_dict()])
        return stored

    def save(self, rule: RecurringRule) -> RecurringRule:
        """기존 규칙을 통째로 교체 저장한다."""
        rows = self._rows()
        target = self._live_row(rows, rule.id)
        target.clear()
        target.update(rule.to_dict())
        self._rewrite([row for row, _ in rows])
        return rule

    def remove(self, rule_id: int) -> RecurringRule:
        rows = self._rows()
        target = self._live_row(rows, rule_id)
        target["removed"] = True  # 번호를 다시 쓰지 않도록 줄은 남긴다(클래스 설명)
        self._rewrite([row for row, _ in rows])
        return RecurringRule.from_dict(target)
