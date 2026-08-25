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

# What a later shell will have; this script modifies its own PATH below and
# must not judge resolvability against the modified copy.
ORIGINAL_PATH="$PATH"

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

# That install is a user install whenever the interpreter is not writable, so
# the console scripts land in site.USER_BASE/bin -- absent from PATH in a
# non-login shell. The modules import fine and a bare `pytest` still fails
# with command not found, which is the one thing this script exists to
# prevent. Put the directory on PATH here and for later shells, and fall back
# to a symlink somewhere already on PATH if those profiles go unread.
USER_BIN="$(python3 -c 'import site, os; print(os.path.join(site.USER_BASE, "bin"))')"
case ":$PATH:" in
  *":$USER_BIN:"*) ;;
  *)
    export PATH="$USER_BIN:$PATH"
    for profile in "$HOME/.profile" "$HOME/.bashrc"; do
      [ -f "$profile" ] || continue
      grep -qs "discogs-browser cloud setup" "$profile" && continue
      printf '\n# discogs-browser cloud setup\nexport PATH="%s:$PATH"\n' "$USER_BIN" >> "$profile"
    done
    ;;
esac

# Judged against ORIGINAL_PATH, not the export above: the shell that later
# runs the tests is a fresh one, and a non-interactive shell reads neither
# ~/.profile nor ~/.bashrc past its early return, so both the export and the
# profile lines miss it. A symlink into a directory already on every PATH is
# what actually makes `pytest` resolve.
if [ -x "$USER_BIN/pytest" ] && ! PATH="$ORIGINAL_PATH" command -v pytest >/dev/null 2>&1; then
  as_root ln -sf "$USER_BIN/pytest" /usr/local/bin/pytest
fi
if ! PATH="$ORIGINAL_PATH" command -v pytest >/dev/null 2>&1; then
  echo "==> ERROR: pytest installed but not resolvable on PATH" >&2
  exit 1
fi

# Some crawler test files launch a real headless Chromium against local HTML
# fixtures, so the browser is required for a full green run, not optional.
# Runs as the session user, never under as_root: the driver escalates the apt
# half itself (transformCommandsForRoot wraps it in `sudo -- sh -c` when not
# root), whereas `sudo python3` would resolve to root's interpreter, where
# playwright is not installed. Same invocation as CI. The marker skips the
# apt half on later sessions, and is keyed to the installed version because
# pyproject.toml floats playwright>=1.62.0: an upgrade can add newly required
# OS packages, and a version-blind marker would skip them and leave Chromium
# unable to launch.
echo "==> Installing Playwright Chromium..."
PLAYWRIGHT_VERSION="$(python3 -c "import importlib.metadata; print(importlib.metadata.version('playwright'))")"
if [ -f "/var/tmp/.playwright-deps-${PLAYWRIGHT_VERSION}" ]; then
  python3 -m playwright install chromium
else
  python3 -m playwright install --with-deps chromium
  touch "/var/tmp/.playwright-deps-${PLAYWRIGHT_VERSION}"
fi

echo "==> Installing frontend dependencies..."
cd "$REPO_ROOT/frontend"
if [ ! -e node_modules/.package-lock.json ] || [ package-lock.json -nt node_modules/.package-lock.json ]; then
  npm ci
fi

echo "==> Ready. Backend: cd backend && pytest. Frontend: cd frontend && npm run test"
