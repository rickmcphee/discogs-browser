import json
import shutil
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from crawler import BROWSER_STATE_FILE, CHROME_PROFILE_DIR
from config import HEADLESS_AUTH
from logging_config import get_logger

router = APIRouter()
log = get_logger("auth")

_login_state: dict = {
    "active": False,
    "active_site": None,
}

_REAL_CHROME_DEFAULT = Path.home() / "Library" / "Application Support" / "Google" / "Chrome" / "Default"


@router.get("/auth/status")
def get_auth_status():
    mtime = None
    if BROWSER_STATE_FILE.exists():
        mtime = BROWSER_STATE_FILE.stat().st_mtime
    return {
        "active": _login_state["active"],
        "active_site": _login_state["active_site"],
        "has_state": BROWSER_STATE_FILE.exists(),
        "state_mtime": mtime,
    }


class LoginRequest(BaseModel):
    site_name: str
    login_url: str


@router.post("/auth/login")
def start_login(body: LoginRequest):
    if HEADLESS_AUTH:
        raise HTTPException(status_code=501, detail="Browser login not available in headless mode")
    if _login_state["active"]:
        raise HTTPException(status_code=409, detail=f"Login session already active for {_login_state['active_site']}")

    subprocess.Popen(["open", "-a", "Google Chrome", body.login_url])
    _login_state["active"] = True
    _login_state["active_site"] = body.site_name
    log.info("Opened login URL in Chrome for %s: %s", body.site_name, body.login_url)
    return {"ok": True}


@router.post("/auth/done")
def finish_login():
    if not _login_state["active"]:
        raise HTTPException(status_code=409, detail="No active login session")

    site = _login_state["active_site"]

    # Copy Chrome's cookie files from real profile into managed profile dir.
    # Chrome uses the macOS keychain for cookie encryption, so both instances
    # share the same key — the managed profile can decrypt cookies written by real Chrome.
    managed_default = CHROME_PROFILE_DIR / "Default"
    managed_default.mkdir(parents=True, exist_ok=True)

    copied = []
    for fname in ("Cookies", "Cookies-journal"):
        src = _REAL_CHROME_DEFAULT / fname
        dst = managed_default / fname
        if src.exists():
            try:
                shutil.copy2(src, dst)
                copied.append(fname)
            except Exception as e:
                log.warning("Could not copy %s: %s", fname, e)

    # Local State lives in the user-data-dir root, not Default/
    local_state_src = _REAL_CHROME_DEFAULT.parent / "Local State"
    local_state_dst = CHROME_PROFILE_DIR / "Local State"
    if local_state_src.exists():
        try:
            shutil.copy2(local_state_src, local_state_dst)
            copied.append("Local State")
        except Exception as e:
            log.warning("Could not copy Local State: %s", e)

    # Write a marker so the UI knows a session has been saved
    BROWSER_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    BROWSER_STATE_FILE.write_text(json.dumps({"cookies": [], "origins": []}))

    log.info("Session saved for %s (copied: %s)", site, ", ".join(copied))

    _login_state["active"] = False
    _login_state["active_site"] = None
    return {"ok": True}


@router.delete("/auth/state")
def clear_auth_state():
    if BROWSER_STATE_FILE.exists():
        BROWSER_STATE_FILE.unlink()
        log.info("Cleared browser session state")
    return {"ok": True}
