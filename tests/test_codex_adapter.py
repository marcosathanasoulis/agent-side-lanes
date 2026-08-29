from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from side_lane.adapters import codex


class CodexAdapterTests(unittest.TestCase):
    provider_config = {
        "base_url": "https://openrouter.ai/api/v1",
    }
    model_config = {
        "runtime_model": "openai/gpt-5.6-terra",
        "wire_api": "responses",
    }

    def make_worktree(self, base: Path, name: str) -> Path:
        path = base / name
        path.mkdir()
        (path / ".git").mkdir()
        return path

    def test_builds_openrouter_responses_command_without_bypass_options(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            worktree = self.make_worktree(Path(temporary), "lane")
            argv = codex.build_codex_command(
                "codex",
                worktree,
                "openrouter",
                "openai/gpt-5.6-terra",
                self.provider_config,
                self.model_config,
                "Implement the approved task in this worktree.",
            )

        self.assertEqual(argv[:2], ("codex", "exec"))
        self.assertIn("--ephemeral", argv)
        self.assertEqual(argv[argv.index("-C") + 1], str(worktree.resolve()))
        self.assertEqual(argv[argv.index("-s") + 1], "danger-full-access")
        self.assertIn('-c', argv)
        self.assertIn('model_provider="side_lane_openrouter"', argv)
        self.assertIn(
            'model_providers.side_lane_openrouter.base_url="https://openrouter.ai/api/v1"',
            argv,
        )
        self.assertIn(
            'model_providers.side_lane_openrouter.wire_api="responses"', argv
        )
        prohibited = {
            "--ignore-user-config",
            "--ignore-rules",
            "--approve-for-me",
            "--dangerously-bypass-approvals-and-sandbox",
            "--dangerously-bypass-hook-trust",
        }
        self.assertTrue(prohibited.isdisjoint(argv))

    def test_rejects_other_provider_and_non_responses_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            worktree = self.make_worktree(Path(temporary), "lane")
            args = (
                "codex",
                worktree,
                "openai",
                "openai/gpt-5.6-terra",
                self.provider_config,
                self.model_config,
                "task",
            )
            with self.assertRaisesRegex(codex.CodexAdapterError, "only the explicit"):
                codex.build_codex_command(*args)
            with self.assertRaisesRegex(codex.CodexAdapterError, "Responses endpoint"):
                codex.build_codex_command(
                    "codex",
                    worktree,
                    "openrouter",
                    "openai/gpt-5.6-terra",
                    {"base_url": "https://openrouter.ai/api"},
                    self.model_config,
                    "task",
                )
            with self.assertRaisesRegex(codex.CodexAdapterError, "wire protocol"):
                codex.build_codex_command(
                    "codex",
                    worktree,
                    "openrouter",
                    "openai/gpt-5.6-terra",
                    {"base_url": "https://openrouter.ai/api/v1"},
                    {"runtime_model": "openai/gpt-5.6-terra", "wire_api": "chat"},
                    "task",
                )

    def test_rejects_model_substitution_and_non_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            worktree = self.make_worktree(root, "lane")
            with self.assertRaisesRegex(codex.CodexAdapterError, "exactly match"):
                codex.build_codex_command(
                    "codex",
                    worktree,
                    "openrouter",
                    "openai/gpt-5.6-terra",
                    self.provider_config,
                    {"runtime_model": "openai/gpt-5.6-sol", "wire_api": "responses"},
                    "task",
                )
            with self.assertRaisesRegex(codex.CodexAdapterError, "not a Git worktree"):
                codex.build_codex_command(
                    "codex",
                    root,
                    "openrouter",
                    "openai/gpt-5.6-terra",
                    self.provider_config,
                    self.model_config,
                    "task",
                )

    def test_child_environment_keeps_ambient_access_but_only_injects_router_key(self) -> None:
        child = codex.build_child_env(
            {
                "PATH": "/bin",
                "GOOGLE_APPLICATION_CREDENTIALS": "/same-user/adc.json",
                "ANTHROPIC_API_KEY": "other-anthropic-key",
                "OPENAI_API_KEY": "other-openai-key",
                "OPENROUTER_API_KEY": "old-router-key",
                "ZAI_API_KEY": "glm-key",
                "ANTHROPIC_CUSTOM_TOKEN": "also-remove-this",
            },
            "selected-router-key",
        )
        self.assertEqual(child["PATH"], "/bin")
        self.assertEqual(child["GOOGLE_APPLICATION_CREDENTIALS"], "/same-user/adc.json")
        self.assertEqual(child[codex.OPENROUTER_ENV_KEY], "selected-router-key")
        for variable in codex.PROVIDER_CREDENTIAL_ENV_NAMES - {
            codex.OPENROUTER_ENV_KEY
        }:
            self.assertNotIn(variable, child)
        self.assertNotIn("ANTHROPIC_CUSTOM_TOKEN", child)

    def test_absent_credential_fails_before_process(self) -> None:
        with self.assertRaisesRegex(codex.CodexAdapterError, "credential is absent"):
            codex.build_child_env({}, "")

    def test_mocked_invocation_is_in_dedicated_worktree_and_redacts_credential(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = self.make_worktree(root, "repo")
            worktree = self.make_worktree(root, "lane")
            runner = mock.Mock(
                return_value=mock.Mock(
                    returncode=17,
                    stdout="provider saw selected-router-key",
                    stderr="failed selected-router-key",
                )
            )
            result = codex.run_codex(
                executable="codex-test",
                repo=repo,
                worktree=worktree,
                provider="openrouter",
                model="openai/gpt-5.6-terra",
                provider_config=self.provider_config,
                model_config=self.model_config,
                prompt="Implement the approved task in this worktree.",
                env={"OPENAI_API_KEY": "do-not-pass", "PATH": "/bin"},
                credential="selected-router-key",
                runner=runner,
            )

        self.assertEqual(result.returncode, 17)
        self.assertNotIn("selected-router-key", result.stdout)
        self.assertNotIn("selected-router-key", result.stderr)
        self.assertIn("[REDACTED_OPENROUTER_KEY]", result.stdout)
        self.assertEqual(result.cwd, worktree.resolve())
        self.assertEqual(runner.call_args.kwargs["cwd"], worktree.resolve())
        self.assertEqual(
            runner.call_args.kwargs["env"][codex.OPENROUTER_ENV_KEY],
            "selected-router-key",
        )
        self.assertNotIn("OPENAI_API_KEY", runner.call_args.kwargs["env"])
        self.assertFalse(runner.call_args.kwargs["check"])

    def test_rejects_main_checkout_as_lane_worktree_before_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = self.make_worktree(Path(temporary), "repo")
            runner = mock.Mock()
            with self.assertRaisesRegex(codex.CodexAdapterError, "dedicated worktree"):
                codex.run_codex(
                    executable="codex",
                    repo=repo,
                    worktree=repo,
                    provider="openrouter",
                    model="openai/gpt-5.6-terra",
                    provider_config=self.provider_config,
                    model_config=self.model_config,
                    prompt="task",
                    credential="selected-router-key",
                    runner=runner,
                )
            runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
