import json
from pathlib import Path
from logging_config import get_logger

log = get_logger("recommendations")

MODEL = "claude-haiku-4-5"
BATCH_SIZE = 40
SYNC_CAP = 300
MAX_TOKENS = 4096

SYSTEM_PROMPT = (Path(__file__).parent / "recommendations_prompt.md").read_text().strip()


def build_batch_content(taste_listing: list[str], batch: list[dict]) -> list[dict]:
    """User-turn content split so the taste listing (identical across every batch
    in a judgment run) is cached separately from the per-batch items list.

    Items travel under a 1-based ordinal rather than their `item_key`: the key is
    a SHA-256 hexdigest the model cannot use for anything but copying back, and
    hex tokenizes badly enough that the round trip was a large share of a run's
    bill -- output bills well above input, so the echo was the expensive half."""
    taste_text = "\n".join(taste_listing) if taste_listing else "(empty — no collection or wishlist yet)"
    # ensure_ascii=False, or this saving reverses itself on the catalog's
    # international half: the default turns "Björk" into a \uXXXX escape and a
    # Japanese name into nothing but escapes, and those sequences tokenize far
    # worse than the characters they replace.
    items_text = "\n".join(
        json.dumps({"n": n, "artist": item["artist"], "title": item["title"]}, ensure_ascii=False)
        for n, item in enumerate(batch, start=1)
    )
    return [
        {
            "type": "text",
            "text": f"Collector's collection and wishlist:\n{taste_text}",
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": f"\n\nItems to judge:\n{items_text}",
        },
    ]


def _log_usage(response, label: str):
    """One line per batch naming what it cost. This is the only record of it --
    nothing else in the backend reads `response.usage` -- and it is what makes
    the caching above verifiable: past the first batch of a run,
    cache_read_input_tokens should dominate input_tokens, and
    cache_creation_input_tokens should be one batch's worth, not every batch's.

    `label` names whose run this was. Judgment runs are per-user and several
    can be in flight at once (`CrawlManager._judgment_tasks`), each on that
    user's own Anthropic key, so unlabelled counters interleave into a stream
    nobody can attribute -- and attributing them is the whole point."""
    usage = getattr(response, "usage", None)
    log.info(
        "Batch usage for %s: input=%s cache_write=%s cache_read=%s output=%s",
        label,
        getattr(usage, "input_tokens", None),
        getattr(usage, "cache_creation_input_tokens", None),
        getattr(usage, "cache_read_input_tokens", None),
        getattr(usage, "output_tokens", None),
    )


def _resolve_entries(parsed, batch: list[dict], label: str) -> list[dict]:
    """Map each response entry's ordinal back to its item_key.

    The prompt has always required one entry per item in the same order, so the
    run already depended on ordering -- an ordinal only makes that dependency
    checkable. It was not, while items were addressed by digest:
    `stock_item_judgments` has no foreign key on `item_key`, so a key the model
    mistyped or invented was written as a judgment against a row that need not
    exist, while the real item stayed unjudged and was paid for again next run.

    `label` names the run for the same reason the usage line carries it: these
    warnings are the record of a model misbehaving on one user's batch, and
    concurrent runs interleave them into a single stream."""
    results = []
    seen = set()
    for entry in parsed:
        if not isinstance(entry, dict) or "recommended" not in entry:
            continue
        n = entry.get("n")
        # bool is an int in Python, and `true` is a plausible thing for a model
        # to emit into a numeric field; it must not index into the batch.
        if isinstance(n, bool) or not isinstance(n, int):
            log.warning("Dropping %s judgment entry with non-integer n: %r", label, n)
            continue
        if not 1 <= n <= len(batch):
            log.warning(
                "Dropping %s judgment entry with out-of-range n: %r (batch of %d)", label, n, len(batch)
            )
            continue
        if n in seen:
            log.warning("Dropping duplicate %s judgment entry for n=%d", label, n)
            continue
        seen.add(n)
        results.append({
            "item_key": batch[n - 1]["item_key"],
            "recommended": bool(entry["recommended"]),
            "reason": entry.get("reason"),
        })
    return results


def judge_batch(client, taste_listing: list[str], batch: list[dict], label: str = "unknown user") -> list[dict]:
    """One Claude call judging a batch of items. Returns [] on any failure —
    caller leaves those items unjudged for retry on the next sync."""
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": build_batch_content(taste_listing, batch)}],
        )
        _log_usage(response, label)
        # max_tokens is a ceiling the model never sees, so hitting it truncates
        # the array mid-entry and surfaces as an opaque JSON parse error. Name it.
        if getattr(response, "stop_reason", None) == "max_tokens":
            log.warning(
                "Judgment batch for %s hit the %d-token output cap and was truncated; "
                "its %d items stay unjudged and retry on the next run",
                label, MAX_TOKENS, len(batch),
            )
            return []
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = "\n".join(line for line in text.splitlines() if not line.startswith("```")).strip()
        return _resolve_entries(json.loads(text), batch, label)
    except Exception as e:
        log.error("Judgment batch for %s failed: %s", label, e, exc_info=True)
        return []
