"""argparse 기반 커맨드 파싱, 대화형 입력, 출력 포맷팅.

모든 옵션은 `--` 표기로 통일한다.
사용법: `python -m budget_app <command> [options]`
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Iterator, Sequence

from . import __version__
from .decorators import EXIT_ERROR, EXIT_OK, command, configure_logging
from .formatter import format_amount, format_bar, format_table
from .models import (
    DATE_FORMAT,
    TRANSACTION_TYPES,
    TYPE_EXPENSE,
    TYPE_INCOME,
    Transaction,
    ValidationError,
)
from .repository import TransactionRepository
from .services import (
    BackupService,
    BudgetService,
    CsvService,
    RecurringService,
    SearchCriteria,
    TransactionService,
    format_tags,
    parse_tags,
    validate_amount,
    validate_date,
    validate_month,
    validate_type,
)
from .stores import BudgetStore, CategoryStore, RecurringStore

DEFAULT_DATA_DIR = Path("./data")
DEFAULT_LIST_LIMIT = 20
DEFAULT_TOP = 3

LIST_HEADERS = ("#", "id", "날짜", "타입", "카테고리", "금액", "메모", "태그")
LIST_ALIGNS = ("right", "left", "left", "left", "left", "right", "left", "left")


# --------------------------------------------------------------------- 컨텍스트
@dataclass(slots=True)
class AppContext:
    """커맨드 실행에 필요한 저장소/서비스 묶음."""

    data_dir: Path
    repo: TransactionRepository
    categories: CategoryStore
    budgets: BudgetStore
    recurring_store: RecurringStore
    transactions: TransactionService
    budget_service: BudgetService
    csv_service: CsvService
    backup_service: BackupService
    recurring_service: RecurringService

    @classmethod
    def create(cls, data_dir: Path) -> "AppContext":
        """저장 폴더를 준비하고(없으면 생성) 서비스 객체들을 조립한다."""
        data_dir = Path(data_dir).expanduser()
        repo = TransactionRepository(data_dir)
        categories = CategoryStore(data_dir)
        budgets = BudgetStore(data_dir)
        recurring_store = RecurringStore(data_dir)
        repo.ensure_files()
        categories.ensure_file()
        budgets.ensure_file()
        recurring_store.ensure_file()
        tx_service = TransactionService(repo, categories)
        return cls(
            data_dir=data_dir,
            repo=repo,
            categories=categories,
            budgets=budgets,
            recurring_store=recurring_store,
            transactions=tx_service,
            budget_service=BudgetService(budgets),
            csv_service=CsvService(tx_service),
            backup_service=BackupService(data_dir),
            recurring_service=RecurringService(recurring_store, tx_service),
        )


# --------------------------------------------------------------------- 공통 출력/입력
def print_error(message: str, hint: str = "") -> None:
    print(f"[오류] {message}", file=sys.stderr)
    if hint:
        print(f"[힌트] {hint}", file=sys.stderr)


def parse_tx_id(value: str) -> int:
    """`TX-3` 또는 `3` 형태의 id 문자열을 정수로 바꾼다."""
    text = str(value).strip().upper()
    if text.startswith("TX-"):
        text = text[3:]
    try:
        tx_id = int(text)
    except ValueError as exc:
        raise ValidationError(
            f"거래 id 형식이 올바르지 않습니다: '{value}'",
            "예: --id TX-3 또는 --id 3",
        ) from exc
    if tx_id < 1:
        raise ValidationError(f"거래 id 는 1 이상이어야 합니다: {tx_id}", "list 로 id 를 확인하세요.")
    return tx_id


def parse_rule_id(value: str) -> int:
    """`RC-2` 또는 `2` 형태의 반복 규칙 id 를 정수로 바꾼다."""
    text = str(value).strip().upper()
    if text.startswith("RC-"):
        text = text[3:]
    try:
        rule_id = int(text)
    except ValueError as exc:
        raise ValidationError(
            f"반복 규칙 id 형식이 올바르지 않습니다: '{value}'", "예: --id RC-2 또는 --id 2"
        ) from exc
    return rule_id


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
    """거래 목록을 표로 출력하고 출력 건수를 돌려준다(스트리밍 유지)."""
    table_rows: list[tuple[str, ...]] = []
    count = 0
    for tx in rows:
        count += 1
        sign = "+" if tx.type == TYPE_INCOME else "-"
        table_rows.append(
            (
                str(count),  # 화면용 순번 (내부 id 와 무관)
                tx.display_id,
                tx.date,
                tx.type,
                tx.category,
                f"{sign}{format_amount(tx.amount)}",
                tx.memo,
                format_tags(tx.tags),
            )
        )
    if not table_rows:
        return 0
    print(format_table(LIST_HEADERS, table_rows, LIST_ALIGNS))
    return count


def maybe_hint_compact(ctx: AppContext) -> None:
    """고아 데이터가 많이 쌓였으면 compact 를 안내한다."""
    if ctx.repo.needs_compact():
        stats = ctx.repo.stats()
        print(
            f"[안내] 로그의 {stats.orphan_ratio * 100:.0f}% 가 오래된 버전입니다. "
            "`compact` 명령으로 정리할 수 있습니다."
        )


# --------------------------------------------------------------------- 커맨드 핸들러
@command
def cmd_add(ctx: AppContext, args: argparse.Namespace) -> int:
    """거래 추가. 옵션이 하나도 없으면 대화형으로 입력받는다."""
    ctx.transactions.ensure_categories_exist()
    option_mode = any(
        v is not None for v in (args.date, args.type, args.category, args.amount, args.memo, args.tags)
    )
    if option_mode:
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
    print("등록된 카테고리: " + ", ".join(f"{i}) {n}" for i, n in enumerate(names, start=1)))

    def category_validator(raw: str) -> str:
        text = raw.strip()
        if text.isdigit() and 1 <= int(text) <= len(names):
            return names[int(text) - 1]
        return ctx.transactions.validate_category(text)

    category = prompt_until_valid("카테고리 (번호 또는 이름): ", category_validator)
    amount = prompt_until_valid("금액 (양의 정수): ", validate_amount)
    memo = input("메모 (선택, 엔터=건너뛰기): ").strip()
    tags = parse_tags(input("태그 (선택, 쉼표 구분): "))
    return Transaction(
        id=0,
        date=str(date),
        type=str(type_),
        category=str(category),
        amount=int(amount),  # type: ignore[arg-type]
        memo=memo,
        tags=tags,
    )


@command
def cmd_list(ctx: AppContext, args: argparse.Namespace) -> int:
    """거래 목록(최신순)."""
    limit = None if args.all else args.limit
    count = render_transactions(ctx.transactions.iter_latest(limit))
    if count == 0:
        print("[안내] 등록된 거래가 없습니다.")
        print("[힌트] `add` 명령으로 거래를 추가하세요.")
        return EXIT_OK
    total_live = ctx.repo.stats().live_count
    suffix = f" / 전체 {total_live}건" if limit is not None and count < total_live else ""
    print(f"\n[완료] {count}건 출력{suffix}")
    return EXIT_OK


@command
def cmd_search(ctx: AppContext, args: argparse.Namespace) -> int:
    """조건 검색(여러 조건은 AND 결합, 최신순)."""
    criteria = _build_criteria(ctx, args)
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


def _build_criteria(ctx: AppContext, args: argparse.Namespace) -> SearchCriteria:
    """공통 필터 옵션을 검증해 SearchCriteria 로 만든다."""
    date_from = validate_date(args.date_from) if getattr(args, "date_from", None) else None
    date_to = validate_date(args.date_to) if getattr(args, "date_to", None) else None
    month = validate_month(args.month) if getattr(args, "month", None) else None
    if date_from and date_to and date_from > date_to:
        raise ValidationError(
            f"기간이 뒤집혔습니다: --from {date_from} > --to {date_to}",
            "--from 에는 더 이른 날짜를 지정하세요.",
        )
    category = getattr(args, "category", None)
    if category:
        category = ctx.transactions.validate_category(category)
    type_ = validate_type(args.type) if getattr(args, "type", None) else None
    return SearchCriteria(
        date_from=date_from,
        date_to=date_to,
        month=month,
        category=category,
        type=type_,
        query=getattr(args, "query", None),
        tag=getattr(args, "tag", None),
    )


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
            (str(rank), name, f"{format_amount(amount)}원",
             f"{amount / summary.total_expense * 100:.1f}%")
            for rank, (name, amount) in enumerate(summary.expense_by_category, start=1)
        ]
        print(format_table(("순위", "카테고리", "지출", "비중"), rows, ("right", "left", "right", "right")))
    else:
        print("\n[카테고리별 지출] 지출 내역 없음")

    usage = ctx.budget_service.usage(month, summary.total_expense)
    print(f"\n[예산]")
    if usage is None:
        print("  설정되지 않음")
        print(f"[힌트] budget set --month {month} --amount <금액> 으로 예산을 설정할 수 있습니다.")
        return EXIT_OK
    print(f"  예산     {format_amount(usage.budget)}원")
    print(f"  사용     {format_amount(usage.spent)}원 ({usage.percent:.1f}%) [{format_bar(usage.ratio)}]")
    if usage.is_over:
        print(f"  [경고] 예산을 {format_amount(-usage.remaining)}원 초과했습니다!")
    else:
        print(f"  잔여     {format_amount(usage.remaining)}원")
    return EXIT_OK


@command
def cmd_update(ctx: AppContext, args: argparse.Namespace) -> int:
    """거래 수정(옵션 기반, 지정하지 않은 필드는 기존 값 유지)."""
    tx_id = parse_tx_id(args.id)
    before, after = ctx.transactions.update(
        tx_id,
        date=args.date,
        type_=args.type,
        category=args.category,
        amount=args.amount,
        memo=args.memo,
        tags=args.tags,
    )
    print(f"[수정 완료] id={after.display_id}")
    changes = [
        (field, str(getattr(before, field)), str(getattr(after, field)))
        for field in ("date", "type", "category", "amount", "memo", "tags")
        if getattr(before, field) != getattr(after, field)
    ]
    if changes:
        print(format_table(("필드", "이전", "이후"), changes, ("left", "left", "left")))
    else:
        print("[안내] 값이 기존과 동일하여 변경된 필드가 없습니다.")
    maybe_hint_compact(ctx)
    return EXIT_OK


@command
def cmd_delete(ctx: AppContext, args: argparse.Namespace) -> int:
    """거래 삭제(인덱스 슬롯만 0으로 초기화)."""
    tx_id = parse_tx_id(args.id)
    tx = ctx.repo.get_transaction(tx_id)
    if not args.yes and sys.stdin.isatty():
        render_transactions([tx])
        answer = input("위 거래를 삭제할까요? (y/N): ").strip().lower()
        if answer not in ("y", "yes"):
            print("[안내] 삭제를 취소했습니다.")
            return EXIT_OK
    ctx.transactions.delete(tx_id)
    print(f"[삭제 완료] id={tx.display_id} ({tx.date} {tx.category} {format_amount(tx.amount)}원)")
    print("[안내] 삭제된 id 는 재사용되지 않습니다(번호 gap 은 정상입니다).")
    return EXIT_OK


@command
def cmd_category(ctx: AppContext, args: argparse.Namespace) -> int:
    """카테고리 추가/목록/삭제."""
    action = args.category_command
    if action == "add":
        name = args.name
        if name is None:
            name = input("추가할 카테고리 이름: ")
        category = ctx.categories.add(name)
        print(f"[저장 완료] 카테고리 '{category.name}' 추가")
        return EXIT_OK

    if action == "list":
        categories = ctx.categories.list_all()
        if not categories:
            print("[안내] 등록된 카테고리가 없습니다.")
            print('[힌트] category add --name "식비" 로 먼저 등록하세요.')
            return EXIT_OK
        rows = [(str(i), c.name, c.created_at or "-") for i, c in enumerate(categories, start=1)]
        print(format_table(("#", "카테고리", "등록일시"), rows, ("right", "left", "left")))
        print(f"\n[완료] {len(categories)}개")
        return EXIT_OK

    # remove
    name = args.name if args.name is not None else input("삭제할 카테고리 이름: ")
    name = name.strip()
    if not ctx.categories.exists(name):
        raise ValidationError(
            f"카테고리 '{name}' 을(를) 찾을 수 없습니다.", "category list 로 목록을 확인하세요."
        )
    in_use = ctx.transactions.is_category_in_use(name)
    if in_use is not None:
        raise ValidationError(
            f"카테고리 '{name}' 은(는) 사용 중이라 삭제할 수 없습니다 (예: {in_use.display_id} {in_use.date}).",
            f'search --category "{name}" 로 확인 후, 해당 거래를 update/delete 한 뒤 다시 시도하세요.',
        )
    ctx.categories.remove(name)
    print(f"[삭제 완료] 카테고리 '{name}'")
    return EXIT_OK


@command
def cmd_budget(ctx: AppContext, args: argparse.Namespace) -> int:
    """예산 설정/조회/삭제."""
    action = args.budget_command
    if action == "set":
        budget, previous = ctx.budget_service.set_budget(args.month, args.amount)
        print(f"[저장 완료] {budget.month} 예산 {format_amount(budget.amount)}원")
        if previous is not None:
            print(f"[안내] 기존 예산 {format_amount(previous.amount)}원을 덮어썼습니다.")
        return EXIT_OK

    if action == "list":
        budgets = ctx.budget_service.list_all()
        if not budgets:
            print("[안내] 설정된 예산이 없습니다.")
            print("[힌트] budget set --month 2024-01 --amount 500000")
            return EXIT_OK
        rows = []
        for budget in budgets:
            summary = ctx.transactions.summarize_month(budget.month, top=0)
            usage = ctx.budget_service.usage(budget.month, summary.total_expense)
            assert usage is not None
            rows.append(
                (
                    budget.month,
                    f"{format_amount(budget.amount)}원",
                    f"{format_amount(usage.spent)}원",
                    f"{usage.percent:.1f}%",
                    "초과" if usage.is_over else "",
                )
            )
        print(format_table(
            ("월", "예산", "사용", "사용률", "상태"),
            rows,
            ("left", "right", "right", "right", "left"),
        ))
        print(f"\n[완료] {len(budgets)}개")
        return EXIT_OK

    if action == "remove":
        budget = ctx.budget_service.remove(args.month)
        print(f"[삭제 완료] {budget.month} 예산")
        return EXIT_OK

    # show
    month = validate_month(args.month)
    budget = ctx.budget_service.get(month)
    if budget is None:
        print(f"[안내] {month} 예산이 설정되지 않았습니다.")
        print(f"[힌트] budget set --month {month} --amount <금액>")
        return EXIT_OK
    summary = ctx.transactions.summarize_month(month, top=0)
    usage = ctx.budget_service.usage(month, summary.total_expense)
    assert usage is not None
    print(f"[{month} 예산] {format_amount(usage.budget)}원")
    print(f"  사용 {format_amount(usage.spent)}원 ({usage.percent:.1f}%) [{format_bar(usage.ratio)}]")
    if usage.is_over:
        print(f"  [경고] 예산을 {format_amount(-usage.remaining)}원 초과했습니다!")
    else:
        print(f"  잔여 {format_amount(usage.remaining)}원")
    return EXIT_OK


@command
def cmd_import(ctx: AppContext, args: argparse.Namespace) -> int:
    """CSV 파일에서 거래를 일괄 등록한다(실패 행은 건너뜀)."""
    report = ctx.csv_service.import_csv(Path(args.source))
    print(f"[완료] {args.source} → imported={report.imported}, skipped={report.skipped}")
    if report.errors:
        print("\n[건너뛴 행]")
        rows = [(str(lineno), message) for lineno, message in report.errors[:20]]
        print(format_table(("행", "사유"), rows, ("right", "left")))
        if len(report.errors) > 20:
            print(f"... 외 {len(report.errors) - 20}건")
    return EXIT_OK


@command
def cmd_export(ctx: AppContext, args: argparse.Namespace) -> int:
    """조건에 맞는 거래를 CSV 로 내보낸다(--month 또는 --from+--to 필수)."""
    has_range = bool(args.date_from) and bool(args.date_to)
    if not args.month and not has_range:
        raise ValidationError(
            "내보내기 조건이 없습니다.",
            "--month YYYY-MM 또는 --from YYYY-MM-DD --to YYYY-MM-DD 를 지정하세요.",
        )
    if args.month and (args.date_from or args.date_to):
        raise ValidationError(
            "--month 와 --from/--to 는 함께 쓸 수 없습니다.",
            "둘 중 한 가지 방식만 사용하세요.",
        )
    criteria = _build_criteria(ctx, args)
    count = ctx.csv_service.export_csv(Path(args.out), ctx.transactions.search(criteria))
    print(f"[완료] {args.out} ({count} records)")
    if count == 0:
        print("[안내] 조건에 맞는 거래가 없어 헤더만 기록했습니다.")
    return EXIT_OK


@command
def cmd_compact(ctx: AppContext, args: argparse.Namespace) -> int:
    """오래된(고아) 레코드를 정리해 로그 파일을 다시 쓴다."""
    before = ctx.repo.compact()
    after = ctx.repo.stats()
    saved = before.log_size - after.log_size
    print("[완료] compact")
    print(
        format_table(
            ("항목", "이전", "이후"),
            [
                ("로그 크기", f"{format_amount(before.log_size)}B", f"{format_amount(after.log_size)}B"),
                ("살아있는 거래", f"{before.live_count}건", f"{after.live_count}건"),
                ("슬롯 수(=최대 id)", f"{before.slot_count}", f"{after.slot_count}"),
            ],
            ("left", "right", "right"),
        )
    )
    print(f"\n[안내] {format_amount(max(saved, 0))}B 를 회수했습니다. id 는 변하지 않습니다.")
    return EXIT_OK


@command
def cmd_backup(ctx: AppContext, args: argparse.Namespace) -> int:
    """데이터 파일을 타임스탬프 폴더로 백업한다."""
    target, copied = ctx.backup_service.create()
    print(f"[완료] 백업 생성: {target}")
    for name in copied:
        print(f"  - {name}")
    return EXIT_OK


@command
def cmd_recurring(ctx: AppContext, args: argparse.Namespace) -> int:
    """반복 거래 규칙 등록/목록/삭제/적용."""
    action = args.recurring_command
    if action == "add":
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

    if action == "list":
        rules = ctx.recurring_service.list_all()
        if not rules:
            print("[안내] 등록된 반복 규칙이 없습니다.")
            print("[힌트] recurring add --day 25 --type income --category 용돈 --amount 300000")
            return EXIT_OK
        rows = [
            (
                rule.display_id,
                f"매월 {rule.day}일",
                rule.type,
                rule.category,
                f"{format_amount(rule.amount)}원",
                rule.memo,
                format_tags(rule.tags),
                str(len(rule.applied_months)),
            )
            for rule in rules
        ]
        print(format_table(
            ("id", "주기", "타입", "카테고리", "금액", "메모", "태그", "적용월수"),
            rows,
            ("left", "left", "left", "left", "right", "left", "left", "right"),
        ))
        print(f"\n[완료] {len(rules)}개")
        return EXIT_OK

    if action == "remove":
        rule = ctx.recurring_service.remove(parse_rule_id(args.id))
        print(f"[삭제 완료] id={rule.display_id}")
        return EXIT_OK

    # apply
    created, skipped = ctx.recurring_service.apply_month(args.month)
    month = validate_month(args.month)
    if not created and not skipped:
        print("[안내] 등록된 반복 규칙이 없습니다.")
        return EXIT_OK
    print(f"[완료] {month} 반복 거래 생성 {len(created)}건, 이미 적용되어 건너뜀 {len(skipped)}건")
    if created:
        render_transactions(created)
    return EXIT_OK


# --------------------------------------------------------------------- 파서
def _add_filter_options(parser: argparse.ArgumentParser, *, with_month: bool = True) -> None:
    parser.add_argument("--from", dest="date_from", metavar="YYYY-MM-DD", help="시작일(포함)")
    parser.add_argument("--to", dest="date_to", metavar="YYYY-MM-DD", help="종료일(포함)")
    if with_month:
        parser.add_argument("--month", metavar="YYYY-MM", help="해당 월만 조회")
    parser.add_argument("--category", help="카테고리 필터(등록된 카테고리만)")
    parser.add_argument("--type", choices=list(TRANSACTION_TYPES), help="income/expense 필터")
    parser.add_argument("--q", dest="query", metavar="KEYWORD", help="메모 키워드 검색")
    parser.add_argument("--tag", help="태그 필터(정확히 일치)")


def build_parser() -> argparse.ArgumentParser:
    """서브커맨드 전체를 정의한 argparse 파서를 만든다."""
    parser = argparse.ArgumentParser(
        prog="python -m budget_app",
        description="콘솔 가계부 — 거래 기록/검색/월별 요약/예산 관리",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "예시:\n"
            '  python -m budget_app category add --name "식비"\n'
            "  python -m budget_app add\n"
            "  python -m budget_app list --limit 10\n"
            "  python -m budget_app search --month 2024-01 --type expense\n"
            "  python -m budget_app summary --month 2024-01 --top 3\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"budget_app {__version__}")
    parser.add_argument(
        "--data-dir",
        default=str(DEFAULT_DATA_DIR),
        metavar="PATH",
        help=f"저장 폴더 (기본값: {DEFAULT_DATA_DIR})",
    )
    parser.add_argument("--verbose", action="store_true", help="실행 로그/시간 측정 출력")

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    # add
    p_add = subparsers.add_parser("add", help="거래 추가 (옵션 없이 실행하면 대화형)")
    p_add.add_argument("--date", metavar="YYYY-MM-DD", help="거래 날짜")
    p_add.add_argument("--type", choices=list(TRANSACTION_TYPES), help="거래 타입")
    p_add.add_argument("--category", help="카테고리(등록된 것만)")
    p_add.add_argument("--amount", help="금액(0보다 큰 정수)")
    p_add.add_argument("--memo", help="메모(선택)")
    p_add.add_argument("--tags", help="태그, 쉼표 구분(선택)")
    p_add.set_defaults(func=cmd_add)

    # list
    p_list = subparsers.add_parser("list", help="거래 목록(최신순)")
    p_list.add_argument("--limit", type=int, default=DEFAULT_LIST_LIMIT,
                        help=f"출력 건수 (기본값: {DEFAULT_LIST_LIMIT})")
    p_list.add_argument("--all", action="store_true", help="전체 출력(--limit 무시)")
    p_list.set_defaults(func=cmd_list)

    # search
    p_search = subparsers.add_parser("search", help="조건 검색(AND 결합, 최신순)")
    _add_filter_options(p_search)
    p_search.add_argument("--limit", type=int, default=DEFAULT_LIST_LIMIT,
                          help=f"출력 건수 (기본값: {DEFAULT_LIST_LIMIT})")
    p_search.add_argument("--all", action="store_true", help="전체 출력(--limit 무시)")
    p_search.set_defaults(func=cmd_search)

    # summary
    p_summary = subparsers.add_parser("summary", help="월별 요약 + 예산 사용률")
    p_summary.add_argument("--month", required=True, metavar="YYYY-MM", help="요약할 달")
    p_summary.add_argument("--top", type=int, default=DEFAULT_TOP,
                           help=f"카테고리 TOP N (기본값: {DEFAULT_TOP})")
    p_summary.set_defaults(func=cmd_summary)

    # update
    p_update = subparsers.add_parser("update", help="거래 수정(옵션 기반, 미지정 필드는 유지)")
    p_update.add_argument("--id", required=True, metavar="TX-N", help="수정할 거래 id")
    p_update.add_argument("--date", metavar="YYYY-MM-DD", help="새 날짜")
    p_update.add_argument("--type", choices=list(TRANSACTION_TYPES), help="새 타입")
    p_update.add_argument("--category", help="새 카테고리")
    p_update.add_argument("--amount", help="새 금액")
    p_update.add_argument("--memo", help="새 메모")
    p_update.add_argument("--tags", help="새 태그(쉼표 구분, 빈 문자열이면 전체 삭제)")
    p_update.set_defaults(func=cmd_update)

    # delete
    p_delete = subparsers.add_parser("delete", help="거래 삭제")
    p_delete.add_argument("--id", required=True, metavar="TX-N", help="삭제할 거래 id")
    p_delete.add_argument("--yes", action="store_true", help="확인 없이 삭제")
    p_delete.set_defaults(func=cmd_delete)

    # category
    p_cat = subparsers.add_parser("category", help="카테고리 관리")
    cat_sub = p_cat.add_subparsers(dest="category_command", metavar="<add|list|remove>")
    c_add = cat_sub.add_parser("add", help="카테고리 추가")
    c_add.add_argument("--name", help="카테고리 이름(미지정 시 대화형 입력)")
    cat_sub.add_parser("list", help="카테고리 목록")
    c_remove = cat_sub.add_parser("remove", help="카테고리 삭제(사용 중이면 차단)")
    c_remove.add_argument("--name", help="카테고리 이름(미지정 시 대화형 입력)")
    p_cat.set_defaults(func=cmd_category, category_command=None)

    # budget
    p_budget = subparsers.add_parser("budget", help="예산 설정/조회")
    bud_sub = p_budget.add_subparsers(dest="budget_command", metavar="<set|show|list|remove>")
    b_set = bud_sub.add_parser("set", help="월 예산 설정(같은 달은 덮어쓰기)")
    b_set.add_argument("--month", required=True, metavar="YYYY-MM")
    b_set.add_argument("--amount", required=True, help="예산 금액(0보다 큰 정수)")
    b_show = bud_sub.add_parser("show", help="특정 달 예산/사용률 조회")
    b_show.add_argument("--month", required=True, metavar="YYYY-MM")
    bud_sub.add_parser("list", help="설정된 예산 전체 조회")
    b_remove = bud_sub.add_parser("remove", help="월 예산 삭제")
    b_remove.add_argument("--month", required=True, metavar="YYYY-MM")
    p_budget.set_defaults(func=cmd_budget, budget_command=None)

    # import / export
    p_import = subparsers.add_parser("import", help="CSV 가져오기")
    p_import.add_argument("--from", dest="source", required=True, metavar="PATH", help="CSV 파일 경로")
    p_import.set_defaults(func=cmd_import)

    p_export = subparsers.add_parser("export", help="CSV 내보내기(--month 또는 --from+--to 필수)")
    p_export.add_argument("--out", required=True, metavar="PATH", help="저장할 CSV 경로")
    _add_filter_options(p_export)
    p_export.set_defaults(func=cmd_export)

    # 유지보수 / 보너스
    subparsers.add_parser("compact", help="오래된 레코드 정리(로그 파일 재작성)").set_defaults(func=cmd_compact)
    subparsers.add_parser("backup", help="데이터 파일 타임스탬프 백업").set_defaults(func=cmd_backup)

    p_rec = subparsers.add_parser("recurring", help="반복 거래 규칙 관리")
    rec_sub = p_rec.add_subparsers(dest="recurring_command", metavar="<add|list|remove|apply>")
    r_add = rec_sub.add_parser("add", help="반복 규칙 등록")
    r_add.add_argument("--day", required=True, help="매월 반복할 일자(1~31)")
    r_add.add_argument("--type", required=True, choices=list(TRANSACTION_TYPES))
    r_add.add_argument("--category", required=True)
    r_add.add_argument("--amount", required=True)
    r_add.add_argument("--memo")
    r_add.add_argument("--tags")
    rec_sub.add_parser("list", help="반복 규칙 목록")
    r_remove = rec_sub.add_parser("remove", help="반복 규칙 삭제")
    r_remove.add_argument("--id", required=True, metavar="RC-N")
    r_apply = rec_sub.add_parser("apply", help="특정 월에 반복 거래 생성")
    r_apply.add_argument("--month", required=True, metavar="YYYY-MM")
    p_rec.set_defaults(func=cmd_recurring, recurring_command=None)

    return parser


SUBCOMMAND_REQUIRED = {
    "category": ("category_command", "add | list | remove"),
    "budget": ("budget_command", "set | show | list | remove"),
    "recurring": ("recurring_command", "add | list | remove | apply"),
}


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 진입점. 종료 코드를 돌려준다(0=성공, 1=오류)."""
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(bool(args.verbose))

    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_OK

    if args.command in SUBCOMMAND_REQUIRED:
        attr, choices = SUBCOMMAND_REQUIRED[args.command]
        if getattr(args, attr, None) is None:
            print_error(
                f"{args.command} 하위 명령을 지정해야 합니다.",
                f"사용 가능: {choices} (자세히: {args.command} --help)",
            )
            return EXIT_ERROR

    ctx = AppContext.create(Path(args.data_dir))
    return int(args.func(ctx, args))
