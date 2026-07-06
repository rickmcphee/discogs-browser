import json
from logging_config import get_logger

log = get_logger("recommendations")

MODEL = "claude-haiku-4-5"
BATCH_SIZE = 40
SYNC_CAP = 300

SYSTEM_PROMPT = (
    "You are helping a vinyl record collector find new records they might like, "
    "based on their existing collection and wishlist.\n\n"
    "You will be given the collector's full collection/wishlist as a list of "
    "\"Artist - Title\" lines, followed by a batch of in-stock catalog items to judge.\n\n"
    "For each item, decide whether it's a good recommendation given the collector's "
    "taste (same genre/scene, related artists, similar labels, adjacent style — not "
    "just exact artist matches). Respond with a JSON array only, no other text, one "
    "entry per item in the same order:\n\n"
    "[{\"item_key\": \"<key>\", \"recommended\": true|false, \"reason\": \"<one short sentence>\"}]"
)


def build_batch_prompt(taste_listing: list[str], batch: list[dict]) -> str:
    taste_text = "\n".join(taste_listing) if taste_listing else "(empty — no collection or wishlist yet)"
    items_text = "\n".join(
        f'{{"item_key": "{item["item_key"]}", "artist": "{item["artist"]}", "title": "{item["title"]}"}}'
        for item in batch
    )
    return f"Collector's collection and wishlist:\n{taste_text}\n\nItems to judge:\n{items_text}"


def judge_batch(client, taste_listing: list[str], batch: list[dict]) -> list[dict]:
    """One Claude call judging a batch of items. Returns [] on any failure —
    caller leaves those items unjudged for retry on the next sync."""
    prompt = build_batch_prompt(taste_listing, batch)
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = "\n".join(line for line in text.splitlines() if not line.startswith("```")).strip()
        parsed = json.loads(text)
        return [
            {"item_key": entry["item_key"], "recommended": bool(entry["recommended"]), "reason": entry.get("reason")}
            for entry in parsed
            if "item_key" in entry and "recommended" in entry
        ]
    except Exception as e:
        log.error("Judgment batch failed: %s", e, exc_info=True)
        return []
