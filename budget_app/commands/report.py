"""집계 커맨드: summary / budget set·show·list·remove."""

from __future__ import annotations

import argparse

from ..console import print_budget_usage
from ..context import AppContext
from ..decorators import EXIT_OK, command
from ..formatter import format_amount, format_table
from ..models import ValidationError
from ..services import BudgetUsage
from ..validators import validate_month


@command
def cmd_summary(ctx: AppContext, args: argparse.Namespace) -> int:
    """월별 요약: 총 수입/지출/잔액 + 카테고리별 지출 TOP N + 예산 사용률."""
    month = validate_month(args.month)
    top = args.top
    if top < 1:
        raise ValidationError(f"--top 은 1 이상이어야 합니다: {top}", "예: --top 3")
    summary = ctx.transactions.summarize_month(month, top)
    budget = ctx.budget_service.get(month)

    print(f"[{month} 요약]")
    if summary.is_empty:
        print("  데이터 없음 (해당 월에 등록된 거래가 없습니다)")
        if budget is not None:
            print(f"  예산  {format_amount(budget.amount)}원 (사용 0원)")
        print("[힌트] `add` 또는 `import --from <csv>` 로 거래를 등록하세요.")
        return EXIT_OK

    print(f"  총 수입  {format_amount(summary.total_income)}원")
    print(f"  총 지출  {format_amount(summary.total_expense)}원")
    print(f"  잔액     {format_amount(summary.balance)}원  (거래 {summary.count}건)")

    if summary.expense_by_category:
        print(f"\n[카테고리별 지출 TOP {top}]")
        rows = [
            [str(rank), name, f"{format_amount(amount)}원",
             f"{amount / summary.total_expense * 100:.1f}%"]
            for rank, (name, amount) in enumerate(summary.expense_by_category, start=1)
        ]
        print(format_table(["순위", "카테고리", "지출", "비중"], rows, ["right", "left", "right", "right"]))
    else:
        print("\n[카테고리별 지출] 지출 내역 없음")

    print("\n[예산]")
    if budget is None:
        print("  설정되지 않음")
        print(f"[힌트] budget set --month {month} --amount <금액> 으로 예산을 설정할 수 있습니다.")
        return EXIT_OK
    print(f"  예산     {format_amount(budget.amount)}원")
    print_budget_usage(BudgetUsage(month, budget.amount, summary.total_expense))
    return EXIT_OK


@command
def cmd_budget_set(ctx: AppContext, args: argparse.Namespace) -> int:
    """월 예산 설정(같은 달은 덮어쓰기)."""
    budget, previous = ctx.budget_service.set_budget(args.month, args.amount)
    print(f"[저장 완료] {budget.month} 예산 {format_amount(budget.amount)}원")
    if previous is not None:
        print(f"[안내] 기존 예산 {format_amount(previous.amount)}원을 덮어썼습니다.")
    return EXIT_OK


@command
def cmd_budget_show(ctx: AppContext, args: argparse.Namespace) -> int:
    """특정 달 예산과 사용률 조회."""
    month = validate_month(args.month)
    budget = ctx.budget_service.get(month)
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
    budgets = ctx.budget_service.list_all()
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
    budget = ctx.budget_service.remove(args.month)
    print(f"[삭제 완료] {budget.month} 예산")
    return EXIT_OK
