"""입력 검증/정규화 함수(날짜·월·타입·금액·일자·태그).

서비스 계층과 CLI 가 함께 쓰며, 실패하면 원인 + 해결 힌트가 담긴 ValidationError 를 던진다.
"""

from __future__ import annotations

import re
from datetime import datetime

from .models import DATE_FORMAT, MONTH_FORMAT, TRANSACTION_TYPES, ValidationError

# 줄바꿈·탭 등. 메모나 태그에 들어가면 목록 표의 한 행이 여러 줄로 나뉘어 열이 어긋난다.
_LINE_BREAKS = re.compile(r"[\r\n\t\v\f\x85\u2028\u2029]+")
# 쉼표는 천 단위(세 자리마다)로 찍은 것만 받는다. 1,2,3 이나 600,00 같은 오타가 다른 금액으로 저장되지 않게 한다.
_GROUPED_AMOUNT = re.compile(r"[+-]?\d{1,3}(?:,\d{3})+")


def validate_date(value: str) -> str:
    """`YYYY-MM-DD` 형식인지 검증하고 정규화한 문자열을 돌려준다."""
    text = (value or "").strip()
    try:
        parsed = datetime.strptime(text, DATE_FORMAT)
    except ValueError as exc:
        raise ValidationError(
            f"날짜 형식이 올바르지 않습니다: '{value}'",
            "YYYY-MM-DD 형식으로 입력하세요. 예: 2024-01-15",
        ) from exc
    normalized = parsed.strftime(DATE_FORMAT)
    if normalized != text:  # 2024-1-5 처럼 자릿수가 어긋난 입력은 거부
        raise ValidationError(
            f"날짜는 자릿수를 맞춰야 합니다: '{value}'",
            f"YYYY-MM-DD 형식으로 입력하세요. 예: {normalized}",
        )
    return normalized


def validate_month(value: str) -> str:
    """`YYYY-MM` 형식인지 검증하고 정규화한 문자열을 돌려준다."""
    text = (value or "").strip()
    try:
        parsed = datetime.strptime(text, MONTH_FORMAT)
    except ValueError as exc:
        raise ValidationError(
            f"월 형식이 올바르지 않습니다: '{value}'",
            "YYYY-MM 형식으로 입력하세요. 예: 2024-01",
        ) from exc
    normalized = parsed.strftime(MONTH_FORMAT)
    if normalized != text:  # 2024-1 처럼 자릿수가 어긋난 입력은 거부
        raise ValidationError(
            f"월은 자릿수를 맞춰야 합니다: '{value}'",
            f"YYYY-MM 형식으로 입력하세요. 예: {normalized}",
        )
    return normalized


def validate_type(value: str) -> str:
    """`income` / `expense` 만 허용한다."""
    text = (value or "").strip().lower()
    if text not in TRANSACTION_TYPES:
        raise ValidationError(
            f"타입이 올바르지 않습니다: '{value}'",
            f"{' 또는 '.join(TRANSACTION_TYPES)} 중 하나를 입력하세요.",
        )
    return text


def validate_amount(value: str | int) -> int:
    """0보다 큰 정수만 허용한다(천 단위 쉼표 입력 허용: 600000 / 600,000)."""
    text = str(value).strip()
    if "," in text:
        if not _GROUPED_AMOUNT.fullmatch(text):
            raise ValidationError(
                f"금액의 쉼표 위치가 올바르지 않습니다: '{value}'",
                "쉼표는 세 자리마다 넣거나 빼고 입력하세요. 예: 600,000 또는 600000",
            )
        text = text.replace(",", "")
    try:
        amount = int(text)
    except ValueError as exc:
        raise ValidationError(
            f"금액은 정수여야 합니다: '{value}'",
            "숫자만 입력하세요. 예: 12000",
        ) from exc
    if amount <= 0:
        raise ValidationError(
            f"금액은 0보다 커야 합니다: {amount}",
            "지출/수입 구분은 --type 으로 하고, 금액은 항상 양수로 입력하세요.",
        )
    return amount


def validate_day(value: str | int) -> int:
    """반복 규칙용 일자(1~31)."""
    text = str(value).strip()
    try:
        day = int(text)
    except ValueError as exc:
        raise ValidationError(
            f"일자는 정수여야 합니다: '{value}'", "1~31 사이의 숫자를 입력하세요."
        ) from exc
    if not 1 <= day <= 31:
        raise ValidationError(f"일자는 1~31 사이여야 합니다: {day}", "예: --day 25")
    return day


def clean_text(value: str | None) -> str:
    """메모·태그·카테고리 이름의 줄바꿈과 탭을 공백 하나로 바꾸고 앞뒤 공백을 지운다(--memo $'a\\nb', CSV 등)."""
    return _LINE_BREAKS.sub(" ", value or "").strip()


def parse_tags(value: str | None) -> list[str]:
    """쉼표 구분 문자열을 태그 리스트로 바꾼다(공백 제거, 빈 값·중복 제거)."""
    if not value:
        return []
    tags: list[str] = []
    for raw in value.split(","):
        tag = clean_text(raw)
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def format_tags(tags: list[str]) -> str:
    """태그 리스트를 CSV/화면용 쉼표 구분 문자열로 바꾼다."""
    return ",".join(tags)
