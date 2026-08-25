from datetime import datetime, timedelta

import pytest

import db


@pytest.fixture
def admin_conn(pg_test_db):
    db.init_global_schema()
    db.init_tenant_schema()
    with db.get_admin_pool().connection() as conn:
        yield conn
        conn.execute("TRUNCATE users, invites CASCADE")
        conn.commit()


def test_create_invite_persists_note(admin_conn):
    user = db.create_user(admin_conn, discogs_user_id=1, discogs_username="admin")
    admin_conn.commit()

    invite = db.create_invite(admin_conn, user["id"], "CODE123", note="for a friend")
    admin_conn.commit()

    assert invite["note"] == "for a friend"


def test_create_invite_note_defaults_to_none(admin_conn):
    user = db.create_user(admin_conn, discogs_user_id=1, discogs_username="admin")
    admin_conn.commit()

    invite = db.create_invite(admin_conn, user["id"], "CODE456")
    admin_conn.commit()

    assert invite["note"] is None


def test_list_invites_resolves_creator_and_redeemer_usernames(admin_conn):
    creator = db.create_user(admin_conn, discogs_user_id=1, discogs_username="admin")
    redeemer = db.create_user(admin_conn, discogs_user_id=2, discogs_username="bob")
    admin_conn.commit()

    db.create_invite(admin_conn, creator["id"], "REDEEMED1", note="for bob")
    admin_conn.execute(
        "UPDATE invites SET redeemed_by = %s, redeemed_at = CURRENT_TIMESTAMP WHERE code = %s",
        [redeemer["id"], "REDEEMED1"],
    )
    admin_conn.commit()

    invites = db.list_invites(admin_conn)
    assert len(invites) == 1
    assert invites[0]["created_by_username"] == "admin"
    assert invites[0]["redeemed_by_username"] == "bob"
    assert invites[0]["note"] == "for bob"


def test_list_invites_orders_newest_first(admin_conn):
    user = db.create_user(admin_conn, discogs_user_id=1, discogs_username="admin")
    admin_conn.commit()

    admin_conn.execute(
        "INSERT INTO invites (code, created_by, created_at) VALUES (%s, %s, %s)",
        ["OLD", user["id"], datetime.utcnow() - timedelta(days=1)],
    )
    admin_conn.execute(
        "INSERT INTO invites (code, created_by, created_at) VALUES (%s, %s, %s)",
        ["NEW", user["id"], datetime.utcnow()],
    )
    admin_conn.commit()

    invites = db.list_invites(admin_conn)
    assert [i["code"] for i in invites] == ["NEW", "OLD"]


def test_list_invites_leaves_unredeemed_fields_none(admin_conn):
    user = db.create_user(admin_conn, discogs_user_id=1, discogs_username="admin")
    admin_conn.commit()
    db.create_invite(admin_conn, user["id"], "UNREDEEMED1")
    admin_conn.commit()

    invites = db.list_invites(admin_conn)
    assert invites[0]["redeemed_by_username"] is None
    assert invites[0]["redeemed_at"] is None
