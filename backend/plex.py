import re
from typing import Optional
import httpx
from rapidfuzz import fuzz

from plex_security import PlexUnsafeAddressError, validate_address

_SUFFIX_RE = re.compile(r"\s*\([^)]*\)\s*$")

# httpx's default timeout is 5s; fetching a whole library's metadata over LAN
# routinely takes longer than that.
_TIMEOUT = 60.0


def _base(base_url: str) -> str:
    base_url = base_url.rstrip("/")
    return base_url if base_url.startswith(("http://", "https://")) else f"http://{base_url}"


def _headers(token: str) -> dict:
    return {"X-Plex-Token": token, "Accept": "application/json"}


def _get(base_url: str, path: str, token: str, params: Optional[dict] = None) -> httpx.Response:
    validate_address(base_url)
    r = httpx.get(
        f"{_base(base_url)}{path}", params=params, headers=_headers(token),
        timeout=_TIMEOUT, follow_redirects=False,
    )
    if r.is_redirect:
        raise PlexUnsafeAddressError(f"Unexpected redirect from Plex server: {r.status_code}")
    r.raise_for_status()
    return r


def normalize(value: str) -> str:
    result = value.strip().lower()
    while True:
        stripped = _SUFFIX_RE.sub("", result).strip()
        if stripped == result:
            break
        result = stripped
    if result.startswith("the "):
        result = result[4:]
    return result.strip()


def get_music_section_key(base_url: str, token: str) -> Optional[str]:
    r = _get(base_url, "/library/sections", token)
    for section in r.json()["MediaContainer"].get("Directory", []):
        if section.get("type") == "artist":
            return section["key"]
    return None


def fetch_albums(base_url: str, token: str, section_key: str) -> list:
    r = _get(base_url, f"/library/sections/{section_key}/all", token, params={"type": 9})
    return [
        {
            "artist": item.get("parentTitle", ""),
            "title": item.get("title", ""),
            "rating_key": item["ratingKey"],
        }
        for item in r.json()["MediaContainer"].get("Metadata", [])
    ]


def get_machine_identifier(base_url: str, token: str) -> str:
    r = _get(base_url, "/", token)
    return r.json()["MediaContainer"]["machineIdentifier"]


def build_album_url(base_url: str, machine_identifier: str, rating_key) -> str:
    return (
        f"{_base(base_url)}/web/index.html#!/server/{machine_identifier}"
        f"/details?key=/library/metadata/{rating_key}"
    )


def find_best_match(artist: str, title: str, albums: list, threshold: int) -> Optional[dict]:
    if not albums:
        return None
    target = f"{normalize(artist)} {normalize(title)}"
    best = None
    best_score = -1.0
    for album in albums:
        candidate = f"{normalize(album['artist'])} {normalize(album['title'])}"
        score = fuzz.WRatio(target, candidate)
        if score > best_score:
            best_score = score
            best = album
    if best is not None and best_score >= threshold:
        return best
    return None
