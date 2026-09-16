"""`python -m budget_app` 진입점.

최상위에서 종료 코드를 sys.exit 으로 전달한다(성공 0, 오류 1, 사용자 중단 130).
"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
