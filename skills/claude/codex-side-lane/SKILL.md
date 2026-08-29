---
name: codex-side-lane
description: Route an approved review or implementation subtask from Claude Code to an exact OpenAI or optional GLM model while retaining Claude-native connectors, including Prompt it staffing and explicit spend routing.
---

# Codex/OpenAI side lane

This is the Claude Code-native wrapper. Install this directory only under `~/.claude/skills/codex-side-lane`; Claude Code loads its instructions and calls the shared `side-lane` executable. The Codex wrapper is a separate package and is not invoked by Claude Code.

Use review mode for a locked-down second opinion. Use execute mode only for an approved implementation subtask with an exact OpenRouter model, dedicated worktree, and explicit capabilities. Never silently substitute or claim models are equivalent.

```bash
side-lane run --mode review --host claude --provider openrouter --model openai/gpt-5.6-sol --repo "$PWD" --prompt "Independently review the cache design."
```

For implementation, stay inside Claude Code so its user/project/local MCP and connectors remain authoritative:

```bash
side-lane run --mode execute --host claude --provider openrouter --model openai/gpt-5.6-terra --lane-name bounded-task --repo "$PWD" --capability workspace-write --prompt-file /tmp/approved-brief.md
```

GLM is optional, explicit, and never a fallback. If Prompt it finds no configured exact route, it continues its normal in-host staffing without installing or substituting anything.

An execute lane may edit/test/commit/push only its dedicated branch and perform only explicitly required workflow writes. It must not deploy, merge, force-push, mutate cloud/IAM/credentials/data, or run database writes. The worktree is not a sandbox. Treat results and diffs as review input to the coordinator.
