"""카테고리 커맨드: category add / list / remove."""

from __future__ import annotations

import argparse

from ..context import AppContext
from ..decorators import EXIT_OK, command
from ..formatter import format_table
from ..models import ValidationError


@command
def cmd_category_add(ctx: AppContext, args: argparse.Namespace) -> int:
    """카테고리 추가(이름 미지정 시 대화형 입력)."""
    name = args.name if args.name is not None else input("추가할 카테고리 이름: ")
    category = ctx.categories.add(name)
    print(f"[저장 완료] 카테고리 '{category.name}' 추가")
    return EXIT_OK


@command
def cmd_category_list(ctx: AppContext, args: argparse.Namespace) -> int:
    """카테고리 목록."""
    categories = ctx.categories.list_all()
    if not categories:
        print("[안내] 등록된 카테고리가 없습니다.")
        print('[힌트] category add --name "식비" 로 먼저 등록하세요.')
        return EXIT_OK
    rows = [[str(i), c.name, c.created_at or "-"] for i, c in enumerate(categories, start=1)]
    print(format_table(["#", "카테고리", "등록일시"], rows, ["right", "left", "left"]))
    print(f"\n[완료] {len(categories)}개")
    return EXIT_OK


@command
def cmd_category_remove(ctx: AppContext, args: argparse.Namespace) -> int:
    """카테고리 삭제(사용 중이면 차단)."""
    name = (args.name if args.name is not None else input("삭제할 카테고리 이름: ")).strip()
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
