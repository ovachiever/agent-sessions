# droid-sessions

A Textual TUI for browsing and resuming [Factory Droid](https://www.factory.ai/) sessions.

![droid-sessions screenshot](docs/screenshot.png)

## Features

- **Split-pane interface** separating parent orchestrator sessions from sub-agent sessions
- **Dynamic width** - descriptions adapt to terminal size
- **Session linking** - automatically matches sub-agents to their parent sessions by timestamp
- **Full prompt display** - scrollable detail panel shows complete prompts and responses
- **Quick resume** - copy resume command or launch directly

## Installation

```bash
# Clone the repo
git clone https://github.com/yourusername/droid-sessions.git
cd droid-sessions

# Install dependencies
pip install textual rich

# Run
python droid_sessions.py
```

Or install to your PATH:

```bash
# Create symlink
ln -sf $(pwd)/droid_sessions.py ~/.local/bin/droid-sessions
chmod +x droid_sessions.py
```

## Usage

```bash
droid-sessions
```

### Keybindings

| Key | Action |
|-----|--------|
| `j/k` or `↑/↓` | Navigate within focused pane |
| `Tab` | Switch between parent and sub-agent panes |
| `Shift+Tab` | Toggle between left panes and detail panel |
| `Enter` | Copy resume command to clipboard |
| `r` | Resume selected session immediately |
| `Escape` | Back to list (from detail) or quit |
| `q` | Quit |

### Panes

- **Top-left**: Parent sessions (your direct interactions with Droid)
- **Bottom-left**: Sub-agent sessions (Task tool invocations from the selected parent)
- **Right**: Session details with full prompt and response

## How It Works

The TUI reads session data from `~/.factory/sessions/` where Factory Droid stores conversation history.

**Session Classification:**
- **Parent sessions**: Direct user interactions (title does NOT start with `# Task Tool Invocation`)
- **Sub-agent sessions**: Task tool invocations (title starts with `# Task Tool Invocation Subagent type:`)

**Session Linking:**
Sub-agents are matched to parents by:
1. Matching `subagent_type` from Task tool calls
2. Timestamp proximity (within 60 seconds)
3. Same working directory

## Requirements

- Python 3.10+
- [Textual](https://textual.textualize.io/) >= 0.40.0
- [Rich](https://rich.readthedocs.io/) >= 13.0.0
- Factory Droid CLI (for session data)

## License

MIT
