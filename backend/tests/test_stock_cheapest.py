import pytest

import db
from routers import stock as stock_router


@pytest.fixture
def admin_conn(pg_test_db):
    db.init_global_schema()
    db.init_tenant_schema()
    with db.get_admin_pool().connection() as conn:
        yield conn
        conn.execute("TRUNCATE catalog, users, crawlers, stock_item_identities CASCADE")
        conn.commit()


@pytest.fixture
def authed_client_factory(authed_client_factory_builder):
    return authed_client_factory_builder([stock_router.router])


def _seed(admin_conn, stores):
    """`stores` maps a store name to its stock rows. Every row is USD unless it
    says otherwise, and every URL is unique per store so no two rows share an
    item_key -- the whole point is that the rows are *not* already grouped."""
    alice = db.create_user(admin_conn, discogs_user_id=1, discogs_username="alice")
    ids = {}
    for name, rows in stores.items():
        db.register_crawler(admin_conn, name, f"/{name}.py", crawler_type="catalog")
        admin_conn.commit()
        ids[name] = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = %s", [name]).fetchone()["id"]
        db.replace_stock_items(admin_conn, ids[name], [
            {"artist": r["artist"], "title": r["title"], "url": f"https://{name}/{i}",
             "price": r.get("price", 10.0), "currency": r.get("currency", "USD")}
            for i, r in enumerate(rows)
        ])
    admin_conn.commit()
    return alice, ids


def _own_rows(result):
    return sorted((i["source"], i["title"], i["price"]) for i in result["items"] if i["is_own"])


def test_cheapest_keeps_the_lowest_priced_store_per_record(admin_conn):
    alice, _ = _seed(admin_conn, {
        "A": [{"artist": "Radiohead", "title": "Kid A - LP Black", "price": 30.0}],
        "B": [{"artist": "Radiohead", "title": "Kid A (Black)", "price": 25.0}],
        "C": [{"artist": "Radiohead", "title": "Kid A — Black Vinyl (Ltd)", "price": 28.0}],
    })
    with db.user_scope(alice["id"]) as conn:
        assert db.get_stock_items(conn, alice["id"])["total"] == 3
        result = db.get_stock_items(conn, alice["id"], cheapest=True)
    assert result["total"] == 1
    assert _own_rows(result) == [("B", "Kid A (Black)", 25.0)]


def test_cheapest_keeps_different_variants_apart(admin_conn):
    alice, _ = _seed(admin_conn, {
        "A": [{"artist": "Radiohead", "title": "Kid A - LP Black", "price": 30.0},
              {"artist": "Radiohead", "title": "Kid A - LP Red", "price": 35.0}],
        "B": [{"artist": "Radiohead", "title": "Kid A (Red)", "price": 32.0}],
    })
    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], cheapest=True)
    assert _own_rows(result) == [("A", "Kid A - LP Black", 30.0), ("B", "Kid A (Red)", 32.0)]


def test_cheapest_folds_the_artist_article(admin_conn):
    alice, _ = _seed(admin_conn, {
        "A": [{"artist": "The Beatles", "title": "Revolver", "price": 30.0}],
        "B": [{"artist": "Beatles, The", "title": "Revolver (LP)", "price": 20.0}],
    })
    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], cheapest=True)
    assert _own_rows(result) == [("B", "Revolver (LP)", 20.0)]


def test_cheapest_keeps_a_tie(admin_conn):
    alice, _ = _seed(admin_conn, {
        "A": [{"artist": "Radiohead", "title": "Kid A", "price": 25.0}],
        "B": [{"artist": "Radiohead", "title": "Kid A", "price": 25.0}],
        "C": [{"artist": "Radiohead", "title": "Kid A", "price": 26.0}],
    })
    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], cheapest=True)
    assert _own_rows(result) == [("A", "Kid A", 25.0), ("B", "Kid A", 25.0)]


def test_cheapest_does_not_compare_across_currencies(admin_conn):
    alice, _ = _seed(admin_conn, {
        "A": [{"artist": "Radiohead", "title": "Kid A", "price": 30.0, "currency": "USD"}],
        "B": [{"artist": "Radiohead", "title": "Kid A", "price": 20.0, "currency": "EUR"}],
        "C": [{"artist": "Radiohead", "title": "Kid A", "price": 22.0, "currency": None}],
    })
    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], cheapest=True)
    # A NULL currency is the USD bucket, as _price_floors and formatPrice both
    # read it, so C undercuts A; the EUR row is its own floor.
    assert _own_rows(result) == [("B", "Kid A", 20.0), ("C", "Kid A", 22.0)]


