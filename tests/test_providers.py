"""Tests for session providers."""

import tempfile
import shutil
from pathlib import Path

import pytest

from agent_sessions.models import Session
from agent_sessions.providers.base import SessionProvider
from agent_sessions.providers.droid import DroidProvider
from agent_sessions.providers.claude_code import ClaudeCodeProvider
from agent_sessions.providers.codex import CodexProvider


class TestDroidProvider:
    """Tests for Factory Droid provider."""

    @pytest.fixture
    def droid_provider(self):
        return DroidProvider()

    @pytest.fixture
    def temp_session_dir(self, tmp_path):
        """Create a temporary session directory with test data."""
        # Create project directory
        project_dir = tmp_path / "test-project"
        project_dir.mkdir()

        # Copy test fixture
        fixture_path = Path(__file__).parent / "fixtures" / "droid_session.jsonl"
        if fixture_path.exists():
            shutil.copy(fixture_path, project_dir / "test-session-id.jsonl")

            # Create settings file
            settings = project_dir / "test-session-id.settings.json"
            settings.write_text('{"model": "claude-opus-4-5-20251101"}')

        return tmp_path

    def test_provider_attributes(self, droid_provider):
        """Test provider has required attributes."""
        assert droid_provider.name == "droid"
        assert droid_provider.display_name == "FactoryAI Droid"
        assert droid_provider.icon == "🤖"
        assert droid_provider.color == "green"

    def test_get_sessions_dir(self, droid_provider):
        """Test sessions directory path."""
        sessions_dir = droid_provider.get_sessions_dir()
        assert sessions_dir == Path.home() / ".factory" / "sessions"

    def test_get_resume_command(self, droid_provider):
        """Test resume command generation."""
        session = Session(
            id="test-123",
            harness="droid",
            raw_path=Path("/tmp/test.jsonl"),
            project_path=Path("/home/user/project"),
            project_name="project",
        )
        cmd = droid_provider.get_resume_command(session)
        assert cmd == "droid --resume test-123"

    def test_parse_session_fixture(self, droid_provider, temp_session_dir):
        """Test parsing a session from fixture."""
        session_file = temp_session_dir / "test-project" / "test-session-id.jsonl"
        if not session_file.exists():
            pytest.skip("Fixture file not found")

        session = droid_provider.parse_session(session_file)

        assert session is not None
        assert session.id == "test-session-id"
        assert session.harness == "droid"
        assert session.title == "Test Session"
        assert "authentication" in session.first_prompt.lower()
        assert session.model == "claude-opus-4-5-20251101"


class TestClaudeCodeProvider:
    """Tests for Claude Code provider."""

    @pytest.fixture
    def claude_provider(self):
        return ClaudeCodeProvider()

    @pytest.fixture
    def temp_session_dir(self, tmp_path):
        """Create a temporary session directory with test data."""
        project_dir = tmp_path / "-home-user-webapp"
        project_dir.mkdir()

        fixture_path = Path(__file__).parent / "fixtures" / "claude_code_session.jsonl"
        if fixture_path.exists():
            shutil.copy(fixture_path, project_dir / "test-claude-session.jsonl")

        return tmp_path

    def test_provider_attributes(self, claude_provider):
        """Test provider has required attributes."""
        assert claude_provider.name == "claude-code"
        assert claude_provider.display_name == "Claude Code"
        assert claude_provider.icon == "🧠"
        assert claude_provider.color == "cyan"

    def test_get_sessions_dir(self, claude_provider):
        """Test sessions directory path."""
        sessions_dir = claude_provider.get_sessions_dir()
        assert sessions_dir == Path.home() / ".claude" / "projects"

    def test_get_resume_command(self, claude_provider):
        """Test resume command generation."""
        session = Session(
            id="test-456",
            harness="claude-code",
            raw_path=Path("/tmp/test.jsonl"),
            project_path=Path("/home/user/webapp"),
            project_name="webapp",
        )
        cmd = claude_provider.get_resume_command(session)
        assert cmd == "claude --resume test-456"

    def test_parse_session_fixture(self, claude_provider, temp_session_dir):
        """Test parsing a session from fixture."""
        session_file = temp_session_dir / "-home-user-webapp" / "test-claude-session.jsonl"
        if not session_file.exists():
            pytest.skip("Fixture file not found")

        session = claude_provider.parse_session(session_file)

        assert session is not None
        assert session.id == "test-claude-session"
        assert session.harness == "claude-code"
        assert "React" in session.first_prompt
        assert session.model == "claude-opus-4-5-20251101"


