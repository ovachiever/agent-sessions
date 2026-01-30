"""Session chunking for semantic search indexing.

Implements turn-based chunking strategy:
1. Session summary chunk (project, tags, first prompt, tools)
2. Turn-based chunks (~400 tokens, respecting message boundaries)
3. Dedicated tool-usage chunks for agent-do mentions
"""

import json
import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class Chunk:
    """A chunk of session content for embedding and search."""

    session_id: str
    message_id: Optional[str]  # None for summary chunks
    chunk_index: int
    chunk_type: str  # 'summary', 'turn', 'tool_usage'
    content: str
    metadata: str  # JSON string
    embedding: Optional[bytes] = None  # Will be populated by Task 5


class SessionChunker:
    """Chunks sessions into searchable segments."""

    TARGET_TOKENS = 400
    SUMMARY_PREVIEW_CHARS = 200

    def __init__(self):
        # Regex to detect agent-do commands
        self.agent_do_pattern = re.compile(
            r'agent-do\s+(\S+)(?:\s+(.+?))?(?:\n|$)',
            re.MULTILINE
        )

    def estimate_tokens(self, text: str) -> int:
        """Rough token estimation: chars / 4."""
        return len(text) // 4

    def extract_tool_mentions(self, messages: list) -> list[str]:
        """Extract unique agent-do tool mentions from messages."""
        tools = set()
        for msg in messages:
            content = msg.get('content', '')
            if isinstance(content, str):
                matches = self.agent_do_pattern.findall(content)
                for tool, _ in matches:
                    tools.add(tool)
        return sorted(tools)

    def create_summary_chunk(
        self,
        session,
        messages: list,
        chunk_index: int
    ) -> Chunk:
        """Create session summary chunk (always first)."""
        # Extract first prompt
        first_prompt = ""
        for msg in messages:
            if msg.get('role') == 'user':
                content = msg.get('content', '')
                if isinstance(content, str) and content.strip():
                    first_prompt = content[:self.SUMMARY_PREVIEW_CHARS]
                    if len(content) > self.SUMMARY_PREVIEW_CHARS:
                        first_prompt += "..."
                    break

        # Get tool mentions
        tools = self.extract_tool_mentions(messages)

        # Build summary content
        parts = [
            f"Project: {session.project_name}",
            f"Path: {session.project_path}",
        ]

        if session.title:
            parts.append(f"Title: {session.title}")

        if first_prompt:
            parts.append(f"First prompt: {first_prompt}")

        if tools:
            parts.append(f"Tools used: {', '.join(tools)}")

        content = "\n".join(parts)

        # Metadata
        metadata = {
            "chunk_type": "summary",
            "session_id": session.id,
            "project_name": session.project_name,
            "harness": session.harness,
            "tools": tools,
        }

        return Chunk(
            session_id=session.id,
            message_id=None,
            chunk_index=chunk_index,
            chunk_type="summary",
            content=content,
            metadata=json.dumps(metadata),
        )

    def create_tool_usage_chunks(
        self,
        session,
        messages: list,
        start_index: int
    ) -> list[Chunk]:
        """Create dedicated chunks for agent-do tool usage."""
        chunks = []
        chunk_index = start_index

        for msg in messages:
            content = msg.get('content', '')
            if not isinstance(content, str):
                continue

            matches = self.agent_do_pattern.finditer(content)
            for match in matches:
                tool = match.group(1)
                command = match.group(2) or ""

                # Extract context around the command (up to 200 chars before/after)
                start = max(0, match.start() - 200)
                end = min(len(content), match.end() + 200)
                context = content[start:end].strip()

                chunk_content = f"Tool: agent-do {tool}\n"
                if command:
                    chunk_content += f"Command: {command}\n"
                chunk_content += f"Context: {context}"

                metadata = {
                    "chunk_type": "tool_usage",
                    "session_id": session.id,
                    "message_id": msg.get('id'),
                    "tool": f"agent-do-{tool}",
                    "command": command,
                }

                chunks.append(Chunk(
                    session_id=session.id,
                    message_id=msg.get('id'),
                    chunk_index=chunk_index,
                    chunk_type="tool_usage",
                    content=chunk_content,
                    metadata=json.dumps(metadata),
                ))
                chunk_index += 1

        return chunks

    def create_turn_chunks(
        self,
        session,
        messages: list,
        start_index: int
    ) -> list[Chunk]:
        """Create turn-based chunks (~400 tokens, respecting message boundaries)."""
        chunks = []
        chunk_index = start_index

        current_chunk_messages = []
        current_chunk_msg_ids = []
        current_tokens = 0

        for msg in messages:
            content = msg.get('content', '')
            if not isinstance(content, str):
                continue

            # Format message with role
            role = msg.get('role', 'unknown')
            formatted = f"[{role}]: {content}"
            msg_tokens = self.estimate_tokens(formatted)

            # Check if adding this message would exceed target
            if current_tokens + msg_tokens > self.TARGET_TOKENS and current_chunk_messages:
                # Flush current chunk
                chunk_content = "\n\n".join(current_chunk_messages)

                metadata = {
                    "chunk_type": "turn",
                    "session_id": session.id,
                    "message_ids": current_chunk_msg_ids,
                    "token_count": current_tokens,
                }

                chunks.append(Chunk(
                    session_id=session.id,
                    message_id=current_chunk_msg_ids[0] if current_chunk_msg_ids else None,
                    chunk_index=chunk_index,
                    chunk_type="turn",
                    content=chunk_content,
                    metadata=json.dumps(metadata),
                ))
                chunk_index += 1

                # Start new chunk
                current_chunk_messages = [formatted]
                current_chunk_msg_ids = [msg.get('id')]
                current_tokens = msg_tokens
            else:
                # Add to current chunk
                current_chunk_messages.append(formatted)
                current_chunk_msg_ids.append(msg.get('id'))
                current_tokens += msg_tokens

        # Flush remaining messages
        if current_chunk_messages:
            chunk_content = "\n\n".join(current_chunk_messages)

            metadata = {
                "chunk_type": "turn",
                "session_id": session.id,
                "message_ids": current_chunk_msg_ids,
                "token_count": current_tokens,
            }

            chunks.append(Chunk(
                session_id=session.id,
                message_id=current_chunk_msg_ids[0] if current_chunk_msg_ids else None,
                chunk_index=chunk_index,
                chunk_type="turn",
                content=chunk_content,
                metadata=json.dumps(metadata),
            ))

        return chunks

    def chunk_session(self, session, messages: list) -> list[Chunk]:
        """
        Chunk a session into searchable segments.

        Args:
            session: Session object with metadata
            messages: List of message dicts with 'role', 'content', 'id'

        Returns:
            List of Chunk objects ready for embedding
        """
        chunks = []
        chunk_index = 0

        # 1. Create summary chunk (always first)
        summary = self.create_summary_chunk(session, messages, chunk_index)
        chunks.append(summary)
        chunk_index += 1

        # 2. Create turn-based chunks
        turn_chunks = self.create_turn_chunks(session, messages, chunk_index)
        chunks.extend(turn_chunks)
        chunk_index += len(turn_chunks)

        # 3. Create tool usage chunks
        tool_chunks = self.create_tool_usage_chunks(session, messages, chunk_index)
        chunks.extend(tool_chunks)

        return chunks
