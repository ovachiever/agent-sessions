# Contributing

## Setup

```bash
git clone https://github.com/erikjamesfritsch/agent-sessions.git
cd agent-sessions
pip install -e ".[dev]"
```

## Running Tests

```bash
pytest tests/
```

## Adding a New Provider

1. Create `agent_sessions/providers/your_provider.py`
2. Implement the `SessionProvider` ABC:

```python
from agent_sessions.providers import register_provider
from agent_sessions.providers.base import SessionProvider

@register_provider
class MyProvider(SessionProvider):
    name = "my-tool"
    display_name = "My Tool"
    icon = "🔧"
    color = "blue"

    def get_sessions_dir(self):
        return Path.home() / ".my-tool" / "sessions"

    def discover_session_files(self):
        ...

    def parse_session(self, path):
        ...

    def get_resume_command(self, session):
        return f"my-tool --resume {session.id}"
```

3. Add tests in `tests/`
4. Submit a PR

## Code Style

- Follow existing patterns and conventions
- Keep functions small and focused
- Comments only for non-obvious logic

## Pull Requests

- One feature/fix per PR
- Include tests for new functionality
- Ensure `pytest tests/` passes
