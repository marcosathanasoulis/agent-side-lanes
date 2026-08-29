from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Mapping, Sequence

from side_lane.credentials import CredentialError, credential_present, read_credential, selected_provider_environment
from side_lane.worktrees import WorktreeError, create_worktree, git_status, write_audit

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PACKAGE_ROOT / "config" / "models.json"
MAX_PROMPT_CHARS = 100_000
REVIEW_PROMPT = """You are an independent read-only analysis lane. Use only Read, Glob, and Grep inside the governed repository. Do not write, edit, patch, commit, push, deploy, mutate external systems, access secrets, or bypass permissions. Return findings to the calling developer."""
EXECUTE_PROMPT = """You are an approved host-native execution lane working in a dedicated Git worktree. Follow AGENTS.md and authoritative CLAUDE.md exactly. You may implement, test, commit, and push only your dedicated lane branch. Use the originating host's configured MCP/connectors and same-user tools only as required by the approved task. Never disclose raw secrets. Database work is read-only SELECT/metadata/EXPLAIN only: no DML, DDL, migrations, or mutation. Do not deploy, force-push, merge, write protected/shared branches, mutate IAM/credentials/cloud infrastructure/data, or broaden external recipients or scope. Workflow/application writes are allowed only when the task explicitly requires them; report each external change. A worktree is edit isolation, not an OS sandbox."""

REVIEW_UNSAFE = tuple(re.compile(p, re.I) for p in (
    r"\b(?:edit|modify|write|delete|create)\s+(?:the\s+|a\s+)?(?:files?|code|repo)",
    r"\b(?:apply|produce)\s+(?:a\s+)?(?:patch|diff)", r"\b(?:commit|push|merge|deploy)\b",
    r"\b(?:bypass|disable|skip)\s+(?:permissions?|sandbox|guardrails?)\b",
))
EXECUTE_UNSAFE = tuple(re.compile(p, re.I) for p in (
    r"--(?:dangerously-skip-permissions|allow-dangerously-skip-permissions|ignore-user-config)",
    r"\b(?:force[- ]?push|deploy|merge\s+(?:the\s+)?(?:pr|branch))\b",
    r"\b(?:insert|update|delete|drop|alter|truncate|create)\s+(?:into\s+|table\s+|database\s+)",
    r"\b(?:change|grant|revoke|rotate|delete)\s+(?:iam|credentials?|secrets?|cloud resources?)\b",
))


class SideLaneError(Exception):
    pass


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SideLaneError(f"cannot load model allowlist: {exc}") from exc
    if config.get("schema_version") != 2 or not isinstance(config.get("providers"), dict):
        raise SideLaneError("model allowlist requires schema_version 2")
    if not isinstance(config.get("capabilities"), list):
        raise SideLaneError("capability allowlist is invalid")
    for provider, item in config["providers"].items():
        if not isinstance(item, dict) or not item.get("credential_service"):
            raise SideLaneError(f"provider {provider!r} has invalid configuration")
        for mode, hosts in item.get("routes", {}).items():
            if mode not in {"review", "execute"} or not isinstance(hosts, dict):
                raise SideLaneError(f"provider {provider!r} has an invalid route")
            for host, route in hosts.items():
                models = route.get("models") if isinstance(route, dict) else None
                if host not in {"codex", "claude"} or not route.get("protocol") or not isinstance(models, list) or not models:
                    raise SideLaneError(f"provider {provider!r} has an invalid host route")
                if len(set(models)) != len(models) or not all(isinstance(m, str) and m for m in models):
                    raise SideLaneError(f"provider {provider!r} has invalid models")
    return config


def select_route(config: Mapping[str, Any], host: str, mode: str, provider: str, model: str) -> tuple[Mapping[str, Any], dict[str, str]]:
    providers = config["providers"]
    if provider not in providers:
        raise SideLaneError(f"unknown provider: {provider}")
    provider_config = providers[provider]
    route = provider_config.get("routes", {}).get(mode, {}).get(host)
    if route is None:
        raise SideLaneError(f"unsupported route: {host}/{mode}/{provider}")
    if model not in route["models"]:
        raise SideLaneError(f"model {model!r} is not allowed for {host}/{mode}/{provider}")
    protocol = str(route["protocol"])
    return provider_config, {"runtime_model": model, "protocol": protocol, "wire_api": protocol}


def validate_selection(config: Mapping[str, Any], provider: str, model: str, *, host: str = "claude", mode: str = "review") -> Mapping[str, Any]:
    return select_route(config, host, mode, provider, model)[1]


