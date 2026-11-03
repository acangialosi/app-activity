from __future__ import annotations

import io
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


class McpCommandTests(unittest.TestCase):
    def test_mcp_serves_stdio_without_starting_service(self) -> None:
        with (
            patch.object(cli.sys, "argv", ["app-activity", "mcp"]),
            patch(
                "app_activity.mcp_server.run_stdio_server", return_value=7
            ) as run_stdio_server,
            patch.object(cli, "start_service") as start_service,
            patch.object(cli, "ensure_data_dir") as ensure_data_dir,
            patch.object(cli, "run_service") as run_service,
            patch.object(cli.sys, "stdout", new_callable=io.StringIO) as stdout,
            self.assertRaises(SystemExit) as raised,
        ):
            cli.main()

        self.assertEqual(7, raised.exception.code)
        run_stdio_server.assert_called_once_with()
        start_service.assert_not_called()
        ensure_data_dir.assert_not_called()
        run_service.assert_not_called()
        self.assertEqual("", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
