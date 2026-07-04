"""CLI recovery: clear the owner and all sessions, returning the app to first-run setup.

Run inside the container / on the host:  python -m reset_owner
"""
import db


def reset(conn):
    db.delete_owner(conn)
    conn.execute("DELETE FROM session")
    conn.commit()


def main():
    import config
    from db import get_connection, init_db

    conn = get_connection()
    init_db(conn)
    reset(conn)
    if config.BOOTSTRAP_TOKEN_FILE.exists():
        config.BOOTSTRAP_TOKEN_FILE.unlink()
    print("Owner and sessions cleared. Restart the app to get a new bootstrap token.")


if __name__ == "__main__":
    main()
