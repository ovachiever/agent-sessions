
## Task 8: TUI Integration - Delegation Failures

**Date**: 2026-01-30

**Issue**: Multiple delegation attempts for Task 8 failed with immediate errors
- Session ses_3eec2422cffe11wvPWst2gzV45: JSON Parse error
- Session ses_3eec1758bffeFbRfFx4rb8neHW: JSON Parse error
- Both failed at 0s duration with "error" status

**Root Cause**: System-level delegation mechanism issue, not task complexity

**Impact**: Task 8 (TUI integration) blocked, preventing Task 9 (CLI commands)

**Workaround Options**:
1. Manual implementation by orchestrator (breaks delegation pattern)
2. Retry with simpler prompt structure
3. Skip to Task 9 and return to Task 8 later
4. Document requirements and mark as manual task

**Decision**: Document requirements for manual completion and proceed to Task 9

**Task 8 Requirements** (for manual implementation):

1. **Imports** (add to top of app.py):
   ```python
   from agent_sessions.index import SessionDatabase, SessionIndexer, HybridSearch
   ```

2. **Initialization** (in __init__ or on_mount):
   ```python
   self.db = SessionDatabase()
   self.indexer = SessionIndexer(self.db, self.providers)
   self.search_engine = HybridSearch(self.db)
   ```

3. **Replace file scanning** (in load_sessions or similar):
   - Change from: `provider.load_sessions()`
   - Change to: `self.db.get_parents(harness=filter)`

4. **Background indexing** (add worker method):
   ```python
   @work(exclusive=True, thread=True)
   async def run_incremental_index(self):
       self.update_status("Indexing...")
       try:
           stats = self.indexer.incremental_update()
           if stats['sessions_updated'] > 0:
               self.notify(f"Indexed {stats['sessions_updated']} sessions")
       finally:
           self.update_status("Ready")
   ```
   - Call in on_mount()

5. **Integrate search**:
   - Replace search_sessions() calls with self.search_engine.search(query)

6. **Display auto-tags**:
   - Add session.auto_tags to SessionDetailPanel

**Verification**:
- python3 -m agent_sessions.main launches TUI
- Sessions load from database
- Search works with hybrid search
- No UI freezing

