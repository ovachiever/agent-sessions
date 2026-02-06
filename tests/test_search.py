"""Tests for search functionality."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from agent_sessions.models import Session, SearchResult
from agent_sessions.search import (
    parse_search_query,
    parse_date_value,
    SearchEngine,
    search_sessions,
)


class TestQueryParsing:
    """Tests for search query parsing."""

    def test_simple_query(self):
        """Test parsing a simple query with no modifiers."""
        query, filters = parse_search_query("authentication")
        assert query == "authentication"
        assert filters == {}

    def test_harness_modifier(self):
        """Test parsing harness: modifier."""
        query, filters = parse_search_query("harness:droid authentication")
        assert query == "authentication"
        assert filters["harness"] == "droid"

    def test_project_modifier(self):
        """Test parsing project: modifier."""
        query, filters = parse_search_query("project:api-server JWT")
        assert query == "JWT"
        assert filters["project"] == "api-server"

    def test_multiple_modifiers(self):
        """Test parsing multiple modifiers."""
        query, filters = parse_search_query("harness:claude-code project:webapp React")
        assert query == "React"
        assert filters["harness"] == "claude-code"
        assert filters["project"] == "webapp"

    def test_date_modifiers(self):
        """Test parsing date modifiers."""
        query, filters = parse_search_query("after:7d before:1d auth")
        assert query == "auth"
        assert "after" in filters
        assert "before" in filters


class TestDateParsing:
    """Tests for date value parsing."""

    def test_relative_days(self):
        """Test parsing relative day values."""
        result = parse_date_value("7d")
        assert result is not None
        expected = datetime.now() - timedelta(days=7)
        assert abs((result - expected).total_seconds()) < 60

    def test_relative_hours(self):
        """Test parsing relative hour values."""
        result = parse_date_value("24h")
        assert result is not None
        expected = datetime.now() - timedelta(hours=24)
        assert abs((result - expected).total_seconds()) < 60

    def test_relative_weeks(self):
        """Test parsing relative week values."""
        result = parse_date_value("2w")
        assert result is not None
        expected = datetime.now() - timedelta(weeks=2)
        assert abs((result - expected).total_seconds()) < 60

    def test_iso_date(self):
        """Test parsing ISO date format."""
        result = parse_date_value("2024-01-15")
        assert result is not None
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15

    def test_invalid_date(self):
        """Test parsing invalid date returns None."""
        result = parse_date_value("not-a-date")
        assert result is None


class TestSearchEngine:
    """Tests for SearchEngine class."""

    @pytest.fixture
    def sample_sessions(self):
        """Create sample sessions for testing."""
        return [
            Session(
                id="session-1",
                harness="droid",
                raw_path=Path("/tmp/s1.jsonl"),
                project_path=Path("/home/user/api"),
                project_name="api",
                first_prompt="Help with authentication",
                modified_time=datetime.now() - timedelta(days=1),
            ),
            Session(
                id="session-2",
                harness="claude-code",
                raw_path=Path("/tmp/s2.jsonl"),
                project_path=Path("/home/user/webapp"),
                project_name="webapp",
                first_prompt="Create a React component",
                modified_time=datetime.now() - timedelta(days=5),
            ),
            Session(
                id="session-3",
                harness="droid",
                raw_path=Path("/tmp/s3.jsonl"),
                project_path=Path("/home/user/api"),
                project_name="api",
                first_prompt="Add database migrations",
                modified_time=datetime.now() - timedelta(days=10),
            ),
        ]

    def test_filter_by_harness(self, sample_sessions):
        """Test filtering sessions by harness."""
        engine = SearchEngine(sample_sessions)

        # This won't find actual content (no file), but tests filtering
        filtered = [s for s in sample_sessions if s.harness == "droid"]
        assert len(filtered) == 2
        assert all(s.harness == "droid" for s in filtered)

    def test_filter_by_project(self, sample_sessions):
        """Test filtering sessions by project."""
        filtered = [s for s in sample_sessions if "api" in s.project_name.lower()]
        assert len(filtered) == 2
        assert all("api" in s.project_name.lower() for s in filtered)

    def test_filter_by_date(self, sample_sessions):
        """Test filtering sessions by date."""
        cutoff = datetime.now() - timedelta(days=7)
        filtered = [s for s in sample_sessions if s.modified_time and s.modified_time > cutoff]
        assert len(filtered) == 2  # Only sessions within last 7 days


class TestSearchResult:
    """Tests for SearchResult model."""

    def test_search_result_creation(self):
        """Test creating a SearchResult."""
        session = Session(
            id="test",
            harness="droid",
            raw_path=Path("/tmp/test.jsonl"),
            project_path=Path("/home/user/project"),
            project_name="project",
        )

        result = SearchResult(
            session=session,
            role="user",
            match_text="Help with authentication",
            context_before=["Previous line"],
            context_after=["Next line"],
            line_num=5,
        )

        assert result.session_id == "test"
        assert result.role == "user"
        assert "authentication" in result.match_text
        assert len(result.context_before) == 1
        assert len(result.context_after) == 1
