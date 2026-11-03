from __future__ import annotations

import sys
import unittest

from tools import mcp_smoke_test


class McpEndToEndTests(unittest.TestCase):
    def test_source_server_passes_stdio_smoke_test(self) -> None:
        mcp_smoke_test.run_smoke_test(mcp_smoke_test.source_command())

    def test_smoke_test_rejects_non_protocol_output(self) -> None:
        command = [sys.executable, "-c", "print('hello')"]

        with self.assertRaises(mcp_smoke_test.SmokeTestError):
            mcp_smoke_test.run_smoke_test(command, timeout=30)


if __name__ == "__main__":
    unittest.main()
