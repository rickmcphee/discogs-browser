import json
from unittest.mock import MagicMock


def _client_returning(text):
    """A fake Anthropic client whose messages.create() returns a canned text
    response, without depending on the SDK's own HTTP transport (httpx vs.
    httpx2 -- see recommendations.py's git history for why this matters:
    mocking at the HTTP layer via respx silently stopped intercepting
    anything the moment the anthropic SDK's transport dependency changed,
    since respx only patches httpx). judge_batch() only ever touches
    response.content[0].text, so that's all this fake needs to provide."""
    client = MagicMock()
    client.messages.create.return_value = MagicMock(content=[MagicMock(text=text)])
    return client


def _client_raising(exc):
    client = MagicMock()
    client.messages.create.side_effect = exc
    return client


def test_system_prompt_loads_from_recommendations_prompt_md():
    from pathlib import Path
    import recommendations
    prompt_file = Path(recommendations.__file__).parent / "recommendations_prompt.md"
    assert prompt_file.exists()
    assert recommendations.SYSTEM_PROMPT == prompt_file.read_text().strip()


def test_build_batch_content_includes_taste_listing_and_items():
    from recommendations import build_batch_content
    blocks = build_batch_content(
        ["Rob Zombie - Hellbilly Deluxe"],
        [{"item_key": "k1", "artist": "NAILS", "title": "T1"}],
    )
    assert "Rob Zombie - Hellbilly Deluxe" in blocks[0]["text"]
    assert "k1" in blocks[1]["text"]
    assert "NAILS" in blocks[1]["text"]


def test_build_batch_content_handles_empty_taste_listing():
    from recommendations import build_batch_content
    blocks = build_batch_content([], [{"item_key": "k1", "artist": "NAILS", "title": "T1"}])
    assert "empty" in blocks[0]["text"].lower()


def test_build_batch_content_caches_taste_block_not_items_block():
    from recommendations import build_batch_content
    blocks = build_batch_content(
        ["Rob Zombie - Hellbilly Deluxe"],
        [{"item_key": "k1", "artist": "NAILS", "title": "T1"}],
    )
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in blocks[1]


def test_judge_batch_sends_cache_control_on_system_and_taste_listing():
    from recommendations import judge_batch
    client = _client_returning(json.dumps([{"item_key": "k1", "recommended": True, "reason": "r"}]))
    judge_batch(client, ["Foo - Bar"], [{"item_key": "k1", "artist": "NAILS", "title": "T1"}])

    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    content = kwargs["messages"][0]["content"]
    assert content[0]["cache_control"] == {"type": "ephemeral"}
    assert "Foo - Bar" in content[0]["text"]
    assert "cache_control" not in content[1]
    assert "k1" in content[1]["text"]


def test_judge_batch_parses_wellformed_response():
    from recommendations import judge_batch, MODEL
    client = _client_returning(json.dumps([{"item_key": "k1", "recommended": True, "reason": "similar genre"}]))
    results = judge_batch(client, ["Foo - Bar"], [{"item_key": "k1", "artist": "NAILS", "title": "T1"}])
    assert results == [{"item_key": "k1", "recommended": True, "reason": "similar genre"}]
    assert client.messages.create.call_args.kwargs["model"] == MODEL


def test_judge_batch_strips_markdown_fences():
    from recommendations import judge_batch
    body = "```json\n" + json.dumps([{"item_key": "k1", "recommended": False, "reason": "no overlap"}]) + "\n```"
    client = _client_returning(body)
    results = judge_batch(client, [], [{"item_key": "k1", "artist": "NAILS", "title": "T1"}])
    assert results == [{"item_key": "k1", "recommended": False, "reason": "no overlap"}]


def test_judge_batch_returns_empty_on_malformed_json():
    from recommendations import judge_batch
    client = _client_returning("not json")
    results = judge_batch(client, [], [{"item_key": "k1", "artist": "NAILS", "title": "T1"}])
    assert results == []


def test_judge_batch_returns_empty_on_api_error():
    from recommendations import judge_batch
    client = _client_raising(RuntimeError("boom"))
    results = judge_batch(client, [], [{"item_key": "k1", "artist": "NAILS", "title": "T1"}])
    assert results == []


def test_judge_batch_skips_entries_missing_required_fields():
    from recommendations import judge_batch
    client = _client_returning(json.dumps([
        {"item_key": "k1"},
        {"item_key": "k2", "recommended": True, "reason": "ok"},
    ]))
    results = judge_batch(client, [], [
        {"item_key": "k1", "artist": "A", "title": "T1"},
        {"item_key": "k2", "artist": "B", "title": "T2"},
    ])
    assert results == [{"item_key": "k2", "recommended": True, "reason": "ok"}]
