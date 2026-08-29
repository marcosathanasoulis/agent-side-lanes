"""Codex-host execution adapter for governed side lanes.

This module deliberately owns only the host invocation.  The caller owns
selection, governance validation, worktree creation, and the task policy
prompt.  Keeping this adapter small makes it possible to test its command and
environment construction without starting Codex, contacting OpenRouter, or
reading a credential store.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
from typing import Any, Callable, Mapping


OPENROUTER_PROVIDER = "openrouter"
OPENROUTER_ENV_KEY = "OPENROUTER_API_KEY"
OPENROUTER_RESPONSES_BASE_URL = "https://openrouter.ai/api/v1"
RESPONSES_WIRE_API = "responses"
MODEL_PROVIDER_NAME = "side_lane_openrouter"

# These are deliberately limited to AI-provider credentials.  Ambient user
# authority such as gcloud ADC, GitHub CLI login, and host-native MCP
# connectors must remain available to the originating Codex harness.
PROVIDER_CREDENTIAL_ENV_NAMES = frozenset(
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
        "GLM_API_KEY",
        "AZURE_OPENAI_API_KEY",
    }
)
PROVIDER_CREDENTIAL_ENV_PREFIXES = (
    "ANTHROPIC_",
    "OPENAI_",
    "OPENROUTER_",
    "ZAI_",
    "ZHIPUAI_",
    "GLM_",
    "AZURE_OPENAI_",
)


class CodexAdapterError(ValueError):
    """Raised before a provider process can be started."""


@dataclass(frozen=True)
class CodexInvocation:
    """Auditable, secret-redacted result from one child Codex invocation."""

    argv: tuple[str, ...]
    cwd: Path
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[..., Any]


def _toml_string(value: str) -> str:
    """Return a TOML string literal suitable for Codex's ``-c`` argument."""

    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _required_string(config: Mapping[str, Any], key: str, label: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value:
        raise CodexAdapterError(f"{label} must be a non-empty string")
    return value


def _validate_selection(
    provider: str,
    model: str,
    provider_config: Mapping[str, Any],
    model_config: Mapping[str, Any],
) -> tuple[str, str]:
    if provider != OPENROUTER_PROVIDER:
        raise CodexAdapterError(
            "Codex execute lanes support only the explicit openrouter provider"
        )
    if not isinstance(model, str) or not model:
        raise CodexAdapterError("model must be an explicit non-empty value")

    runtime_model = _required_string(model_config, "runtime_model", "runtime_model")
    if runtime_model != model:
        raise CodexAdapterError("runtime_model must exactly match the selected model")

    base_url = _required_string(provider_config, "base_url", "base_url")
    if base_url.rstrip("/") != OPENROUTER_RESPONSES_BASE_URL:
        raise CodexAdapterError(
            "Codex execute lanes require the OpenRouter Responses endpoint"
        )
    wire_api = _required_string(model_config, "wire_api", "wire_api")
    if wire_api != RESPONSES_WIRE_API:
        raise CodexAdapterError(
            "Codex execute lanes require the Responses wire protocol"
        )
    return runtime_model, base_url.rstrip("/")


def _validate_worktree(worktree: Path | str) -> Path:
    path = Path(worktree).expanduser().resolve()
    if not path.is_dir():
        raise CodexAdapterError(f"worktree is not a directory: {path}")
    if not (path / ".git").exists():
        raise CodexAdapterError(f"worktree is not a Git worktree: {path}")
    return path


def build_child_env(
    inherited: Mapping[str, str], credential: str
) -> dict[str, str]:
    """Remove provider credentials and expose the selected OpenRouter key only."""

    if not isinstance(credential, str) or not credential:
        raise CodexAdapterError("OpenRouter credential is absent")
    child = {
        name: value
        for name, value in inherited.items()
        if name not in PROVIDER_CREDENTIAL_ENV_NAMES
        and not name.startswith(PROVIDER_CREDENTIAL_ENV_PREFIXES)
    }
    child[OPENROUTER_ENV_KEY] = credential
    return child


def build_codex_command(
    executable: str,
    worktree: Path | str,
    provider: str,
    model: str,
    provider_config: Mapping[str, Any],
    model_config: Mapping[str, Any],
    prompt: str,
) -> tuple[str, ...]:
    """Build a noninteractive Codex command without disabling host controls."""

    if not isinstance(executable, str) or not executable:
        raise CodexAdapterError("Codex executable is required")
    if not isinstance(prompt, str) or not prompt.strip():
        raise CodexAdapterError("task prompt must be non-empty")
    resolved_worktree = _validate_worktree(worktree)
    runtime_model, base_url = _validate_selection(
        provider, model, provider_config, model_config
    )

    # Do not add --ignore-user-config, --ignore-rules, --approve-for-me, or
    # either dangerous bypass flag.  Existing Codex login, MCP/connectors,
    # project rules, and approval policy therefore remain the host's own.
    return (
        executable,
        "exec",
        "--ephemeral",
        "-C",
        str(resolved_worktree),
        "-s",
        "danger-full-access",
        "-m",
        runtime_model,
        "-c",
        f"model_provider={_toml_string(MODEL_PROVIDER_NAME)}",
        "-c",
        f"model_providers.{MODEL_PROVIDER_NAME}.name={_toml_string('OpenRouter')}",
        "-c",
        f"model_providers.{MODEL_PROVIDER_NAME}.base_url={_toml_string(base_url)}",
        "-c",
        f"model_providers.{MODEL_PROVIDER_NAME}.env_key={_toml_string(OPENROUTER_ENV_KEY)}",
        "-c",
        f"model_providers.{MODEL_PROVIDER_NAME}.wire_api={_toml_string(RESPONSES_WIRE_API)}",
        prompt,
    )


def _redact(value: str | None, credential: str) -> str:
    """Avoid echoing the injected provider key through captured child output."""

    text = value or ""
    return text.replace(credential, "[REDACTED_OPENROUTER_KEY]")


def run_codex(
    *,
    executable: str,
    repo: Path | str,
    worktree: Path | str,
    provider: str,
    model: str,
    provider_config: Mapping[str, Any],
    model_config: Mapping[str, Any],
    prompt: str,
    env: Mapping[str, str] | None = None,
    credential: str,
    runner: Runner = subprocess.run,
) -> CodexInvocation:
    """Run Codex in an existing dedicated worktree and return redacted metadata.

    ``repo`` makes the expected main checkout explicit for the caller's audit
    record.  It is validated as a Git directory, while execution occurs only
    in ``worktree``.
    """

    repo_path = _validate_worktree(repo)
    worktree_path = _validate_worktree(worktree)
    if repo_path == worktree_path:
        raise CodexAdapterError("execute lane requires a dedicated worktree")
    argv = build_codex_command(
        executable,
        worktree_path,
        provider,
        model,
        provider_config,
        model_config,
        prompt,
    )
    child_env = build_child_env(os.environ if env is None else env, credential)
    try:
        completed = runner(
            argv,
            cwd=worktree_path,
            env=child_env,
            stdin=subprocess.DEVNULL,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise CodexAdapterError(f"could not start Codex executable: {exc}") from exc

    return CodexInvocation(
        argv=argv,
        cwd=worktree_path,
        returncode=int(completed.returncode),
        stdout=_redact(getattr(completed, "stdout", ""), credential),
        stderr=_redact(getattr(completed, "stderr", ""), credential),
    )
