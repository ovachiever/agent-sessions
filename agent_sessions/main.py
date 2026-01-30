#!/usr/bin/env python3
"""Agent Sessions - Universal AI Coding Sessions Browser.

Entry point for the CLI application.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from datetime import datetime


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
    from .index import SessionDatabase, HybridSearch
    from .providers import get_provider

    db = SessionDatabase()
    search = HybridSearch(db)
    
    results = search.search(query=args.query, limit=args.limit)
    
    if not results:
        print(f"No matches found for: {args.query}")
        return

    print(f"Found {len(results)} sessions:\n")

    for result in results:
        session = db.get_session(result.session_id)
        if not session:
            continue
            
        provider = get_provider(session.harness)
        provider_icon = provider.icon if provider else "?"

        print(f"{provider_icon} {session.project_name} - {session.first_prompt_preview[:50] if session.first_prompt_preview else 'No preview'}")
        print(f"   ID: {result.session_id}")
        print(f"   Score: {result.score:.2f}")
        print()


def cmd_cache(args):
    """Manage summary and metadata caches."""
    from .cache import SummaryCache, DEFAULT_CACHE_PATH, METADATA_CACHE_PATH

    if args.action == "clear":
        cleared = []
        if DEFAULT_CACHE_PATH.exists():
            DEFAULT_CACHE_PATH.unlink()
            cleared.append(f"Summary cache: {DEFAULT_CACHE_PATH}")
        if METADATA_CACHE_PATH.exists():
            METADATA_CACHE_PATH.unlink()
            cleared.append(f"Metadata cache: {METADATA_CACHE_PATH}")

        if cleared:
            print("Cleared caches:")
            for c in cleared:
                print(f"  {c}")
        else:
            print("No cache files found.")
    elif args.action == "info":
        print("Cache info:")
        print()
        if DEFAULT_CACHE_PATH.exists():
            with open(DEFAULT_CACHE_PATH) as f:
                data = json.load(f)
            print(f"Summary cache: {DEFAULT_CACHE_PATH}")
            print(f"  Cached summaries: {len(data)}")
            size = DEFAULT_CACHE_PATH.stat().st_size
            print(f"  Size: {size / 1024:.1f} KB")
        else:
            print(f"Summary cache: not found")

        print()
        if METADATA_CACHE_PATH.exists():
            with open(METADATA_CACHE_PATH) as f:
                data = json.load(f)
            print(f"Metadata cache: {METADATA_CACHE_PATH}")
            print(f"  Cached sessions: {len(data)}")
            size = METADATA_CACHE_PATH.stat().st_size
            print(f"  Size: {size / 1024:.1f} KB")
        else:
            print(f"Metadata cache: not found")


def cmd_reindex(args):
    """Perform full reindex of all sessions."""
    from .index import SessionIndexer, SessionDatabase
    from .providers import get_available_providers
    
    print("Starting full reindex...")
    print()
    
    db = SessionDatabase()
    providers = list(get_available_providers())
    indexer = SessionIndexer(db, providers)
    
    def progress_callback(current, total, message):
        pct = (current / total * 100) if total > 0 else 0
        bar_width = 40
        filled = int(bar_width * current / total) if total > 0 else 0
        bar = "█" * filled + "░" * (bar_width - filled)
        print(f"\r[{bar}] {pct:3.0f}% ({current}/{total}) {message}", end="", flush=True)
    
    stats = indexer.full_reindex(progress_callback=progress_callback)
    
    print()
    print()
    print("✓ Reindex complete!")
    print(f"  Sessions indexed: {stats.get('sessions_indexed', 0)}")
    print(f"  Messages indexed: {stats.get('messages_indexed', 0)}")
    print(f"  Chunks created: {stats.get('chunks_created', 0)}")
    print(f"  Projects updated: {stats.get('projects_updated', 0)}")


def cmd_generate_embeddings(args):
    """Generate embeddings for all chunks."""
    from .index import SessionDatabase, EmbeddingGenerator, ChunkRow, Chunk
    
    db = SessionDatabase()
    generator = EmbeddingGenerator()
    
    if not generator.available:
        print("❌ OpenAI API key not configured.")
        print("   Set OPENAI_API_KEY environment variable to enable embeddings.")
        return
    
    all_chunks = db.get_all_chunk_embeddings()
    chunks_without_embeddings = [c for c in all_chunks if c[2] is None]
    
    if not chunks_without_embeddings:
        print("✓ All chunks already have embeddings!")
        return
    
    print(f"Generating embeddings for {len(chunks_without_embeddings)} chunks...")
    print()
    
    chunk_objects = []
    for session_id, chunk_index, _ in chunks_without_embeddings:
        chunk_rows = db.get_session_chunks(session_id)
        for row in chunk_rows:
            if row.chunk_index == chunk_index:
                chunk = Chunk(
                    session_id=row.session_id,
                    chunk_type=row.chunk_type,
                    content=row.content,
                    metadata=json.loads(row.metadata) if row.metadata else {},
                    embedding=None
                )
                chunk_objects.append((chunk, row))
                break
    
    batch_size = 100
    total = len(chunk_objects)
    
    for i in range(0, total, batch_size):
        batch = [c[0] for c in chunk_objects[i:i+batch_size]]
        original_rows = [c[1] for c in chunk_objects[i:i+batch_size]]
        embedded_batch = generator.embed_chunks(batch)
        
        chunk_rows_to_update = []
        for chunk, orig_row in zip(embedded_batch, original_rows):
            if chunk.embedding:
                chunk_rows_to_update.append(ChunkRow(
                    id=orig_row.id,
                    session_id=chunk.session_id,
                    message_id=orig_row.message_id,
                    chunk_index=orig_row.chunk_index,
                    chunk_type=chunk.chunk_type,
                    content=chunk.content,
                    metadata=json.dumps(chunk.metadata),
                    embedding=EmbeddingGenerator.serialize_embedding(chunk.embedding),
                    embedding_model="text-embedding-3-small",
                    created_at=orig_row.created_at
                ))
        
        if chunk_rows_to_update:
            db.upsert_chunks(chunk_rows_to_update)
        
        pct = min((i + batch_size) / total * 100, 100)
        bar_filled = int(pct / 2.5)
        bar = "█" * bar_filled + "░" * (40 - bar_filled)
        print(f"\r[{bar}] {pct:3.0f}% ({min(i+batch_size, total)}/{total})", end="", flush=True)
    
    print()
    print()
    print(f"✓ Generated embeddings for {total} chunks!")


def cmd_stats(args):
    """Show database statistics."""
    from .index import SessionDatabase
    
    db = SessionDatabase()
    
    print("Database Statistics")
    print("=" * 60)
    print()
    
    total_sessions = db.count_sessions()
    parent_sessions = len(db.get_parents())
    child_sessions = total_sessions - parent_sessions
    
    print(f"Sessions:")
    print(f"  Total: {total_sessions}")
    print(f"  Parent: {parent_sessions}")
    print(f"  Child: {child_sessions}")
    print()
    
    message_count = db.count_messages()
    print(f"Messages: {message_count}")
    print()
    
    chunk_count = db.count_chunks()
    chunks_with_embeddings = db.count_chunks_with_embeddings()
    print(f"Chunks: {chunk_count}")
    print(f"  With embeddings: {chunks_with_embeddings}")
    print(f"  Without embeddings: {chunk_count - chunks_with_embeddings}")
    print()
    
    db_path = Path.home() / ".cache" / "agent-sessions" / "sessions.db"
    if db_path.exists():
        size_mb = db_path.stat().st_size / (1024 * 1024)
        print(f"Database size: {size_mb:.2f} MB")
    print()
    
    print("Sessions by harness:")
    for harness in ["droid", "claude-code", "opencode", "cursor"]:
        count = db.count_sessions(harness=harness)
        if count > 0:
            print(f"  {harness}: {count}")


def cmd_projects(args):
    """Show project activity statistics."""
    from .index import SessionDatabase
    
    db = SessionDatabase()
    
    conn = db._get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT project_name, total_sessions, parent_sessions, child_sessions, 
               total_messages, last_session_time
        FROM project_stats
        ORDER BY total_sessions DESC
        LIMIT 20
    """)
    
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        print("No projects found.")
        return
    
    print("Top Projects by Session Count")
    print("=" * 80)
    print()
    print(f"{'Project':<30} {'Sessions':<12} {'Messages':<12} {'Last Activity':<20}")
    print("-" * 80)
    
    for row in rows:
        project_name = row[0] or "Unknown"
        total_sessions = row[1]
        total_messages = row[4]
        last_time = row[5]
        
        if last_time:
            last_dt = datetime.fromtimestamp(last_time)
            last_str = last_dt.strftime("%Y-%m-%d %H:%M")
        else:
            last_str = "Never"
        
        print(f"{project_name:<30} {total_sessions:<12} {total_messages:<12} {last_str:<20}")


