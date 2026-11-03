from __future__ import annotations

import builtins
import codecs
import io
import json
import sys
import types
import unittest
from collections.abc import Callable
from typing import Any
from unittest.mock import Mock, patch

import app_activity
from app_activity import mcp_server
from app_activity.mcp_errors import ToolError, UnknownToolError


MODERN_VERSION = "2026-07-28"
NEWEST_LEGACY_VERSION = "2025-11-25"
SERVER_INFO_KEY = "io.modelcontextprotocol/serverInfo"
SERVER_INFO = {
    "name": "app-activity",
    "title": "App Activity",
    "version": app_activity.__version__,
}
TOOLS = [
    {
        "name": "echo",
        "title": "Echo",
        "description": "Return the arguments it was called with.",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
        "annotations": {"readOnlyHint": True},
    }
]


def stub_call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "echo":
        return {"arguments": arguments}
    if name == "invalid":
        raise ToolError("date must use YYYY-MM-DD.")
    if name == "crash":
        raise RuntimeError("database exploded")
    raise UnknownToolError(name)


def returning(value: Any) -> Callable[[str, dict[str, Any]], Any]:
    return lambda name, arguments: value


def request(request_id: Any, method: str, params: Any = None) -> dict[str, Any]:
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def notification(method: str, params: Any = None) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        message["params"] = params
    return message


def envelope(version: Any = MODERN_VERSION, **params: Any) -> dict[str, Any]:
    meta = {
        mcp_server.PROTOCOL_VERSION_META_KEY: version,
        mcp_server.CLIENT_CAPABILITIES_META_KEY: {},
    }
    return {**params, "_meta": meta}


def encode(*messages: Any) -> bytes:
    return b"".join(
        json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n"
        for message in messages
    )


def reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


class ServerTestCase(unittest.TestCase):
    def run_server(
        self,
        data: bytes,
        *,
        tools: Any = TOOLS,
        call_tool: Callable[[str, dict[str, Any]], Any] = stub_call_tool,
        error_stream: io.StringIO | None = None,
    ) -> bytes:
        output = io.BytesIO()
        mcp_server.serve(
            io.BytesIO(data),
            output,
            tools=tools,
            call_tool=call_tool,
            error_stream=error_stream,
        )
        return output.getvalue()

    def parse_output(self, output: bytes) -> list[Any]:
        if not output:
            return []
        self.assertTrue(output.endswith(b"\n"))
        self.assertNotIn(b"\r", output)
        messages = []
        for line in output.split(b"\n")[:-1]:
            message = json.loads(line.decode("utf-8"), parse_constant=reject_constant)
            for item in message if isinstance(message, list) else [message]:
                self.assertEqual("2.0", item["jsonrpc"])
            messages.append(message)
        return messages

    def exchange(self, *messages: Any, **options: Any) -> list[Any]:
        return self.parse_output(self.run_server(encode(*messages), **options))

    def respond(self, message: Any, **options: Any) -> dict[str, Any]:
        responses = self.exchange(message, **options)
        self.assertEqual(1, len(responses))
        return responses[0]

    def result(self, message: dict[str, Any], **options: Any) -> dict[str, Any]:
        response = self.respond(message, **options)
        self.assertEqual(message["id"], response["id"])
        self.assertNotIn("error", response)
        return response["result"]

    def assert_error(
        self, response: dict[str, Any], code: int, request_id: str | int | None
    ) -> None:
        self.assertEqual(request_id, response["id"])
        self.assertEqual(code, response["error"]["code"])
        self.assertIsInstance(response["error"]["message"], str)
        self.assertNotIn("result", response)


