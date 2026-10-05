"""데이터 파일 커맨드: import / export / compact / backup."""

from __future__ import annotations

import argparse
from pathlib import Path

from ..context import AppContext
from ..decorators import EXIT_OK, command
from ..formatter import format_amount, format_table
from ..models import ValidationError
from .transaction import build_criteria

MAX_SHOWN_ERRORS = 20


@command
def cmd_import(ctx: AppContext, args: argparse.Namespace) -> int:
    """CSV 파일에서 거래를 일괄 등록한다(실패 행은 건너뜀)."""
    report = ctx.csv_service.import_csv(Path(args.source))
    print(f"[완료] {args.source} → imported={report.imported}, skipped={report.skipped}")
    if report.errors:
        print("\n[건너뛴 행]")
        rows = [[str(lineno), message] for lineno, message in report.errors[:MAX_SHOWN_ERRORS]]
        print(format_table(["행", "사유"], rows, ["right", "left"]))
        if len(report.errors) > MAX_SHOWN_ERRORS:
            print(f"... 외 {len(report.errors) - MAX_SHOWN_ERRORS}건")
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
    criteria = build_criteria(ctx, args)
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
            ["항목", "이전", "이후"],
            [
                ["로그 크기", f"{format_amount(before.log_size)}B", f"{format_amount(after.log_size)}B"],
                ["살아있는 거래", f"{before.live_count}건", f"{after.live_count}건"],
                ["슬롯 수(=최대 id)", f"{before.slot_count}", f"{after.slot_count}"],
            ],
            ["left", "right", "right"],
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
