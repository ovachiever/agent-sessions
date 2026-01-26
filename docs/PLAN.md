# Agent Sessions - Universal AI Coding Sessions Browser

## Overview

**Agent Sessions** is a universal TUI for browsing, searching, and resuming sessions across multiple AI coding assistants. One tool to find any conversation you've had with any AI agent.

```
┌────────────────────────────────────────────────────────────────┐
│  agent-sessions                                                │
│                                                                │
│  "Where did I implement that auth flow?"                       │
│  "What did Cursor suggest for the caching problem?"            │
│  "Resume my Droid session on the API refactor"                 │
│                                                                │
│  → Search once, find everywhere                                │
└────────────────────────────────────────────────────────────────┘
```

---

## Supported Harnesses

| Harness | Status | Session Location | Format |
|---------|--------|------------------|--------|
| Factory Droid | ✅ Done | `~/.factory/sessions/` | JSONL |
| Claude Code | 🎯 Priority | `~/.claude/projects/` | JSONL |
| Cursor | 📋 Planned | `~/.cursor/` | SQLite |
| Amp (Sourcegraph) | 📋 Planned | TBD | TBD |
| Aider | 📋 Planned | `.aider.chat.history.md` | Markdown |
| Continue.dev | 📋 Planned | `~/.continue/sessions/` | JSON |
| Cline/Roo | 📋 Planned | VS Code storage | JSON |
| OpenCode | 📋 Planned | TBD | TBD |

---

## Architecture

### Design Principles

1. **Provider Pattern** - Each harness is a pluggable provider
2. **Unified Model** - All sessions normalize to one schema
3. **Lazy Discovery** - Only scan enabled providers
4. **Cached Summaries** - AI summaries shared across harnesses
5. **Non-Destructive** - Read-only access to session files

### System Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         TUI Layer                               │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐  │
│  │ Session List │  │ Children/    │  │ Detail Panel          │  │
│  │ (filterable) │  │ Sub-agents   │  │ (prompt, response,    │  │
│  │              │  │              │  │  resume command)      │  │
│  └──────────────┘  └──────────────┘  └───────────────────────┘  │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │ Filter Bar: [All] [Droid] [Claude] [Cursor] | Search: _ │    │
│  └─────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Core Layer                                 │
│                                                                 │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  │
│  │ SessionManager  │  │ SearchEngine    │  │ SummaryCache    │  │
│  │                 │  │                 │  │                 │  │
│  │ - load_all()    │  │ - search()      │  │ - get/set       │  │
│  │ - filter()      │  │ - index()       │  │ - persist       │  │
│  │ - group()       │  │ - rank()        │  │                 │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Provider Layer                               │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              SessionProvider (ABC)                      │    │
│  │                                                         │    │
│  │  name: str           # "droid", "claude-code"           │    │
│  │  icon: str           # "🤖", "🧠"                        │    │
│  │  color: str          # for UI theming                   │    │
│  │                                                         │    │
│  │  get_sessions_dir() -> Path                             │    │
│  │  is_available() -> bool                                 │    │
│  │  discover_sessions() -> list[Path]                      │    │
│  │  parse_session(path) -> Session                         │    │
│  │  get_resume_command(session) -> str                     │    │
│  │  find_children(parent, all_sessions) -> list[Session]   │    │
│  └─────────────────────────────────────────────────────────┘    │
│           │              │              │              │        │
│           ▼              ▼              ▼              ▼        │
│     ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐     │
│     │  Droid   │  │  Claude  │  │  Cursor  │  │   Amp    │     │
│     │ Provider │  │  Code    │  │ Provider │  │ Provider │     │
│     └──────────┘  └──────────┘  └──────────┘  └──────────┘     │
└─────────────────────────────────────────────────────────────────┘
```

### Unified Session Model

```python
@dataclass
class Session:
    # Identity
    id: str                      # unique identifier
    harness: str                 # provider name
    raw_path: Path               # original file location
    
    # Project context
    project_path: Path           # working directory
    project_name: str            # derived from path
    
    # Content
    title: str                   # session title if available
    first_prompt: str            # initial user message
    last_prompt: str             # most recent user message
    last_response: str           # most recent assistant response
    
    # Timing
    created_time: datetime
    modified_time: datetime
    
    # Hierarchy
    is_child: bool = False       # sub-agent/child session
    child_type: str = ""         # e.g., "debugger", "code-reviewer"
    parent_id: str | None = None # link to parent session
    
    # Metadata
    model: str = ""              # claude-sonnet-4, gpt-4, etc.
    tool_calls: list[str] = field(default_factory=list)
    tokens_used: int | None = None
    
    # Computed (cached)
    summary: str | None = None   # AI-generated summary
    content_hash: str = ""       # for cache invalidation
