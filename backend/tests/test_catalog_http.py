import httpx
import pytest
import respx

from catalog_http import REQUEST_TIMEOUT, get_with_retry

_URL = "https://example.test/products.json"


@respx.mock
async def test_retries_a_connect_timeout_then_succeeds():
    # The 2026-09-01 Dark Descent failure: one TLS handshake past the connect
    # timeout must cost one retry, not the whole catalog crawl.
    route = respx.get(_URL)
    route.side_effect = [httpx.ConnectTimeout("handshake timed out"), httpx.Response(200, json=[])]
    async with httpx.AsyncClient() as client:
        r = await get_with_retry(client, _URL, delay=0, failure_limit=3)
    assert r.status_code == 200
    assert route.call_count == 2


@respx.mock
async def test_raises_after_failure_limit_consecutive_failures():
    route = respx.get(_URL).mock(return_value=httpx.Response(503))
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError):
            await get_with_retry(client, _URL, delay=0, failure_limit=2)
    assert route.call_count == 2


@respx.mock
async def test_transport_error_raises_after_failure_limit():
    route = respx.get(_URL)
    route.side_effect = httpx.ConnectTimeout("handshake timed out")
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.ConnectTimeout):
            await get_with_retry(client, _URL, delay=0, failure_limit=3)
    assert route.call_count == 3


@respx.mock
async def test_fails_fast_when_failure_limit_is_zero():
    # A limit of 0 means "disabled" elsewhere, but disabled must not mean
    # unlimited retries where there is no next item to fall through to.
    route = respx.get(_URL).mock(return_value=httpx.Response(503))
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError):
            await get_with_retry(client, _URL, delay=0, failure_limit=0)
    assert route.call_count == 1


@respx.mock
async def test_429_raises_immediately_without_retrying():
    route = respx.get(_URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "5"}))
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await get_with_retry(client, _URL, delay=0, failure_limit=3)
    assert exc_info.value.response.status_code == 429
    assert route.call_count == 1


@respx.mock
async def test_429_logs_response_headers_at_debug_level(caplog):
    respx.get(_URL).mock(
        return_value=httpx.Response(429, headers={"Retry-After": "5", "X-Edge-Signal": "1/40"})
    )
    with caplog.at_level("DEBUG", logger="catalog_http"):
        async with httpx.AsyncClient() as client:
            with pytest.raises(httpx.HTTPStatusError):
                await get_with_retry(client, _URL, delay=0, failure_limit=3)
    debug_records = [r for r in caplog.records if r.levelname == "DEBUG" and "429" in r.message]
    assert len(debug_records) == 1
    assert "retry-after" in debug_records[0].message.lower()
    assert "x-edge-signal" in debug_records[0].message.lower()


@respx.mock
async def test_allow_404_returns_the_response_without_retrying():
    route = respx.get(_URL).mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient() as client:
        r = await get_with_retry(client, _URL, delay=0, failure_limit=3, allow_404=True)
    assert r.status_code == 404
    assert route.call_count == 1


@respx.mock
async def test_404_raises_without_allow_404():
    route = respx.get(_URL).mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError):
            await get_with_retry(client, _URL, delay=0, failure_limit=2)
    assert route.call_count == 2


@respx.mock
async def test_every_attempt_is_paced_with_the_jittered_delay(monkeypatch):
    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr("catalog_http.sleep", fake_sleep)
    route = respx.get(_URL)
    route.side_effect = [httpx.Response(503), httpx.Response(200, json=[])]
    async with httpx.AsyncClient() as client:
        await get_with_retry(client, _URL, delay=40, failure_limit=3)
    assert len(sleep_calls) == 2
    assert all(20 <= s <= 40 for s in sleep_calls)


@respx.mock
async def test_request_carries_the_explicit_timeout():
    # httpx's default 5s connect budget is what one slow TLS handshake blew
    # through; the helper must stamp its own generous bound on every request.
    route = respx.get(_URL).mock(return_value=httpx.Response(200, json=[]))
    async with httpx.AsyncClient() as client:
        await get_with_retry(client, _URL, delay=0, failure_limit=1)
    assert route.calls[0].request.extensions["timeout"] == REQUEST_TIMEOUT.as_dict()
