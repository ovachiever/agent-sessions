"""Provider registry and discovery."""

from typing import Type
from .base import SessionProvider

# Registry of all available providers
_PROVIDERS: dict[str, Type[SessionProvider]] = {}


def register_provider(provider_class: Type[SessionProvider]) -> Type[SessionProvider]:
    """Decorator to register a provider class."""
    _PROVIDERS[provider_class.name] = provider_class
    return provider_class


def get_provider(name: str) -> SessionProvider | None:
    """Get an instance of a provider by name."""
    provider_class = _PROVIDERS.get(name)
    if provider_class:
        return provider_class()
    return None


def get_all_providers() -> list[SessionProvider]:
    """Get instances of all registered providers."""
    return [cls() for cls in _PROVIDERS.values()]


def get_available_providers() -> list[SessionProvider]:
    """Get instances of all available (installed) providers."""
    return [p for p in get_all_providers() if p.is_available()]


def discover_all_sessions():
    """Discover sessions from all available providers."""
    from ..models import Session

    all_sessions: list[Session] = []
    for provider in get_available_providers():
        sessions = provider.load_sessions()
        all_sessions.extend(sessions)

    # Sort by modified time, newest first
    all_sessions.sort(key=lambda s: s.modified_time or s.created_time, reverse=True)
    return all_sessions


# Import providers to trigger registration
from . import droid  # noqa: F401, E402
from . import claude_code  # noqa: F401, E402
from . import cursor  # noqa: F401, E402
from . import opencode  # noqa: F401, E402
