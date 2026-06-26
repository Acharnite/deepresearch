"""Tests for detect_time_filter boundary cases."""

from __future__ import annotations


class TestTimeFilter:
    """detect_time_filter boundary cases."""

    def test_detect_time_filter_day(self) -> None:
        from deepresearch.tools.time_filter import detect_time_filter

        assert detect_time_filter("latest news today") == "day"
        assert detect_time_filter("breaking story") == "day"
        assert detect_time_filter("just released update") == "day"

    def test_detect_time_filter_week(self) -> None:
        from deepresearch.tools.time_filter import detect_time_filter

        assert detect_time_filter("events this week") == "week"
        assert detect_time_filter("news past week") == "week"

    def test_detect_time_filter_month(self) -> None:
        from deepresearch.tools.time_filter import detect_time_filter

        assert detect_time_filter("trends this month") == "month"
        assert detect_time_filter("recent developments") == "month"

    def test_detect_time_filter_year(self) -> None:
        from deepresearch.tools.time_filter import detect_time_filter

        assert detect_time_filter("2026 olympics") == "year"
        assert detect_time_filter("growth this year") == "year"

    def test_detect_time_filter_no_match(self) -> None:
        from deepresearch.tools.time_filter import detect_time_filter

        assert detect_time_filter("quantum computing") is None
        assert detect_time_filter("history of mathematics") is None

    def test_detect_time_filter_empty(self) -> None:
        from deepresearch.tools.time_filter import detect_time_filter

        assert detect_time_filter("") is None

    def test_detect_time_filter_case_insensitive(self) -> None:
        from deepresearch.tools.time_filter import detect_time_filter

        assert detect_time_filter("LATEST NEWS") == "day"
        assert detect_time_filter("This Week In Tech") == "week"

    def test_detect_time_filter_word_boundary(self) -> None:
        """Keyword matching should use word boundaries to avoid partial matches."""
        from deepresearch.tools.time_filter import detect_time_filter

        assert detect_time_filter("latest AI news") == "day"
        assert detect_time_filter("todayilearned") is None
        assert detect_time_filter("heartbreaking story") is None

    def test_detect_time_filter_mixed(self) -> None:
        """First matching rule wins."""
        from deepresearch.tools.time_filter import detect_time_filter

        result = detect_time_filter("latest news this year")
        assert result is not None
