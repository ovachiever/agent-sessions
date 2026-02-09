"""Session indexer for full and incremental indexing."""

import json
import logging
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Callable, Optional

from ..models import Session
from ..providers.base import SessionProvider
from .chunker import SessionChunker
from .database import ChunkRow, MessageRow, SessionDatabase
from .embeddings import EmbeddingGenerator

logger = logging.getLogger(__name__)

ProjectStats = dict[str, list | int | defaultdict]


def _create_project_entry() -> dict:
    return {
        "sessions": [],
        "messages": 0,
        "harness_counts": defaultdict(int),
    }

OPENCODE_MESSAGE_DIR = (
    Path.home() / ".local" / "share" / "opencode" / "storage" / "message"
)
OPENCODE_PART_DIR = (
    Path.home() / ".local" / "share" / "opencode" / "storage" / "part"
)


class SessionIndexer:
    """Index sessions into SQLite database with full and incremental modes."""

    def __init__(
        self,
        db: SessionDatabase,
        providers: list[SessionProvider],
    ):
        self.db = db
        self.providers = providers
        self.tagger = None
        self.chunker = SessionChunker()
        self.embedder = EmbeddingGenerator()

    def _get_tagger(self):
        if self.tagger is None:
            from .tagger import AutoTagger
            self.tagger = AutoTagger()
        return self.tagger

    def full_reindex(
        self,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        metadata_only: bool = False,
    ) -> dict:
        """
        Perform full reindex of all sessions from all providers.

        Args:
            progress_callback: Optional callback(current, total, session_id) for progress

        Returns:
            Stats dict with sessions_indexed, messages_indexed, chunks_created, time_ms
        """
        start_time = time.time()
        stats = {
            "sessions_indexed": 0,
            "messages_indexed": 0,
            "chunks_created": 0,
            "time_ms": 0,
        }
        
        projects: dict[str, dict] = defaultdict(_create_project_entry)

        all_session_paths = []
        for provider in self.providers:
            if provider.is_available():
                paths = provider.discover_session_files()
                all_session_paths.extend((provider, p) for p in paths)

        total = len(all_session_paths)
        logger.info(f"Full reindex: {total} sessions from {len(self.providers)} providers")

        for i, (provider, path) in enumerate(all_session_paths):
            try:
                session = provider.parse_session(path)
                if not session:
                    continue

                result = self._index_session(session, provider, metadata_only=metadata_only)
                if result:
                    stats["sessions_indexed"] += 1
                    stats["messages_indexed"] += result["messages"]
                    stats["chunks_created"] += result["chunks"]

                    project_key = str(session.project_path) if session.project_path else "unknown"
                    projects[project_key]["sessions"].append(session)
                    projects[project_key]["messages"] += result["messages"]
                    projects[project_key]["harness_counts"][session.harness] += 1

                if progress_callback:
                    progress_callback(i + 1, total, session.id)

            except Exception as e:
                logger.warning(f"Failed to index {path}: {e}")
                continue

        try:
            self._update_all_project_stats(projects)
        except Exception as e:
            logger.warning(f"Failed to update project stats: {e}")

        stats["time_ms"] = int((time.time() - start_time) * 1000)
        logger.info(
            f"Full reindex complete: {stats['sessions_indexed']} sessions, "
            f"{stats['messages_indexed']} messages, {stats['chunks_created']} chunks "
            f"in {stats['time_ms']}ms"
        )
        return stats

    def incremental_update(self, max_age_hours: int | None = None) -> dict:
        """
        Perform incremental update - only index new or changed sessions.

        Uses file mtime vs indexed_at to detect changes.
        Special handling for OpenCode: checks all message file mtimes.

        Args:
            max_age_hours: If set, only index new sessions modified within this many hours.
                Already-indexed sessions that changed are always re-indexed regardless.

        Returns:
            Stats dict with sessions_indexed, messages_indexed, chunks_created, time_ms
        """
        start_time = time.time()
        stats = {
            "sessions_indexed": 0,
            "messages_indexed": 0,
            "chunks_created": 0,
            "time_ms": 0,
        }

        age_cutoff = None
        if max_age_hours is not None:
            age_cutoff = int(time.time()) - (max_age_hours * 3600)

        indexed_sessions: dict[str, tuple[int, int]] = {}
        for row in self.db.get_session_rows():
            if row.file_mtime and row.indexed_at:
                indexed_sessions[row.id] = (row.file_mtime, row.indexed_at)

        projects: dict[str, dict] = defaultdict(_create_project_entry)

        sessions_to_index = []

        for provider in self.providers:
            if not provider.is_available():
                continue

            # Skip slow-discovery providers during quick startup sync
            if age_cutoff and not provider.fast_discovery:
                continue

            for path in provider.discover_session_files():
                session_id = path.stem
                
                file_mtime = self._get_session_mtime(provider, path, session_id)
                if file_mtime is None:
                    continue

                needs_index = False
                if session_id not in indexed_sessions:
                    if age_cutoff and file_mtime < age_cutoff:
                        continue
                    needs_index = True
                else:
                    stored_mtime, indexed_at = indexed_sessions[session_id]
                    if file_mtime > indexed_at:
                        needs_index = True

                if needs_index:
                    sessions_to_index.append((provider, path))

        logger.info(f"Incremental update: {len(sessions_to_index)} sessions to index")

        for provider, path in sessions_to_index:
            try:
                session = provider.parse_session(path)
                if not session:
                    continue

                result = self._index_session(session, provider)
                if result:
                    stats["sessions_indexed"] += 1
                    stats["messages_indexed"] += result["messages"]
                    stats["chunks_created"] += result["chunks"]

                    project_key = str(session.project_path) if session.project_path else "unknown"
                    projects[project_key]["sessions"].append(session)
                    projects[project_key]["messages"] += result["messages"]
                    projects[project_key]["harness_counts"][session.harness] += 1

            except Exception as e:
                logger.warning(f"Failed to index {path}: {e}")
                continue

        self._update_all_project_stats(projects)

        stats["time_ms"] = int((time.time() - start_time) * 1000)
        logger.info(
            f"Incremental update complete: {stats['sessions_indexed']} sessions, "
            f"{stats['messages_indexed']} messages, {stats['chunks_created']} chunks "
            f"in {stats['time_ms']}ms"
        )
        return stats

    def _index_session(
        self,
        session: Session,
        provider: SessionProvider,
        metadata_only: bool = False,
    ) -> Optional[dict]:
        try:
            if metadata_only:
                messages = []
                tags = []
                chunks = []
            else:
                messages = self._get_session_messages(session, provider)
                tags = self._get_tagger().generate_tags(session, messages)
                chunks = self.chunker.chunk_session(session, messages)

            file_mtime = self._get_session_mtime(provider, session.raw_path, session.id)
            indexed_at = int(time.time())

            turn_count = sum(1 for m in messages if m.get("role") == "user")

            first_prompt_preview = None
            if session.first_prompt:
                first_prompt_preview = session.first_prompt[:200]
                if len(session.first_prompt) > 200:
                    first_prompt_preview += "..."

            timestamp = int(session.created_time.timestamp()) if session.created_time else indexed_at
            timestamp_end = int(session.modified_time.timestamp()) if session.modified_time else None

            # Only set parent_id if parent exists in DB (FK constraint)
            safe_parent_id = None
            if session.parent_id:
                existing = self.db.get_session(session.parent_id)
                if existing:
                    safe_parent_id = session.parent_id

            self.db.upsert_session(
                session_id=session.id,
                harness=session.harness,
                timestamp=timestamp,
                project_path=str(session.project_path) if session.project_path else None,
                project_name=session.project_name,
                timestamp_end=timestamp_end,
                is_child=session.is_child,
                parent_id=safe_parent_id,
                child_type=session.child_type if session.is_child else None,
                message_count=len(messages),
                turn_count=turn_count,
                first_prompt_preview=first_prompt_preview,
                file_path=str(session.raw_path) if session.raw_path else None,
                file_mtime=file_mtime,
                indexed_at=indexed_at,
                auto_tags=tags,
            )

            if not metadata_only:
                self.db.delete_messages_for_session(session.id)
                self.db.delete_chunks_for_session(session.id)

                message_rows = []
                for i, msg in enumerate(messages):
                    msg_id = msg.get("id", f"{session.id}_msg_{i}")
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        content = " ".join(
                            block.get("text", "") for block in content
                            if isinstance(block, dict) and block.get("type") == "text"
                        )

                    has_code = "```" in content or "def " in content or "function " in content

                    tool_mentions = None
                    if "agent-do" in content:
                        tools = re.findall(r'agent-do\s+(\w+)', content)
                        if tools:
                            tool_mentions = json.dumps(list(set(tools)))

                    message_rows.append(MessageRow(
                        id=msg_id,
                        session_id=session.id,
                        role=msg.get("role", "unknown"),
                        content=content,
                        timestamp=msg.get("timestamp"),
                        sequence=i,
                        has_code=has_code,
                        tool_mentions=tool_mentions,
                    ))

                self.db.upsert_messages(message_rows)

                # Generate embeddings if available
                if self.embedder.available:
                    chunks = self.embedder.embed_chunks(chunks)

                chunk_rows = [
                    ChunkRow(
                        id=None,
                        session_id=chunk.session_id,
                        message_id=chunk.message_id,
                        chunk_index=chunk.chunk_index,
                        chunk_type=chunk.chunk_type,
                        content=chunk.content,
                        metadata=chunk.metadata,
                        embedding=chunk.embedding,
                        embedding_model="text-embedding-3-small" if chunk.embedding else None,
                        created_at=None,
                    )
                    for chunk in chunks
                ]
                self.db.upsert_chunks(chunk_rows)

            return {
                "messages": len(messages),
                "chunks": len(chunks),
            }

        except Exception as e:
            logger.error(f"Failed to index session {session.id}: {e}")
            return None

    def _get_session_messages(
        self,
        session: Session,
        provider: SessionProvider,
    ) -> list[dict]:
        messages = []

        if provider.name == "opencode":
            message_dir = OPENCODE_MESSAGE_DIR / session.id
            if message_dir.exists():
                for msg_file in sorted(message_dir.glob("*.json")):
                    try:
                        with open(msg_file) as f:
                            msg_data = json.load(f)

                        role = msg_data.get("role", "")
                        msg_id = msg_data.get("id", "")
                        content = self._get_opencode_message_content(msg_id)

                        time_data = msg_data.get("time", {})
                        timestamp = time_data.get("created")
                        if timestamp:
                            timestamp = int(timestamp / 1000)

                        messages.append({
                            "id": msg_id,
                            "role": role,
                            "content": content,
                            "timestamp": timestamp,
                        })
                    except (json.JSONDecodeError, IOError):
                        continue

        else:
            if session.raw_path and session.raw_path.exists():
                try:
                    if session.raw_path.suffix == ".jsonl":
                        with open(session.raw_path) as f:
                            for i, line in enumerate(f):
                                try:
                                    msg = json.loads(line)
                                    messages.append({
                                        "id": msg.get("id", f"{session.id}_msg_{i}"),
                                        "role": msg.get("role", ""),
                                        "content": msg.get("content", ""),
                                        "timestamp": msg.get("timestamp"),
                                    })
                                except json.JSONDecodeError:
                                    continue
                except IOError:
                    pass

        if not messages:
            if session.first_prompt:
                messages.append({
                    "id": f"{session.id}_msg_0",
                    "role": "user",
                    "content": session.first_prompt,
                    "timestamp": int(session.created_time.timestamp()) if session.created_time else None,
                })
            if session.last_response:
                messages.append({
                    "id": f"{session.id}_msg_1",
                    "role": "assistant",
                    "content": session.last_response,
                    "timestamp": int(session.modified_time.timestamp()) if session.modified_time else None,
                })

        return messages

    def _get_opencode_message_content(self, message_id: str) -> str:
        if not message_id:
            return ""

        part_dir = OPENCODE_PART_DIR / message_id
        if not part_dir.exists():
            return ""

        texts = []
        for part_file in sorted(part_dir.glob("*.json")):
            try:
                with open(part_file) as f:
                    part = json.load(f)
                if part.get("type") == "text" and part.get("text"):
                    texts.append(part["text"])
            except (json.JSONDecodeError, IOError):
                continue

        return "\n".join(texts)

    def _get_session_mtime(
        self,
        provider: SessionProvider,
        path: Path,
        session_id: str,
    ) -> Optional[int]:
        try:
            if provider.name == "opencode":
                message_dir = OPENCODE_MESSAGE_DIR / session_id
                if message_dir.exists():
                    message_files = list(message_dir.glob("*.json"))
                    if message_files:
                        return int(max(f.stat().st_mtime for f in message_files))
            else:
                if path.exists():
                    return int(path.stat().st_mtime)
        except OSError:
            pass
        return None

    def _opencode_has_new_messages(self, session_id: str, indexed_at: int) -> bool:
        try:
            message_dir = OPENCODE_MESSAGE_DIR / session_id
            if not message_dir.exists():
                return False

            for msg_file in message_dir.glob("*.json"):
                if msg_file.stat().st_mtime > indexed_at:
                    return True

            part_base = OPENCODE_PART_DIR
            for part_dir in part_base.glob(f"{session_id}*"):
                for part_file in part_dir.glob("*.json"):
                    if part_file.stat().st_mtime > indexed_at:
                        return True

        except OSError:
            pass
        return False

    def _update_all_project_stats(self, projects: dict):
        current_time = int(time.time())

        for project_path, data in projects.items():
            sessions = data["sessions"]
            if not sessions:
                continue

            total_sessions = len(sessions)
            parent_sessions = sum(1 for s in sessions if not s.is_child)
            child_sessions = sum(1 for s in sessions if s.is_child)

            timestamps = [
                int(s.created_time.timestamp()) if s.created_time else 0
                for s in sessions
            ]
            positive_timestamps = [t for t in timestamps if t > 0]
            first_session_time = min(positive_timestamps) if positive_timestamps else None
            last_session_time = max(positive_timestamps) if positive_timestamps else None

            project_name = sessions[0].project_name if sessions else None

            all_tags: dict[str, int] = defaultdict(int)
            for session in sessions:
                session_row = self.db.get_session(session.id)
                if session_row and session_row.auto_tags:
                    try:
                        tags = json.loads(session_row.auto_tags)
                        for tag in tags:
                            all_tags[tag] += 1
                    except json.JSONDecodeError:
                        pass

            common_tags = sorted(all_tags.items(), key=lambda x: x[1], reverse=True)[:10]
            common_tags_list = [tag for tag, _ in common_tags]

            self.db.update_project_stats(
                project_path=project_path,
                project_name=project_name,
                total_sessions=total_sessions,
                parent_sessions=parent_sessions,
                child_sessions=child_sessions,
                first_session_time=first_session_time,
                last_session_time=last_session_time,
                harness_counts=dict(data["harness_counts"]),
                total_messages=data["messages"],
                common_tags=common_tags_list,
                updated_at=current_time,
            )
