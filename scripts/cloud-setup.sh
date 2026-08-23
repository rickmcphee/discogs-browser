#!/usr/bin/env bash
# Provisions a Claude Code cloud session so `pytest` and `npm run test` can
# actually run. Mirrors the two CI jobs in .github/workflows/fly-deploy.yml --
# that workflow is the authoritative statement of what a green run needs, and
# this script is its sandbox equivalent.
#
# Wired up as a SessionStart hook in .claude/settings.json rather than as the
# cloud environment's "Setup script" field, because the environment cache is a
# filesystem snapshot: it keeps installed packages but loses running processes,
# so a Postgres started by a setup script is gone by the second session. The
# database has to be started per session, which is what a SessionStart hook is
# for. See https://code.claude.com/docs/en/cloud-environments#environment-caching
#
# No-ops outside a cloud session so it is harmless as a local hook. Pass
# --force to run it anyway (it will overwrite backend/.env).
set -e

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ] && [ "${1:-}" != "--force" ]; then
  exit 0
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Cloud setup scripts run as root, but a SessionStart hook runs as whatever
# user Claude Code runs as, which is not guaranteed to be root.
as_root() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  else
    sudo "$@"
  fi
}

echo "==> Starting Postgres..."
as_root service postgresql start

for _ in $(seq 1 30); do
  pg_isready -h localhost -p 5432 >/dev/null 2>&1 && break
  sleep 1
done
if ! pg_isready -h localhost -p 5432 >/dev/null 2>&1; then
  echo "==> ERROR: Postgres did not become ready" >&2
  exit 1
fi

# The suite connects over TCP as `postgres`, which needs a password (the
# cluster's default local auth is peer, over the unix socket, and pg_hba's
# 127.0.0.1 rule is scram-sha-256). Superuser, not just CREATEDB: conftest.py
# poisons app_user/app_identity with ALTER ROLE ... BYPASSRLS, which only a
# superuser can set. See README.md's "Running tests".
echo "==> Configuring the postgres role and test database..."
as_root su postgres -c "psql -v ON_ERROR_STOP=1 -c \"ALTER ROLE postgres PASSWORD 'postgres'\"" >/dev/null
as_root su postgres -c "psql -tAc \"SELECT 1 FROM pg_database WHERE datname = 'discogs_browser_test'\"" \
  | grep -q 1 \
  || as_root su postgres -c "createdb discogs_browser_test"

# pytest-dotenv (a dev dependency) loads backend/.env from the rundir, so this
# is what makes a bare `cd backend && pytest` work without an env var prefix.
# Gitignored; values match the ones documented in README.md.
echo "==> Writing backend/.env..."
cat > backend/.env <<'ENV'
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/discogs_browser_test
IDENTITY_DB_PASSWORD=test
APP_DB_PASSWORD=test
ENV

echo "==> Installing backend dependencies..."
cd "$REPO_ROOT/backend"
# Ubuntu 24.04 marks its system Python externally-managed (PEP 668); the
# fallback covers a session whose default interpreter is that one rather than
# a virtualenv.
python3 -m pip install -e ".[dev]" >/dev/null || python3 -m pip install -e ".[dev]" --break-system-packages >/dev/null

# Five crawler test files launch a real headless Chromium against local HTML
# fixtures, so the browser is required for a full green run, not optional.
# install-deps is the apt half and needs root; the browser download does not,
# and must stay unprivileged so it lands in the cache of the user that runs
# the tests.
echo "==> Installing Playwright Chromium..."
if [ ! -f /var/tmp/.playwright-deps-installed ]; then
  as_root python3 -m playwright install-deps chromium
  as_root touch /var/tmp/.playwright-deps-installed
fi
python3 -m playwright install chromium

echo "==> Installing frontend dependencies..."
cd "$REPO_ROOT/frontend"
if [ ! -e node_modules/.package-lock.json ] || [ package-lock.json -nt node_modules/.package-lock.json ]; then
  npm ci
fi

echo "==> Ready. Backend: cd backend && pytest. Frontend: cd frontend && npm run test"
