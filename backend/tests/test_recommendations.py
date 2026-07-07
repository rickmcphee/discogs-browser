import json
import respx
import httpx
import anthropic


_MESSAGES_URL = "https://api.anthropic.com/v1/messages"


def _mock_text_response(mock, model, text):
    mock.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={
        "id": "msg_1", "type": "message", "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "model": model, "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 10},
    }))


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


@respx.mock
def test_judge_batch_sends_cache_control_on_system_and_taste_listing():
    from recommendations import judge_batch, MODEL
    client = anthropic.Anthropic(api_key="test-key", max_retries=0)
    route = respx.post(_MESSAGES_URL)
    route.mock(return_value=httpx.Response(200, json={
        "id": "msg_1", "type": "message", "role": "assistant",
        "content": [{"type": "text", "text": json.dumps([{"item_key": "k1", "recommended": True, "reason": "r"}])}],
        "model": MODEL, "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 10},
    }))
    judge_batch(client, ["Foo - Bar"], [{"item_key": "k1", "artist": "NAILS", "title": "T1"}])

    body = json.loads(route.calls.last.request.content)
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    content = body["messages"][0]["content"]
    assert content[0]["cache_control"] == {"type": "ephemeral"}
    assert "Foo - Bar" in content[0]["text"]
    assert "cache_control" not in content[1]
    assert "k1" in content[1]["text"]


@respx.mock
def test_judge_batch_parses_wellformed_response():
    from recommendations import judge_batch, MODEL
    client = anthropic.Anthropic(api_key="test-key", max_retries=0)
    _mock_text_response(respx, MODEL, json.dumps([{"item_key": "k1", "recommended": True, "reason": "similar genre"}]))
    results = judge_batch(client, ["Foo - Bar"], [{"item_key": "k1", "artist": "NAILS", "title": "T1"}])
    assert results == [{"item_key": "k1", "recommended": True, "reason": "similar genre"}]


@respx.mock
def test_judge_batch_strips_markdown_fences():
    from recommendations import judge_batch, MODEL
    client = anthropic.Anthropic(api_key="test-key", max_retries=0)
    body = "```json\n" + json.dumps([{"item_key": "k1", "recommended": False, "reason": "no overlap"}]) + "\n```"
    _mock_text_response(respx, MODEL, body)
    results = judge_batch(client, [], [{"item_key": "k1", "artist": "NAILS", "title": "T1"}])
    assert results == [{"item_key": "k1", "recommended": False, "reason": "no overlap"}]


@respx.mock
def test_judge_batch_returns_empty_on_malformed_json():
    from recommendations import judge_batch, MODEL
    client = anthropic.Anthropic(api_key="test-key", max_retries=0)
    _mock_text_response(respx, MODEL, "not json")
    results = judge_batch(client, [], [{"item_key": "k1", "artist": "NAILS", "title": "T1"}])
    assert results == []


@respx.mock
def test_judge_batch_returns_empty_on_api_error():
    from recommendations import judge_batch
    client = anthropic.Anthropic(api_key="test-key", max_retries=0)
    respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(500, json={
        "type": "error", "error": {"type": "api_error", "message": "boom"},
    }))
    results = judge_batch(client, [], [{"item_key": "k1", "artist": "NAILS", "title": "T1"}])
    assert results == []


@respx.mock
def test_judge_batch_skips_entries_missing_required_fields():
    from recommendations import judge_batch, MODEL
    client = anthropic.Anthropic(api_key="test-key", max_retries=0)
    _mock_text_response(respx, MODEL, json.dumps([
        {"item_key": "k1"},
        {"item_key": "k2", "recommended": True, "reason": "ok"},
    ]))
    results = judge_batch(client, [], [
        {"item_key": "k1", "artist": "A", "title": "T1"},
        {"item_key": "k2", "artist": "B", "title": "T2"},
    ])
    assert results == [{"item_key": "k2", "recommended": True, "reason": "ok"}]