class InitializeTests(ServerTestCase):
    def test_echoes_supported_protocol_version(self) -> None:
        for version in ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"):
            with self.subTest(version=version):
                params = {
                    "protocolVersion": version,
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "1.0"},
                }
                result = self.result(request(1, "initialize", params))
                self.assertEqual(version, result["protocolVersion"])

    def test_answers_unsupported_version_with_newest_handshake_version(self) -> None:
        for version in ("2099-01-01", "1.0", MODERN_VERSION):
            with self.subTest(version=version):
                result = self.result(request(1, "initialize", {"protocolVersion": version}))
                self.assertEqual(NEWEST_LEGACY_VERSION, result["protocolVersion"])

    def test_requires_string_protocol_version(self) -> None:
        for params in (None, {}, {"protocolVersion": 20250618}, ["2025-06-18"]):
            with self.subTest(params=params):
                response = self.respond(request(1, "initialize", params))
                self.assert_error(response, -32602, 1)

    def test_result_describes_read_only_server(self) -> None:
        result = self.result(request(1, "initialize", {"protocolVersion": "2025-06-18"}))

        self.assertEqual(
            {"protocolVersion", "capabilities", "serverInfo", "instructions"}, set(result)
        )
        self.assertEqual({"tools": {"listChanged": False}}, result["capabilities"])
        self.assertEqual(SERVER_INFO, result["serverInfo"])
        for phrase in ("read-only", "UTC offset", "include_window_titles", "get_tracking_status"):
            self.assertIn(phrase, result["instructions"])

    def test_serves_requests_before_and_after_initialize(self) -> None:
        responses = self.exchange(
            request(1, "tools/list"),
            request(2, "initialize", {"protocolVersion": "2025-06-18"}),
            notification("notifications/initialized"),
            request(3, "ping"),
        )

        self.assertEqual([1, 2, 3], [response["id"] for response in responses])
        self.assertEqual(TOOLS, responses[0]["result"]["tools"])
        self.assertEqual({}, responses[2]["result"])


