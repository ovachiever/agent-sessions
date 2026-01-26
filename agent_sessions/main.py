#!/usr/bin/env python3
"""Agent Sessions - Universal AI Coding Sessions Browser.

Entry point for the CLI application.
"""

import argparse
import os
import sys


def cmd_browse(args):
    """Launch the TUI browser."""
    from .app import AgentSessionsBrowser

    app = AgentSessionsBrowser(
        harness_filter=args.harness,
        project_filter=args.project,
    )
    result = app.run()

    if result and isinstance(result, str):
        print(f"\n[Resuming session...]\n{result}\n")
        parts = result.split()
        if parts:
            os.execvp(parts[0], parts)


def cmd_providers(args):
    """List available providers."""
    from .providers import get_all_providers

    providers = get_all_providers()

    if args.status:
        print("Provider Status:")
        print("-" * 60)
        for p in providers:
            available = "✓" if p.is_available() else "✗"
            status = "available" if p.is_available() else "not found"
            sessions_dir = p.get_sessions_dir()

            print(f"{available} {p.icon} {p.display_name:<15} ({p.name})")
            print(f"    Path: {sessions_dir}")
            print(f"    Status: {status}")

            if p.is_available():
                sessions = p.load_sessions()
                parents = sum(1 for s in sessions if not s.is_child)
                children = sum(1 for s in sessions if s.is_child)
                print(f"    Sessions: {parents} parent, {children} child")
            print()
    else:
        print("Available providers:")
        for p in providers:
            status = "✓" if p.is_available() else "✗"
            print(f"  {status} {p.icon} {p.display_name} ({p.name})")


def cmd_search(args):
    """Search sessions from CLI."""
    from .providers import get_available_providers
    from .search import SearchEngine

    # Load all sessions
    all_sessions = []
    for provider in get_available_providers():
        if args.harness and provider.name != args.harness:
            continue
        all_sessions.extend(provider.load_sessions())

    if not all_sessions:
        print("No sessions found.")
        return

    # Search
    engine = SearchEngine(all_sessions)
    results = engine.search(
        query=args.query,
        harness=args.harness,
        project=args.project,
    )

    if not results:
        print(f"No matches found for: {args.query}")
        return

    # Display results
    total = sum(len(r) for r in results.values())
    print(f"Found {total} matches in {len(results)} sessions:\n")

    for session_id, matches in list(results.items())[:args.limit]:
        session = matches[0].session
        provider_icon = "?"
        from .providers import get_provider
        p = get_provider(session.harness)
        if p:
            provider_icon = p.icon

        print(f"{provider_icon} {session.project_name} - {session.title[:50]}")
        print(f"   ID: {session.id}")

        for match in matches[:3]:  # Show first 3 matches per session
            role = "U" if match.role == "user" else "A"
            text = match.match_text[:80].replace('\n', ' ')
            print(f"   [{role}] {text}")

        if len(matches) > 3:
            print(f"   ... and {len(matches) - 3} more matches")
        print()


def cmd_cache(args):
    """Manage summary cache."""
    from .cache import SummaryCache, DEFAULT_CACHE_PATH

    if args.action == "clear":
        if DEFAULT_CACHE_PATH.exists():
            DEFAULT_CACHE_PATH.unlink()
            print(f"Cleared cache: {DEFAULT_CACHE_PATH}")
        else:
            print("Cache file not found.")
    elif args.action == "info":
        if DEFAULT_CACHE_PATH.exists():
            import json
            with open(DEFAULT_CACHE_PATH) as f:
                data = json.load(f)
            print(f"Cache location: {DEFAULT_CACHE_PATH}")
            print(f"Cached summaries: {len(data)}")
            size = DEFAULT_CACHE_PATH.stat().st_size
            print(f"Cache size: {size / 1024:.1f} KB")
        else:
            print("No cache file found.")


def main():
    """Main entry point for agent-sessions CLI."""
    parser = argparse.ArgumentParser(
        description="Browse and resume sessions from multiple AI coding assistants",
        prog="agent-sessions",
    )
    parser.add_argument(
        "--version", "-v",
        action="store_true",
        help="Show version"
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Browse command (default)
    browse_parser = subparsers.add_parser("browse", help="Launch TUI browser (default)")
    browse_parser.add_argument("--harness", "-H", help="Filter to specific harness")
    browse_parser.add_argument("--project", "-p", help="Filter to specific project")

    # Providers command
    providers_parser = subparsers.add_parser("providers", help="List available providers")
    providers_parser.add_argument("--status", "-s", action="store_true", help="Show detailed status")

    # Search command
    search_parser = subparsers.add_parser("search", help="Search sessions")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument("--harness", "-H", help="Filter to specific harness")
    search_parser.add_argument("--project", "-p", help="Filter to specific project")
    search_parser.add_argument("--limit", "-l", type=int, default=10, help="Max sessions to show")

    # Cache command
    cache_parser = subparsers.add_parser("cache", help="Manage summary cache")
    cache_parser.add_argument("action", choices=["clear", "info"], help="Cache action")

    args = parser.parse_args()

    if args.version:
        from . import __version__
        print(f"agent-sessions {__version__}")
        return

    if args.command == "providers":
        cmd_providers(args)
    elif args.command == "search":
        cmd_search(args)
    elif args.command == "cache":
        cmd_cache(args)
    elif args.command == "browse":
        cmd_browse(args)
    else:
        # Default: launch TUI with any top-level args
        # Re-parse with browse-like args
        browse_args = argparse.Namespace(
            harness=getattr(args, 'harness', None),
            project=getattr(args, 'project', None),
        )
        cmd_browse(browse_args)


if __name__ == "__main__":
    main()
