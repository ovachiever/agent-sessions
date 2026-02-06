# Public Release Readiness Checklist

## Current State

Solid foundation: good README, MIT license, clean pyproject.toml, 25 passing tests, no hardcoded secrets in source, no TODOs/FIXMEs.

---

## Must-Fix (blocking release)

- [ ] **No `.gitignore`** -- add one covering `__pycache__/`, `*.egg-info/`, `dist/`, `build/`, `.pytest_cache/`, `.env`, etc. The `agent_sessions.egg-info/` directory is currently tracked.
- [ ] **Remove `agent_sessions.egg-info/`** from git history -- build artifact shouldn't be in the repo.
- [ ] **Remove `droid_sessions.py`** (or exclude it) -- 1400-line legacy monolith that duplicates `agent_sessions/`. Confusing for contributors; if keeping, at minimum add a deprecation note and exclude from the package.
- [ ] **Remove `docs/PLAN.md` and `docs/SQLITE_INDEX_PLAN.md`** -- internal dev planning docs, not useful for end users.
- [ ] **No git remote configured** -- create the GitHub repo at `erikjamesfritsch/agent-sessions` and push.
- [ ] **Summary cache hardcoded to `~/.factory/`** -- `DEFAULT_CACHE_PATH` points to `~/.factory/session-summaries.json` which is Factory-specific. Should default to `~/.cache/agent-sessions/`.

## Should-Do (quality / credibility)

- [ ] **Add `CHANGELOG.md`** -- even a simple initial entry for v0.1.0.
- [ ] **Add `CONTRIBUTING.md`** -- brief guide: how to add a provider, run tests, submit PRs.
- [ ] **Add `.github/` workflows** -- at minimum a CI workflow running `pytest` on push/PR.
- [ ] **README updates**:
  - Add the `i` (reindex) keybinding to the keybindings table
  - Mention `anthropic` SDK is optional (for summaries) and `openai` SDK is optional (for embeddings) -- currently the README says anthropic is optional but `pyproject.toml` lists it as a hard dependency
  - Add a GIF/screenshot of the TUI
- [ ] **Make `anthropic` optional in `pyproject.toml`** -- move from `dependencies` to `[project.optional-dependencies]` (e.g., `summaries = ["anthropic>=0.40.0"]`). Already guarded with `try/except` in code, but pip installs it unconditionally.
- [ ] **Add more tests** -- no tests for the indexer, database, search, cache, or chunker modules. 25 tests only cover providers and search basics.
- [ ] **Add `py.typed` marker** for downstream type-checking support.

## Nice-to-Have

- [ ] **PyPI publishing setup** -- GitHub Actions workflow for building/publishing on tag push, or document manual `python -m build && twine upload`.
- [ ] **Add `[project.optional-dependencies]` for embeddings** -- `openai` is imported in `index/embeddings.py` but not listed anywhere in pyproject.toml.
- [ ] **Homebrew formula or `pipx` install instructions** in README.
- [ ] **Git history cleanup** -- 35 commits, many are auto-commits with agent IDs. Consider squashing into a clean v0.1.0 initial commit before first public push.
