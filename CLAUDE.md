# Agent Side Lanes governance

- Preserve explicit host, mode, provider, and model selection. Never silently fall back or substitute.
- Review mode stays read-only. Execute mode uses a dedicated Git worktree and same-user host authority; do not describe it as a sandbox.
- Execute lanes may edit, test, commit, and push only their dedicated branch. They may not deploy, merge, force-push, mutate IAM/credentials/cloud/data, or run database writes.
- Credentials use macOS Keychain or Windows Credential Manager. Never log, print, commit, or accept keys as CLI arguments.
- Tests use mocks and temporary repositories. Never make a paid provider request or read a real credential during validation.
