from __future__ import annotations

import json
import sys
import threading
import time
from datetime import datetime, timezone
from http.client import HTTPException
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from . import __version__


RELEASES_URL = "https://github.com/acangialosi/app-activity/releases"
MANIFEST_NAME = "AppActivity-update.json"
CHECK_INTERVAL = 3600
RETRY_INTERVAL = 300
MAX_MANIFEST_BYTES = 16384


def parse_build_info(value: object) -> tuple[str, str, datetime]:
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise ValueError("Unsupported update metadata")
    version, channel, built_at = (
        value.get("version"), value.get("channel"), value.get("built_at")
    )
    if (
        not isinstance(version, str)
        or not version
        or len(version) > 100
        or channel not in ("nightly", "stable")
        or not isinstance(built_at, str)
    ):
        raise ValueError("Invalid update metadata")
    timestamp = datetime.fromisoformat(built_at)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Build timestamp must include a timezone")
    return version, channel, timestamp.astimezone(timezone.utc)


class UpdateChecker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_check = 0.0
        self._current: tuple[str, str, datetime] | None = None
        self._status: dict[str, str] = {
            "status": "source",
            "version": __version__,
            "release_url": RELEASES_URL,
            "message": "Running from source; view GitHub releases for installers.",
        }
        if getattr(sys, "frozen", False):
            try:
                self._current = parse_build_info(
                    json.loads(Path(__file__).with_name("build_info.json").read_text(
                        encoding="utf-8-sig"
                    ))
                )
            except (OSError, ValueError):
                self._status.update(
                    status="error",
                    message="Build information is unavailable; check GitHub releases manually.",
                )
            else:
                version, channel, _ = self._current
                self._status.update(
                    status="pending",
                    version=version,
                    channel=channel,
                    release_url=f"{RELEASES_URL}/{'tag/nightly' if channel == 'nightly' else 'latest'}",
                    message="Checking for updates...",
                )

    def status(self, *, force: bool = False) -> dict[str, str]:
        with self._lock:
            if (
                self._current is not None
                and self._next_check != float("inf")
                and (force or time.monotonic() >= self._next_check)
            ):
                self._next_check = float("inf")
                self._status.update(status="pending", message="Checking for updates...")
                threading.Thread(
                    target=self._check, name="release-update-check", daemon=True
                ).start()
            return dict(self._status)

    def _check(self) -> None:
        assert self._current is not None
        _, channel, installed_at = self._current
        location = "download/nightly" if channel == "nightly" else "latest/download"
        request = Request(
            f"{RELEASES_URL}/{location}/{MANIFEST_NAME}",
            headers={"User-Agent": "AppActivity-update-check", "Accept": "application/json"},
        )
        interval = CHECK_INTERVAL
        try:
            with urlopen(request, timeout=5) as response:
                raw = response.read(MAX_MANIFEST_BYTES + 1)
            if len(raw) > MAX_MANIFEST_BYTES:
                raise ValueError("Update metadata is too large")
            version, remote_channel, built_at = parse_build_info(
                json.loads(raw.decode("utf-8-sig"))
            )
            if remote_channel != channel:
                raise ValueError("Release channel mismatch")
            newer = built_at > installed_at
            result = {
                "status": "available" if newer else "current",
                "message": (
                    f"Update available: {version} ({built_at:%Y-%m-%d %H:%M} UTC)."
                    if newer else "You're up to date."
                ),
            }
        except (OSError, URLError, HTTPException, ValueError):
            interval = RETRY_INTERVAL
            result = {
                "status": "error",
                "message": "Could not check for updates; retrying later. You can check GitHub releases manually.",
            }
        with self._lock:
            self._status.update(result)
            self._next_check = time.monotonic() + interval
