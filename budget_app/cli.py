"""argparse 파서 정의와 진입점.

모든 옵션은 `--` 표기로 통일한다.
사용법: `python -m budget_app <command> [options]`

커맨드 핸들러는 `commands/` 에, 공용 입출력 보조는 `console.py` 에 있다.
이 모듈은 "어떤 커맨드가 어떤 핸들러에 연결되는가"만 책임진다.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Callable

from . import __version__
from .commands.category import cmd_category_add, cmd_category_list, cmd_category_remove
from .commands.data import cmd_backup, cmd_compact, cmd_export, cmd_import
from .commands.recurring import (
    cmd_recurring_add,
    cmd_recurring_apply,
    cmd_recurring_list,
    cmd_recurring_remove,
)
from .commands.report import (
    cmd_budget_list,
    cmd_budget_remove,
    cmd_budget_set,
    cmd_budget_show,
    cmd_summary,
)
from .commands.transaction import cmd_add, cmd_delete, cmd_list, cmd_search, cmd_update
from .console import print_error
from .context import AppContext
from .decorators import EXIT_ERROR, EXIT_OK, configure_logging
from .models import TRANSACTION_TYPES

DEFAULT_DATA_DIR = Path("./data")
DEFAULT_LIST_LIMIT = 20
DEFAULT_TOP = 3


def _add_filter_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--from", dest="date_from", metavar="YYYY-MM-DD", help="시작일(포함)")
    parser.add_argument("--to", dest="date_to", metavar="YYYY-MM-DD", help="종료일(포함)")
    parser.add_argument("--month", metavar="YYYY-MM", help="해당 월만 조회")
    parser.add_argument("--category", help="카테고리 필터(등록된 카테고리만)")
    parser.add_argument("--type", choices=list(TRANSACTION_TYPES), help="income/expense 필터")
    parser.add_argument("--q", dest="query", metavar="KEYWORD", help="메모 키워드 검색")
    parser.add_argument("--tag", help="태그 필터(정확히 일치)")


def _add_limit_options(parser: argparse.ArgumentParser) -> None:
    """--limit 와 --all 은 서로 모순되므로 함께 쓸 수 없게 한다."""
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--limit", type=int, default=DEFAULT_LIST_LIMIT,
                       help=f"출력 건수 (기본값: {DEFAULT_LIST_LIMIT}, --all 과 함께 사용 불가)")
    group.add_argument("--all", action="store_true", help="전체 출력(--limit 과 함께 사용 불가)")


def _require_subcommand(name: str, choices: str) -> Callable[[AppContext, argparse.Namespace], int]:
    """하위 명령 없이 `category` 처럼만 실행했을 때 오류 + 힌트를 출력하는 핸들러."""

    def handler(ctx: AppContext, args: argparse.Namespace) -> int:
        print_error(
            f"{name} 하위 명령을 지정해야 합니다.",
            f"사용 가능: {choices} (자세히: {name} --help)",
        )
        return EXIT_ERROR

    return handler


def _add_transaction_parsers(sub: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    p_add = sub.add_parser("add", help="거래 추가 (옵션 없이 실행하면 대화형)")
    p_add.add_argument("--date", metavar="YYYY-MM-DD", help="거래 날짜")
    p_add.add_argument("--type", choices=list(TRANSACTION_TYPES), help="거래 타입")
    p_add.add_argument("--category", help="카테고리(등록된 것만)")
    p_add.add_argument("--amount", help="금액(0보다 큰 정수)")
    p_add.add_argument("--memo", help="메모(선택)")
    p_add.add_argument("--tags", help="태그, 쉼표 구분(선택)")
    p_add.set_defaults(func=cmd_add)

    p_list = sub.add_parser("list", help="거래 목록(최신순)")
    _add_limit_options(p_list)
    p_list.set_defaults(func=cmd_list)

    p_search = sub.add_parser("search", help="조건 검색(AND 결합, 최신순)")
    _add_filter_options(p_search)
    _add_limit_options(p_search)
    p_search.set_defaults(func=cmd_search)

    p_update = sub.add_parser("update", help="거래 수정(대화형: 현재 값을 보며 바꿀 항목만 입력)")
    p_update.add_argument("--id", required=True, metavar="TX-N", help="수정할 거래 id")
    p_update.set_defaults(func=cmd_update)

    p_delete = sub.add_parser("delete", help="거래 삭제")
    p_delete.add_argument("--id", required=True, metavar="TX-N", help="삭제할 거래 id")
    p_delete.add_argument("--yes", action="store_true", help="확인 없이 삭제")
    p_delete.set_defaults(func=cmd_delete)


def _add_report_parsers(sub: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    p_summary = sub.add_parser("summary", help="월별 요약 + 예산 사용률")
    p_summary.add_argument("--month", required=True, metavar="YYYY-MM", help="요약할 달")
    p_summary.add_argument("--top", type=int, default=DEFAULT_TOP,
                           help=f"카테고리 TOP N (기본값: {DEFAULT_TOP})")
    p_summary.set_defaults(func=cmd_summary)

    p_budget = sub.add_parser("budget", help="예산 설정/조회")
    bud_sub = p_budget.add_subparsers(dest="budget_command", metavar="<set|show|list|remove>")
    p_budget.set_defaults(func=_require_subcommand("budget", "set | show | list | remove"))

    b_set = bud_sub.add_parser("set", help="월 예산 설정(같은 달은 덮어쓰기)")
    b_set.add_argument("--month", required=True, metavar="YYYY-MM")
    b_set.add_argument("--amount", required=True, help="예산 금액(0보다 큰 정수)")
    b_set.set_defaults(func=cmd_budget_set)

    b_show = bud_sub.add_parser("show", help="특정 달 예산/사용률 조회")
    b_show.add_argument("--month", required=True, metavar="YYYY-MM")
    b_show.set_defaults(func=cmd_budget_show)

    bud_sub.add_parser("list", help="설정된 예산 전체 조회").set_defaults(func=cmd_budget_list)

    b_remove = bud_sub.add_parser("remove", help="월 예산 삭제")
    b_remove.add_argument("--month", required=True, metavar="YYYY-MM")
    b_remove.set_defaults(func=cmd_budget_remove)


def _add_category_parsers(sub: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    p_cat = sub.add_parser("category", help="카테고리 관리")
    cat_sub = p_cat.add_subparsers(dest="category_command", metavar="<add|list|remove>")
    p_cat.set_defaults(func=_require_subcommand("category", "add | list | remove"))

    c_add = cat_sub.add_parser("add", help="카테고리 추가")
    c_add.add_argument("--name", help="카테고리 이름(미지정 시 대화형 입력)")
    c_add.set_defaults(func=cmd_category_add)

    cat_sub.add_parser("list", help="카테고리 목록").set_defaults(func=cmd_category_list)

    c_remove = cat_sub.add_parser("remove", help="카테고리 삭제(사용 중이면 차단)")
    c_remove.add_argument("--name", help="카테고리 이름(미지정 시 대화형 입력)")
    c_remove.set_defaults(func=cmd_category_remove)


def _add_data_parsers(sub: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    p_import = sub.add_parser("import", help="CSV 가져오기")
    p_import.add_argument("--from", dest="source", required=True, metavar="PATH", help="CSV 파일 경로")
    p_import.set_defaults(func=cmd_import)

    p_export = sub.add_parser("export", help="CSV 내보내기(--month 또는 --from+--to 필수)")
    p_export.add_argument("--out", required=True, metavar="PATH", help="저장할 CSV 경로")
    _add_filter_options(p_export)
    p_export.set_defaults(func=cmd_export)

    sub.add_parser("compact", help="오래된 레코드 정리(로그 파일 재작성)").set_defaults(func=cmd_compact)
    sub.add_parser("backup", help="데이터 파일 타임스탬프 백업").set_defaults(func=cmd_backup)


def _add_recurring_parsers(sub: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    p_rec = sub.add_parser("recurring", help="반복 거래 규칙 관리")
    rec_sub = p_rec.add_subparsers(dest="recurring_command", metavar="<add|list|remove|apply>")
    p_rec.set_defaults(func=_require_subcommand("recurring", "add | list | remove | apply"))

    r_add = rec_sub.add_parser("add", help="반복 규칙 등록")
    r_add.add_argument("--day", required=True, help="매월 반복할 일자(1~31)")
    r_add.add_argument("--type", required=True, choices=list(TRANSACTION_TYPES))
    r_add.add_argument("--category", required=True)
    r_add.add_argument("--amount", required=True)
    r_add.add_argument("--memo")
    r_add.add_argument("--tags")
    r_add.set_defaults(func=cmd_recurring_add)

    rec_sub.add_parser("list", help="반복 규칙 목록").set_defaults(func=cmd_recurring_list)

    r_remove = rec_sub.add_parser("remove", help="반복 규칙 삭제")
    r_remove.add_argument("--id", required=True, metavar="RC-N")
    r_remove.set_defaults(func=cmd_recurring_remove)

    r_apply = rec_sub.add_parser("apply", help="특정 월에 반복 거래 생성")
    r_apply.add_argument("--month", required=True, metavar="YYYY-MM")
    r_apply.set_defaults(func=cmd_recurring_apply)


class FriendlyParser(argparse.ArgumentParser):
    """argparse 의 사용법 오류도 `[오류]`/`[힌트]` 형식으로 출력한다(종료 코드 2 유지).

    서브커맨드 파서도 부모와 같은 클래스로 만들어지므로 모든 커맨드에 적용된다.
    """

    def error(self, message: str):  # type: ignore[override]
        print_error(message, f"`{self.prog} --help` 로 사용법과 허용 값을 확인하세요.")
        self.exit(2)


def build_parser() -> argparse.ArgumentParser:
    """서브커맨드 전체를 정의한 argparse 파서를 만든다."""
    parser = FriendlyParser(
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

    sub = parser.add_subparsers(dest="command", metavar="<command>")
    _add_transaction_parsers(sub)
    _add_report_parsers(sub)
    _add_category_parsers(sub)
    _add_data_parsers(sub)
    _add_recurring_parsers(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점. 종료 코드를 돌려준다(0=성공, 1=오류)."""
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(bool(args.verbose))

    if not args.command:
        parser.print_help()
        return EXIT_OK

    ctx = AppContext.create(Path(args.data_dir))
    return int(args.func(ctx, args))
