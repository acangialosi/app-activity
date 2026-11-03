"""Serve App Activity history to local AI agents over MCP on stdio.

Messages are newline-delimited JSON-RPC 2.0 in UTF-8. The server is dual-era:
requests carrying the 2026-07-28 per-request ``_meta`` envelope are served
statelessly, and all other requests follow the ``initialize`` handshake of
2025-11-25 and earlier revisions.
"""

from __future__ import annotations

import codecs
import json
import sys
import traceback
from collections.abc import Callable, Mapping, Sequence
from typing import Any, BinaryIO, TextIO

from . import __version__
from .mcp_errors import ToolError, UnknownToolError


MODERN_PROTOCOL_VERSIONS = ("2026-07-28",)
LEGACY_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
SUPPORTED_PROTOCOL_VERSIONS = MODERN_PROTOCOL_VERSIONS + LEGACY_PROTOCOL_VERSIONS

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
UNSUPPORTED_PROTOCOL_VERSION = -32022

PROTOCOL_VERSION_META_KEY = "io.modelcontextprotocol/protocolVersion"
CLIENT_CAPABILITIES_META_KEY = "io.modelcontextprotocol/clientCapabilities"
SERVER_INFO_META_KEY = "io.modelcontextprotocol/serverInfo"
CACHE_TTL_MS = 60 * 60 * 1000

SERVER_INFO = {"name": "app-activity", "title": "App Activity", "version": __version__}
INSTRUCTIONS = (
    "App Activity provides read-only history of the applications that were "
    "in the foreground on this computer and how long each was used. Times are "
    "in this computer's local time and include UTC offsets. Window titles are "
    "privacy-sensitive: they are omitted unless a tool is called with "
    "include_window_titles set to true, so request them only when the task "
    "needs them. Call get_tracking_status to check whether tracking is "
    "running and how current the history is."
)

_LINE_BREAK_ESCAPES = (
    ("\u0085", "\\u0085"),
    ("\u2028", "\\u2028"),
    ("\u2029", "\\u2029"),
)

_ToolCaller = Callable[[str, dict[str, Any]], dict[str, Any]]
_Handler = Callable[[dict[str, Any]], dict[str, Any]]


class _RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def serve(
    input_stream: BinaryIO,
    output_stream: BinaryIO,
    *,
    tools: Sequence[Mapping[str, Any]],
    call_tool: _ToolCaller,
    error_stream: TextIO | None = None,
) -> None:
    """Answer MCP messages from ``input_stream`` until it reaches end of file."""
    server = _Server(tools, call_tool, error_stream)
    first_line = True
    try:
        while True:
            try:
                line = input_stream.readline()
            except (OSError, ValueError) as error:
                server.log(f"Stopped reading requests: {error}")
                return
            if not line:
                return
            if first_line:
                line = line.removeprefix(codecs.BOM_UTF8)
                first_line = False
            response = server.handle_line(line)
            if response is not None and not _write(output_stream, response):
                return
    except KeyboardInterrupt:
        return


def run_stdio_server() -> int:
    """Serve the App Activity tools on this process's standard streams."""
    stdout = sys.stdout
    input_stream = getattr(sys.stdin, "buffer", None)
    output_stream = getattr(stdout, "buffer", None)
    if input_stream is None or output_stream is None:
        return 1
    error_stream = sys.stderr
    # Stray print() calls must not corrupt the protocol stream on stdout.
    sys.stdout = error_stream
    try:
        mcp_tools = _load_tools(error_stream)
        if mcp_tools is None:
            return 1
        serve(
            input_stream,
            output_stream,
            tools=mcp_tools.TOOLS,
            call_tool=lambda name, arguments: mcp_tools.call_tool(name, arguments),
            error_stream=error_stream,
        )
    finally:
        sys.stdout = stdout
    return 0


def _load_tools(error_stream: TextIO | None) -> Any:
    try:
        from . import mcp_tools
    except Exception:
        # Report and exit instead of raising: the windowed build shows unhandled
        # exceptions in a dialog, which would block an agent-launched server.
        _log(error_stream, f"Could not load the tools.\n{traceback.format_exc()}")
        return None
    return mcp_tools


