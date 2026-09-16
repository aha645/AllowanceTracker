"""외부 라이브러리 없이 컬럼 폭을 계산해 표를 정렬 출력하는 포맷터.

한글·이모지처럼 터미널에서 두 칸을 차지하는 문자를 고려해 표시 폭을 계산한다.
"""

from __future__ import annotations

import unicodedata
from typing import Iterable, Sequence

Row = Sequence[str]


def display_width(text: str) -> int:
    """터미널 표시 폭(전각 문자는 2칸)."""
    width = 0
    for ch in text:
        if unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def pad(text: str, width: int, align: str = "left") -> str:
    """표시 폭 기준으로 문자열을 채운다. align 은 left/right/center."""
    gap = max(width - display_width(text), 0)
    if align == "right":
        return " " * gap + text
    if align == "center":
        left = gap // 2
        return " " * left + text + " " * (gap - left)
    return text + " " * gap


def format_table(
    headers: Sequence[str],
    rows: Iterable[Row],
    aligns: Sequence[str] | None = None,
) -> str:
    """헤더와 행들을 정렬된 표 문자열로 만든다. 행이 없으면 빈 문자열."""
    body = [list(map(str, row)) for row in rows]
    if not body:
        return ""
    aligns = list(aligns or ["left"] * len(headers))
    widths = [display_width(h) for h in headers]
    for row in body:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], display_width(cell))

    lines = ["  ".join(pad(h, widths[i], aligns[i]) for i, h in enumerate(headers)).rstrip()]
    lines.append("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in body:
        lines.append(
            "  ".join(pad(cell, widths[i], aligns[i]) for i, cell in enumerate(row)).rstrip()
        )
    return "\n".join(lines)


def format_amount(value: int) -> str:
    """천 단위 구분 기호를 넣은 금액 문자열."""
    return f"{value:,}"


def format_bar(ratio: float, width: int = 20) -> str:
    """0.0~1.0+ 비율을 막대 문자열로 표현한다(100% 초과분은 채움 표시)."""
    filled = min(int(round(ratio * width)), width)
    return "#" * filled + "." * (width - filled)