def cmd_search_history(args):
    """Show search pattern analysis."""
    from .index import SessionDatabase
    
    db = SessionDatabase()
    
    conn = db._get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT query, result_count, search_time_ms, timestamp
        FROM semantic_searches
        ORDER BY timestamp DESC
        LIMIT 20
    """)
    
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        print("No search history found.")
        return
    
    print("Recent Searches")
    print("=" * 100)
    print()
    print(f"{'Query':<50} {'Results':<10} {'Time (ms)':<12} {'Timestamp':<20}")
    print("-" * 100)
    
    for row in rows:
        query = row[0][:50] if row[0] else "Unknown"
        result_count = row[1] or 0
        search_time = row[2] or 0
        timestamp = row[3]
        
        if timestamp:
            dt = datetime.fromtimestamp(timestamp)
            ts_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        else:
            ts_str = "Unknown"
        
        print(f"{query:<50} {result_count:<10} {search_time:<12} {ts_str:<20}")


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
    parser.add_argument(
        "--reindex",
        action="store_true",
        help="Full reindex with progress"
    )
    parser.add_argument(
        "--generate-embeddings",
        action="store_true",
        help="Batch embedding generation"
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Database statistics"
    )
    parser.add_argument(
        "--projects",
        action="store_true",
        help="Project activity listing"
    )
    parser.add_argument(
        "--search-history",
        action="store_true",
        help="Search pattern analysis"
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    browse_parser = subparsers.add_parser("browse", help="Launch TUI browser (default)")
    browse_parser.add_argument("--harness", "-H", help="Filter to specific harness")
    browse_parser.add_argument("--project", "-p", help="Filter to specific project")

    providers_parser = subparsers.add_parser("providers", help="List available providers")
    providers_parser.add_argument("--status", "-s", action="store_true", help="Show detailed status")

    search_parser = subparsers.add_parser("search", help="Search sessions")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument("--harness", "-H", help="Filter to specific harness")
    search_parser.add_argument("--project", "-p", help="Filter to specific project")
    search_parser.add_argument("--limit", "-l", type=int, default=10, help="Max sessions to show")

    cache_parser = subparsers.add_parser("cache", help="Manage summary cache")
    cache_parser.add_argument("action", choices=["clear", "info"], help="Cache action")

    args = parser.parse_args()

    if args.version:
        from . import __version__
        print(f"agent-sessions {__version__}")
        return

    if args.reindex:
        cmd_reindex(args)
    elif args.generate_embeddings:
        cmd_generate_embeddings(args)
    elif args.stats:
        cmd_stats(args)
    elif args.projects:
        cmd_projects(args)
    elif args.search_history:
        cmd_search_history(args)
    elif args.command == "providers":
        cmd_providers(args)
    elif args.command == "search":
        cmd_search(args)
    elif args.command == "cache":
        cmd_cache(args)
    elif args.command == "browse":
        cmd_browse(args)
    else:
        browse_args = argparse.Namespace(
            harness=getattr(args, 'harness', None),
            project=getattr(args, 'project', None),
        )
        cmd_browse(browse_args)


if __name__ == "__main__":
    main()
