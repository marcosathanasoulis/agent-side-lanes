"""Claude Code execution adapter for a dedicated side-lane worktree.

The launcher owns allowlist selection, credential retrieval, governance checks,
and worktree creation. This adapter deliberately owns only the host-native
Claude Code invocation. It does not replace the user's Claude configuration,
MCP servers, or connector sessions: those remain properties of the Claude Code
host that launches the lane.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Any, Mapping, Protocol, Sequence
from urllib.parse import urlparse


MAX_PROMPT_CHARS = 100_000
ANTHROPIC_COMPATIBLE_PROTOCOL = "anthropic-compatible"
SUPPORTED_PROVIDERS = frozenset({"openrouter", "glm"})

# Only credentials and provider-routing overrides are scrubbed. Deliberately
# retain ordinary user/session environment (for example gcloud ADC, GitNexus,
# and CLAUDE_CONFIG_DIR) because execute lanes have the same user authority as
# their primary host and need its configured connectors.
SCRUB_EXACT = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_SMALL_FAST_MODEL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENROUTER_API_KEY",
        "ZAI_API_KEY",
        "ZHIPUAI_API_KEY",
        "CLAUDECODE",
        "CLAUDE_CODE_ENTRYPOINT",
    }
)
SCRUB_PREFIXES = ("OPENROUTER_", "ZAI_", "ZHIPUAI_")

EXECUTE_SYSTEM_PROMPT = """You are a delegated implementation lane running in a dedicated Git worktree and branch. Work only on the assigned task and follow this repository's AGENTS.md and authoritative CLAUDE.md. You may inspect, edit, test, commit, and push only this dedicated lane branch. Never deploy, release, force-push, merge, write protected/shared branches, alter IAM or credentials, run destructive cloud/infrastructure/data operations, or disclose raw secrets. You may use existing host-native connectors and local tools under the same developer identity. Database work is read-only only: SELECT, metadata inspection, and EXPLAIN are allowed; no DML, DDL, migrations, or destructive SQL. If the explicit task authorizes a workflow update (such as a task tracker, message, or pull request), make only that named update through the host connector and report exactly what changed. Stop and report if a requested action would exceed these boundaries."""


class ClaudeAdapterError(RuntimeError):
    """An execute lane cannot be safely launched through Claude Code."""


