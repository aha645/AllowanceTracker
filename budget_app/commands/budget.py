"""예산 커맨드: budget set / show / list / remove."""

from __future__ import annotations

import argparse

from ..console import print_budget_usage
from ..context import AppContext
from ..decorators import EXIT_OK, command
from ..formatter import format_amount, format_table
from ..services import BudgetUsage
from ..validators import validate_month


@command
def cmd_budget_set(ctx: AppContext, args: argparse.Namespace) -> int:
    """월 예산 설정(같은 달은 덮어쓰기)."""
    budget, previous = ctx.budgets.set_budget(args.month, args.amount)
    print(f"[저장 완료] {budget.month} 예산 {format_amount(budget.amount)}원")
    if previous is not None:
        print(f"[안내] 기존 예산 {format_amount(previous.amount)}원을 덮어썼습니다.")
    return EXIT_OK


@command
def cmd_budget_show(ctx: AppContext, args: argparse.Namespace) -> int:
    """특정 달 예산과 사용률 조회."""
    month = validate_month(args.month)
    budget = ctx.budgets.get(month)
    if budget is None:
        print(f"[안내] {month} 예산이 설정되지 않았습니다.")
        print(f"[힌트] budget set --month {month} --amount <금액>")
        return EXIT_OK
    spent = ctx.transactions.summarize_month(month, top=0).total_expense
    print(f"[{month} 예산] {format_amount(budget.amount)}원")
    print_budget_usage(BudgetUsage(month, budget.amount, spent))
    return EXIT_OK


@command
def cmd_budget_list(ctx: AppContext, args: argparse.Namespace) -> int:
    """설정된 예산 전체 조회."""
    budgets = ctx.budgets.list_all()
    if not budgets:
        print("[안내] 설정된 예산이 없습니다.")
        print("[힌트] budget set --month 2024-01 --amount 500000")
        return EXIT_OK
    rows = []
    for budget in budgets:
        spent = ctx.transactions.summarize_month(budget.month, top=0).total_expense
        usage = BudgetUsage(budget.month, budget.amount, spent)
        rows.append(
            [
                budget.month,
                f"{format_amount(budget.amount)}원",
                f"{format_amount(usage.spent)}원",
                f"{usage.percent:.1f}%",
                "초과" if usage.is_over else "",
            ]
        )
    print(format_table(
        ["월", "예산", "사용", "사용률", "상태"],
        rows,
        ["left", "right", "right", "right", "left"],
    ))
    print(f"\n[완료] {len(budgets)}개")
    return EXIT_OK


@command
def cmd_budget_remove(ctx: AppContext, args: argparse.Namespace) -> int:
    """월 예산 삭제."""
    budget = ctx.budgets.remove(args.month)
    print(f"[삭제 완료] {budget.month} 예산")
    return EXIT_OK