class ToolTests(ServerTestCase):
    def test_ping_returns_empty_result(self) -> None:
        self.assertEqual({}, self.result(request("ping-1", "ping")))

    def test_lists_tools_and_ignores_cursor(self) -> None:
        for params in (None, {"cursor": "page-2"}):
            with self.subTest(params=params):
                self.assertEqual({"tools": TOOLS}, self.result(request(1, "tools/list", params)))

    def test_call_returns_compact_text_and_structured_content(self) -> None:
        params = {"name": "echo", "arguments": {"text": "hi", "count": 2}}

        result = self.result(request(1, "tools/call", params))

        self.assertEqual(
            {
                "content": [
                    {"type": "text", "text": '{"arguments":{"text":"hi","count":2}}'}
                ],
                "structuredContent": {"arguments": {"text": "hi", "count": 2}},
                "isError": False,
            },
            result,
        )

    def test_call_without_arguments_passes_empty_object(self) -> None:
        for params in ({"name": "echo"}, {"name": "echo", "arguments": None}):
            with self.subTest(params=params):
                result = self.result(request(1, "tools/call", params))
                self.assertEqual({"arguments": {}}, result["structuredContent"])

    def test_call_rejects_invalid_params_without_calling_tool(self) -> None:
        call_tool = Mock(return_value={})
        cases = (
            {"arguments": {}},
            {"name": 5},
            {"name": "echo", "arguments": ["text"]},
            {"name": "echo", "arguments": "text"},
        )
        for params in cases:
            with self.subTest(params=params):
                response = self.respond(request(1, "tools/call", params), call_tool=call_tool)
                self.assert_error(response, -32602, 1)
        call_tool.assert_not_called()

    def test_tool_error_becomes_error_result(self) -> None:
        result = self.result(request(1, "tools/call", {"name": "invalid"}))

        self.assertEqual(
            {
                "content": [{"type": "text", "text": "date must use YYYY-MM-DD."}],
                "isError": True,
            },
            result,
        )

    def test_unknown_tool_is_invalid_params(self) -> None:
        params = {"name": "delete_history", "arguments": {}}

        response = self.respond(request(1, "tools/call", params))

        self.assert_error(response, -32602, 1)
        self.assertEqual("Unknown tool: delete_history", response["error"]["message"])

    def test_unexpected_failure_is_error_result_and_serving_continues(self) -> None:
        errors = io.StringIO()

        responses = self.exchange(
            request(1, "tools/call", {"name": "crash"}),
            request(2, "tools/call", {"name": "echo"}),
            error_stream=errors,
        )

        failure = responses[0]["result"]
        self.assertTrue(failure["isError"])
        self.assertTrue(failure["content"][0]["text"].startswith("Internal error"))
        self.assertNotIn("database exploded", failure["content"][0]["text"])
        self.assertNotIn("structuredContent", failure)
        self.assertFalse(responses[1]["result"]["isError"])
        self.assertIn("RuntimeError: database exploded", errors.getvalue())

    def test_unexpected_failure_without_error_stream(self) -> None:
        responses = self.exchange(
            request(1, "tools/call", {"name": "crash"}), request(2, "ping"), error_stream=None
        )

        self.assertTrue(responses[0]["result"]["isError"])
        self.assertEqual({}, responses[1]["result"])

    def test_unusable_tool_results_are_error_results(self) -> None:
        for value in (["not", "a", "dict"], {"when": object()}, {"ratio": float("nan")}):
            with self.subTest(value=value):
                result = self.result(
                    request(1, "tools/call", {"name": "echo"}), call_tool=returning(value)
                )
                self.assertTrue(result["isError"])
                self.assertTrue(result["content"][0]["text"].startswith("Internal error"))

    def test_non_ascii_text_round_trips_as_utf8(self) -> None:
        arguments = {"application": "Café – 日本語 🚀"}

        output = self.run_server(
            encode(request(1, "tools/call", {"name": "echo", "arguments": arguments}))
        )

        self.assertIn("Café – 日本語 🚀".encode("utf-8"), output)
        result = self.parse_output(output)[0]["result"]
        self.assertEqual({"arguments": arguments}, result["structuredContent"])
        self.assertEqual({"arguments": arguments}, json.loads(result["content"][0]["text"]))

    def test_line_separators_and_lone_surrogates_stay_on_one_valid_line(self) -> None:
        title = "a\u2028b\u2029c\u0085d\ud800e"

        output = self.run_server(
            encode(request(1, "tools/call", {"name": "echo"})),
            call_tool=returning({"title": title}),
        )

        self.assertEqual(1, output.count(b"\n"))
        for character in ("\u2028", "\u2029", "\u0085"):
            self.assertNotIn(character.encode("utf-8"), output)
        result = self.parse_output(output)[0]["result"]
        self.assertEqual(title, result["structuredContent"]["title"])
        self.assertEqual({"title": title}, json.loads(result["content"][0]["text"]))


