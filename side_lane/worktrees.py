from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Callable, Sequence


class WorktreeError(Exception):
    pass


Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class WorktreeRun:
    repository: Path
    worktree: Path
    branch: str
    lane_name: str


def _git(repo: Path, args: Sequence[str], runner: Runner) -> str:
    result = runner(["git", "-C", str(repo), *args], stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, check=False)
    if result.returncode:
        raise WorktreeError((result.stderr or result.stdout).strip() or "git command failed")
    return result.stdout.strip()


def safe_lane_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not normalized or len(normalized) > 48:
        raise WorktreeError("lane name must contain 1-48 safe alphanumeric characters")
    return normalized


def create_worktree(repo: Path, lane_name: str, *, runner: Runner = subprocess.run,
                    now: datetime | None = None) -> WorktreeRun:
    repo = repo.resolve()
    top = Path(_git(repo, ["rev-parse", "--show-toplevel"], runner)).resolve()
    if top != repo:
        raise WorktreeError("execute mode requires the repository root")
    checkout_status = _git(repo, ["status", "--porcelain"], runner).splitlines()
    material_status = [line for line in checkout_status if line != "?? INPROCESS.md"]
    if material_status:
        raise WorktreeError("coordinator checkout is dirty; commit or stash before lane creation")
    if not _git(repo, ["branch", "--show-current"], runner):
        raise WorktreeError("detached HEAD is not supported")
    coordination = repo / "INPROCESS.md"
    if coordination.exists():
        tracked = runner(
            ["git", "-C", str(repo), "ls-files", "--error-unmatch", "INPROCESS.md"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        if tracked.returncode == 0:
            raise WorktreeError("INPROCESS.md must be untracked before automatic coordination")
    safe = safe_lane_name(lane_name)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d%H%M%S")
    branch = f"side-lane/{safe}-{stamp}"
    worktree = repo / ".side-lanes" / "worktrees" / f"{safe}-{stamp}"
    _git(repo, ["check-ref-format", "--branch", branch], runner)
    if worktree.exists():
        raise WorktreeError(f"worktree destination already exists: {worktree}")
    _git(repo, ["worktree", "add", "-b", branch, str(worktree), "HEAD"], runner)
    run = WorktreeRun(repo, worktree, branch, safe)
    _append_coordination_claim(run, runner)
    return run


def _append_coordination_claim(run: WorktreeRun, runner: Runner) -> None:
    path = run.repository / "INPROCESS.md"
    if not path.exists():
        return
    marker = f"## side-lane: {run.lane_name}\n- Branch: {run.branch}\n- Worktree: {run.worktree}\n- Status: active\n"
    existing = path.read_text(encoding="utf-8")
    if run.branch not in existing:
        with path.open("a", encoding="utf-8") as handle:
            if existing and not existing.endswith("\n\n"):
                handle.write("\n")
            handle.write(marker)


def git_status(run: WorktreeRun, runner: Runner = subprocess.run) -> str:
    return _git(run.worktree, ["status", "--short", "--branch"], runner)


def write_audit(run: WorktreeRun, *, host: str, mode: str, provider: str,
                model: str, prompt: str, exit_status: int, status: str) -> Path:
    result = subprocess.run(["git", "-C", str(run.repository), "rev-parse", "--git-dir"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, check=True)
    git_dir = Path(result.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = run.repository / git_dir
    destination = git_dir / "side-lane-runs"
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / f"{run.branch.replace('/', '-')}.json"
    path.write_text(json.dumps({
        "schema_version": 1, "host": host, "mode": mode,
        "provider": provider, "model": model,
        "repository": str(run.repository), "worktree": str(run.worktree),
        "branch": run.branch,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "exit_status": exit_status, "git_status": status,
    }, indent=2) + "\n", encoding="utf-8")
    return path


def remove_worktree(run: WorktreeRun, runner: Runner = subprocess.run) -> None:
    if _git(run.worktree, ["status", "--porcelain"], runner):
        raise WorktreeError("refusing to remove a dirty lane worktree")
    merged = _git(run.repository, ["branch", "--merged", "HEAD"], runner).splitlines()
    if not any(line.strip().lstrip("* ") == run.branch for line in merged):
        raise WorktreeError("refusing to remove an unmerged lane worktree")
    _git(run.repository, ["worktree", "remove", str(run.worktree)], runner)