class Runner(Protocol):
    def __call__(self, args: Sequence[str], **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class LaunchResult:
    """Non-secret metadata returned after a Claude Code child exits."""

    argv: tuple[str, ...]
    returncode: int
    worktree: Path
    provider: str
    model: str
    stdout: str
    stderr: str


def _nonempty_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ClaudeAdapterError(f"{field} must be a non-empty string")
    return value.strip()


def _validate_endpoint(value: object, provider: str) -> str:
    endpoint = _nonempty_string(value, f"{provider} base_url")
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
        raise ClaudeAdapterError(f"{provider} base_url must be a clean HTTPS endpoint")
    return endpoint.rstrip("/")


def _validate_route(
    provider: str,
    model: str,
    provider_config: Mapping[str, Any],
    model_config: Mapping[str, Any],
) -> tuple[str, str]:
    if provider not in SUPPORTED_PROVIDERS:
        raise ClaudeAdapterError(f"unsupported Claude execute provider: {provider}")
    if not isinstance(provider_config, Mapping) or not isinstance(
        model_config, Mapping
    ):
        raise ClaudeAdapterError("provider and model configuration must be mappings")

    protocol = provider_config.get("protocol", ANTHROPIC_COMPATIBLE_PROTOCOL)
    if protocol != ANTHROPIC_COMPATIBLE_PROTOCOL:
        raise ClaudeAdapterError(
            f"unsupported Claude execute protocol for {provider}: {protocol!r}"
        )
    endpoint = _validate_endpoint(provider_config.get("base_url"), provider)
    requested_model = _nonempty_string(model, "model")
    runtime_model = _nonempty_string(model_config.get("runtime_model"), "runtime_model")
    if runtime_model != requested_model:
        raise ClaudeAdapterError(
            "model configuration would silently substitute a model"
        )

    models = provider_config.get("models")
    if models is not None:
        if (
            not isinstance(models, Mapping)
            or models.get(requested_model) != model_config
        ):
            raise ClaudeAdapterError(
                f"model {requested_model!r} is not allowlisted for {provider!r}"
            )
    return endpoint, runtime_model


def validate_worktree(worktree: str | Path) -> Path:
    """Require an existing Git worktree prepared by the shared launcher."""

    path = Path(worktree).expanduser().resolve()
    if not path.is_dir() or not (path / ".git").exists():
        raise ClaudeAdapterError(
            "execute mode requires an already-created Git worktree"
        )
    return path


def scrub_environment(inherited: Mapping[str, str]) -> dict[str, str]:
    """Remove inherited provider credentials and routing overrides."""

    return {
        name: value
        for name, value in inherited.items()
        if name not in SCRUB_EXACT and not name.startswith(SCRUB_PREFIXES)
    }


def build_transport_environment(
    inherited: Mapping[str, str],
    *,
    provider: str,
    model: str,
    provider_config: Mapping[str, Any],
    model_config: Mapping[str, Any],
    secret: str,
) -> dict[str, str]:
    """Inject exactly one selected Anthropic-compatible transport credential.

    ``secret`` is intentionally accepted only as a value to pass to the child;
    this module never formats, logs, or returns it separately.
    """

    endpoint, runtime_model = _validate_route(
        provider, model, provider_config, model_config
    )
    selected_secret = _nonempty_string(secret, "selected transport credential")
    child = scrub_environment(inherited)
    child["ANTHROPIC_AUTH_TOKEN"] = selected_secret
    child["ANTHROPIC_BASE_URL"] = endpoint
    child["ANTHROPIC_MODEL"] = runtime_model
    child["ANTHROPIC_SMALL_FAST_MODEL"] = runtime_model
    return child


def build_command(
    *,
    executable: str,
    worktree: str | Path,
    provider: str,
    model: str,
    provider_config: Mapping[str, Any],
    model_config: Mapping[str, Any],
    prompt: str,
) -> list[str]:
    """Build the Claude Code command without unsafe isolation/bypass flags."""

    executable = _nonempty_string(executable, "Claude executable")
    validate_worktree(worktree)
    _endpoint, runtime_model = _validate_route(
        provider, model, provider_config, model_config
    )
    task = _nonempty_string(prompt, "prompt")
    if len(task) > MAX_PROMPT_CHARS:
        raise ClaudeAdapterError(f"prompt exceeds {MAX_PROMPT_CHARS} characters")

    # Do not add --bare, --strict-mcp-config, --mcp-config, any sandbox option,
    # or a bypass-permissions flag. This preserves the originating Claude host's
    # ordinary settings/MCP connector state while acceptEdits permits normal
    # implementation inside the isolated Git worktree.
    return [
        executable,
        "-p",
        task,
        "--model",
        runtime_model,
        "--permission-mode",
        "acceptEdits",
        "--setting-sources",
        "user,project,local",
        "--output-format",
        "text",
        "--append-system-prompt",
        EXECUTE_SYSTEM_PROMPT,
    ]


def _redact(value: object, credential: str) -> str:
    """Keep an accidentally echoed selected transport key out of return data."""

    return str(value or "").replace(credential, "[REDACTED_PROVIDER_KEY]")


def launch(
    *,
    executable: str,
    worktree: str | Path,
    provider: str,
    model: str,
    provider_config: Mapping[str, Any],
    model_config: Mapping[str, Any],
    prompt: str,
    env: Mapping[str, str],
    runner: Runner | None = None,
) -> LaunchResult:
    """Run Claude Code in a prepared worktree and return non-secret metadata."""

    path = validate_worktree(worktree)
    command = build_command(
        executable=executable,
        worktree=path,
        provider=provider,
        model=model,
        provider_config=provider_config,
        model_config=model_config,
        prompt=prompt,
    )
    run_child = runner or subprocess.run
    try:
        completed = run_child(
            command,
            cwd=path,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ClaudeAdapterError("Claude Code CLI executable was not found") from exc
    returncode = getattr(completed, "returncode", None)
    if not isinstance(returncode, int):
        raise ClaudeAdapterError("Claude Code runner returned no integer return code")
    return LaunchResult(
        argv=tuple(command),
        returncode=returncode,
        worktree=path,
        provider=provider,
        model=model,
        stdout=_redact(
            getattr(completed, "stdout", ""), env.get("ANTHROPIC_AUTH_TOKEN", "")
        ),
        stderr=_redact(
            getattr(completed, "stderr", ""), env.get("ANTHROPIC_AUTH_TOKEN", "")
        ),
    )


def run_claude(
    *,
    executable: str,
    worktree: str | Path,
    provider: str,
    model: str,
    provider_config: Mapping[str, Any],
    model_config: Mapping[str, Any],
    prompt: str,
    env: Mapping[str, str],
    credential: str,
    runner: Runner | None = None,
) -> LaunchResult:
    """Compatibility entry point used by the shared launcher.

    Keeping environment construction here means no inherited provider key can
    reach the child even when the shared launcher is called by another skill.
    """

    child_env = build_transport_environment(
        env,
        provider=provider,
        model=model,
        provider_config=provider_config,
        model_config=model_config,
        secret=credential,
    )
    return launch(
        executable=executable,
        worktree=worktree,
        provider=provider,
        model=model,
        provider_config=provider_config,
        model_config=model_config,
        prompt=prompt,
        env=child_env,
        runner=runner,
    )
