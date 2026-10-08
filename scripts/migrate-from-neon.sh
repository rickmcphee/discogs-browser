#!/usr/bin/env bash
# One-off: copy the hosted deployment's database out of Neon and into the
# self-hosted docker-compose Postgres. Run on the NAS, from the repository
# checkout that docker-compose.yml lives in, with the Fly app already stopped
# (fly scale count 0) so nothing writes to Neon while it is being copied.
#
#   NEON_DATABASE_URL='postgresql://<owner>:<pw>@ep-xxx.<region>.aws.neon.tech/<db>?sslmode=require' \
#     bash scripts/migrate-from-neon.sh [--set-aside-existing]
#
# Use Neon's *unpooled* connection string (no "-pooler" in the host): pg_dump
# needs one consistent session, which a transaction-mode pooler does not give.
#
# Never drops anything. If the local discogs_browser database already holds
# tables, the script stops unless --set-aside-existing is passed, which
# renames that database to discogs_browser_pre_neon_<timestamp> and restores
# into a fresh, empty one -- the old data stays in the cluster until a person
# drops it by hand.
#
# Grants and roles are deliberately not copied (--no-owner --no-acl): Neon's
# owner role does not exist here, and the backend's init_tenant_schema()
# recreates app_identity/app_user and every grant on its next start.
# Row-level-security policies name no role, so they restore as they are.
#
# See docs/specifications/shaping/2026-10-07-undeploy-hosted-infrastructure-design.md.
set -euo pipefail

SET_ASIDE=0
for arg in "$@"; do
  case "$arg" in
    --set-aside-existing) SET_ASIDE=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

if [ -z "${NEON_DATABASE_URL:-}" ]; then
  echo "NEON_DATABASE_URL must be set to Neon's unpooled connection string" >&2
  exit 2
fi
case "$NEON_DATABASE_URL" in
  *-pooler.*) echo "NEON_DATABASE_URL is Neon's pooled endpoint; use the unpooled one" >&2; exit 2 ;;
esac

COMPOSE="${COMPOSE:-docker-compose}"
LOCAL_DB=discogs_browser
OUT_DIR=neon-export
STAMP="$(date +%Y%m%d%H%M%S)"
DUMP="$OUT_DIR/neon-$STAMP.sql"

# The dump holds every user's encrypted Discogs tokens and session rows.
umask 077
mkdir -p "$OUT_DIR"

neon_psql() {
  docker run --rm -e NEON_DATABASE_URL "postgres:${NEON_MAJOR:-16}" \
    sh -c 'psql "$NEON_DATABASE_URL" -X -v ON_ERROR_STOP=1 -tA "$@"' psql "$@"
}

local_psql() {
  local db="$1"; shift
  $COMPOSE exec -T postgres psql -U postgres -d "$db" -X -v ON_ERROR_STOP=1 "$@"
}

check_local_db() {
  EXISTING="$(local_psql postgres -tA -c "SELECT 1 FROM pg_database WHERE datname = '$LOCAL_DB'")"
  if [ -n "$EXISTING" ]; then
    TABLES="$(local_psql "$LOCAL_DB" -tA -c "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")"
  else
    TABLES=0
  fi
  if [ "$TABLES" != "0" ] && [ "$SET_ASIDE" != "1" ]; then
    echo "Local database $LOCAL_DB already has tables. Re-run with --set-aside-existing" >&2
    echo "to rename it to ${LOCAL_DB}_pre_neon_<timestamp> and restore into a fresh one." >&2
    exit 1
  fi
}

echo "==> Starting local Postgres..."
$COMPOSE up -d postgres
until $COMPOSE exec -T postgres pg_isready -U postgres >/dev/null 2>&1; do sleep 2; done
# Read-only, and before anything is stopped, so a refusal leaves the local app
# running as it was.
check_local_db

echo "==> Reading Neon's server version..."
NEON_MAJOR="$(neon_psql -c 'SHOW server_version_num' | tr -d '[:space:]')"
NEON_MAJOR=$((NEON_MAJOR / 10000))
echo "    Neon runs Postgres $NEON_MAJOR; dumping with pg_dump $NEON_MAJOR"

echo "==> Dumping Neon to $DUMP ..."
# pg_dump must be at least the server's major version, hence the image tag.
# Lines dropped from the plain-SQL dump, each because the target can refuse it:
#   SET transaction_timeout -- a Postgres 17 setting the local 16 rejects
#   \restrict / \unrestrict -- psql meta-commands newer pg_dump releases emit,
#     unknown to an older psql in the local container; they guard against a
#     hostile dump, and this one comes straight from our own database.
docker run --rm -e NEON_DATABASE_URL "postgres:$NEON_MAJOR" \
  sh -c 'pg_dump --format=plain --no-owner --no-acl --no-publications --no-subscriptions --dbname "$NEON_DATABASE_URL"' \
  | sed -e '/^SET transaction_timeout/d' -e '/^\\restrict /d' -e '/^\\unrestrict /d' \
  > "$DUMP"
echo "    $(wc -c < "$DUMP") bytes"

echo "==> Stopping the local backend..."
# The backend creates the schema on boot; it must not race the restore or hold
# a connection to the database being renamed. Checked again once it is down,
# since a running backend may have changed the answer since the first check.
$COMPOSE stop backend >/dev/null 2>&1 || true
check_local_db

if [ "$TABLES" != "0" ]; then
  echo "==> Renaming existing $LOCAL_DB to ${LOCAL_DB}_pre_neon_$STAMP ..."
  local_psql postgres -c "ALTER DATABASE $LOCAL_DB RENAME TO ${LOCAL_DB}_pre_neon_$STAMP"
  EXISTING=""
fi
if [ -z "$EXISTING" ]; then
  local_psql postgres -c "CREATE DATABASE $LOCAL_DB"
fi

echo "==> Restoring into local $LOCAL_DB (one transaction; any error rolls it all back)..."
local_psql "$LOCAL_DB" -q --single-transaction < "$DUMP" >/dev/null

echo "==> Comparing row counts table by table..."
TABLE_LIST="$(local_psql "$LOCAL_DB" -tA -c \
  "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")"
COUNT_SQL="$(printf '%s\n' "$TABLE_LIST" | awk '
  NF { printf "%sSELECT %c%s %c || count(*) FROM public.\"%s\"", sep, 39, $0, 39, $0; sep = " UNION ALL " }')"
neon_psql -c "$COUNT_SQL" | sort > "$OUT_DIR/counts-neon-$STAMP.txt"
local_psql "$LOCAL_DB" -tA -c "$COUNT_SQL" | sort > "$OUT_DIR/counts-local-$STAMP.txt"
if ! diff -u "$OUT_DIR/counts-neon-$STAMP.txt" "$OUT_DIR/counts-local-$STAMP.txt"; then
  echo "Row counts differ (above). Was the Fly app still writing to Neon?" >&2
  exit 1
fi
cat "$OUT_DIR/counts-local-$STAMP.txt"

cat <<EOF

Done: every table has the same row count as on Neon. The dump stays at $DUMP --
keep it until the NAS has run for a while, then delete it (it holds every
user's encrypted Discogs tokens).

Next:
  1. Make sure .env carries the TOKEN_ENCRYPTION_KEY and DISCOGS_CONSUMER_KEY/
     DISCOGS_CONSUMER_SECRET the Fly app ran with -- a different key leaves
     every stored Discogs token unreadable -- and BACKEND_BASE_URL set to the
     NAS's own address.
  2. $COMPOSE up -d    (the backend re-creates roles and grants on boot)
  3. Log in and check your library, Store and recommendations before tearing
     anything down.
EOF
