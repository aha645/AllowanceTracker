"""월별 요약 커맨드: summary."""

from __future__ import annotations

import argparse

from ..console import print_budget_usage
from ..context import AppContext
from ..decorators import EXIT_OK, command
from ..formatter import format_amount, format_table
from ..services import BudgetUsage
from ..validators import validate_month


@command
def cmd_summary(ctx: AppContext, args: argparse.Namespace) -> int:
    """월별 요약: 총 수입/지출/잔액 + 카테고리별 지출 TOP N + 예산 사용률."""
    month = validate_month(args.month)
    top = args.top  # 1 이상만 들어온다(cli 의 _positive_int)
    summary = ctx.transactions.summarize_month(month, top)
    budget = ctx.budgets.get(month)

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
        # 제목은 요청한 N 이 아니라 실제로 출력하는 줄 수(카테고리가 N 개보다 적으면 그만큼만 나온다)
        print(f"\n[카테고리별 지출 TOP {len(summary.expense_by_category)}]")
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
