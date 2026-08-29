from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from side_lane import cli


class SideLaneTests(unittest.TestCase):
    def make_repo(self, agents: str | None = None) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        repo = Path(temporary.name)
        (repo / ".git").mkdir()
        (repo / "CLAUDE.md").write_text("# Rules\n", encoding="utf-8")
        if agents:
            (repo / "AGENTS.md").write_text(agents + "\n", encoding="utf-8")
        return repo

    def test_exact_host_mode_provider_model_matrix(self) -> None:
        config = cli.load_config()
        provider, model = cli.select_route(
            config, "codex", "execute", "openrouter", "anthropic/claude-sonnet-5"
        )
        self.assertEqual(provider["base_url"], "https://openrouter.ai/api/v1")
        self.assertEqual(model["runtime_model"], "anthropic/claude-sonnet-5")
        self.assertEqual(model["wire_api"], "responses")
        with self.assertRaisesRegex(cli.SideLaneError, "unsupported route"):
            cli.select_route(config, "codex", "execute", "claude", "claude-sonnet-5")
        with self.assertRaisesRegex(cli.SideLaneError, "not allowed"):
            cli.select_route(config, "claude", "execute", "openrouter", "anthropic/claude-sonnet-5")

    def test_config_requires_versioned_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "models.json"
            path.write_text(json.dumps({"claude": {}}), encoding="utf-8")
            with self.assertRaisesRegex(cli.SideLaneError, "schema_version 2"):
                cli.load_config(path)

    def test_parser_rejects_credential_and_bypass_arguments(self) -> None:
        parser = cli.make_parser()
        base = ["run", "--provider", "claude", "--model", "claude-sonnet-5",
                "--repo", ".", "--prompt", "Review this."]
        for extra in (["--api-key", "secret"], ["--dangerously-skip-permissions"]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parser.parse_args(base + extra)

    def test_review_and_execute_prompt_policies_differ(self) -> None:
        with self.assertRaises(cli.SideLaneError):
            cli.load_prompt("Edit the files.", None, "review")
        self.assertEqual(cli.load_prompt("Edit the files.", None, "execute"), "Edit the files.")
        for value in ("Force-push this branch.", "Deploy it.", "DROP TABLE users;"):
            with self.subTest(value=value), self.assertRaises(cli.SideLaneError):
                cli.load_prompt(value, None, "execute")

    def test_governance_requires_authoritative_link(self) -> None:
        repo = self.make_repo(
            "You must read [CLAUDE.md](./CLAUDE.md); it is the authoritative source of truth."
        )
        self.assertEqual(cli.validate_governance(str(repo)), repo.resolve())
        absent = self.make_repo()
        with self.assertRaisesRegex(cli.SideLaneError, "requires root"):
            cli.validate_governance(str(absent))

    def test_capability_check_is_presence_only(self) -> None:
        config = cli.load_config()
        with mock.patch("side_lane.cli.credential_present", return_value=False), \
             mock.patch("side_lane.cli.shutil.which", return_value="/bin/tool"), \
             mock.patch("side_lane.cli._discover_mcp_names", return_value={"gitnexus", "github"}):
            report = cli._capability_report(
                config, "codex", "execute", "openrouter", "anthropic/claude-sonnet-5"
            )
        self.assertEqual(report["credential"], "absent")
        self.assertEqual(report["route"], "configured")
        self.assertNotIn("credential_value", report)
        self.assertTrue(report["capabilities"]["gitnexus"])
        self.assertTrue(report["capabilities"]["workflow-write"])

    def test_discovers_connector_names_without_connector_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.toml").write_text(
                '[mcp_servers.gitnexus]\ncommand = "secret-command"\n',
                encoding="utf-8",
            )
            with mock.patch.dict("os.environ", {"CODEX_HOME": str(root)}):
                self.assertEqual(cli._discover_mcp_names("codex"), {"gitnexus"})
            repo = root / "repo"
            repo.mkdir()
            (repo / ".mcp.json").write_text(
                '{"mcpServers":{"codegraph":{"command":"hidden"}}}', encoding="utf-8"
            )
            self.assertIn("codegraph", cli._discover_mcp_names("claude", repo))

    def test_execute_requires_lane_name_and_known_capabilities_before_secret(self) -> None:
        config = cli.load_config()
        repo = self.make_repo(
            "You must read [CLAUDE.md](./CLAUDE.md); it is the authoritative source of truth."
        )
        args = mock.Mock(lane_name=None, capability=[], provider="openrouter",
                         model="anthropic/claude-sonnet-5", host="codex")
        with self.assertRaisesRegex(cli.SideLaneError, "lane-name"):
            cli._execute(args, config, repo, "Implement it.")
        args.lane_name = "worker"
        args.capability = ["production-deploy"]
        with self.assertRaisesRegex(cli.SideLaneError, "unknown capabilities"):
            cli._execute(args, config, repo, "Implement it.")

    def test_review_invocation_remains_fixed_no_write(self) -> None:
        repo = self.make_repo(
            "You must read [CLAUDE.md](./CLAUDE.md); it is the authoritative source of truth."
        )
        command = cli.build_command("claude-sonnet-5", repo, "Review this.")
        self.assertIn("plan", command)
        self.assertIn("Read,Glob,Grep", command)
        self.assertIn("--bare", command)
        self.assertNotIn("acceptEdits", command)


if __name__ == "__main__":
    unittest.main()