def _log(error_stream: TextIO | None, message: str) -> None:
    if error_stream is None:
        return
    try:
        error_stream.write(f"app-activity mcp: {message.rstrip()}\n")
        error_stream.flush()
    except (OSError, ValueError):
        pass


class _Server:
    def __init__(
        self,
        tools: Sequence[Mapping[str, Any]],
        call_tool: _ToolCaller,
        error_stream: TextIO | None,
    ) -> None:
        self._tools = tools
        self._call_tool = call_tool
        self._error_stream = error_stream
        self._legacy_methods: dict[str, _Handler] = {
            "initialize": self._initialize,
            "ping": self._ping,
            "tools/list": self._list_tools,
            "tools/call": self._call,
        }
        self._modern_methods: dict[str, _Handler] = {
            "server/discover": self._discover,
            "tools/list": self._list_cacheable_tools,
            "tools/call": self._call,
        }

    def handle_line(self, line: bytes) -> bytes | None:
        line = line.strip()
        if not line:
            return None
        try:
            message = json.loads(line.decode("utf-8"), parse_constant=_reject_constant)
        except (ValueError, RecursionError):
            return self._encode(_error_response(None, PARSE_ERROR, "Parse error"))
        if not isinstance(message, list):
            response = self.handle_message(message)
            return None if response is None else self._encode(response)
        if not message:
            return self._encode(_error_response(None, INVALID_REQUEST, "Invalid Request"))
        responses = [self.handle_message(item) for item in message]
        encoded = [self._encode(item) for item in responses if item is not None]
        return b"[" + b",".join(encoded) + b"]" if encoded else None

    def handle_message(self, message: Any) -> dict[str, Any] | None:
        if not isinstance(message, dict):
            return _error_response(None, INVALID_REQUEST, "Invalid Request")
        request_id = message.get("id")
        usable_id = request_id if _is_valid_id(request_id) else None
        if "method" not in message:
            if "result" in message or "error" in message:
                return None
            return _error_response(usable_id, INVALID_REQUEST, "Invalid Request")
        method = message["method"]
        # Validate the envelope first: a malformed message without an id is
        # an invalid request, not a notification, so it still gets an error.
        if message.get("jsonrpc") != "2.0" or not isinstance(method, str):
            return _error_response(usable_id, INVALID_REQUEST, "Invalid Request")
        if "id" not in message:
            return None
        if not _is_valid_id(request_id):
            return _error_response(None, INVALID_REQUEST, "Invalid Request")
        try:
            result = self._dispatch(method, message.get("params"))
        except _RpcError as error:
            return _error_response(request_id, error.code, error.message, error.data)
        except Exception:
            self.log_exception(f"Unexpected error while handling {method}")
            return _error_response(request_id, INTERNAL_ERROR, "Internal error")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def log(self, message: str) -> None:
        _log(self._error_stream, message)

    def log_exception(self, context: str) -> None:
        self.log(f"{context}\n{traceback.format_exc()}")

    def _dispatch(self, method: str, params: Any) -> dict[str, Any]:
        if params is None:
            params = {}
        modern = _uses_modern_protocol(params)
        methods = self._modern_methods if modern else self._legacy_methods
        handler = methods.get(method)
        if handler is None:
            raise _RpcError(METHOD_NOT_FOUND, f"Method not found: {method}")
        if not isinstance(params, dict):
            raise _RpcError(INVALID_PARAMS, "Invalid params: params must be an object")
        result = handler(params)
        if not modern:
            return result
        return {
            "resultType": "complete",
            **result,
            "_meta": {SERVER_INFO_META_KEY: dict(SERVER_INFO)},
        }

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        requested = params.get("protocolVersion")
        if not isinstance(requested, str):
            raise _RpcError(INVALID_PARAMS, "Invalid params: protocolVersion must be a string")
        # The initialize handshake only exists in legacy revisions, so it never
        # negotiates a modern one.
        if requested in LEGACY_PROTOCOL_VERSIONS:
            version = requested
        else:
            version = LEGACY_PROTOCOL_VERSIONS[0]
        return {
            "protocolVersion": version,
            "capabilities": _capabilities(),
            "serverInfo": dict(SERVER_INFO),
            "instructions": INSTRUCTIONS,
        }

    def _ping(self, params: dict[str, Any]) -> dict[str, Any]:
        return {}

    def _discover(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "supportedVersions": list(MODERN_PROTOCOL_VERSIONS),
            "capabilities": _capabilities(),
            "instructions": INSTRUCTIONS,
            **_cache_hint(),
        }

    def _list_tools(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"tools": [dict(tool) for tool in self._tools]}

    def _list_cacheable_tools(self, params: dict[str, Any]) -> dict[str, Any]:
        return {**self._list_tools(params), **_cache_hint()}

    def _call(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str):
            raise _RpcError(INVALID_PARAMS, "Invalid params: name must be a string")
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        elif not isinstance(arguments, dict):
            raise _RpcError(INVALID_PARAMS, "Invalid params: arguments must be an object")
        try:
            result = self._call_tool(name, arguments)
            if not isinstance(result, dict):
                raise TypeError(f"{name} returned {type(result).__name__}, not a dict")
            text = _to_json(result)
        except ToolError as error:
            return _tool_error(str(error) or f"{name} failed.")
        except UnknownToolError:
            raise _RpcError(INVALID_PARAMS, f"Unknown tool: {name}") from None
        except Exception:
            self.log_exception(f"Tool {name} failed")
            return _tool_error(f"Internal error while running {name}.")
        return {
            "content": [{"type": "text", "text": text}],
            "structuredContent": result,
            "isError": False,
        }

    def _encode(self, message: dict[str, Any]) -> bytes:
        try:
            text = _to_json(message)
        except (TypeError, ValueError, RecursionError):
            self.log_exception("Could not serialize a response")
            fallback = _error_response(message.get("id"), INTERNAL_ERROR, "Internal error")
            text = _to_json(fallback)
        # Keep every message on one line even for clients that split on Unicode
        # line breaks, and escape lone surrogates (possible in window titles).
        for character, escape in _LINE_BREAK_ESCAPES:
            text = text.replace(character, escape)
        return text.encode("utf-8", "backslashreplace")


