from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from app_activity import cli


class ServiceCommandTests(unittest.TestCase):
    def test_frozen_app_relaunches_itself_in_run_mode(self) -> None:
        executable = Path(r"C:\Program Files\AppActivity\AppActivity.exe")

        with (
            patch.object(cli, "IS_FROZEN", True),
            patch.object(cli.sys, "executable", str(executable)),
        ):
            command, working_directory = cli._service_command()

        self.assertEqual([str(executable), "run"], command)
        self.assertEqual(executable.parent, working_directory)

    def test_source_app_uses_hidden_python_runner(self) -> None:
        with patch.object(cli, "IS_FROZEN", False):
            command, working_directory = cli._service_command()

        self.assertEqual(str(cli.HIDDEN_RUNNER), command[1])
        self.assertEqual(cli.ROOT, working_directory)


if __name__ == "__main__":
    unittest.main()
