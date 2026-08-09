import csv
import io
import re
from datetime import datetime, timezone
from typing import Optional

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_ROWS = 100_000
MAX_REPORTED_ERRORS = 10
REQUIRED_COLUMNS = ("item_key", "recommended", "judged_at")

_ITEM_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
_TRUE = {"true", "t", "yes", "1"}
_FALSE = {"false", "f", "no", "0"}


class InvalidImportError(Exception):
    """The file is unusable as a whole -- bad header, or too many rows."""


def _parse_recommended(raw: str) -> bool:
    value = (raw or "").strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValueError(f"recommended must be true or false, got {raw!r}")


def _parse_judged_at(raw: str) -> datetime:
    value = (raw or "").strip()
    if not value:
        raise ValueError("judged_at is required")
    # datetime.fromisoformat only accepts a trailing 'Z' from 3.11 on, and
    # this repo's floor is 3.9, so rewrite it. An offset-aware result is
    # converted to UTC and stripped to naive, matching the column's
    # TIMESTAMP (no time zone) type; a naive value is taken as UTC as-is.
    if value.endswith(("Z", "z")):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"judged_at is not an ISO-8601 timestamp: {raw!r}")
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def parse_judgment_csv(text: str) -> tuple[list[dict], list[dict], int]:
    """Parse an exported judgment ledger.

    Returns (judgments, errors, skipped). Best-effort per row: an unparseable
    row is skipped and counted, the rest still parse. Raises
    InvalidImportError only for whole-file problems, where a per-row error
    list would be one entry long per row and tell the user nothing.
    """
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if not reader.fieldnames:
        raise InvalidImportError("File is empty or has no header row.")
    missing = [c for c in REQUIRED_COLUMNS if c not in reader.fieldnames]
    if missing:
        raise InvalidImportError(f"Missing required column(s): {', '.join(missing)}.")

    by_key: dict[str, dict] = {}
    errors: list[dict] = []
    skipped = 0
    seen = 0

    def _fail(line: int, message: str):
        nonlocal skipped
        skipped += 1
        if len(errors) < MAX_REPORTED_ERRORS:
            errors.append({"line": line, "error": message})

    for row in reader:
        seen += 1
        if seen > MAX_ROWS:
            raise InvalidImportError(f"File has more than {MAX_ROWS} rows.")
        line = reader.line_num
        item_key = (row.get("item_key") or "").strip()
        if not _ITEM_KEY_RE.match(item_key):
            _fail(line, "item_key must be 64 lowercase hex characters")
            continue
        try:
            recommended = _parse_recommended(row.get("recommended"))
            judged_at = _parse_judged_at(row.get("judged_at"))
        except ValueError as e:
            _fail(line, str(e))
            continue
        reason: Optional[str] = (row.get("reason") or "").strip() or None
        candidate = {
            "item_key": item_key,
            "recommended": recommended,
            "reason": reason,
            "judged_at": judged_at,
        }
        # Postgres raises "ON CONFLICT DO UPDATE command cannot affect row a
        # second time" if one statement presents the same conflict target
        # twice, so a duplicated key has to be collapsed here rather than
        # left for the upsert -- otherwise one duplicate fails the whole
        # import. Newest judged_at wins, matching the upsert's own rule.
        existing = by_key.get(item_key)
        if existing is None:
            by_key[item_key] = candidate
            continue
        _fail(line, f"duplicate item_key {item_key}, keeping the newest judged_at")
        if candidate["judged_at"] > existing["judged_at"]:
            by_key[item_key] = candidate

    return list(by_key.values()), errors, skipped