def _uses_modern_protocol(params: Any) -> bool:
    """Whether a request claims a modern revision through its ``_meta`` envelope.

    Requests without the claim are legacy traffic. A claim naming a legacy
    revision is served as legacy traffic, and a malformed or unsupported claim
    is rejected rather than silently served.
    """
    meta = params.get("_meta") if isinstance(params, dict) else None
    if not isinstance(meta, dict) or PROTOCOL_VERSION_META_KEY not in meta:
        return False
    version = meta[PROTOCOL_VERSION_META_KEY]
    if not isinstance(version, str):
        raise _RpcError(
            INVALID_PARAMS, f"Invalid params: {PROTOCOL_VERSION_META_KEY} must be a string"
        )
    if version in LEGACY_PROTOCOL_VERSIONS:
        return False
    if version not in MODERN_PROTOCOL_VERSIONS:
        raise _RpcError(
            UNSUPPORTED_PROTOCOL_VERSION,
            "Unsupported protocol version",
            {"supported": list(SUPPORTED_PROTOCOL_VERSIONS), "requested": version},
        )
    if not isinstance(meta.get(CLIENT_CAPABILITIES_META_KEY), dict):
        raise _RpcError(
            INVALID_PARAMS, f"Invalid params: {CLIENT_CAPABILITIES_META_KEY} must be an object"
        )
    return True


def _capabilities() -> dict[str, Any]:
    return {"tools": {"listChanged": False}}


def _cache_hint() -> dict[str, Any]:
    return {"ttlMs": CACHE_TTL_MS, "cacheScope": "public"}


def _tool_error(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _error_response(
    request_id: str | int | None, code: int, message: str, data: Any = None
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _is_valid_id(value: Any) -> bool:
    return isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool))


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


def _to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _write(output_stream: BinaryIO, payload: bytes) -> bool:
    try:
        output_stream.write(payload + b"\n")
        output_stream.flush()
    except (OSError, ValueError):
        return False
    return True
