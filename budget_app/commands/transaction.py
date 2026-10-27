"""거래 커맨드: add / list / search / update / delete."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from typing import Callable, TypeVar

from ..console import (
    build_criteria,
    maybe_hint_compact,
    parse_tx_id,
    prompt_until_valid,
    render_transactions,
)
from ..context import AppContext
from ..decorators import EXIT_OK, command
from ..formatter import format_amount, format_table
from ..models import (
    DATE_FORMAT,
    TRANSACTION_TYPES,
    TYPE_EXPENSE,
    Transaction,
    ValidationError,
)
from ..validators import (
    format_tags,
    parse_tags,
    validate_amount,
    validate_date,
    validate_type,
)

T = TypeVar("T")


def _category_validator(ctx: AppContext) -> Callable[[str], str]:
    """번호(1부터) 또는 이름으로 등록된 카테고리를 고르는 검증기."""
    names = ctx.categories.names()

    def validator(raw: str) -> str:
        text = raw.strip()
        if text.isdigit() and 1 <= int(text) <= len(names):
            return names[int(text) - 1]
        return ctx.categories.validate(text)

    return validator


def _print_categories(names: list[str]) -> None:
    """카테고리 입력 전에 번호로 고를 수 있도록 등록된 카테고리를 보여준다."""
    print("등록된 카테고리: " + ", ".join(f"{i}) {n}" for i, n in enumerate(names, start=1)))


def _prompt_transaction(ctx: AppContext) -> Transaction:
    """대화형 입력으로 거래 1건을 구성한다(검증 실패 시 재입력)."""
    names = ctx.categories.names()
    today = datetime.now().strftime(DATE_FORMAT)
    print("[안내] 거래 정보를 입력하세요. (Ctrl+C 로 취소)")

    date = prompt_until_valid(
        f"날짜 (YYYY-MM-DD, 엔터={today}): ", validate_date, default=today, allow_blank=True
    )
    type_ = prompt_until_valid(
        f"타입 ({'/'.join(TRANSACTION_TYPES)}, 엔터={TYPE_EXPENSE}): ",
        validate_type,
        default=TYPE_EXPENSE,
        allow_blank=True,
    )
    _print_categories(names)
    category = prompt_until_valid("카테고리 (번호 또는 이름): ", _category_validator(ctx))
    amount = prompt_until_valid("금액 (양의 정수): ", validate_amount)
    memo = input("메모 (선택, 엔터=건너뛰기, 따옴표 없이 입력): ").strip()
    tags = parse_tags(input("태그 (선택, 엔터=건너뛰기, 쉼표 구분): "))
    return Transaction(
        id=0,
        date=date,
        type=type_,
        category=category,
        amount=amount,
        memo=memo,
        tags=tags,
    )


@command
def cmd_add(ctx: AppContext, args: argparse.Namespace) -> int:
    """거래 추가. 옵션이 하나도 없으면 대화형으로 입력받는다."""
    ctx.categories.ensure_registered()
    option_values = (args.date, args.type, args.category, args.amount, args.memo, args.tags)
    if any(v is not None for v in option_values):
        missing = [
            name
            for name, value in (
                ("--date", args.date),
                ("--type", args.type),
                ("--category", args.category),
                ("--amount", args.amount),
            )
            if value is None
        ]
        if missing:
            raise ValidationError(
                f"옵션 방식 add 에는 다음 값이 필요합니다: {', '.join(missing)}",
                "옵션 없이 `add` 만 실행하면 대화형으로 입력할 수 있습니다.",
            )
        tx = ctx.transactions.build_transaction(
            date=args.date,
            type_=args.type,
            category=args.category,
            amount=args.amount,
            memo=args.memo or "",
            tags=args.tags or "",
        )
    else:
        tx = _prompt_transaction(ctx)
    saved = ctx.transactions.add(tx)
    print(f"[저장 완료] id={saved.display_id}")
    return EXIT_OK


@command
def cmd_list(ctx: AppContext, args: argparse.Namespace) -> int:
    """거래 목록(최신순)."""
    limit = None if args.all else args.limit
    count = render_transactions(ctx.transactions.iter_latest(limit))
    total_live = ctx.transactions.live_count()
    if count == 0 and total_live == 0:
        print("[안내] 등록된 거래가 없습니다.")
        print("[힌트] `add` 명령으로 거래를 추가하세요.")
        return EXIT_OK
    if count:
        suffix = f" / 전체 {total_live}건" if limit is not None and count < total_live else ""
        print(f"\n[완료] {count}건 출력{suffix}")
    # 목록은 날짜 인덱스를, 전체 건수는 위치 인덱스(transactions.idx)를 센다. 끝까지 읽었는데(limit 에서
    # 멈추지 않음) 둘이 다르면 날짜 인덱스가 손상돼 거래가 빠진 것이다. 읽는 중에는 수정 전 옛 항목과
    # 구분할 수 없어 조용히 건너뛰므로, 여기서 건수로 잡아 알린다(빠진 거래가 있어도 이 외에는 신호가 없다).
    read_to_end = limit is None or count < limit
    if read_to_end and count != total_live:
        print(f"[경고] 날짜 인덱스와 저장된 거래 수가 맞지 않습니다 (목록 {count}건, 저장 {total_live}건).")
        print("[힌트] compact 를 실행하면 날짜 인덱스를 원본에서 다시 만듭니다.")
    return EXIT_OK


@command
def cmd_search(ctx: AppContext, args: argparse.Namespace) -> int:
    """조건 검색(여러 조건은 AND 결합, 최신순)."""
    criteria = build_criteria(ctx, args)
    if criteria.is_empty:
        raise ValidationError(
            "검색 조건이 하나도 지정되지 않았습니다.",
            "--from/--to/--month/--category/--type/--q/--tag 중 최소 하나를 지정하세요.",
        )
    limit = None if args.all else args.limit
    count = render_transactions(ctx.transactions.search(criteria, limit))
    if count == 0:
        print("[안내] 조건에 맞는 거래가 없습니다.")
        return EXIT_OK
    print(f"\n[완료] {count}건 검색됨")
    if limit is not None and count == limit:
        print(f"[안내] --limit {limit} 까지만 출력했습니다. 더 보려면 --limit 을 늘리거나 --all 을 쓰세요.")
    return EXIT_OK


CLEAR_TOKEN = "-"  # update 대화형에서 메모/태그를 비울 때 입력하는 값


def _clearable(validator: Callable[[str], str]) -> Callable[[str], str]:
    """`-` 입력은 "비우기"(빈 문자열)로 취급하는 검증기를 만든다."""

    def wrapper(raw: str) -> str:
        return "" if raw.strip() == CLEAR_TOKEN else validator(raw)

    return wrapper


def _if_changed(new: T, old: T) -> T | None:
    return None if new == old else new


@command
def cmd_update(ctx: AppContext, args: argparse.Namespace) -> int:
    """거래 수정(대화형). 현재 값을 보여주고 바꿀 항목만 입력받는다(엔터=유지, -=비우기)."""
    tx_id = parse_tx_id(args.id)
    current = ctx.transactions.get(tx_id)  # 없는 id 는 입력 전에 바로 오류 처리
    print("[현재 값]")
    render_transactions([current])
    print(f"[안내] 바꿀 항목만 입력하세요. 엔터=기존 값 유지, 메모/태그는 '{CLEAR_TOKEN}' 입력 시 비움 (Ctrl+C 로 취소)")

    date = prompt_until_valid(
        f"날짜 [{current.date}]: ", validate_date, default=current.date, allow_blank=True
    )
    type_ = prompt_until_valid(
        f"타입 [{current.type}]: ", validate_type, default=current.type, allow_blank=True
    )
    _print_categories(ctx.categories.names())
    category = prompt_until_valid(
        f"카테고리 [{current.category}] (번호 또는 이름): ",
        _category_validator(ctx),
        default=current.category,
        allow_blank=True,
    )
    amount = prompt_until_valid(
        f"금액 [{current.amount}]: ", validate_amount, default=current.amount, allow_blank=True
    )
    memo = prompt_until_valid(
        f"메모 [{current.memo}] (따옴표 없이 입력): ", _clearable(lambda raw: raw.strip()),
        default=current.memo, allow_blank=True,
    )
    tags = prompt_until_valid(
        f"태그 [{format_tags(current.tags)}] (쉼표 구분): ",
        _clearable(lambda raw: format_tags(parse_tags(raw))),
        default=format_tags(current.tags),
        allow_blank=True,
    )

    current_tags = format_tags(current.tags)
    if (date, type_, category, amount, memo, tags) == (
        current.date, current.type, current.category, current.amount, current.memo, current_tags
    ):
        print("[안내] 값이 기존과 동일하여 변경된 필드가 없습니다.")
        return EXIT_OK

    # 바뀐 필드만 넘긴다(None = 그대로 둠). 키워드로 하나씩 넘겨야 필드별 타입이 검사된다.
    before, after = ctx.transactions.update(
        tx_id,
        date=_if_changed(date, current.date),
        type_=_if_changed(type_, current.type),
        category=_if_changed(category, current.category),
        amount=_if_changed(amount, current.amount),
        memo=_if_changed(memo, current.memo),
        tags=_if_changed(tags, current_tags),
    )
    print(f"[수정 완료] id={after.display_id}")
    rows = [
        [field, str(getattr(before, field)), str(getattr(after, field))]
        for field in ("date", "type", "category", "amount", "memo", "tags")
        if getattr(before, field) != getattr(after, field)
    ]
    print(format_table(["필드", "이전", "이후"], rows, ["left", "left", "left"]))
    maybe_hint_compact(ctx)
    return EXIT_OK


@command
def cmd_delete(ctx: AppContext, args: argparse.Namespace) -> int:
    """거래 삭제(인덱스 슬롯만 0으로 초기화)."""
    tx_id = parse_tx_id(args.id)
    tx = ctx.transactions.get(tx_id)
    if not args.yes and sys.stdin.isatty():
        render_transactions([tx])
        answer = input("위 거래를 삭제할까요? (y/N): ").strip().lower()
        if answer not in ("y", "yes"):
            print("[안내] 삭제를 취소했습니다.")
            return EXIT_OK
    ctx.transactions.delete(tx_id)
    print(f"[삭제 완료] id={tx.display_id} ({tx.date} {tx.category} {format_amount(tx.amount)}원)")
    print("[안내] 삭제된 id 는 재사용되지 않습니다(번호 gap 은 정상입니다).")
    maybe_hint_compact(ctx)
    return EXIT_OK
