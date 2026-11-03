from __future__ import annotations


class ToolError(Exception):
    """A tool call failed in a way the calling agent can correct or report."""


class UnknownToolError(LookupError):
    """The requested tool is not provided by this server."""
