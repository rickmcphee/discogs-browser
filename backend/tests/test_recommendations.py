import json
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock


def _response(text, stop_reason="end_turn", usage="default"):
    """A stand-in for the SDK's Message, built from SimpleNamespace so a test can
    control which attributes actually exist -- `usage=None` has to produce an
    object with no `usage` at all, which a MagicMock can never do."""
    if usage == "default":
        usage = SimpleNamespace(
            input_tokens=100,
            cache_creation_input_tokens=200,
            cache_read_input_tokens=300,
            output_tokens=400,
        )
    fields = {"content": [SimpleNamespace(text=text)], "stop_reason": stop_reason}
    if usage is not None:
        fields["usage"] = usage
    return SimpleNamespace(**fields)


def _client_returning(text, **kwargs):
    """A fake Anthropic client whose messages.create() returns a canned
    response, without depending on the SDK's own HTTP transport (httpx vs.
    httpx2 -- see recommendations.py's git history for why this matters:
    mocking at the HTTP layer via respx silently stopped intercepting
    anything the moment the anthropic SDK's transport dependency changed,
    since respx only patches httpx). judge_batch() reads content[0].text,
    stop_reason and usage, so that's what the response carries; the client
    itself stays a MagicMock so call_args is still inspectable."""
    client = MagicMock()
    client.messages.create.return_value = _response(text, **kwargs)
    return client


def _client_raising(exc):
    client = MagicMock()
    client.messages.create.side_effect = exc
    return client


def _items(*keys):
    return [{"item_key": k, "artist": f"Artist {k}", "title": f"Title {k}"} for k in keys]


def _item_lines(block):
    """The items block opens with an "Items to judge:" header, so only the
    lines that are actually items are JSON."""
    return [json.loads(line) for line in block["text"].splitlines() if line.startswith("{")]


def test_system_prompt_loads_from_recommendations_prompt_md():
    from pathlib import Path
    import recommendations
    prompt_file = Path(recommendations.__file__).parent / "recommendations_prompt.md"
    assert prompt_file.exists()
    assert recommendations.SYSTEM_PROMPT == prompt_file.read_text().strip()


def test_build_batch_content_includes_taste_listing_and_items():
    from recommendations import build_batch_content
    blocks = build_batch_content(["Rob Zombie - Hellbilly Deluxe"], _items("k1"))
    assert "Rob Zombie - Hellbilly Deluxe" in blocks[0]["text"]
    assert "Artist k1" in blocks[1]["text"]


def test_build_batch_content_handles_empty_taste_listing():
    from recommendations import build_batch_content
    blocks = build_batch_content([], _items("k1"))
    assert "empty" in blocks[0]["text"].lower()


def test_build_batch_content_caches_taste_block_not_items_block():
    from recommendations import build_batch_content
    blocks = build_batch_content(["Rob Zombie - Hellbilly Deluxe"], _items("k1"))
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in blocks[1]


def test_build_batch_content_numbers_items_from_one():
    from recommendations import build_batch_content
    blocks = build_batch_content([], _items("k1", "k2", "k3"))
    assert [item["n"] for item in _item_lines(blocks[1])] == [1, 2, 3]


def test_build_batch_content_omits_the_item_key():
    """The whole point of the ordinal: a SHA-256 digest the model can only copy
    back costs real money in both directions, output side worst."""
    from recommendations import build_batch_content
    digest = "a" * 64
    blocks = build_batch_content([], [{"item_key": digest, "artist": "NAILS", "title": "Unsilent Death"}])
    assert digest not in blocks[1]["text"]
    assert "item_key" not in blocks[1]["text"]


def test_build_batch_content_escapes_quotes_in_artist_and_title():
    """The previous f-string rendering interpolated these straight into a
    JSON-shaped string, so a quote produced malformed JSON in the prompt."""
    from recommendations import build_batch_content
    blocks = build_batch_content([], [{"item_key": "k1", "artist": 'The "Band"', "title": 'A "Record"'}])
    assert _item_lines(blocks[1]) == [{"n": 1, "artist": 'The "Band"', "title": 'A "Record"'}]


def test_judge_batch_sends_cache_control_on_system_and_taste_listing():
    from recommendations import judge_batch
    client = _client_returning(json.dumps([{"n": 1, "recommended": True, "reason": "r"}]))
    judge_batch(client, ["Foo - Bar"], _items("k1"))

    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    content = kwargs["messages"][0]["content"]
    assert content[0]["cache_control"] == {"type": "ephemeral"}
    assert "Foo - Bar" in content[0]["text"]
    assert "cache_control" not in content[1]
    assert "Artist k1" in content[1]["text"]