class JsonRpcTests(ServerTestCase):
    def test_unknown_method_is_method_not_found(self) -> None:
        self.assert_error(self.respond(request(1, "resources/list")), -32601, 1)

    def test_non_object_params_are_invalid_params(self) -> None:
        responses = self.exchange(request(1, "ping", [1]), request(2, "missing", [1]))

        self.assert_error(responses[0], -32602, 1)
        self.assert_error(responses[1], -32601, 2)

    def test_notifications_get_no_response(self) -> None:
        call_tool = Mock(return_value={})

        output = self.run_server(
            encode(
                notification("notifications/initialized"),
                notification("notifications/cancelled", {"requestId": 1, "reason": "x"}),
                notification("notifications/unknown"),
                notification("tools/call", {"name": "echo"}),
                {"jsonrpc": "1.0", "method": 5},
            ),
            call_tool=call_tool,
        )

        self.assertEqual(b"", output)
        call_tool.assert_not_called()

    def test_client_responses_are_ignored(self) -> None:
        output = self.run_server(
            encode(
                {"jsonrpc": "2.0", "id": 1, "result": {}},
                {"jsonrpc": "2.0", "id": 2, "error": {"code": -1, "message": "Denied"}},
            )
        )

        self.assertEqual(b"", output)

    def test_unparseable_lines_are_parse_errors_and_serving_continues(self) -> None:
        lines = (
            b'{"jsonrpc": "2.0", "id": 1, "method": "ping"',
            b'{"jsonrpc":"2.0","id":1,"method":"\xff"}',
            b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{"x":NaN}}',
            b"[" * 100_000 + b"]" * 100_000,
        )
        for line in lines:
            with self.subTest(line=line[:60]):
                output = self.run_server(line + b"\n" + encode(request(2, "ping")))
                responses = self.parse_output(output)
                self.assertEqual(2, len(responses))
                self.assert_error(responses[0], -32700, None)
                self.assertEqual({}, responses[1]["result"])

    def test_invalid_id_types_are_invalid_requests(self) -> None:
        for request_id in (None, True, 1.5, {"id": 1}, [1]):
            with self.subTest(request_id=request_id):
                self.assert_error(self.respond(request(request_id, "ping")), -32600, None)

    def test_malformed_messages_are_invalid_requests(self) -> None:
        cases = (
            ({"jsonrpc": "1.0", "id": 1, "method": "ping"}, 1),
            ({"id": 2, "method": "ping"}, 2),
            ({"jsonrpc": "2.0", "id": "three", "method": 3}, "three"),
            ({"jsonrpc": "2.0", "id": 4}, 4),
            ({}, None),
            ("ping", None),
            (5, None),
            (None, None),
        )
        for message, request_id in cases:
            with self.subTest(message=message):
                self.assert_error(self.respond(message), -32600, request_id)

    def test_batch_returns_one_array_of_responses(self) -> None:
        output = self.run_server(
            encode(
                [
                    request(1, "ping"),
                    notification("notifications/initialized"),
                    request(2, "unknown/method"),
                    {"jsonrpc": "2.0", "id": 9, "result": {}},
                    5,
                ]
            )
        )

        self.assertEqual(1, output.count(b"\n"))
        (batch,) = self.parse_output(output)
        self.assertEqual(3, len(batch))
        self.assertEqual({}, batch[0]["result"])
        self.assert_error(batch[1], -32601, 2)
        self.assert_error(batch[2], -32600, None)

    def test_batch_without_requests_writes_nothing(self) -> None:
        batch = [notification("notifications/initialized"), notification("notifications/cancelled")]

        self.assertEqual(b"", self.run_server(encode(batch)))

    def test_empty_batch_is_one_invalid_request(self) -> None:
        self.assert_error(self.respond([]), -32600, None)

    def test_handler_failure_is_internal_error_and_serving_continues(self) -> None:
        class BrokenTools:
            def __iter__(self) -> Any:
                raise RuntimeError("tool definitions unavailable")

        errors = io.StringIO()

        responses = self.exchange(
            request(1, "tools/list"), request(2, "ping"), tools=BrokenTools(), error_stream=errors
        )

        self.assert_error(responses[0], -32603, 1)
        self.assertEqual({}, responses[1]["result"])
        self.assertIn("RuntimeError: tool definitions unavailable", errors.getvalue())

    def test_unserializable_result_is_internal_error(self) -> None:
        tools = [{"name": "bad", "tags": {"set"}}]

        responses = self.exchange(request(1, "tools/list"), request(2, "ping"), tools=tools)

        self.assert_error(responses[0], -32603, 1)
        self.assertEqual({}, responses[1]["result"])


