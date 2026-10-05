"""반복 거래 커맨드: recurring add / list / remove / apply."""

from __future__ import annotations

import argparse

from ..console import parse_rule_id, render_transactions
from ..context import AppContext
from ..decorators import EXIT_OK, command
from ..formatter import format_amount, format_table
from ..validators import format_tags, validate_month


@command
def cmd_recurring_add(ctx: AppContext, args: argparse.Namespace) -> int:
    """반복 규칙 등록."""
    rule = ctx.recurring_service.add(
        day=args.day,
        type_=args.type,
        category=args.category,
        amount=args.amount,
        memo=args.memo or "",
        tags=args.tags or "",
    )
    print(f"[저장 완료] id={rule.display_id} (매월 {rule.day}일 {rule.category} {format_amount(rule.amount)}원)")
    return EXIT_OK


@command
def cmd_recurring_list(ctx: AppContext, args: argparse.Namespace) -> int:
    """반복 규칙 목록."""
    rules = ctx.recurring_service.list_all()
    if not rules:
        print("[안내] 등록된 반복 규칙이 없습니다.")
        print("[힌트] recurring add --day 25 --type income --category 용돈 --amount 300000")
        return EXIT_OK
    rows = [
        [
            rule.display_id,
            f"매월 {rule.day}일",
            rule.type,
            rule.category,
            f"{format_amount(rule.amount)}원",
            rule.memo,
            format_tags(rule.tags),
            str(len(rule.applied_months)),
        ]
        for rule in rules
    ]
    print(format_table(
        ["id", "주기", "타입", "카테고리", "금액", "메모", "태그", "적용월수"],
        rows,
        ["left", "left", "left", "left", "right", "left", "left", "right"],
    ))
    print(f"\n[완료] {len(rules)}개")
    return EXIT_OK


@command
def cmd_recurring_remove(ctx: AppContext, args: argparse.Namespace) -> int:
    """반복 규칙 삭제."""
    rule = ctx.recurring_service.remove(parse_rule_id(args.id))
    print(f"[삭제 완료] id={rule.display_id}")
    return EXIT_OK


@command
def cmd_recurring_apply(ctx: AppContext, args: argparse.Namespace) -> int:
    """특정 월에 반복 거래를 생성한다(이미 적용된 규칙은 건너뜀)."""
    month = validate_month(args.month)
    created, skipped = ctx.recurring_service.apply_month(month)
    if not created and not skipped:
        print("[안내] 등록된 반복 규칙이 없습니다.")
        return EXIT_OK
    print(f"[완료] {month} 반복 거래 생성 {len(created)}건, 이미 적용되어 건너뜀 {len(skipped)}건")
    if created:
        render_transactions(created)
    return EXIT_OK
