# AGENTS.md

- Handle simple, local, low-risk tasks directly. Do not create a plan, use subagents, or run a full test suite unless needed.
- For complex, multi-file, ambiguous, or high-risk work, state a brief plan with concrete success checks.
- Surface assumptions only when they materially affect the result. Ask before proceeding only when a wrong assumption would be costly or unsafe.
- Use `rg` to locate relevant code. Inspect targeted files first; do not scan the whole repository without a clear reason.
- Keep changes minimal and surgical. Do not add speculative features, abstractions, cleanup, or unrelated refactors.
- Limit command and log output to the portion needed for diagnosis. Prefer targeted searches and tails over full dumps.
- Run the narrowest relevant test first. Run broader suites only when the change scope or risk justifies them.
- Verify results in proportion to risk before claiming completion.

## Default Git Workflow

- After development and verification are complete, commit the relevant changes and push them to the GitHub `main` branch by default.
- Use a concise Conventional Commit message that describes the completed change.
- Never stage unrelated user changes, local secrets, environment files, databases, generated artifacts, or untracked planning documents unless explicitly requested.
- Before committing, run `git diff --check`, review the staged diff, and scan it for credentials or secrets.
- Do not commit or push when relevant tests fail, sensitive information is detected, the remote has diverged, or pushing would require a destructive Git operation. Report the blocker instead.
- Never force-push unless the user explicitly requests it.