```

---

## Implementation Plan

### Phase 1: Refactor to Provider Pattern
**Goal:** Extract Droid-specific code without changing UX

```
agent-sessions/
├── agent_sessions/
│   ├── __init__.py
│   ├── main.py              # entry point
│   ├── app.py               # TUI application
│   ├── models.py            # Session dataclass
│   ├── cache.py             # SummaryCache
│   ├── search.py            # SearchEngine
│   └── providers/
│       ├── __init__.py
│       ├── base.py          # SessionProvider ABC
│       └── droid.py         # Factory Droid provider
├── pyproject.toml
└── README.md
```

**Tasks:**
- [ ] Create `agent_sessions/` package structure
- [ ] Define `Session` dataclass in `models.py`
- [ ] Define `SessionProvider` ABC in `providers/base.py`
- [ ] Migrate Droid logic to `providers/droid.py`
- [ ] Update TUI to use provider interface
- [ ] Verify identical behavior to original

### Phase 2: Claude Code Provider
**Goal:** Add Claude Code as second provider

**Research:**
```bash
# Claude Code session locations (to verify)
~/.claude/projects/          # project-specific sessions
~/.claude/settings.json      # global config
```

**Tasks:**
- [ ] Document Claude Code session format
- [ ] Implement `ClaudeCodeProvider`
- [ ] Add provider auto-detection
- [ ] Test with real Claude Code sessions

### Phase 3: Multi-Provider UI
**Goal:** Filter and identify sessions by harness

**UI Changes:**
```
┌─────────────────────────────────────────────────────────────┐
│ Filter: [●All] [●Droid] [○Claude] [○Cursor]  Sessions: 247 │
├─────────────────────────────────────────────────────────────┤
│ 01-26 14:30 │ 🤖 api-server   │ Implemented auth middleware │
│ 01-26 13:15 │ 🧠 api-server   │ Debugged rate limiting      │
│ 01-26 12:00 │ 🤖 frontend     │ Added dark mode toggle      │
└─────────────────────────────────────────────────────────────┘
```

**Tasks:**
- [ ] Add harness icon/badge to session list
- [ ] Add filter bar with toggle buttons
- [ ] Persist filter preference
- [ ] Update search to respect filters

### Phase 4: Enhanced Search
**Goal:** Powerful cross-harness search

**Search Modes:**
| Key | Mode | Description |
|-----|------|-------------|
| `/` | Global | Search all sessions, all harnesses |
| `f/` | Filtered | Search within current filter |
| `p:name /` | Project | Search specific project |

**Tasks:**
- [ ] Implement scoped search
- [ ] Add search syntax (harness:, project:, before:, after:)
- [ ] Highlight search terms in results
- [ ] Optional: persistent search index for speed

### Phase 5: Additional Providers
**Goal:** Expand harness support based on demand

**Cursor Provider:**
```python
class CursorProvider(SessionProvider):
    name = "cursor"
    icon = "⌘"
    
    def get_sessions_dir(self):
        return Path.home() / ".cursor" / "User" / "workspaceStorage"
    
    # Cursor uses SQLite: state.vscdb
    def parse_session(self, path):
        # Query SQLite for conversation history
        ...