def test_cheapest_hides_an_unpriced_row_only_beside_a_priced_one(admin_conn):
    alice, _ = _seed(admin_conn, {
        "A": [{"artist": "Radiohead", "title": "Kid A", "price": None},
              {"artist": "Radiohead", "title": "Amnesiac", "price": None}],
        "B": [{"artist": "Radiohead", "title": "Kid A", "price": 30.0}],
    })
    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], cheapest=True)
    assert _own_rows(result) == [("A", "Amnesiac", None), ("B", "Kid A", 30.0)]


def test_cheapest_competes_only_among_visible_stores(admin_conn):
    alice, ids = _seed(admin_conn, {
        "A": [{"artist": "Radiohead", "title": "Kid A", "price": 30.0}],
        "B": [{"artist": "Radiohead", "title": "Kid A", "price": 20.0}],
    })
    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], cheapest=True, exclude_crawler_ids=[ids["B"]])
    # B is hidden, so its price cannot win -- A is the cheapest the user can see.
    assert _own_rows(result) == [("A", "Kid A", 30.0)]


def test_cheapest_competes_only_within_the_saved_filter(admin_conn):
    alice, _ = _seed(admin_conn, {
        "A": [{"artist": "Radiohead", "title": "Kid A", "price": 30.0}],
        "B": [{"artist": "Radiohead", "title": "Kid A", "price": 20.0}],
    })
    with db.user_scope(alice["id"]) as conn:
        db.save_stock_item(conn, alice["id"], db.compute_item_key("Radiohead", "Kid A", "https://A/0"))
        conn.commit()
        result = db.get_stock_items(conn, alice["id"], cheapest=True, saved_only=True)
    # The saved copy stays even though an unsaved store is cheaper: Saved is
    # the view, and Cheapest narrows the view rather than emptying it.
    assert _own_rows(result) == [("A", "Kid A", 30.0)]


def test_cheapest_keeps_the_winners_marketplace_comparisons(admin_conn):
    alice, _ = _seed(admin_conn, {
        "A": [{"artist": "Radiohead", "title": "Kid A", "price": 30.0}],
        "B": [{"artist": "Radiohead", "title": "Kid A", "price": 20.0}],
    })
    db.register_crawler(admin_conn, "Discogs", "/discogs.py", crawler_type="release")
    admin_conn.commit()
    discogs_id = admin_conn.execute("SELECT id FROM crawlers WHERE site_name = 'Discogs'").fetchone()["id"]
    for store, price in (("A", 15.0), ("B", 16.0)):
        db.upsert_stock_item_listing(
            admin_conn, db.compute_item_key("Radiohead", "Kid A", f"https://{store}/0"),
            discogs_id, f"https://discogs/{store}", price, None, "USD", "VG+",
        )
    admin_conn.commit()
    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], cheapest=True)
    # Marketplace listings do not decide the winner -- A's cheaper Discogs
    # comparison does not rescue A's own dearer row -- but the winner's own
    # comparisons still hang under it, so the cheaper marketplace price is
    # still on screen.
    assert [(i["source"], i["price"], i["is_own"]) for i in result["items"]] == [
        ("B", 20.0, True), ("Discogs", 16.0, False),
    ]


def test_cheapest_under_a_cost_sort_flattens_only_the_winners(admin_conn):
    alice, _ = _seed(admin_conn, {
        "A": [{"artist": "Radiohead", "title": "Kid A", "price": 30.0}],
        "B": [{"artist": "Radiohead", "title": "Kid A", "price": 20.0},
              {"artist": "Radiohead", "title": "Amnesiac", "price": 40.0}],
    })
    with db.user_scope(alice["id"]) as conn:
        result = db.get_stock_items(conn, alice["id"], cheapest=True, sort="price")
    assert result["total"] == result["row_total"] == 2
    assert [(i["source"], i["price"]) for i in result["items"]] == [("B", 20.0), ("B", 40.0)]


