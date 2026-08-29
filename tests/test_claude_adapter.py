from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from side_lane.adapters import claude


class ClaudeAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = "openrouter"
        self.model = "openai/gpt-5.6-sol"
        self.model_config = {"runtime_model": self.model}
        self.provider_config = {
            "protocol": "anthropic-compatible",
            "base_url": "https://openrouter.ai/api/v1",
            "models": {self.model: self.model_config},
        }

    def make_worktree(self) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name)
        # A linked worktree has a .git file; a normal checkout has a directory.
        (path / ".git").write_text("gitdir: /tmp/example\n", encoding="utf-8")
        return path

    def test_builds_host_native_execute_command_with_settings_and_connectors(
        self,
    ) -> None:
        worktree = self.make_worktree()
        command = claude.build_command(
            executable="claude",
            worktree=worktree,
            provider=self.provider,
            model=self.model,
            provider_config=self.provider_config,
            model_config=self.model_config,
            prompt="Implement the approved parser change and run focused tests.",
        )
        self.assertEqual(command[:2], ["claude", "-p"])
        self.assertIn("--permission-mode", command)
        self.assertEqual(command[command.index("--permission-mode") + 1], "acceptEdits")
        self.assertIn("--setting-sources", command)
        self.assertEqual(
            command[command.index("--setting-sources") + 1], "user,project,local"
        )
        self.assertIn("--append-system-prompt", command)
        self.assertNotIn("--bare", command)
        self.assertNotIn("--strict-mcp-config", command)
        self.assertNotIn("--mcp-config", command)
        self.assertNotIn("--dangerously-skip-permissions", command)
        self.assertNotIn("--allow-dangerously-skip-permissions", command)
        self.assertNotIn("--sandbox", command)

    def test_transport_environment_scrubs_inherited_provider_keys_only(self) -> None:
        inherited = {
            "PATH": "/bin",
            "ANTHROPIC_API_KEY": "parent-anthropic",
            "ANTHROPIC_AUTH_TOKEN": "parent-token",
            "ANTHROPIC_BASE_URL": "https://wrong.example",
            "OPENROUTER_API_KEY": "parent-router",
            "OPENROUTER_SITE_URL": "https://wrong.example",
            "ZAI_API_KEY": "parent-glm",
            "CLAUDECODE": "nested",
            "CLAUDE_CONFIG_DIR": "/Users/person/.claude",
            "GOOGLE_APPLICATION_CREDENTIALS": "/tmp/adc.json",
            "UNRELATED": "kept",
        }
        env = claude.build_transport_environment(
            inherited,
            provider=self.provider,
            model=self.model,
            provider_config=self.provider_config,
            model_config=self.model_config,
            secret="selected-openrouter-secret",
        )
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], "selected-openrouter-secret")
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "https://openrouter.ai/api/v1")
        self.assertEqual(env["ANTHROPIC_MODEL"], self.model)
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], "/Users/person/.claude")
        self.assertEqual(env["GOOGLE_APPLICATION_CREDENTIALS"], "/tmp/adc.json")
        self.assertEqual(env["UNRELATED"], "kept")
        for key in (
            "ANTHROPIC_API_KEY",
            "OPENROUTER_API_KEY",
            "OPENROUTER_SITE_URL",
            "ZAI_API_KEY",
            "CLAUDECODE",
        ):
            self.assertNotIn(key, env)

    def test_rejects_unknown_provider_protocol_endpoint_and_model_substitution(
        self,
    ) -> None:
        worktree = self.make_worktree()
        base = dict(
            executable="claude",
            worktree=worktree,
            provider=self.provider,
            model=self.model,
            provider_config=self.provider_config,
            model_config=self.model_config,
            prompt="Implement the approved task.",
        )
        with self.assertRaisesRegex(
            claude.ClaudeAdapterError, "unsupported Claude execute provider"
        ):
            claude.build_command(**(base | {"provider": "anthropic"}))
        with self.assertRaisesRegex(
            claude.ClaudeAdapterError, "unsupported Claude execute protocol"
        ):
            claude.build_command(
                **(
                    base
                    | {
                        "provider_config": self.provider_config
                        | {"protocol": "responses"}
                    }
                )
            )
        with self.assertRaisesRegex(claude.ClaudeAdapterError, "clean HTTPS"):
            claude.build_command(
                **(
                    base
                    | {
                        "provider_config": self.provider_config
                        | {"base_url": "http://bad.example"}
                    }
                )
            )
        with self.assertRaisesRegex(claude.ClaudeAdapterError, "silently substitute"):
            claude.build_command(
                **(base | {"model_config": {"runtime_model": "another-model"}})
            )

    def test_rejects_missing_or_unprepared_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                claude.ClaudeAdapterError, "already-created Git worktree"
            ):
                claude.build_command(
                    executable="claude",
                    worktree=directory,
                    provider=self.provider,
                    model=self.model,
                    provider_config=self.provider_config,
                    model_config=self.model_config,
                    prompt="Implement the approved task.",
                )

    def test_mocked_launch_uses_worktree_and_selected_environment(self) -> None:
        worktree = self.make_worktree()
        env = claude.build_transport_environment(
            {"PATH": "/bin"},
            provider=self.provider,
            model=self.model,
            provider_config=self.provider_config,
            model_config=self.model_config,
            secret="selected-secret",
        )
        runner = mock.Mock(
            return_value=subprocess.CompletedProcess(
                [], 17, stdout="selected-secret\n", stderr="diagnostic selected-secret"
            )
        )
        result = claude.launch(
            executable="claude-custom",
            worktree=worktree,
            provider=self.provider,
            model=self.model,
            provider_config=self.provider_config,
            model_config=self.model_config,
            prompt="Implement the approved task.",
            env=env,
            runner=runner,
        )
        self.assertEqual(result.returncode, 17)
        self.assertEqual(result.worktree, worktree.resolve())
        self.assertEqual(result.argv[0], "claude-custom")
        self.assertEqual(runner.call_args.kwargs["cwd"], worktree.resolve())
        self.assertEqual(
            runner.call_args.kwargs["env"]["ANTHROPIC_AUTH_TOKEN"], "selected-secret"
        )
        self.assertEqual(runner.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertTrue(runner.call_args.kwargs["text"])
        self.assertTrue(runner.call_args.kwargs["capture_output"])
        self.assertFalse(runner.call_args.kwargs["check"])
        self.assertEqual(result.stdout, "[REDACTED_PROVIDER_KEY]\n")
        self.assertEqual(result.stderr, "diagnostic [REDACTED_PROVIDER_KEY]")

    def test_launch_fails_closed_when_cli_is_missing_or_runner_is_malformed(
        self,
    ) -> None:
        worktree = self.make_worktree()
        kwargs = dict(
            executable="claude",
            worktree=worktree,
            provider=self.provider,
            model=self.model,
            provider_config=self.provider_config,
            model_config=self.model_config,
            prompt="Implement the approved task.",
            env={"PATH": "/bin"},
        )
        with self.assertRaisesRegex(claude.ClaudeAdapterError, "was not found"):
            claude.launch(**kwargs, runner=mock.Mock(side_effect=FileNotFoundError))
        with self.assertRaisesRegex(claude.ClaudeAdapterError, "integer return code"):
            claude.launch(**kwargs, runner=mock.Mock(return_value=object()))

    def test_run_claude_scrubs_before_invocation(self) -> None:
        worktree = self.make_worktree()
        runner = mock.Mock(return_value=subprocess.CompletedProcess([], 0))
        result = claude.run_claude(
            executable="claude",
            worktree=worktree,
            provider=self.provider,
            model=self.model,
            provider_config=self.provider_config,
            model_config=self.model_config,
            prompt="Implement the approved task.",
            env={"OPENROUTER_API_KEY": "inherited", "PATH": "/bin"},
            credential="selected-secret",
            runner=runner,
        )
        self.assertEqual(result.returncode, 0)
        child = runner.call_args.kwargs["env"]
        self.assertNotIn("OPENROUTER_API_KEY", child)
        self.assertEqual(child["ANTHROPIC_AUTH_TOKEN"], "selected-secret")


if __name__ == "__main__":
    unittest.main()