```

**Aider Provider:**
```python
class AiderProvider(SessionProvider):
    name = "aider"
    icon = "🔧"
    
    def discover_sessions(self):
        # Aider stores per-project: .aider.chat.history.md
        # Need to scan common project locations
        ...
```

### Phase 6: Configuration & Polish
**Goal:** User customization

**Config File:** `~/.config/agent-sessions/config.toml`
```toml
[general]
theme = "dark"
default_filter = "all"  # or "droid", "claude-code"

[providers]
# Enable/disable providers
droid = true
claude_code = true
cursor = false

[providers.droid]
sessions_dir = "~/.factory/sessions"  # override default

[summaries]
enabled = true
model = "claude-haiku-4-5-20251001"
max_concurrent = 3

[search]
max_results = 100
context_lines = 3
```

---

## File Structure (Final)

```
agent-sessions/
├── agent_sessions/
│   ├── __init__.py
│   ├── main.py                 # CLI entry point
│   ├── app.py                  # DroidSessionsBrowser → AgentSessionsBrowser
│   ├── models.py               # Session, SearchResult
│   ├── config.py               # Configuration loading
│   ├── cache.py                # SummaryCache
│   ├── search.py               # SearchEngine
│   ├── providers/
│   │   ├── __init__.py         # Provider registry
│   │   ├── base.py             # SessionProvider ABC
│   │   ├── droid.py            # Factory Droid
│   │   ├── claude_code.py      # Claude Code (Anthropic CLI)
│   │   ├── cursor.py           # Cursor
│   │   ├── aider.py            # Aider
│   │   └── amp.py              # Sourcegraph Amp
│   └── ui/
│       ├── __init__.py
│       ├── widgets.py          # SessionItem, DetailPanel
│       └── styles.py           # CSS constants
├── tests/
│   ├── test_providers.py
│   ├── test_search.py
│   └── fixtures/               # Sample session files
├── docs/
│   ├── PLAN.md                 # This file
│   └── PROVIDERS.md            # Provider implementation guide
├── pyproject.toml
├── README.md
└── LICENSE
```

---

## CLI Interface

```bash
# Launch TUI (default)
agent-sessions
ais                          # short alias

# Filter on launch
ais --harness droid          # only Droid sessions
ais --harness claude-code    # only Claude Code
ais --project api-server     # only specific project

# Search from CLI
ais search "auth middleware" # search and display results
ais search -h droid "auth"   # search specific harness

# Management
ais providers                # list available providers
ais providers --status       # show which are detected/enabled
ais cache clear              # clear summary cache
ais config                   # open config in $EDITOR
```

---

## Migration Path

For users of `droid-sessions`:

1. **Install:** `pip install agent-sessions`
2. **Run:** `ais` or `agent-sessions`
3. **Automatic:** Droid sessions appear immediately
4. **Optional:** Enable additional providers in config

Summary cache (`~/.factory/session-summaries.json`) is compatible and will be reused.

---

## Success Metrics

- [ ] All Droid functionality preserved
- [ ] <2s startup with 500+ sessions across providers
- [ ] Search returns results in <500ms
- [ ] AI summaries generate without blocking UI
- [ ] Users can find sessions from any harness with one tool

---

## Open Questions

1. **Cross-harness linking** - Should we detect when same project was worked on in multiple harnesses and show them together?

2. **Session export** - Should we support exporting session context to continue in a different harness?

3. **Remote sessions** - Some harnesses may store sessions remotely. Support?

4. **Plugin architecture** - Allow third-party providers via entry points?

---

## Next Steps

1. **Verify Claude Code format** - Check `~/.claude/` structure
2. **Create package structure** - `agent_sessions/` module
3. **Define ABC** - `SessionProvider` interface
4. **Migrate Droid** - Extract to provider
5. **Add tests** - Ensure parity with original