class TransportTests(ServerTestCase):
    def test_accepts_crlf_blank_lines_and_missing_final_newline(self) -> None:
        data = (
            b'{"jsonrpc":"2.0","id":1,"method":"ping"}\r\n'
            b"\r\n   \n\t\n"
            b'{"jsonrpc":"2.0","id":2,"method":"ping"}\r\n'
            b'{"jsonrpc":"2.0","id":3,"method":"ping"}'
        )

        responses = self.parse_output(self.run_server(data))

        self.assertEqual([1, 2, 3], [response["id"] for response in responses])

    def test_accepts_utf8_bom_on_first_line(self) -> None:
        data = codecs.BOM_UTF8 + encode(request(1, "ping"), request(2, "ping"))

        responses = self.parse_output(self.run_server(data))

        self.assertEqual([1, 2], [response["id"] for response in responses])

    def test_writes_one_compact_json_message_per_line(self) -> None:
        output = self.run_server(
            encode(
                request(1, "initialize", {"protocolVersion": "2025-06-18"}),
                request(2, "tools/list"),
                request(3, "tools/call", {"name": "echo", "arguments": {"text": "a\nb"}}),
            )
        )

        lines = output.split(b"\n")
        self.assertEqual(4, len(lines))
        self.assertEqual(b"", lines[-1])
        for line in lines[:-1]:
            compact = json.dumps(json.loads(line), ensure_ascii=False, separators=(",", ":"))
            self.assertEqual(compact.encode("utf-8"), line)

    def test_returns_at_end_of_input(self) -> None:
        self.assertEqual(b"", self.run_server(b""))

    def test_stops_quietly_when_output_is_closed(self) -> None:
        output = Mock()
        output.write.side_effect = BrokenPipeError

        mcp_server.serve(
            io.BytesIO(encode(request(1, "ping"), request(2, "ping"))),
            output,
            tools=TOOLS,
            call_tool=stub_call_tool,
        )

        output.write.assert_called_once()

    def test_stops_quietly_when_input_fails_or_is_interrupted(self) -> None:
        for failure in (KeyboardInterrupt, OSError("The pipe has been ended")):
            with self.subTest(failure=failure):
                input_stream = Mock()
                input_stream.readline.side_effect = failure
                output = io.BytesIO()

                mcp_server.serve(
                    input_stream,
                    output,
                    tools=TOOLS,
                    call_tool=stub_call_tool,
                    error_stream=io.StringIO(),
                )

                self.assertEqual(b"", output.getvalue())


class ModernProtocolTests(ServerTestCase):
    def test_discover_advertises_modern_versions_and_capabilities(self) -> None:
        result = self.result(request(1, "server/discover", envelope()))

        self.assertEqual([MODERN_VERSION], result["supportedVersions"])
        self.assertEqual({"tools": {"listChanged": False}}, result["capabilities"])
        self.assertIn("get_tracking_status", result["instructions"])
        self.assertEqual("complete", result["resultType"])
        self.assertEqual("public", result["cacheScope"])
        self.assertGreater(result["ttlMs"], 0)
        self.assertEqual(SERVER_INFO, result["_meta"][SERVER_INFO_KEY])

    def test_tools_list_and_call_include_modern_result_fields(self) -> None:
        responses = self.exchange(
            request(1, "tools/list", envelope()),
            request(2, "tools/call", envelope(name="echo", arguments={"text": "hi"})),
        )

        listed = responses[0]["result"]
        self.assertEqual(TOOLS, listed["tools"])
        self.assertEqual("complete", listed["resultType"])
        self.assertEqual("public", listed["cacheScope"])
        self.assertGreater(listed["ttlMs"], 0)
        called = responses[1]["result"]
        self.assertEqual({"arguments": {"text": "hi"}}, called["structuredContent"])
        self.assertFalse(called["isError"])
        self.assertEqual("complete", called["resultType"])
        self.assertEqual(SERVER_INFO, called["_meta"][SERVER_INFO_KEY])

    def test_unsupported_version_lists_supported_versions(self) -> None:
        response = self.respond(request(1, "tools/list", envelope("2099-01-01")))

        self.assert_error(response, -32022, 1)
        self.assertEqual(
            {
                "supported": [
                    MODERN_VERSION,
                    "2025-11-25",
                    "2025-06-18",
                    "2025-03-26",
                    "2024-11-05",
                ],
                "requested": "2099-01-01",
            },
            response["error"]["data"],
        )

    def test_malformed_envelope_is_invalid_params(self) -> None:
        version_key = mcp_server.PROTOCOL_VERSION_META_KEY
        capabilities_key = mcp_server.CLIENT_CAPABILITIES_META_KEY
        cases = (
            envelope(20260728),
            {"_meta": {version_key: MODERN_VERSION}},
            {"_meta": {version_key: MODERN_VERSION, capabilities_key: []}},
        )
        for params in cases:
            with self.subTest(params=params):
                self.assert_error(self.respond(request(1, "tools/list", params)), -32602, 1)

    def test_methods_outside_modern_revision_are_not_found(self) -> None:
        cases = (
            request(1, "initialize", envelope(protocolVersion=MODERN_VERSION)),
            request(1, "ping", envelope()),
            request(1, "server/discover"),
        )
        for message in cases:
            with self.subTest(method=message["method"]):
                self.assert_error(self.respond(message), -32601, 1)

    def test_requests_without_modern_claim_get_legacy_results(self) -> None:
        cases = (
            {"_meta": {"progressToken": "progress-1"}},
            envelope("2025-06-18"),
        )
        for params in cases:
            with self.subTest(params=params):
                self.assertEqual({"tools": TOOLS}, self.result(request(1, "tools/list", params)))


