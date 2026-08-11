import os
import subprocess

_DATE_CMD = ["git", "log", "-1", "--format=%cd", "--date=format:%Y.%m.%d"]
_SHA_CMD = ["git", "rev-parse", "--short", "HEAD"]


def _git_version():
    """Commit date + short SHA, or None when git can't answer.

    Only reached in local development: the deployed image carries no .git and
    always has APP_VERSION baked in. The timeout is a boot-safety measure, not
    a performance one -- this runs at import, so a hung git would hang startup.
    """
    try:
        date = subprocess.run(_DATE_CMD, capture_output=True, text=True,
                              timeout=5, check=True).stdout.strip()
        sha = subprocess.run(_SHA_CMD, capture_output=True, text=True,
                             timeout=5, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if not date or not sha:
        return None
    return f"{date}+{sha}"


# main.py imports this at module scope, so a raise here would break app
# startup -- every failure path lands on "dev" instead.
VERSION = os.environ.get("APP_VERSION") or _git_version() or "dev"
