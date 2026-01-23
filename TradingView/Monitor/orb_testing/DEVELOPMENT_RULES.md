# Development Rules (read me before making changes)

## Golden rule
If you change any trading logic, notification behavior, logging format, state handling, or configuration:

1. Update **CHANGELOG.md**
2. Update **PROJECT_CONTEXT.md** (if user-facing behavior changed)
3. Update inline comments/docstrings where the change was made

These files are the "handoff contract" so we can resume work quickly in future sessions without re-reading chat history.