def validate_governance(repo_argument: str) -> Path:
    repo = Path(repo_argument).expanduser().resolve()
    if not repo.is_dir() or not (repo / ".git").exists():
        raise SideLaneError(f"target is not a Git repository root: {repo}")
    agents, claude = repo / "AGENTS.md", repo / "CLAUDE.md"
    if not agents.is_file() or not claude.is_file():
        raise SideLaneError("governance requires root AGENTS.md and CLAUDE.md files")
    text = agents.read_text(encoding="utf-8")
    links = re.findall(r"\[[^\]]*CLAUDE\.md[^\]]*\]\(([^)]+)\)", text, re.I)
    resolved = {(repo / value.strip().strip("<>").split("#", 1)[0]).resolve() for value in links}
    if resolved != {claude.resolve()}:
        raise SideLaneError("AGENTS.md must link unambiguously to root CLAUDE.md")
    if not any("CLAUDE.md" in line and re.search(r"\b(?:must|required)\b", line, re.I)
               and re.search(r"\b(?:authoritative|source of truth)\b", line, re.I) for line in text.splitlines()):
        raise SideLaneError("AGENTS.md must identify CLAUDE.md as required and authoritative")
    if claude.is_symlink() or claude.resolve().parent != repo:
        raise SideLaneError("root CLAUDE.md must be a regular repository file")
    return repo


def load_prompt(prompt: str | None, prompt_file: str | None, mode: str = "review") -> str:
    if prompt_file:
        path = Path(prompt_file).expanduser()
        if not path.is_file():
            raise SideLaneError(f"prompt file is not readable: {path}")
        value = path.read_text(encoding="utf-8")
    else:
        value = prompt or ""
    if not value.strip() or len(value) > MAX_PROMPT_CHARS:
        raise SideLaneError("prompt is empty or too long")
    patterns = REVIEW_UNSAFE if mode == "review" else EXECUTE_UNSAFE
    if any(pattern.search(value) for pattern in patterns):
        raise SideLaneError(f"prompt requests an action prohibited in {mode} mode")
    return value


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="side-lane", allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    credentials = sub.add_parser("credentials", allow_abbrev=False)
    credentials.add_argument("--json", action="store_true")
    check = sub.add_parser("check-capabilities", allow_abbrev=False)
    check.add_argument("--host", choices=("codex", "claude"), required=True)
    check.add_argument("--mode", choices=("review", "execute"), default="execute")
    check.add_argument("--provider")
    check.add_argument("--model")
    check.add_argument("--json", action="store_true")
    run = sub.add_parser("run", allow_abbrev=False)
    run.add_argument("--host", choices=("codex", "claude"), default="claude")
    run.add_argument("--mode", choices=("review", "execute"), default="review")
    run.add_argument("--provider", required=True)
    run.add_argument("--model", required=True)
    run.add_argument("--repo", required=True)
    run.add_argument("--lane-name")
    run.add_argument("--capability", action="append", default=[])
    prompt = run.add_mutually_exclusive_group(required=True)
    prompt.add_argument("--prompt")
    prompt.add_argument("--prompt-file")
    return parser


def build_command(model: str, repo: Path, prompt: str) -> list[str]:
    return ["claude", "-p", prompt, "--bare", "--add-dir", str(repo), "--model", model,
            "--permission-mode", "plan", "--tools", "Read,Glob,Grep", "--strict-mcp-config",
            "--mcp-config", '{"mcpServers":{}}', "--disable-slash-commands",
            "--no-session-persistence", "--output-format", "text",
            "--append-system-prompt", REVIEW_PROMPT]


def invoke_provider(command: Sequence[str], env: Mapping[str, str], cwd: Path) -> int:
    try:
        return subprocess.run(list(command), env=dict(env), cwd=cwd, stdin=subprocess.DEVNULL, check=False).returncode
    except FileNotFoundError as exc:
        raise SideLaneError("required host CLI is not installed") from exc


def build_child_env(inherited: Mapping[str, str], provider: str, provider_config: Mapping[str, Any], model_config: Mapping[str, Any], secret: str) -> dict[str, str]:
    return selected_provider_environment(inherited, provider, provider_config,
                                         str(model_config["runtime_model"]), secret, "claude")


def keychain_present(service: str) -> bool:
    return credential_present(service)


def read_keychain_secret(service: str) -> str:
    return read_credential(service)


def _capability_report(config: Mapping[str, Any], host: str, mode: str, provider: str | None, model: str | None) -> dict[str, Any]:
    runtime = shutil.which(host)
    mcp_names = _discover_mcp_names(host)
    lowered = {name.lower() for name in mcp_names}
    states = {
        "workspace-write": mode == "execute",
        "shell": bool(runtime),
        "git-push": bool(shutil.which("git")),
        "gitnexus": any("gitnexus" in name for name in lowered),
        "codegraph": any("codegraph" in name for name in lowered),
        "gcloud-read": bool(shutil.which("gcloud")),
        "secret-use": bool(shutil.which("gcloud")) or mode == "execute",
        "database-read": bool(shutil.which("psql")),
        "workflow-write": any(
            marker in name
            for name in lowered
            for marker in ("asana", "slack", "teams", "github")
        ),
    }
    report: dict[str, Any] = {
        "host": host, "mode": mode, "runtime": runtime,
        "route": "not-requested", "mcp_connectors": sorted(mcp_names),
    }
    if bool(provider) != bool(model):
        raise SideLaneError("provider and model must be supplied together")
    if provider and model:
        provider_config, _ = select_route(config, host, mode, provider, model)
        report["route"] = "configured"
        report["credential"] = "present" if credential_present(provider_config["credential_service"]) else "absent"
    report["capabilities"] = {name: bool(states.get(name, False)) for name in config["capabilities"]}
    return report


