"""Smoke-test an App Activity MCP server command over stdio.

The server runs with LOCALAPPDATA pointed at temporary directories, so the
user's real history is never read or modified. The test checks the MCP
initialize handshake and stateless 2026-07-28 requests, tool listing and
calls, stdout hygiene, opt-in window titles, and that the server never
creates or changes history files.

    python tools\\mcp_smoke_test.py --source
    python tools\\mcp_smoke_test.py dist\\AppActivity\\AppActivity.exe mcp
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app_activity.database import ActivityDatabase, ActivityState  # noqa: E402


DEFAULT_TIMEOUT = 60.0
EXPECTED_TOOLS = {
    "get_activity_segments",
    "get_activity_summary",
    "get_tracking_status",
}
SEED_DAY = "2026-01-05"
BROWSER = "Microsoft Edge"
BROWSER_TITLE = "Smoke test page"
EDITOR = "Visual Studio Code"
WAL_FILES = {"activity.db-shm", "activity.db-wal"}
MODERN_VERSION = "2026-07-28"
PROTOCOL_VERSION_KEY = "io.modelcontextprotocol/protocolVersion"
CLIENT_CAPABILITIES_KEY = "io.modelcontextprotocol/clientCapabilities"
SERVER_INFO_KEY = "io.modelcontextprotocol/serverInfo"


class SmokeTestError(Exception):
    pass


def source_command() -> list[str]:
    return [sys.executable, str(ROOT / "app_activity_launcher.py"), "mcp"]


def resolve_command(command: list[str]) -> list[str]:
    executable = shutil.which(command[0])
    if executable is None:
        raise SmokeTestError(f"Server command not found: {command[0]}")
    return [str(Path(executable).resolve()), *command[1:]]


class McpSession:
    def __init__(
        self,
        command: list[str],
        local_app_data: Path,
        workdir: Path,
        timeout: float,
    ) -> None:
        environment = dict(os.environ)
        environment["LOCALAPPDATA"] = str(local_app_data)
        self.timeout = timeout
        self._next_id = 0
        self._lines: queue.Queue[bytes | None] = queue.Queue()
        self._stderr = bytearray()
        self.process = subprocess.Popen(
            command,
            cwd=workdir,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._stdout_thread = threading.Thread(
            target=self._read_stdout, daemon=True
        )
        self._stderr_thread = threading.Thread(
            target=self._read_stderr, daemon=True
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

    def __enter__(self) -> McpSession:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.kill()

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self._lines.put(line)
        self._lines.put(None)

    def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        while chunk := self.process.stderr.read1(65536):
            self._stderr.extend(chunk)

    def send(self, message: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        data = json.dumps(message, separators=(",", ":")).encode("utf-8")
        try:
            self.process.stdin.write(data + b"\n")
            self.process.stdin.flush()
        except OSError as error:
            raise self.failure(
                f"Could not write to the server: {error}"
            ) from error

    def notify(self, method: str) -> None:
        self.send({"jsonrpc": "2.0", "method": method})

    def request(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self._next_id += 1
        message: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": method,
        }
        if params is not None:
            message["params"] = params
        self.send(message)
        response = self._read_message(f"the {method} response")
        if response.get("id") != self._next_id:
            raise self.failure(
                f"Expected the response to request {self._next_id}, "
                f"got {response!r}"
            )
        return response

    def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"name": name}
        if arguments is not None:
            params["arguments"] = arguments
        response = self.request("tools/call", params)
        result = response.get("result")
        if not isinstance(result, dict):
            raise self.failure(f"{name} did not return a result: {response!r}")
        return result

    def structured_call(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        result = self.call_tool(name, arguments)
        if result.get("isError"):
            raise self.failure(f"{name} failed: {result!r}")
        structured = result.get("structuredContent")
        if not isinstance(structured, dict):
            raise self.failure(f"{name} returned no structured result")
        try:
            text = json.loads(result["content"][0]["text"])
        except (KeyError, IndexError, TypeError, json.JSONDecodeError):
            text = None
        if text != structured:
            raise self.failure(
                f"{name} text content does not match its structured result"
            )
        return structured

    def close(self) -> None:
        assert self.process.stdin is not None
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            code = self.process.wait(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            raise self.failure(
                "The server did not exit after stdin was closed"
            ) from None
        self._stdout_thread.join(timeout=self.timeout)
        extra = []
        while not self._lines.empty():
            line = self._lines.get_nowait()
            if line is not None:
                extra.append(line)
        if extra:
            raise self.failure(f"Unexpected output on stdout: {extra!r}")
        if code != 0:
            raise self.failure(f"The server exited with code {code}")

    def kill(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=10)
        if self.process.stdin is not None and not self.process.stdin.closed:
            try:
                self.process.stdin.close()
            except OSError:
                pass
        self._stdout_thread.join(timeout=10)
        self._stderr_thread.join(timeout=10)
        for stream in (self.process.stdout, self.process.stderr):
            if stream is not None:
                stream.close()

    def failure(self, message: str) -> SmokeTestError:
        self.kill()
        stderr = self._stderr.decode("utf-8", "replace").strip()
        if stderr:
            message = f"{message}\nServer stderr:\n{stderr[-4000:]}"
        return SmokeTestError(message)

    def _read_message(self, description: str) -> dict[str, Any]:
        try:
            line = self._lines.get(timeout=self.timeout)
        except queue.Empty:
            raise self.failure(f"Timed out waiting for {description}") from None
        if line is None:
            raise self.failure(f"The server closed stdout before {description}")
        if not line.endswith(b"\n") or line.endswith(b"\r\n"):
            raise self.failure(f"Output is not newline-delimited: {line!r}")
        try:
            message = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise self.failure(
                f"Non-JSON output on stdout: {line[:200]!r}"
            ) from None
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise self.failure(f"Not a JSON-RPC 2.0 message: {message!r}")
        return message


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeTestError(message)


def _local_timestamp(time_of_day: str) -> float:
    return datetime.fromisoformat(f"{SEED_DAY}T{time_of_day}").timestamp()


def seed_history(database_path: Path) -> None:
    segments = (
        (ActivityState(BROWSER, False, window_title=BROWSER_TITLE),
         "09:00", "09:30"),
        (ActivityState(EDITOR, False, window_title="notes.txt"),
         "09:30", "10:00"),
        (ActivityState("Idle", True), "10:00", "10:10"),
    )
    database = ActivityDatabase(database_path)
    try:
        for state, start, end in segments:
            segment_id = database.record_sample(
                None, None, state, _local_timestamp(start)
            )
            database.record_sample(
                segment_id, state, state, _local_timestamp(end)
            )
    finally:
        database.close()


def _file_hashes(directory: Path) -> dict[str, str]:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in directory.iterdir()
        if path.is_file()
    }


def initialize(session: McpSession) -> None:
    response = session.request(
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "app-activity-smoke-test", "version": "1"},
        },
    )
    result = response.get("result")
    expect(isinstance(result, dict), f"initialize failed: {response!r}")
    expect(
        isinstance(result.get("protocolVersion"), str),
        "initialize returned no protocol version",
    )
    expect(
        result.get("serverInfo", {}).get("name") == "app-activity",
        f"Unexpected serverInfo: {result.get('serverInfo')!r}",
    )
    expect(
        "tools" in result.get("capabilities", {}),
        "The server does not advertise tools",
    )
    session.notify("notifications/initialized")


def check_without_history(
    command: list[str], base: Path, timeout: float
) -> None:
    local_app_data = base / "empty"
    local_app_data.mkdir()
    with McpSession(command, local_app_data, base, timeout) as session:
        initialize(session)
        status = session.structured_call("get_tracking_status")
        expect(
            status.get("history_available") is False,
            f"Expected no history: {status!r}",
        )
        expect(status.get("recording") is False, f"Unexpected: {status!r}")
        summary = session.call_tool("get_activity_summary")
        expect(
            summary.get("isError") is True,
            f"A summary without history should fail: {summary!r}",
        )
        session.close()
    expect(
        not (local_app_data / "AppActivity").exists(),
        "The server created the App Activity data directory",
    )


def check_with_history(command: list[str], base: Path, timeout: float) -> None:
    local_app_data = base / "seeded"
    data_dir = local_app_data / "AppActivity"
    seed_history(data_dir / "activity.db")
    before = _file_hashes(data_dir)
    with McpSession(command, local_app_data, base, timeout) as session:
        initialize(session)
        expect(
            session.request("ping").get("result") == {},
            "ping did not return an empty result",
        )
        tools = session.request("tools/list").get("result", {}).get("tools")
        expect(isinstance(tools, list), "tools/list returned no tools")
        expect(
            {tool.get("name") for tool in tools} == EXPECTED_TOOLS,
            f"Unexpected tools: {[tool.get('name') for tool in tools]!r}",
        )
        expect(
            all(
                tool.get("annotations", {}).get("readOnlyHint") is True
                for tool in tools
            ),
            "Every tool must be annotated as read-only",
        )

        status = session.structured_call("get_tracking_status")
        expect(
            status.get("history_available") is True
            and status.get("recording") is False
            and bool(status.get("first_recorded_at"))
            and bool(status.get("last_recorded_at")),
            f"Unexpected tracking status: {status!r}",
        )

        day = {"period": "day", "date": SEED_DAY}
        summary = session.structured_call("get_activity_summary", day)
        expect(
            summary.get("active_seconds") == 3600
            and summary.get("idle_seconds") == 600,
            f"Unexpected summary totals: {summary!r}",
        )
        applications = summary.get("applications", [])
        expect(
            {app.get("name") for app in applications} == {BROWSER, EDITOR},
            f"Unexpected applications: {applications!r}",
        )
        expect(
            all("windows" not in app for app in applications),
            "Window titles were returned without include_window_titles",
        )
        titled = session.structured_call(
            "get_activity_summary", {**day, "include_window_titles": True}
        )
        titles = {
            window.get("title")
            for app in titled.get("applications", [])
            for window in app.get("windows", [])
        }
        expect(
            BROWSER_TITLE in titles,
            f"Window titles were not returned on request: {titled!r}",
        )

        segments = session.structured_call(
            "get_activity_segments", {"start": SEED_DAY, "end": SEED_DAY}
        )
        expect(
            segments.get("total") == 3
            and [
                segment.get("application")
                for segment in segments.get("segments", [])
            ] == [BROWSER, EDITOR, "Idle"],
            f"Unexpected segments: {segments!r}",
        )
        expect(
            all(
                "window_title" not in segment
                for segment in segments.get("segments", [])
            ),
            "Segment window titles were returned without opting in",
        )
        filtered = session.structured_call(
            "get_activity_segments",
            {
                "start": SEED_DAY,
                "end": SEED_DAY,
                "application": EDITOR.upper(),
                "include_idle": False,
            },
        )
        expect(
            filtered.get("total") == 1,
            f"Unexpected filtered segments: {filtered!r}",
        )

        invalid = session.call_tool("get_activity_summary", {"period": "x"})
        expect(
            invalid.get("isError") is True,
            f"Invalid arguments should fail: {invalid!r}",
        )
        unknown = session.request("tools/call", {"name": "delete_history"})
        expect(
            unknown.get("error", {}).get("code") == -32602,
            f"Unknown tools should be rejected: {unknown!r}",
        )
        session.close()

    check_stateless_requests(command, local_app_data, base, timeout)

    after = _file_hashes(data_dir)
    expect(
        after.get("activity.db") == before.get("activity.db"),
        "The server modified the history database",
    )
    created = set(after) - set(before) - WAL_FILES
    expect(not created, f"The server created files: {sorted(created)!r}")


def _modern(
    params: dict[str, Any] | None = None, version: str = MODERN_VERSION
) -> dict[str, Any]:
    meta = {PROTOCOL_VERSION_KEY: version, CLIENT_CAPABILITIES_KEY: {}}
    return {**(params or {}), "_meta": meta}


def _complete(response: dict[str, Any], description: str) -> dict[str, Any]:
    result = response.get("result")
    expect(
        isinstance(result, dict)
        and result.get("resultType") == "complete"
        and result.get("_meta", {}).get(SERVER_INFO_KEY, {}).get("name")
        == "app-activity",
        f"Unexpected {description} result: {response!r}",
    )
    return result


def check_stateless_requests(
    command: list[str], local_app_data: Path, base: Path, timeout: float
) -> None:
    with McpSession(command, local_app_data, base, timeout) as session:
        discovered = _complete(
            session.request("server/discover", _modern()), "server/discover"
        )
        expect(
            MODERN_VERSION in discovered.get("supportedVersions", [])
            and "tools" in discovered.get("capabilities", {}),
            f"Unexpected discovery result: {discovered!r}",
        )
        listed = _complete(
            session.request("tools/list", _modern()), "tools/list"
        )
        expect(
            {tool.get("name") for tool in listed.get("tools", [])}
            == EXPECTED_TOOLS,
            f"Unexpected stateless tools: {listed!r}",
        )
        called = _complete(
            session.request(
                "tools/call",
                _modern(
                    {
                        "name": "get_activity_summary",
                        "arguments": {"period": "day", "date": SEED_DAY},
                    }
                ),
            ),
            "tools/call",
        )
        expect(
            called.get("isError") is False
            and called.get("structuredContent", {}).get("active_seconds")
            == 3600,
            f"Unexpected stateless tool result: {called!r}",
        )
        unsupported = session.request(
            "server/discover", _modern(version="2099-01-01")
        )
        expect(
            unsupported.get("error", {}).get("code") == -32022,
            f"Unsupported versions should be rejected: {unsupported!r}",
        )
        session.close()


def run_smoke_test(
    command: list[str], *, timeout: float = DEFAULT_TIMEOUT
) -> None:
    with tempfile.TemporaryDirectory(
        prefix="app-activity-mcp-", ignore_cleanup_errors=True
    ) as directory:
        base = Path(directory)
        check_without_history(command, base, timeout)
        check_with_history(command, base, timeout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Smoke-test an App Activity MCP server over stdio."
    )
    parser.add_argument(
        "--source",
        action="store_true",
        help="test the source checkout (app_activity_launcher.py mcp)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help="seconds to wait for each server response",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="server command, for example AppActivity.exe mcp",
    )
    args = parser.parse_args(argv)
    if args.source == bool(args.command):
        parser.error("pass either --source or a server command")
    try:
        command = (
            source_command() if args.source else resolve_command(args.command)
        )
        run_smoke_test(command, timeout=args.timeout)
    except SmokeTestError as error:
        print(f"MCP smoke test failed: {error}", file=sys.stderr)
        return 1
    print("MCP smoke test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
