from __future__ import annotations

import unittest
from unittest import mock

from side_lane import credentials


class CredentialTests(unittest.TestCase):
    def test_scrubs_only_provider_transport_environment(self) -> None:
        inherited = {
            "OPENROUTER_API_KEY": "old", "ANTHROPIC_API_KEY": "old",
            "GOOGLE_APPLICATION_CREDENTIALS": "/adc.json", "GH_TOKEN": "ambient",
            "CLAUDE_CONFIG_DIR": "/config",
        }
        child = credentials.selected_provider_environment(
            inherited, "openrouter", {"base_url": "https://openrouter.ai/api/v1"},
            "openai/model", "selected", "codex"
        )
        self.assertEqual(child["OPENROUTER_API_KEY"], "selected")
        self.assertEqual(child["GOOGLE_APPLICATION_CREDENTIALS"], "/adc.json")
        self.assertEqual(child["CLAUDE_CONFIG_DIR"], "/config")
        self.assertNotIn("ANTHROPIC_API_KEY", child)

    def test_presence_never_requests_secret_value(self) -> None:
        completed = mock.Mock(returncode=0)
        with mock.patch("side_lane.credentials.subprocess.run", return_value=completed) as run:
            self.assertTrue(credentials.credential_present("service", "Darwin"))
        self.assertNotIn("-w", run.call_args.args[0])
        self.assertEqual(run.call_args.kwargs["stdout"], credentials.subprocess.DEVNULL)

    def test_unsupported_platform_fails_closed(self) -> None:
        self.assertFalse(credentials.credential_present("service", "Linux"))
        with self.assertRaises(credentials.CredentialError):
            credentials.read_credential("service", "Linux")


if __name__ == "__main__":
    unittest.main()
