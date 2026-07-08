import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from routers import settings as settings_router


@pytest.fixture
def client(tmp_config_dir):
    app = FastAPI()
    app.include_router(settings_router.router, prefix="/api")
    yield TestClient(app)


def test_get_settings_anthropic_api_key_defaults_empty(client):
    r = client.get("/api/settings")
    assert r.json()["anthropic_api_key"] == ""


def test_post_settings_round_trips_anthropic_api_key(client):
    r = client.post("/api/settings", json={"discogs_token": "", "anthropic_api_key": "sk-ant-test"})
    assert r.status_code == 200
    r2 = client.get("/api/settings")
    assert r2.json()["anthropic_api_key"] == "sk-ant-test"


def test_get_settings_recommendation_item_limit_defaults_300(client):
    r = client.get("/api/settings")
    assert r.json()["recommendation_item_limit"] == 300


def test_post_settings_round_trips_recommendation_item_limit(client):
    r = client.post("/api/settings", json={"discogs_token": "", "recommendation_item_limit": 50})
    assert r.status_code == 200
    r2 = client.get("/api/settings")
    assert r2.json()["recommendation_item_limit"] == 50


def test_post_settings_round_trips_recommendation_item_limit_zero(client):
    r = client.post("/api/settings", json={"discogs_token": "", "recommendation_item_limit": 0})
    assert r.status_code == 200
    r2 = client.get("/api/settings")
    assert r2.json()["recommendation_item_limit"] == 0
