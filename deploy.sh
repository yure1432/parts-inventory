#!/usr/bin/env bash
# One-shot deploy: parts-inventory -> Turso (libSQL) + Vercel.
# Idempotent: safe to re-run. Skips anything already done.
#
# Usage:
#   ADMIN_PASSWORD=... ./deploy.sh              # set the app admin password
#   ./deploy.sh                                 # prompts for it if unset
# Optional env:
#   DB_NAME=parts-inventory   Turso database name (default below)
#   SEED_CSV=parts.csv        after deploy, bulk-load this CSV into Turso
#   SECRET_KEY=...            cookie-signing key (auto-generated & reused if unset)
set -euo pipefail

DB_NAME="${DB_NAME:-parts-inventory}"
SEED_CSV="${SEED_CSV:-}"

# --- make CLIs reachable regardless of login shell PATH ---
export PATH="$HOME/.npm-global/bin:$HOME/.turso:$HOME/.turso/bin:$PATH"

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

cd "$(dirname "$0")"

# If URL + token are already supplied, skip the turso CLI entirely (e.g. you
# created the DB in the web dashboard at app.turso.tech). NOTE: the AUR `turso`
# package installs `tursodb`, the local DB engine — NOT the cloud CLI this needs.
HAVE_TURSO_CREDS=0
if [ -n "${TURSO_DATABASE_URL:-}" ] && [ -n "${TURSO_AUTH_TOKEN:-}" ]; then
    HAVE_TURSO_CREDS=1
fi

# ---------------------------------------------------------------------------
say "1/7  Checking CLIs"

if ! command -v vercel >/dev/null 2>&1; then
    say "Installing Vercel CLI via npm"
    npm i -g vercel
fi
command -v vercel >/dev/null 2>&1 || die "vercel still not on PATH after install"
echo "vercel $(vercel --version)"

if [ "$HAVE_TURSO_CREDS" = 1 ]; then
    echo "TURSO_DATABASE_URL + TURSO_AUTH_TOKEN already set — skipping turso CLI."
elif ! command -v turso >/dev/null 2>&1; then
    die "turso CLI not found (the cloud CLI, not the AUR 'tursodb' engine).
     Either:
       (A) create the DB at https://app.turso.tech and re-run with:
             TURSO_DATABASE_URL=... TURSO_AUTH_TOKEN=... ADMIN_PASSWORD=... ./deploy.sh
       (B) install the real cloud CLI (review the script first):
             curl -sSfL https://get.tur.so/install.sh | bash"
fi

# ---------------------------------------------------------------------------
if [ "$HAVE_TURSO_CREDS" = 1 ]; then
    say "2-4/7  Using provided Turso credentials"
    echo "URL: $TURSO_DATABASE_URL"
    echo "Token: (from environment, not printed)"
else
    echo "turso $(turso --version)"

    say "2/7  Turso auth"
    if ! turso auth token >/dev/null 2>&1 && ! turso db list >/dev/null 2>&1; then
        echo "Not logged in — opening browser login..."
        turso auth login
    fi
    turso db list >/dev/null 2>&1 || die "Turso login failed"
    echo "Logged in to Turso."

    say "3/7  Turso database '$DB_NAME'"
    if turso db list | awk '{print $1}' | grep -qx "$DB_NAME"; then
        echo "Database already exists — reusing it."
    else
        turso db create "$DB_NAME"
    fi

    TURSO_DATABASE_URL="$(turso db show --url "$DB_NAME")"
    [ -n "$TURSO_DATABASE_URL" ] || die "could not get database URL"
    say "4/7  Minting auth token"
    TURSO_AUTH_TOKEN="$(turso db tokens create "$DB_NAME")"
    [ -n "$TURSO_AUTH_TOKEN" ] || die "could not create auth token"
    echo "URL: $TURSO_DATABASE_URL"
    echo "Token: (captured, not printed)"
fi

# ---------------------------------------------------------------------------
say "5/7  App secrets"
if [ -z "${ADMIN_PASSWORD:-}" ]; then
    read -rsp "Admin password (app login): " ADMIN_PASSWORD; echo
fi
[ -n "$ADMIN_PASSWORD" ] || die "ADMIN_PASSWORD is required"

# ---------------------------------------------------------------------------
say "6/7  Linking project & setting Vercel env vars (production)"
# Link (idempotent: creates .vercel/ once, no-op after).
[ -d .vercel ] || vercel link --yes

# Reuse an existing SECRET_KEY on Vercel so admin sessions survive redeploys.
existing_env() { vercel env ls production 2>/dev/null | awk '{print $1}' | grep -qx "$1"; }

if [ -z "${SECRET_KEY:-}" ]; then
    if existing_env SECRET_KEY; then
        echo "SECRET_KEY already set on Vercel — keeping it."
        SET_SECRET=0
    else
        SECRET_KEY="$(python3 -c 'import secrets;print(secrets.token_hex(32))')"
        SET_SECRET=1
    fi
else
    SET_SECRET=1
fi

set_env() {  # name value -> remove-then-add so the value is exactly what we intend
    local name="$1" value="$2"
    vercel env rm "$name" production --yes >/dev/null 2>&1 || true
    printf '%s' "$value" | vercel env add "$name" production >/dev/null
    echo "set $name"
}

set_env TURSO_DATABASE_URL "$TURSO_DATABASE_URL"
set_env TURSO_AUTH_TOKEN   "$TURSO_AUTH_TOKEN"
set_env ADMIN_PASSWORD     "$ADMIN_PASSWORD"
[ "${SET_SECRET:-1}" = "1" ] && set_env SECRET_KEY "$SECRET_KEY"

# ---------------------------------------------------------------------------
say "7/7  Deploying to production"
DEPLOY_URL="$(vercel deploy --prod --yes)"
echo "Deployed: $DEPLOY_URL"

# ---------------------------------------------------------------------------
if [ -n "$SEED_CSV" ]; then
    say "Seeding Turso from $SEED_CSV"
    [ -f "$SEED_CSV" ] || die "SEED_CSV file not found: $SEED_CSV"
    [ -d .venv ] || python3 -m venv .venv
    ./.venv/bin/pip install -q -r requirements.txt
    TURSO_DATABASE_URL="$TURSO_DATABASE_URL" TURSO_AUTH_TOKEN="$TURSO_AUTH_TOKEN" \
        ./.venv/bin/python -c "import db; db.init_db(); c=db.get_db(); print('seeded:', db.import_csv(c, open('$SEED_CSV').read()))"
fi

say "Done"
echo "URL:   $DEPLOY_URL"
echo "Login: click 'Admin login', use the ADMIN_PASSWORD you set."
echo "Re-run this script anytime to redeploy (env vars are refreshed)."
