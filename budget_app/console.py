"""커맨드 핸들러가 공유하는 콘솔 입출력 보조 함수(오류 출력, id 파싱, 재입력 루프, 표 출력)."""

from __future__ import annotations

import sys
from typing import Callable, Iterable

from .context import AppContext
from .formatter import format_amount, format_bar, format_table
from .models import TYPE_INCOME, Transaction, ValidationError
from .services import BudgetUsage
from .validators import format_tags

LIST_HEADERS = ["#", "id", "날짜", "타입", "카테고리", "금액", "메모", "태그"]
LIST_ALIGNS = ["right", "left", "left", "left", "left", "right", "left", "left"]


def print_error(message: str, hint: str = "") -> None:
    print(f"[오류] {message}", file=sys.stderr)
    if hint:
        print(f"[힌트] {hint}", file=sys.stderr)


def _parse_prefixed_id(value: str, prefix: str, label: str) -> int:
    """`<prefix>-N` 또는 `N` 형태의 id 문자열을 정수로 바꾼다."""
    text = str(value).strip().upper()
    if text.startswith(f"{prefix}-"):
        text = text[len(prefix) + 1:]
    try:
        return int(text)
    except ValueError as exc:
        raise ValidationError(
            f"{label} id 형식이 올바르지 않습니다: '{value}'",
            f"예: --id {prefix}-3 또는 --id 3",
        ) from exc


def parse_tx_id(value: str) -> int:
    """`TX-3` 또는 `3` 형태의 거래 id 를 정수로 바꾼다."""
    tx_id = _parse_prefixed_id(value, "TX", "거래")
    if tx_id < 1:
        raise ValidationError(f"거래 id 는 1 이상이어야 합니다: {tx_id}", "list 로 id 를 확인하세요.")
    return tx_id


def parse_rule_id(value: str) -> int:
    """`RC-2` 또는 `2` 형태의 반복 규칙 id 를 정수로 바꾼다."""
    return _parse_prefixed_id(value, "RC", "반복 규칙")


def prompt_until_valid(
    label: str,
    validator: Callable[[str], object],
    *,
    default: object | None = None,
    allow_blank: bool = False,
) -> object:
    """검증을 통과할 때까지 재입력을 요구하는 대화형 입력 루프."""
    while True:
        raw = input(label)
        if not raw.strip() and allow_blank:
            return default
        try:
            return validator(raw)
        except ValidationError as exc:
            print_error(exc.message, exc.hint)


def render_transactions(rows: Iterable[Transaction]) -> int:
    """거래 목록을 표로 출력하고 출력 건수를 돌려준다."""
    table_rows: list[list[str]] = []
    for count, tx in enumerate(rows, start=1):
        sign = "+" if tx.type == TYPE_INCOME else "-"
        table_rows.append(
            [
                str(count),  # 화면용 순번 (내부 id 와 무관)
                tx.display_id,
                tx.date,
                tx.type,
                tx.category,
                f"{sign}{format_amount(tx.amount)}",
                tx.memo,
                format_tags(tx.tags),
            ]
        )
    if not table_rows:
        return 0
    print(format_table(LIST_HEADERS, table_rows, LIST_ALIGNS))
    return len(table_rows)


def print_budget_usage(usage: BudgetUsage) -> None:
    """예산 대비 사용률/초과 경고/잔여 금액을 출력한다(summary, budget show 공용)."""
    print(f"  사용     {format_amount(usage.spent)}원 ({usage.percent:.1f}%) [{format_bar(usage.ratio)}]")
    if usage.is_over:
        print(f"  [경고] 예산을 {format_amount(-usage.remaining)}원 초과했습니다!")
    else:
        print(f"  잔여     {format_amount(usage.remaining)}원")


def maybe_hint_compact(ctx: AppContext) -> None:
    """고아 데이터가 많이 쌓였으면 compact 를 안내한다."""
    if ctx.repo.needs_compact():
        stats = ctx.repo.stats()
        print(
            f"[안내] 로그의 {stats.orphan_ratio * 100:.0f}% 가 오래된 버전입니다. "
            "`compact` 명령으로 정리할 수 있습니다."
        )