def test_judge_batch_parses_wellformed_response():
    from recommendations import judge_batch, MODEL
    client = _client_returning(json.dumps([{"n": 1, "recommended": True, "reason": "similar genre"}]))
    results = judge_batch(client, ["Foo - Bar"], _items("k1"))
    assert results == [{"item_key": "k1", "recommended": True, "reason": "similar genre"}]
    assert client.messages.create.call_args.kwargs["model"] == MODEL


def test_judge_batch_maps_ordinals_back_to_item_keys_out_of_order():
    """Ordinal, not position in the response -- a reordered array must still
    land each judgment on the item it was made about."""
    from recommendations import judge_batch
    client = _client_returning(json.dumps([
        {"n": 3, "recommended": True, "reason": "third"},
        {"n": 1, "recommended": False, "reason": None},
    ]))
    results = judge_batch(client, [], _items("k1", "k2", "k3"))
    assert results == [
        {"item_key": "k3", "recommended": True, "reason": "third"},
        {"item_key": "k1", "recommended": False, "reason": None},
    ]


def test_judge_batch_strips_markdown_fences():
    from recommendations import judge_batch
    body = "```json\n" + json.dumps([{"n": 1, "recommended": False, "reason": None}]) + "\n```"
    client = _client_returning(body)
    results = judge_batch(client, [], _items("k1"))
    assert results == [{"item_key": "k1", "recommended": False, "reason": None}]


def test_judge_batch_returns_empty_on_malformed_json():
    from recommendations import judge_batch
    client = _client_returning("not json")
    assert judge_batch(client, [], _items("k1")) == []


def test_judge_batch_returns_empty_on_api_error():
    from recommendations import judge_batch
    client = _client_raising(RuntimeError("boom"))
    assert judge_batch(client, [], _items("k1")) == []


def test_judge_batch_skips_entries_missing_required_fields():
    from recommendations import judge_batch
    client = _client_returning(json.dumps([
        {"n": 1},
        {"recommended": True, "reason": "no n"},
        {"n": 2, "recommended": True, "reason": "ok"},
    ]))
    results = judge_batch(client, [], _items("k1", "k2"))
    assert results == [{"item_key": "k2", "recommended": True, "reason": "ok"}]


def test_judge_batch_drops_out_of_range_ordinals():
    """Previously unrepresentable: an invented item_key was written as a
    judgment against a row that need not exist (no FK on the column), while
    the real item stayed unjudged and was paid for again next run."""
    from recommendations import judge_batch
    client = _client_returning(json.dumps([
        {"n": 0, "recommended": True, "reason": "too low"},
        {"n": 3, "recommended": True, "reason": "too high"},
        {"n": 2, "recommended": True, "reason": "ok"},
    ]))
    results = judge_batch(client, [], _items("k1", "k2"))
    assert results == [{"item_key": "k2", "recommended": True, "reason": "ok"}]


def test_judge_batch_drops_non_integer_ordinals():
    from recommendations import judge_batch
    client = _client_returning(json.dumps([
        {"n": "1", "recommended": True, "reason": "string"},
        {"n": 1.5, "recommended": True, "reason": "float"},
        {"n": None, "recommended": True, "reason": "null"},
    ]))
    assert judge_batch(client, [], _items("k1", "k2")) == []


def test_judge_batch_drops_boolean_ordinals():
    """bool is an int in Python, so `true` would otherwise index the batch."""
    from recommendations import judge_batch
    client = _client_returning(json.dumps([{"n": True, "recommended": True, "reason": "boolean"}]))
    assert judge_batch(client, [], _items("k1", "k2")) == []


def test_judge_batch_drops_duplicate_ordinals_keeping_the_first():
    from recommendations import judge_batch
    client = _client_returning(json.dumps([
        {"n": 1, "recommended": True, "reason": "first"},
        {"n": 1, "recommended": False, "reason": None},
    ]))
    results = judge_batch(client, [], _items("k1", "k2"))
    assert results == [{"item_key": "k1", "recommended": True, "reason": "first"}]


