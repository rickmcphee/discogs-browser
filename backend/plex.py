import re
from typing import Optional
import httpx
from rapidfuzz import fuzz
from logging_config import get_logger

log = get_logger("plex")

_SUFFIX_RE = re.compile(r"\s*\([^)]*\)\s*$")


def _base(base_url: str) -> str:
    return base_url if base_url.startswith(("http://", "https://")) else f"http://{base_url}"


def _headers(token: str) -> dict:
    return {"X-Plex-Token": token, "Accept": "application/json"}


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
    r = httpx.get(f"{_base(base_url)}/library/sections", headers=_headers(token))
    r.raise_for_status()
    for section in r.json()["MediaContainer"].get("Directory", []):
        if section.get("type") == "artist":
            return section["key"]
    return None


def fetch_albums(base_url: str, token: str, section_key: str) -> list:
    r = httpx.get(
        f"{_base(base_url)}/library/sections/{section_key}/all",
        params={"type": 9},
        headers=_headers(token),
    )
    r.raise_for_status()
    return [
        {
            "artist": item.get("parentTitle", ""),
            "title": item.get("title", ""),
            "rating_key": item["ratingKey"],
        }
        for item in r.json()["MediaContainer"].get("Metadata", [])
    ]


def get_machine_identifier(base_url: str, token: str) -> str:
    r = httpx.get(f"{_base(base_url)}/", headers=_headers(token))
    r.raise_for_status()
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
