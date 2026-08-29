---
name: claude-side-lane
description: Route an approved review or implementation subtask from Codex to an exact Claude or optional GLM model while retaining Codex-native connectors, including Prompt it staffing and explicit spend routing.
---

# Claude/GLM side lane

This is the Codex-native wrapper. Install this directory only under `~/.agents/skills/claude-side-lane`; Codex loads its instructions and calls the shared `side-lane` executable. The Claude Code wrapper is a separate package and is not invoked by Codex.

Use review mode for a locked-down second opinion. Use execute mode only for an approved implementation subtask with a dedicated worktree, exact capabilities, and explicit provider/model. Selection may be capability- or spend-driven; never silently substitute or claim equivalence.

For a read-only native Claude review:

Run from the governed target repository:

```bash
side-lane run --mode review --host claude --provider claude --model claude-sonnet-5 --repo "$PWD" --prompt "Review the error-handling design in src/example.py."
```

For implementation, stay inside the Codex harness so Codex's logged-in MCP/connectors remain authoritative:

```bash
side-lane run --mode execute --host codex --provider openrouter --model anthropic/claude-sonnet-5 --lane-name bounded-task --repo "$PWD" --capability workspace-write --prompt-file /tmp/approved-brief.md
```

GLM is an optional explicit OpenRouter model route, not a fallback and not a third connector identity. When Prompt it proposes a lane, invoke it only after approval of the host, provider/model, task, worktree intent, and capabilities. If `side-lane check-capabilities` is unavailable or reports the route unavailable, continue ordinary in-host staffing; do not install, substitute, or block.

An execute lane may edit/test/commit/push only its dedicated branch and may perform only explicitly required workflow writes. It must not deploy, merge, force-push, mutate cloud/IAM/credentials/data, or run database writes. The worktree is not a sandbox. The target must have an `AGENTS.md` line requiring and authoritatively linking root `CLAUDE.md`.
