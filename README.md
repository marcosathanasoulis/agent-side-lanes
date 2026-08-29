# Agent Side Lanes

MIT-licensed, host-native cross-provider worktree lanes for Codex and Claude
Code. One shared Python runner ships two proper host packages:

- a Codex skill that routes to an exact Claude or GLM model while retaining
  Codex MCP/connectors;
- a Claude Code skill that routes to an exact OpenAI or GLM model while
  retaining Claude Code MCP/connectors.

The skill formats are not interchangeable. They are host-native wrappers over
the same runner and route configuration.

## Modes

- review is a locked-down Read/Glob/Grep second-opinion lane.
- execute creates a dedicated branch/worktree and runs with the originating
  host's normal same-user authority. The worktree is not an OS sandbox.

Execute lanes may edit, test, commit, and push their own branch. They may not
deploy, merge, force-push, mutate IAM/credentials/cloud infrastructure/data, or
run database writes. An explicitly assigned workflow write may use the
originating host connector and must be reported.

## Install

On macOS:

    ./scripts/install.sh install codex
    ./scripts/install.sh check codex

    # Or:
    ./scripts/install.sh install claude

On Windows:

    .\scripts\install.ps1 install codex
    .\scripts\install.ps1 check codex

The macOS installer uses owned symlinks. Windows uses owned copies plus a
manifest. Both refuse unrelated destinations and support reversible uninstall.

## Credentials

Keys are per-person and local. Use separate services:

    security add-generic-password -U -a "$USER" -s agent-side-lanes-openrouter -w
    security add-generic-password -U -a "$USER" -s agent-side-lanes-anthropic -w
    security add-generic-password -U -a "$USER" -s agent-side-lanes-glm -w

Windows:

    .\scripts\credential.ps1 set agent-side-lanes-openrouter

Use your own provider or organization-member key. Never distribute a personal
key, store it in a shell profile, or put it on a command line. The runner's
credential check reports presence/absence only.

GLM is optional. It uses the originating host's connectors; it has no separate
connector identity.

## Routes

Run side-lane list. Selection is always explicit:

    side-lane run --mode execute --host codex \
      --provider openrouter --model anthropic/claude-sonnet-5 \
      --lane-name parser-task --repo "$PWD" \
      --capability workspace-write --prompt-file /path/to/approved-brief.md

    side-lane run --mode execute --host claude \
      --provider openrouter --model openai/gpt-5.6-terra \
      --lane-name api-task --repo "$PWD" \
      --capability workspace-write --prompt-file /path/to/approved-brief.md

There is no default, model-equivalence claim, quota detector, fallback, or
silent substitution. Edit config/models.json deliberately to maintain your
reviewed allowlist.

## Governance and capability checks

Targets must have root AGENTS.md and CLAUDE.md, with AGENTS.md unambiguously
identifying CLAUDE.md as required and authoritative.

Presence-only discovery does not retrieve a key or call a model:

    side-lane check-capabilities --host codex --mode execute --repo "$PWD" \
      --provider openrouter --model anthropic/claude-sonnet-5 --json

Planning/staffing tools may use that check optionally. If this package or the
exact route is absent, they should continue their ordinary in-host workflow.

## Updates and provenance

The canonical source is https://github.com/marcosathanasoulis/agent-side-lanes.
A ZIP is a release snapshot, not an update channel. Keep its release tag,
source URL, and checksum. Git installs can update by reviewing the upstream
release and fast-forwarding a clean checkout, then rerunning install/check.
Never update a dirty/diverged checkout or an installation whose canonical
remote is different. Run python scripts/update.py check; apply requires an
explicit signed semantic-version tag and performs only a verified fast-forward.

## Validation

    python3 -m unittest discover -s tests -v
    python3 -m compileall -q side_lane tests
    bash -n scripts/install.sh

Tests use mocks/stubs and temporary repositories. They must not read real
credentials or make paid provider, database, cloud, or messaging calls.