def test_source_counts_cheapest_counts_only_the_winners(admin_conn):
    alice, _ = _seed(admin_conn, {
        "A": [{"artist": "Radiohead", "title": "Kid A", "price": 30.0},
              {"artist": "Radiohead", "title": "Amnesiac", "price": 30.0}],
        "B": [{"artist": "Radiohead", "title": "Kid A", "price": 20.0}],
    })
    with db.user_scope(alice["id"]) as conn:
        counts = db.get_stock_source_counts(conn, alice["id"], cheapest=True)
        total = db.get_stock_items(conn, alice["id"], cheapest=True)["total"]
    assert sorted((c["site_name"], c["count"]) for c in counts) == [("A", 1), ("B", 1)]
    assert sum(c["count"] for c in counts) == total


def test_distinct_artists_are_unchanged_by_cheapest(admin_conn):
    # Every record keeps at least one row, so no artist can drop out of the
    # sidebar; the sidebar query therefore never takes the flag.
    alice, _ = _seed(admin_conn, {
        "A": [{"artist": "Radiohead", "title": "Kid A", "price": 30.0}],
        "B": [{"artist": "Radiohead", "title": "Kid A", "price": 20.0}],
    })
    with db.user_scope(alice["id"]) as conn:
        assert db.get_distinct_stock_artists(conn, alice["id"]) == ["Radiohead"]


def test_replace_stock_items_writes_title_key(admin_conn):
    _, ids = _seed(admin_conn, {"A": [{"artist": "Radiohead", "title": "Kid A - LP Black"}]})
    row = admin_conn.execute("SELECT title_key FROM stock_items WHERE crawler_id = %s", [ids["A"]]).fetchone()
    assert row["title_key"] == "a black kid"


def test_init_tenant_schema_backfills_missing_title_keys(admin_conn):
    _, ids = _seed(admin_conn, {"A": [{"artist": "Radiohead", "title": "Kid A - LP Black"}]})
    admin_conn.execute("UPDATE stock_items SET title_key = NULL WHERE crawler_id = %s", [ids["A"]])
    admin_conn.commit()

    db.init_tenant_schema()

    row = admin_conn.execute("SELECT title_key FROM stock_items WHERE crawler_id = %s", [ids["A"]]).fetchone()
    assert row["title_key"] == "a black kid"


def test_get_stock_cheapest_keeps_one_row_per_record(admin_conn, authed_client_factory):
    alice, _ = _seed(admin_conn, {
        "Store A": [{"artist": "Radiohead", "title": "Kid A - LP Black", "price": 30.0},
                    {"artist": "Radiohead", "title": "Amnesiac", "price": 30.0}],
        "Store B": [{"artist": "Radiohead", "title": "Kid A (Black)", "price": 20.0}],
    })
    client = authed_client_factory(alice["id"])
    assert client.get("/api/stock").json()["total"] == 3

    r = client.get("/api/stock", params={"cheapest": "true"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert sorted((i["source"], i["title"]) for i in body["items"]) == [
        ("Store A", "Amnesiac"), ("Store B", "Kid A (Black)"),
    ]


def test_get_stock_cheapest_respects_hidden_sources(admin_conn, authed_client_factory):
    alice, ids = _seed(admin_conn, {
        "Store A": [{"artist": "Radiohead", "title": "Kid A - LP Black", "price": 30.0}],
        "Store B": [{"artist": "Radiohead", "title": "Kid A (Black)", "price": 20.0}],
    })
    client = authed_client_factory(alice["id"])

    body = client.get("/api/stock", params={"cheapest": "true", "hidden_crawler_ids": str(ids["Store B"])}).json()
    assert [i["title"] for i in body["items"]] == ["Kid A - LP Black"]


def test_get_stock_stats_cheapest_counts_the_same_rows_the_list_shows(admin_conn, authed_client_factory):
    alice, ids = _seed(admin_conn, {
        "Store A": [{"artist": "Radiohead", "title": "Kid A - LP Black", "price": 30.0},
                    {"artist": "Radiohead", "title": "Amnesiac", "price": 30.0}],
        "Store B": [{"artist": "Radiohead", "title": "Kid A (Black)", "price": 20.0}],
    })
    client = authed_client_factory(alice["id"])

    body = client.get("/api/stock/stats", params={"cheapest": "true"}).json()
    assert body["total"] == 2
    assert sorted((s["crawler_id"], s["count"]) for s in body["sources"]) == [
        (ids["Store A"], 1), (ids["Store B"], 1),
    ]