def test_judge_batch_logs_usage(caplog):
    """The only record of what a run costs -- nothing else in the backend reads
    response.usage, so without this the caching above is unverifiable."""
    from recommendations import judge_batch
    client = _client_returning(json.dumps([{"n": 1, "recommended": False, "reason": None}]))
    with caplog.at_level(logging.INFO, logger="recommendations"):
        judge_batch(client, [], _items("k1"), "alice")

    usage_logs = [rec.getMessage() for rec in caplog.records if "Batch usage" in rec.getMessage()]
    assert len(usage_logs) == 1
    # Runs for different users interleave in one log stream, so an unattributed
    # counter is not worth much.
    assert "for alice" in usage_logs[0]
    assert "input=100" in usage_logs[0]
    assert "cache_write=200" in usage_logs[0]
    assert "cache_read=300" in usage_logs[0]
    assert "output=400" in usage_logs[0]


def test_judge_batch_tolerates_a_response_without_usage(caplog):
    from recommendations import judge_batch
    client = _client_returning(json.dumps([{"n": 1, "recommended": False, "reason": None}]), usage=None)
    with caplog.at_level(logging.INFO, logger="recommendations"):
        results = judge_batch(client, [], _items("k1"))

    assert results == [{"item_key": "k1", "recommended": False, "reason": None}]
    assert any("input=None" in rec.getMessage() for rec in caplog.records)


def test_judge_batch_reports_a_max_tokens_stop_as_itself(caplog):
    """A truncated array surfaced as an opaque JSON parse error, with nothing
    pointing at the output cap as the cause."""
    from recommendations import judge_batch, MAX_TOKENS
    truncated = '[{"n": 1, "recommended": true, "reason": "cut off here'
    client = _client_returning(truncated, stop_reason="max_tokens")
    with caplog.at_level(logging.INFO, logger="recommendations"):
        results = judge_batch(client, [], _items("k1"))

    assert results == []
    messages = [rec.getMessage() for rec in caplog.records]
    assert any(str(MAX_TOKENS) in m and "cap" in m for m in messages)
    assert not any("failed" in m for m in messages)


def test_build_batch_content_preserves_non_ascii_names():
    """json.dumps defaults to ensure_ascii=True, which would expand accented and
    non-Latin names into escape sequences -- more tokens than the characters
    they replace, reversing this change's own saving on the catalog's
    international half."""
    from recommendations import build_batch_content
    blocks = build_batch_content([], [{"item_key": "k1", "artist": "Björk", "title": "少年ナイフ"}])
    assert "Björk" in blocks[1]["text"]
    assert "少年ナイフ" in blocks[1]["text"]
    assert "\\u" not in blocks[1]["text"]
    assert _item_lines(blocks[1]) == [{"n": 1, "artist": "Björk", "title": "少年ナイフ"}]


def test_judge_batch_names_the_run_in_the_truncation_warning(caplog):
    from recommendations import judge_batch
    client = _client_returning('[{"n": 1, "recommended": true', stop_reason="max_tokens")
    with caplog.at_level(logging.INFO, logger="recommendations"):
        judge_batch(client, [], _items("k1"), "alice")

    assert any("for alice" in r.getMessage() and "cap" in r.getMessage() for r in caplog.records)


def test_judge_batch_names_the_run_in_every_validation_warning(caplog):
    """A dropped entry is the record of the model misbehaving on one user's
    batch; concurrent runs interleave these into one stream, so each has to say
    whose batch it was, exactly as the usage line does."""
    from recommendations import judge_batch
    client = _client_returning(json.dumps([
        {"n": "1", "recommended": True, "reason": "non-integer"},
        {"n": 9, "recommended": True, "reason": "out of range"},
        {"n": 1, "recommended": True, "reason": "first"},
        {"n": 1, "recommended": False, "reason": "duplicate"},
    ]))
    with caplog.at_level(logging.INFO, logger="recommendations"):
        judge_batch(client, [], _items("k1", "k2"), "alice")

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 3
    assert all("alice" in w for w in warnings)


def test_judge_batch_labels_the_run_without_a_caller_supplied_name(caplog):
    """The default has to be legible too -- an unlabelled line in a shared
    stream is the thing this guards against."""
    from recommendations import judge_batch
    client = _client_returning(json.dumps([{"n": 1, "recommended": False, "reason": None}]))
    with caplog.at_level(logging.INFO, logger="recommendations"):
        judge_batch(client, [], _items("k1"))

    usage_logs = [r.getMessage() for r in caplog.records if "Batch usage" in r.getMessage()]
    assert "for unknown user" in usage_logs[0]
