from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
RELEASES = "https://github.com/gitleaks/gitleaks/releases/download"


class UpdateError(Exception):
    pass


def download(url: str) -> bytes:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "app-activity-gitleaks-updater"})
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError) as error:
        raise UpdateError("Official Gitleaks release lookup/download failed; pins were not changed.") from error


def version_tuple(version: str) -> tuple[int, ...]:
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise UpdateError("Expected a stable numeric Gitleaks version.")
    return tuple(int(part) for part in version.split("."))


def assignments(source: str) -> dict[str, ast.Assign]:
    nodes = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in ("GITLEAKS_VERSION", "GITLEAKS_HASHES"):
                if target.id in nodes:
                    raise UpdateError("Duplicate Gitleaks pins; review the updater.")
                nodes[target.id] = node
    if set(nodes) != {"GITLEAKS_VERSION", "GITLEAKS_HASHES"}:
        raise UpdateError("Gitleaks pin layout changed; review the updater.")
    return nodes


def release_hashes(version: str, release: dict) -> dict[str, str]:
    assets = release.get("assets")
    if not isinstance(assets, list) or any(not isinstance(asset, dict) for asset in assets):
        raise UpdateError("Gitleaks release assets are malformed.")
    names = [asset.get("name") for asset in assets]
    checksum_name = f"gitleaks_{version}_checksums.txt"
    archives = {arch: f"gitleaks_{version}_windows_{arch}.zip" for arch in ("arm64", "x64")}
    if any(names.count(name) != 1 for name in (checksum_name, *archives.values())):
        raise UpdateError("Gitleaks release is missing unique Windows archives/checksums.")
    base = f"{RELEASES}/v{version}"
    checksums = download(f"{base}/{checksum_name}").decode("utf-8")
    hashes = {}
    for arch, name in archives.items():
        matches = re.findall(rf"^([0-9a-f]{{64}})  {re.escape(name)}\r?$", checksums, re.MULTILINE)
        if len(matches) != 1:
            raise UpdateError(f"Missing or ambiguous checksum for Windows {arch}.")
        if hashlib.sha256(download(f"{base}/{name}")).hexdigest() != matches[0]:
            raise UpdateError(f"Windows {arch} archive checksum mismatch; pins were not changed.")
        hashes[arch] = matches[0]
    return hashes


def update(path: Path) -> str | None:
    source = path.read_text(encoding="utf-8")
    nodes = assignments(source)
    current = ast.literal_eval(nodes["GITLEAKS_VERSION"].value)
    current_version = version_tuple(current)
    release = json.loads(download("https://api.github.com/repos/gitleaks/gitleaks/releases/latest"))
    if not isinstance(release, dict) or release.get("draft") is not False or release.get("prerelease") is not False:
        raise UpdateError("GitHub did not return a published stable Gitleaks release.")
    tag = release.get("tag_name")
    if not isinstance(tag, str) or not tag.startswith("v"):
        raise UpdateError("Unexpected Gitleaks release tag.")
    version = tag[1:]
    latest_version = version_tuple(version)
    if latest_version < current_version:
        raise UpdateError("Latest release is older than the repository pin; refusing a downgrade.")
    if latest_version == current_version:
        print(f"Gitleaks {current} is already the latest stable release.")
        return None
    hashes = release_hashes(version, release)
    replacements = {
        "GITLEAKS_VERSION": f'GITLEAKS_VERSION = "{version}"\n',
        "GITLEAKS_HASHES": (
            "GITLEAKS_HASHES = {\n"
            + "".join(f'    "{arch}": "{digest}",\n' for arch, digest in hashes.items())
            + "}\n"
        ),
    }
    lines = source.splitlines(keepends=True)
    for name, node in sorted(nodes.items(), key=lambda item: item[1].lineno, reverse=True):
        lines[node.lineno - 1:node.end_lineno] = [replacements[name]]
    updated = "".join(lines)
    ast.parse(updated)
    path.write_text(updated, encoding="utf-8", newline="\n")
    print(f"Updated Gitleaks pin from {current} to {version}; review and run CI before merging.")
    return version


def main() -> int:
    try:
        update(ROOT / "tools" / "security_check.py")
    except (UpdateError, OSError, ValueError, SyntaxError) as error:
        message = str(error) if isinstance(error, UpdateError) else "Malformed release data or local pin file; update failed."
        print(f"GITLEAKS UPDATE FAILED: {message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
