# AGENTS.md

- Handle simple, local, low-risk tasks directly. Do not create a plan, use subagents, or run a full test suite unless needed.
- For complex, multi-file, ambiguous, or high-risk work, state a brief plan with concrete success checks.
- Surface assumptions only when they materially affect the result. Ask before proceeding only when a wrong assumption would be costly or unsafe.
- Use `rg` to locate relevant code. Inspect targeted files first; do not scan the whole repository without a clear reason.
- Keep changes minimal and surgical. Do not add speculative features, abstractions, cleanup, or unrelated refactors.
- Limit command and log output to the portion needed for diagnosis. Prefer targeted searches and tails over full dumps.
- Run the narrowest relevant test first. Run broader suites only when the change scope or risk justifies them.
- Verify results in proportion to risk before claiming completion.
- For a small, isolated change, run only the directly affected checks. Do not run adjacent or full test suites unless the shared path was changed or there is concrete regression risk.
- Keep tool output and final handoff concise: report the outcome, required action, and only the verification that matters to the request. Avoid test inventories, repeated explanations, and unnecessary implementation detail.

## Default Git Workflow

- After development and verification are complete, commit the relevant changes and push them to the GitHub `main` branch by default.
- Use a concise Conventional Commit message that describes the completed change.
- Never stage unrelated user changes, local secrets, environment files, databases, generated artifacts, or untracked planning documents unless explicitly requested.
- Before committing, run `git diff --check`, review the staged diff, and scan it for credentials or secrets.
- Do not commit or push when relevant tests fail, sensitive information is detected, the remote has diverged, or pushing would require a destructive Git operation. Report the blocker instead.
- Never force-push unless the user explicitly requests it.
## Local Development and Cloud Server Access

- All code development, editing, debugging, and testing must be performed on the local development machine. Do not develop or modify source code on the cloud server.
- The cloud server is for deployment and runtime operations only. For project code, only pull the latest committed code from the GitHub `main` branch; do not create, edit, patch, or commit source files on the server.
- Connect from Windows PowerShell with `ssh -i "$env:USERPROFILE\.ssh\guijie.pem" ubuntu@150.158.52.233`.
- The SSH private key must remain local and must never be copied into the repository or committed to Git.

## External API Integrations

- For Eyun/WeChat integration work, consult the official API documentation at `https://wkteam.cn/docs/api-wen-dang2/` before guessing endpoint names, request parameters, response fields, or error handling.
- Prefer documented provider APIs to enrich data that is absent from webhook payloads. Verify the real response shape against the documentation before implementing field mappings.
