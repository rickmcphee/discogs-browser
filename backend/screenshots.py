import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import config
from logging_config import get_logger

log = get_logger("screenshots")
SCREENSHOTS_DIR = config.CONFIG_DIR / "screenshots"


def _safe(s: str, maxlen: int = 40) -> str:
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s-]+", "_", s).strip("_")
    return s[:maxlen].lower()


def new_session_dir() -> Path:
    d = SCREENSHOTS_DIR / datetime.now().strftime("%Y%m%d_%H%M%S")
    d.mkdir(parents=True, exist_ok=True)
    return d


class CrawlScreenshotter:
    """Auto-screenshots every page load for qualifying searches."""

    def __init__(self, page, session_dir: Path, interval: int = 1):
        self._page = page
        self._session_dir = session_dir
        self._interval = interval
        self._search_count = 0
        self._step = 0
        self._active = False
        self._subdir: Optional[Path] = None
        self._site_name = ""
        self._current_screenshots: list[str] = []
        self._all_entries: list[dict] = []
        # Keep a reference to the sync wrapper so remove_listener gets the same object
        self._listener = self._make_listener()

    def _make_listener(self):
        """Sync wrapper: Playwright's page.on() calls handlers synchronously.
        Async handlers must be scheduled explicitly or the coroutine is discarded."""
        def _sync():
            asyncio.ensure_future(self._on_load())
        return _sync

    def start_search(self, release_slug: str, site_name: str) -> bool:
        self._search_count += 1
        self._step = 0
        self._current_screenshots = []
        self._site_name = site_name

        is_first = self._search_count == 1
        self._active = self._interval > 0 and (
            is_first or self._search_count % self._interval == 0
        )

        if self._active:
            self._subdir = (
                self._session_dir
                / _safe(site_name)
                / _safe(release_slug)
            )
            self._subdir.mkdir(parents=True, exist_ok=True)

        return self._active

    def get_search_screenshots(self) -> list[str]:
        return list(self._current_screenshots)

    async def capture_now(self) -> Optional[str]:
        """Take an explicit screenshot at the current page state, regardless of load events."""
        if not self._active or not self._subdir:
            return None
        url = self._page.url
        if not url or url in ("about:blank", ""):
            return None
        self._step += 1
        path = self._subdir / f"{self._step:02d}.png"
        try:
            await self._page.screenshot(path=str(path), full_page=False)
            rel = str(path.relative_to(SCREENSHOTS_DIR))
            self._current_screenshots.append(rel)
            self._all_entries.append({"path": rel, "url": url})
            log.info("[%s] Step %d — %s  SCREENSHOT:%s", self._site_name, self._step, url, rel)
            return rel
        except Exception as e:
            log.warning("[%s] Screenshot failed at step %d: %s", self._site_name, self._step, e)
            return None

    async def _on_load(self):
        if not self._active or not self._subdir:
            return
        url = self._page.url
        if not url or url in ("about:blank", ""):
            return
        self._step += 1
        path = self._subdir / f"{self._step:02d}.png"
        try:
            await self._page.screenshot(path=str(path), full_page=False)
            rel = str(path.relative_to(SCREENSHOTS_DIR))
            self._current_screenshots.append(rel)
            self._all_entries.append({"path": rel, "url": url})
            # SCREENSHOT: marker is parsed by the Log Viewer to render a clickable image link
            log.info("[%s] Step %d — %s  SCREENSHOT:%s", self._site_name, self._step, url, rel)
        except Exception as e:
            log.warning("[%s] Screenshot failed at step %d: %s", self._site_name, self._step, e)

    def attach(self):
        self._page.on("load", self._listener)

    def detach(self):
        try:
            self._page.remove_listener("load", self._listener)
        except Exception:
            pass

    def save_manifest(self):
        if self._all_entries:
            manifest = self._session_dir / "manifest.json"
            manifest.write_text(json.dumps(self._all_entries, indent=2))
            log.info("Screenshots saved to %s (%d files)", self._session_dir, len(self._all_entries))


def list_sessions() -> list[dict]:
    if not SCREENSHOTS_DIR.exists():
        return []
    sessions = []
    for s in sorted(SCREENSHOTS_DIR.iterdir(), reverse=True):
        if not s.is_dir():
            continue
        manifest_path = s / "manifest.json"
        if manifest_path.exists():
            entries = json.loads(manifest_path.read_text())
        else:
            entries = [
                {"path": str(p.relative_to(SCREENSHOTS_DIR)), "url": ""}
                for p in sorted(s.rglob("*.png"))
            ]
        sessions.append({"session_id": s.name, "entries": entries})
    return sessions


def clear_screenshots():
    import shutil
    if SCREENSHOTS_DIR.exists():
        shutil.rmtree(SCREENSHOTS_DIR)
        log.info("Screenshots directory cleared")


def get_screenshot_path(rel_path: str) -> Optional[Path]:
    base = SCREENSHOTS_DIR.resolve()
    path = (base / rel_path).resolve()
    if not path.is_relative_to(base):
        return None
    if path.exists() and path.suffix in (".png", ".jpg", ".pdf"):
        return path
    return None
