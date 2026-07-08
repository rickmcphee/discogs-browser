import json
from pathlib import Path
from logging_config import get_logger

log = get_logger("recommendations")

MODEL = "claude-haiku-4-5"
BATCH_SIZE = 40
SYNC_CAP = 300

SYSTEM_PROMPT = (Path(__file__).parent / "recommendations_prompt.md").read_text().strip()


def build_batch_content(taste_listing: list[str], batch: list[dict]) -> list[dict]:
    """User-turn content split so the taste listing (identical across every batch
    in a judgment run) is cached separately from the per-batch items list."""
    taste_text = "\n".join(taste_listing) if taste_listing else "(empty — no collection or wishlist yet)"
    items_text = "\n".join(
        f'{{"item_key": "{item["item_key"]}", "artist": "{item["artist"]}", "title": "{item["title"]}"}}'
        for item in batch
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


def judge_batch(client, taste_listing: list[str], batch: list[dict]) -> list[dict]:
    """One Claude call judging a batch of items. Returns [] on any failure —
    caller leaves those items unjudged for retry on the next sync."""
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": build_batch_content(taste_listing, batch)}],
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
