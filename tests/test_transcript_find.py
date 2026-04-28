"""Tests for in-transcript find helpers."""

from agent_sessions.ui.widgets import find_all_matches, offset_to_line_col


class TestFindAllMatches:
    def test_empty_query_returns_empty(self):
        assert find_all_matches("hello world", "") == []

    def test_no_match(self):
        assert find_all_matches("hello world", "xyz") == []

    def test_single_match(self):
        assert find_all_matches("hello world", "world") == [6]

    def test_multiple_matches(self):
        assert find_all_matches("ababab", "ab") == [0, 2, 4]

    def test_overlapping_matches(self):
        # "aaa" with "aa" → matches at offsets 0 and 1 (overlapping)
        assert find_all_matches("aaa", "aa") == [0, 1]

    def test_case_insensitive(self):
        assert find_all_matches("Hello WORLD", "world") == [6]
        assert find_all_matches("HELLO world", "HELLO") == [0]

    def test_unicode_casefold(self):
        # Greek capital sigma vs final sigma — both fold to lowercase sigma
        assert find_all_matches("ΣΣΣ", "σ") == [0, 1, 2]

    def test_match_across_newline(self):
        text = "first line\nsecond line"
        assert find_all_matches(text, "line\nsecond") == [6]

    def test_haystack_with_query_at_end(self):
        assert find_all_matches("foo bar", "bar") == [4]


class TestOffsetToLineCol:
    def test_zero_offset(self):
        assert offset_to_line_col("hello", 0) == (0, 0)

    def test_negative_offset(self):
        assert offset_to_line_col("hello", -5) == (0, 0)

    def test_first_line_middle(self):
        assert offset_to_line_col("hello world", 6) == (0, 6)

    def test_after_first_newline(self):
        # "abc\ndef" → offset 4 is start of "def" → line 1, col 0
        assert offset_to_line_col("abc\ndef", 4) == (1, 0)

    def test_second_line_middle(self):
        # "abc\ndef" → offset 5 is "e" → line 1, col 1
        assert offset_to_line_col("abc\ndef", 5) == (1, 1)

    def test_multi_line(self):
        text = "line0\nline1\nline2"
        # offset of "line2" = 12
        assert offset_to_line_col(text, 12) == (2, 0)
        # "n" inside "line2"
        assert offset_to_line_col(text, 14) == (2, 2)

    def test_offset_at_newline(self):
        # offset of "\n" itself — col equals line length, line is the originating line
        text = "abc\ndef"
        assert offset_to_line_col(text, 3) == (0, 3)