def _discover_mcp_names(host: str) -> set[str]:
    names: set[str] = set()
    if host == "codex":
        path = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "config.toml"
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            names.update(re.findall(r"^\[mcp_servers\.([A-Za-z0-9_.-]+)\]", text, re.M))
    else:
        for path in (Path.home() / ".claude.json", Path.home() / ".claude" / "settings.json"):
            if not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            _collect_mcp_names(payload, names)
    return names


def _collect_mcp_names(value: object, names: set[str]) -> None:
    if isinstance(value, dict):
        servers = value.get("mcpServers")
        if isinstance(servers, dict):
            names.update(str(name) for name in servers)
        for child in value.values():
            _collect_mcp_names(child, names)
    elif isinstance(value, list):
        for child in value:
            _collect_mcp_names(child, names)


def _execute(args: argparse.Namespace, config: Mapping[str, Any], repo: Path, prompt: str) -> int:
    if not args.lane_name:
        raise SideLaneError("execute mode requires --lane-name")
    unknown = sorted(set(args.capability) - set(config["capabilities"]))
    if unknown:
        raise SideLaneError(f"unknown capabilities: {', '.join(unknown)}")
    readiness = _capability_report(
        config, args.host, "execute", args.provider, args.model
    )["capabilities"]
    missing = [name for name in args.capability if not readiness.get(name)]
    if missing:
        raise SideLaneError(f"required capabilities unavailable: {', '.join(missing)}")
    provider_config, model_config = select_route(config, args.host, "execute", args.provider, args.model)
    secret = read_credential(provider_config["credential_service"])
    lane = create_worktree(repo, args.lane_name)
    task = EXECUTE_PROMPT + "\n\nApproved capabilities: " + (", ".join(args.capability) or "workspace-write, shell") + "\n\nApproved task:\n" + prompt
    if args.host == "codex":
        from side_lane.adapters.codex import run_codex
        result = run_codex(executable="codex", repo=repo, worktree=lane.worktree,
                           provider=args.provider, model=args.model,
                           provider_config=provider_config, model_config=model_config,
                           prompt=task, credential=secret)
    else:
        from side_lane.adapters.claude import build_transport_environment, launch
        route_provider = dict(provider_config)
        route_provider["protocol"] = model_config["protocol"]
        env = build_transport_environment(
            os.environ, provider=args.provider, model=args.model,
            provider_config=route_provider, model_config=model_config, secret=secret
        )
        result = launch(executable="claude", worktree=lane.worktree,
                        provider=args.provider, model=args.model,
                        provider_config=route_provider, model_config=model_config,
                        prompt=task, env=env)
    status = git_status(lane)
    audit = write_audit(lane, host=args.host, mode="execute", provider=args.provider,
                        model=args.model, prompt=prompt, exit_status=result.returncode, status=status)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    print(json.dumps({"branch": lane.branch, "worktree": str(lane.worktree),
                      "exit_status": result.returncode, "git_status": status,
                      "audit": str(audit)}, indent=2))
    return result.returncode


def run(argv: Sequence[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    config = load_config()
    if args.command == "list":
        for provider, item in config["providers"].items():
            for mode, hosts in item["routes"].items():
                for host, route in hosts.items():
                    for model in route["models"]:
                        print(f"{host}\t{mode}\t{provider}\t{model}")
        return 0
    if args.command == "credentials":
        states = {provider: ("present" if credential_present(item["credential_service"]) else "absent") for provider, item in config["providers"].items()}
        print(json.dumps(states, sort_keys=True) if args.json else "\n".join(f"{k}\t{v}" for k, v in states.items()))
        return 0
    if args.command == "check-capabilities":
        report = _capability_report(config, args.host, args.mode, args.provider, args.model)
        print(json.dumps(report, sort_keys=True) if args.json else "\n".join(f"{k}\t{v}" for k, v in report.items()))
        return 0
    provider_config, _ = select_route(config, args.host, args.mode, args.provider, args.model)
    repo = validate_governance(args.repo)
    prompt = load_prompt(args.prompt, args.prompt_file, args.mode)
    if args.mode == "execute":
        return _execute(args, config, repo, prompt)
    secret = read_credential(provider_config["credential_service"])
    env = selected_provider_environment(os.environ, args.provider, provider_config, args.model, secret, "claude")
    return invoke_provider(build_command(args.model, repo, prompt), env, repo)


def main() -> None:
    try:
        raise SystemExit(run())
    except (SideLaneError, CredentialError, WorktreeError) as exc:
        print(f"side-lane: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