class TestCodexProvider:
    """Tests for Codex provider."""

    @pytest.fixture
    def codex_provider(self):
        return CodexProvider()

    def test_provider_attributes(self, codex_provider):
        """Test provider has required attributes."""
        assert codex_provider.name == "codex"
        assert codex_provider.display_name == "Codex"
        assert codex_provider.icon == "✦"
        assert codex_provider.color == "yellow"

    def test_get_sessions_dir(self, codex_provider):
        """Test sessions directory path."""
        sessions_dir = codex_provider.get_sessions_dir()
        assert sessions_dir == Path.home() / ".codex" / "sessions"

    def test_get_resume_command(self, codex_provider):
        """Test resume command generation."""
        session = Session(
            id="019da1da-2983-7da0-bf88-7bcdb7a230ca",
            harness="codex",
            raw_path=Path("/tmp/test.jsonl"),
            project_path=Path("/home/user/project"),
            project_name="project",
        )
        cmd = codex_provider.get_resume_command(session)
        assert cmd == "codex resume 019da1da-2983-7da0-bf88-7bcdb7a230ca"

    def test_parse_parent_session_fixture(self, codex_provider):
        """Test parsing a normal parent Codex session."""
        fixture_path = Path(__file__).parent / "fixtures" / "codex_parent_session.jsonl"

        session = codex_provider.parse_session(fixture_path)

        assert session is not None
        assert session.id == "019da1da-2983-7da0-bf88-7bcdb7a230ca"
        assert session.harness == "codex"
        assert session.project_path == Path("/Users/tester/project")
        assert session.project_name == "project"
        assert session.is_child is False
        assert session.parent_id is None
        assert "Codex provider support" in session.first_prompt
        assert session.model == "gpt-5.4"
        assert session.tool_calls == ["exec_command"]

    def test_parse_child_session_fixture(self, codex_provider):
        """Test parsing an explicit Codex sub-agent session."""
        fixture_path = Path(__file__).parent / "fixtures" / "codex_child_session.jsonl"

        session = codex_provider.parse_session(fixture_path)

        assert session is not None
        assert session.id == "019d791f-abbd-7980-b012-cf9d5182e597"
        assert session.harness == "codex"
        assert session.project_path == Path("/Users/tester/agent-do")
        assert session.project_name == "agent-do"
        assert session.is_child is True
        assert session.parent_id == "019d7912-5a47-7c01-b9ae-90ac2060a27e"
        assert session.child_type == "worker"
        assert session.model == "gpt-5.2-codex"

    def test_get_session_messages(self, codex_provider):
        """Test conversational message extraction from event records."""
        fixture_path = Path(__file__).parent / "fixtures" / "codex_parent_session.jsonl"
        session = codex_provider.parse_session(fixture_path)

        assert session is not None
        messages = codex_provider.get_session_messages(session)

        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert "Codex provider support" in messages[0]["content"]
        assert messages[1]["role"] == "assistant"
        assert "provider registry" in messages[1]["content"]


class TestProviderRegistry:
    """Tests for provider registry."""

    def test_get_available_providers(self):
        """Test getting available providers."""
        from agent_sessions.providers import get_available_providers

        providers = get_available_providers()
        # Should return list (may be empty if dirs don't exist)
        assert isinstance(providers, list)

    def test_get_all_providers(self):
        """Test getting all registered providers."""
        from agent_sessions.providers import get_all_providers

        providers = get_all_providers()
        assert len(providers) >= 3  # At least Droid, Claude Code, and Codex

        names = [p.name for p in providers]
        assert "droid" in names
        assert "claude-code" in names
        assert "codex" in names

    def test_get_provider_by_name(self):
        """Test getting provider by name."""
        from agent_sessions.providers import get_provider

        droid = get_provider("droid")
        assert droid is not None
        assert droid.name == "droid"

        claude = get_provider("claude-code")
        assert claude is not None
        assert claude.name == "claude-code"

        codex = get_provider("codex")
        assert codex is not None
        assert codex.name == "codex"

        unknown = get_provider("unknown-provider")
        assert unknown is None