class RunStdioServerTests(unittest.TestCase):
    def test_returns_1_without_standard_streams(self) -> None:
        stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        for stdin, stdout in ((None, stream), (stream, None)):
            with (
                self.subTest(stdin=stdin, stdout=stdout),
                patch.object(sys, "stdin", stdin),
                patch.object(sys, "stdout", stdout),
            ):
                self.assertEqual(1, mcp_server.run_stdio_server())

    def test_serves_tools_on_standard_streams(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []

        def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            calls.append((name, arguments))
            print("stray output")
            return {"ok": True}

        tools_module = types.SimpleNamespace(TOOLS=TOOLS, call_tool=call_tool)
        stdin = io.TextIOWrapper(
            io.BytesIO(
                encode(
                    request(1, "tools/list"),
                    request(2, "tools/call", {"name": "echo", "arguments": {"a": 1}}),
                )
            ),
            encoding="utf-8",
        )
        stdout_bytes = io.BytesIO()
        stdout = io.TextIOWrapper(stdout_bytes, encoding="utf-8")
        stderr = io.StringIO()

        with (
            patch.object(app_activity, "mcp_tools", tools_module, create=True),
            patch.object(sys, "stdin", stdin),
            patch.object(sys, "stdout", stdout),
            patch.object(sys, "stderr", stderr),
        ):
            exit_code = mcp_server.run_stdio_server()
            restored_stdout = sys.stdout

        self.assertEqual(0, exit_code)
        self.assertIs(stdout, restored_stdout)
        responses = [json.loads(line) for line in stdout_bytes.getvalue().splitlines()]
        self.assertEqual(TOOLS, responses[0]["result"]["tools"])
        self.assertEqual({"ok": True}, responses[1]["result"]["structuredContent"])
        self.assertEqual([("echo", {"a": 1})], calls)
        self.assertIn("stray output", stderr.getvalue())

    def test_reports_tools_that_fail_to_load_without_raising(self) -> None:
        real_import = builtins.__import__

        def failing_import(
            name: str,
            globals_: Any = None,
            locals_: Any = None,
            fromlist: Any = (),
            level: int = 0,
        ) -> Any:
            if level and "mcp_tools" in (fromlist or ()):
                raise ImportError("broken tools")
            return real_import(name, globals_, locals_, fromlist, level)

        stdin = io.TextIOWrapper(io.BytesIO(encode(request(1, "ping"))), encoding="utf-8")
        stdout_bytes = io.BytesIO()
        stdout = io.TextIOWrapper(stdout_bytes, encoding="utf-8")
        stderr = io.StringIO()

        with (
            patch.object(builtins, "__import__", failing_import),
            patch.object(sys, "stdin", stdin),
            patch.object(sys, "stdout", stdout),
            patch.object(sys, "stderr", stderr),
        ):
            exit_code = mcp_server.run_stdio_server()
            restored_stdout = sys.stdout

        self.assertEqual(1, exit_code)
        self.assertIs(stdout, restored_stdout)
        self.assertEqual(b"", stdout_bytes.getvalue())
        self.assertIn("Could not load the tools", stderr.getvalue())
        self.assertIn("broken tools", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
