"""One Playwright instance per process, shared by the PDF renderer and the
application bot.

Playwright's sync API cannot run two instances in one thread, and launching a
browser costs about a second, so everything that needs a browser borrows it
from here: a throwaway headless Chromium for rendering PDFs, and a persistent
(logged-in) Chrome profile for applying.
"""

from __future__ import annotations

import atexit
import os
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Playwright, sync_playwright

_pw: Playwright | None = None
_headless: Browser | None = None
_persistent: BrowserContext | None = None


def playwright() -> Playwright:
    global _pw
    if _pw is None:
        _pw = sync_playwright().start()
        atexit.register(shutdown)
    return _pw


def headless_browser() -> Browser:
    """Headless Chromium for HTML -> PDF (always Playwright's bundled build)."""
    global _headless
    if _headless is None:
        _headless = playwright().chromium.launch(headless=True)
    return _headless


def persistent_context(profile_dir: Path, channel: str = "chrome", headless: bool = False,
                       slow_mo: int = 0) -> BrowserContext:
    """A browser whose cookies survive between runs, so you log in to LinkedIn /
    Naukri once by hand and the bot reuses that session. Your password is never
    seen or stored by this tool."""
    global _persistent
    if _persistent is None:
        profile_dir.mkdir(parents=True, exist_ok=True)
        kwargs = dict(user_data_dir=str(profile_dir), headless=headless, slow_mo=slow_mo,
                      viewport={"width": 1366, "height": 900}, accept_downloads=False)
        try:
            _persistent = playwright().chromium.launch_persistent_context(
                channel=None if channel == "chromium" else channel, **kwargs)
        except Exception:
            if channel == "chromium":
                raise
            # Google Chrome not installed -> fall back to Playwright's Chromium.
            _persistent = playwright().chromium.launch_persistent_context(**kwargs)
    return _persistent


def close_persistent() -> None:
    global _persistent
    if _persistent is not None:
        try:
            _persistent.close()
        except Exception:  # noqa: BLE001 - closing a dead browser is not an error
            pass
        _persistent = None


def shutdown() -> None:
    global _pw, _headless
    close_persistent()
    if _headless is not None:
        try:
            _headless.close()
        except Exception:  # noqa: BLE001
            pass
        _headless = None
    if _pw is not None:
        try:
            _pw.stop()
        except Exception:  # noqa: BLE001
            pass
        _pw = None


def notify(title: str, message: str) -> None:
    """Desktop notification + terminal bell, so a paused run gets noticed."""
    sys.stdout.write("\a")
    sys.stdout.flush()
    if sys.platform == "darwin" and not os.getenv("CI"):
        safe = message.replace('"', "'")[:200]
        subprocess.run(["osascript", "-e", f'display notification "{safe}" with title "{title}"'],
                       check=False, capture_output=True)
