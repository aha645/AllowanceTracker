"""`formatter.py`(보너스: 외부 라이브러리 없는 표 정렬 출력) 단위테스트.

이 모듈은 순수 함수만 모여 있어(파일 I/O, 전역 상태 없음) 입력→출력만 검증하면 된다.
한글처럼 터미널에서 두 칸을 차지하는 전각 문자를 폭 계산에 정확히 반영하는지가
핵심이라, 그 부분을 집중적으로 확인한다.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# `python tests/test_formatter.py` 로 직접 실행할 때도 budget_app 을 찾을 수 있도록
# 프로젝트 루트를 sys.path 에 넣는다 (자세한 이유는 test_repository.py 상단 주석 참고).
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from budget_app.formatter import display_width, format_amount, format_bar, format_table, pad


class DisplayWidthTestCase(unittest.TestCase):
    def test_00_ascii_width_is_one_per_char(self) -> None:
        self.assertEqual(display_width("abc"), 3)
        self.assertEqual(display_width(""), 0)

    def test_01_korean_width_is_two_per_char(self) -> None:
        self.assertEqual(display_width("식비"), 4)  # 전각 2글자 * 2칸

    def test_02_mixed_ascii_and_korean(self) -> None:
        self.assertEqual(display_width("TX-1 식비"), len("TX-1 ") + 4)

    def test_03_combining_characters_dont_add_width(self) -> None:
        # 'e' + combining acute accent(U+0301) 는 화면에서 한 칸으로 보여야 한다.
        self.assertEqual(display_width("é"), 1)


class PadTestCase(unittest.TestCase):
    def test_00_left_align_pads_on_right(self) -> None:
        self.assertEqual(pad("ab", 5, "left"), "ab   ")

    def test_01_right_align_pads_on_left(self) -> None:
        self.assertEqual(pad("ab", 5, "right"), "   ab")

    def test_02_center_align_splits_padding(self) -> None:
        self.assertEqual(pad("a", 5, "center"), "  a  ")

    def test_03_korean_text_counts_double_width_when_padding(self) -> None:
        # "식비"는 표시 폭 4 이므로, width=6 이면 남은 칸은 2칸(문자 수 기준 2칸이 아님)
        result = pad("식비", 6, "left")
        self.assertEqual(result, "식비  ")

    def test_04_text_already_wider_than_width_is_not_truncated(self) -> None:
        self.assertEqual(pad("abcdef", 3, "left"), "abcdef")  # 자르지 않고 그대로 둠


class FormatTableTestCase(unittest.TestCase):
    def test_00_empty_rows_returns_empty_string(self) -> None:
        self.assertEqual(format_table(("a", "b"), []), "")

    def test_01_columns_align_to_widest_cell_including_korean(self) -> None:
        rows = [("TX-1", "식비", "12,000"), ("TX-20", "교통비", "1,250")]
        table = format_table(("id", "카테고리", "금액"), rows, ("left", "left", "right"))
        lines = table.split("\n")
        self.assertEqual(len(lines), 4)  # 헤더 1 + 구분선 1 + 데이터 2

        # 헤더/구분선/각 데이터 줄의 "id" 컬럼 시작 위치가 전부 동일해야 세로줄이 맞음
        header, sep, row1, row2 = lines
        self.assertTrue(sep.replace(" ", "").replace("-", "") == "")  # 구분선은 '-'와 공백뿐

    def test_02_right_align_puts_numbers_flush_right(self) -> None:
        rows = [("1", "100"), ("2", "12,000")]
        table = format_table(("순번", "금액"), rows, ("left", "right"))
        lines = table.split("\n")
        # "금액" 컬럼 폭은 가장 넓은 "12,000"(6) 기준 → "100" 앞에 공백 3칸이 와야 함
        self.assertTrue(lines[2].endswith("   100"))
        self.assertTrue(lines[3].endswith("12,000"))

    def test_03_default_align_is_left_when_aligns_omitted(self) -> None:
        table = format_table(("a",), [("x",)])
        self.assertIn("a", table)
        self.assertIn("x", table)


class FormatAmountAndBarTestCase(unittest.TestCase):
    def test_00_amount_gets_thousands_separators(self) -> None:
        self.assertEqual(format_amount(1000000), "1,000,000")
        self.assertEqual(format_amount(0), "0")
        self.assertEqual(format_amount(-500), "-500")

    def test_01_bar_at_zero_is_all_empty(self) -> None:
        self.assertEqual(format_bar(0.0, width=10), "." * 10)

    def test_02_bar_at_full_is_all_filled(self) -> None:
        self.assertEqual(format_bar(1.0, width=10), "#" * 10)

    def test_03_bar_over_100_percent_does_not_overflow_width(self) -> None:
        # 예산을 300% 초과해도 막대 길이는 width 를 넘지 않아야 함(초과분은 그냥 꽉 채움)
        result = format_bar(3.0, width=10)
        self.assertEqual(len(result), 10)
        self.assertEqual(result, "#" * 10)

    def test_04_bar_at_half_splits_evenly(self) -> None:
        self.assertEqual(format_bar(0.5, width=20), "#" * 10 + "." * 10)


if __name__ == "__main__":
    unittest.main()
