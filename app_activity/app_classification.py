from __future__ import annotations


BROWSER_PRODUCT_NAMES = frozenset(
    {
        "arc",
        "brave",
        "brave browser",
        "firefox",
        "google chrome",
        "microsoft edge",
        "mozilla firefox",
        "opera",
        "opera gx",
        "vivaldi",
    }
)


def uses_title_details(app_name: str) -> bool:
    return app_name.strip().casefold() in BROWSER_PRODUCT_NAMES
