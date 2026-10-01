from __future__ import annotations

import binascii
import struct
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "assets" / "app_activity.ico"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def main() -> None:
    images = [_png(size) for size in SIZES]
    header_size = 6 + 16 * len(images)
    offset = header_size
    entries = []
    for size, image in zip(SIZES, images):
        dimension = 0 if size == 256 else size
        entries.append(
            struct.pack(
                "<BBBBHHII",
                dimension,
                dimension,
                0,
                0,
                1,
                32,
                len(image),
                offset,
            )
        )
        offset += len(image)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes(
        struct.pack("<HHH", 0, 1, len(images))
        + b"".join(entries)
        + b"".join(images)
    )
    print(f"Wrote {OUTPUT}")


def _png(size: int) -> bytes:
    pixels = bytearray()
    radius = max(2, size // 7)
    bars = (
        (0.18, 0.61, 0.31, 0.78),
        (0.39, 0.43, 0.52, 0.78),
        (0.61, 0.25, 0.74, 0.78),
    )
    for y in range(size):
        pixels.append(0)
        for x in range(size):
            if _outside_rounded_square(x, y, size, radius):
                color = (0, 0, 0, 0)
            else:
                color = (21, 29, 43, 255)
                for left, top, right, bottom in bars:
                    if (
                        left * size <= x < right * size
                        and top * size <= y < bottom * size
                    ):
                        color = (114, 167, 255, 255)
                if (
                    0.14 * size <= x < 0.86 * size
                    and 0.82 * size <= y < 0.88 * size
                ):
                    color = (145, 160, 184, 255)
            pixels.extend(color)

    payload = zlib.compress(bytes(pixels), level=9)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(
            b"IHDR",
            struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0),
        )
        + _chunk(b"IDAT", payload)
        + _chunk(b"IEND", b"")
    )


def _outside_rounded_square(x: int, y: int, size: int, radius: int) -> bool:
    corners = (
        (radius, radius),
        (size - radius - 1, radius),
        (radius, size - radius - 1),
        (size - radius - 1, size - radius - 1),
    )
    if radius <= x < size - radius or radius <= y < size - radius:
        return False
    center_x, center_y = min(
        corners, key=lambda point: (x - point[0]) ** 2 + (y - point[1]) ** 2
    )
    return (x - center_x) ** 2 + (y - center_y) ** 2 > radius**2


def _chunk(kind: bytes, data: bytes) -> bytes:
    checksum = binascii.crc32(kind + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)


if __name__ == "__main__":
    main()
