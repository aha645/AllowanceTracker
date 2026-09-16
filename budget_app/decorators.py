"""공통 관심사 데코레이터: 예외 처리 / 실행 로그 / 실행 시간 측정.

모두 `functools.wraps` 로 원본 함수의 메타데이터(__name__, __doc__)를 보존한다.
"""

from __future__ import annotations

import functools
import logging
import sys
import time
from typing import Any, Callable, TypeVar

from .models import BudgetAppError

F = TypeVar("F", bound=Callable[..., Any])

logger = logging.getLogger("budget_app")

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INTERRUPTED = 130


def configure_logging(verbose: bool) -> None:
    """--verbose 일 때만 로그/실행시간을 stderr 로 보여준다."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="[log] %(message)s",
        stream=sys.stderr,
        force=True,
    )


def handle_errors(func: F) -> F:
    """예외를 잡아 `[오류] 원인` + `[힌트] 해결 방법` 으로 출력하고 종료 코드를 설정한다.

    스택트레이스는 사용자에게 보여주지 않는다(--verbose 시 로그로만 남긴다).
    """

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> int:
        try:
            result = func(*args, **kwargs)
        except BudgetAppError as exc:
            logger.debug("%s 처리 중 예외", func.__name__, exc_info=True)
            print(f"[오류] {exc.message}", file=sys.stderr)
            if exc.hint:
                print(f"[힌트] {exc.hint}", file=sys.stderr)
            return EXIT_ERROR
        except FileNotFoundError as exc:
            logger.debug("%s 처리 중 파일 없음", func.__name__, exc_info=True)
            print(f"[오류] 파일을 찾을 수 없습니다: {exc.filename}", file=sys.stderr)
            print("[힌트] 경로를 확인하거나 --data-dir 옵션을 점검하세요.", file=sys.stderr)
            return EXIT_ERROR
        except PermissionError as exc:
            logger.debug("%s 처리 중 권한 오류", func.__name__, exc_info=True)
            print(f"[오류] 파일에 접근할 권한이 없습니다: {exc.filename}", file=sys.stderr)
            print("[힌트] 저장 폴더의 쓰기 권한을 확인하세요.", file=sys.stderr)
            return EXIT_ERROR
        except OSError as exc:
            logger.debug("%s 처리 중 입출력 오류", func.__name__, exc_info=True)
            print(f"[오류] 입출력 오류가 발생했습니다: {exc}", file=sys.stderr)
            print("[힌트] 디스크 여유 공간과 저장 폴더 상태를 확인하세요.", file=sys.stderr)
            return EXIT_ERROR
        except (KeyboardInterrupt, EOFError):
            print()
            print("[중단] 사용자가 입력을 취소했습니다.", file=sys.stderr)
            return EXIT_INTERRUPTED
        return EXIT_OK if result is None else int(result)

    return wrapper  # type: ignore[return-value]


def log_call(func: F) -> F:
    """커맨드 진입/종료를 디버그 로그로 남긴다."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        logger.debug("-> %s 시작", func.__name__)
        try:
            return func(*args, **kwargs)
        finally:
            logger.debug("<- %s 종료", func.__name__)

    return wrapper  # type: ignore[return-value]


def timeit(func: F) -> F:
    """커맨드 실행 시간을 측정해 디버그 로그로 남긴다."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            return func(*args, **kwargs)
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            logger.debug("%s 실행 시간 %.2fms", func.__name__, elapsed)

    return wrapper  # type: ignore[return-value]


def command(func: F) -> F:
    """커맨드 핸들러용 조합 데코레이터 (@handle_errors + @log_call + @timeit)."""
    return handle_errors(log_call(timeit(func)))  # type: ignore[return-value]
