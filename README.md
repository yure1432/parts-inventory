# Parts Inventory

A small web app to manage a parts inventory. **Public read-only** for everyone;
**login-gated write** (add/edit/delete/import) for admins. Built to hold roughly
1,500–2,000 parts.

This README is also the handoff spec — the stack is fixed (see below), the data
model and behavior are defined, and the repo is a working reference implementation.

---

## 1. What it does

- Anyone can open the site and see the full parts table: **search across all
  fields** and **sort by any column**. No login required.
- Admins log in with a **single shared password** and can:
  - add / edit / delete parts,
  - add **custom fields** (extra columns) when the fixed schema isn't enough,
  - **import a CSV** to bulk-load or update parts,
  - **export a CSV** (backup / round-trip).

There are no per-user accounts by design — admin access is a single shared
internal password. "User of part" is just a free-text field on each part, not a
login identity.

---

## 2. Stack (fixed)

| Layer     | Choice                                    |
|-----------|-------------------------------------------|
| Language  | Python 3.10+                              |
| Web       | FastAPI + Starlette `SessionMiddleware`   |
| Templates | Jinja2 (server-rendered HTML)             |
| DB        | SQLite locally / **Turso (libSQL)** in prod — same SQL, no ORM |
| Frontend  | One HTML page + ~60 lines of vanilla JS   |
| Server    | uvicorn (local) / Vercel Functions (prod) |

**Dual-mode database** (`db.py`): if `TURSO_DATABASE_URL` is set, the app talks
to a hosted Turso/libSQL database over HTTP (`turso_serverless`, pure-Python
DB-API); otherwise it uses a local `sqlite3` file. Both expose the same DB-API,
so the data layer is identical. This is what makes serverless (Vercel) work —
serverless has no persistent disk, so a local SQLite file cannot survive there.

No SPA framework, no ORM, no auth library. At ~2k rows the whole table is sent to
the browser and searched/sorted client-side — instant, no pagination.

> ⚠️ Tested on Python 3.14 with `starlette>=1.6`, which **requires** the
> `TemplateResponse(request, name, context)` argument order (request first). The
> old `TemplateResponse(name, context)` form raises a cryptic error. Keep the
> order as written in `app.py`.

---

## 3. Data model

### `parts` table — core columns (real, typed)

| Column       | Type    | Notes                                  |
|--------------|---------|----------------------------------------|
| `id`         | INTEGER | primary key, autoincrement             |
| `name`       | TEXT    | required                               |
| `details`    | TEXT    | free text / notes                      |
| `location`   | TEXT    | free text; UI offers existing values via a `<datalist>` — typing a new one "adds" it |
| `quantity`   | INTEGER | coerced from input; blank → 0          |
| `part_user`  | TEXT    | the "user of part" free field          |
| `extras`     | TEXT    | JSON blob holding custom-field values  |
| `created_at` | TEXT    | set on insert                          |
| `updated_at` | TEXT    | bumped on update                       |

### `custom_fields` table — the "add a column occasionally" escape hatch

| Column | Type    | Notes                    |
|--------|---------|--------------------------|
| `id`   | INTEGER | primary key              |
| `name` | TEXT    | unique; the field label  |

Custom fields are registered here so **every row shows a consistent set of extra
columns**; the actual values live in each part's `extras` JSON. This keeps the
common path (the 5 core fields) cleanly typed and searchable, while allowing rare
new fields without a schema migration.

**Why not fully dynamic columns?** Adding a column is a twice-a-year event, so a
self-serve schema editor isn't worth its cost. Core fields stay real columns;
extras cover the rest. If a custom field becomes heavily used and needs proper
typing/indexing, promote it to a real column via a one-line migration.

---

## 4. Routes

| Method | Path                  | Access | Purpose                          |
|--------|-----------------------|--------|----------------------------------|
| GET    | `/`                   | public | inventory table (search + sort)  |
| GET    | `/login`              | public | login form                       |
| POST   | `/login`              | public | check password, set session      |
| POST   | `/logout`             | any    | clear session                    |
| GET    | `/export`             | admin  | download all parts as CSV        |
| GET    | `/parts/new`          | admin  | add-part form                    |
| POST   | `/parts`              | admin  | create part                      |
| GET    | `/parts/{id}/edit`    | admin  | edit-part form                   |
| POST   | `/parts/{id}`         | admin  | update part                      |
| POST   | `/parts/{id}/delete`  | admin  | delete part                      |
| POST   | `/fields`             | admin  | add a custom field               |
| POST   | `/import`             | admin  | import CSV (bulk add/update)     |

Admin routes reject non-admins with a 303 redirect to `/login`. Writes use plain
HTML `POST` forms (no JS required to operate; JS only enhances search/sort).

---

## 5. CSV import/export

**Import** (`POST /import`, multipart file upload):

- Headers are matched **case-insensitively** to core columns, with aliases:
  - `name` / `part` / `part name` → name
  - `details` / `detail` / `description` / `desc` → details
  - `location` / `loc` → location
  - `quantity` / `qty` / `count` → quantity
  - `user` / `user of part` / `owner` / `assigned to` → user
  - `id` → row id (used for updates)
- **Unknown headers auto-become custom fields** and their values land in `extras`.
  This is also the second way to introduce a new column.
- **Upsert rule:** a row with an `id` matching an existing part **updates** it;
  every other row **inserts**. A CSV without an `id` column therefore always
  appends — export first (to get ids) if you want a re-import to update in place.
- Rows without a `name` are skipped.
- Returns a summary: `N added, M updated, K new field(s)`.

**Export** (`GET /export`): all parts as CSV, including `id` and every custom
field. Round-trips cleanly with import. See `sample_parts.csv` for the format.

---

## 6. Auth

- One shared password in the `ADMIN_PASSWORD` env var, compared on `POST /login`.
- On success, `session["admin"] = True` in a signed cookie (`SessionMiddleware`,
  signed with `SECRET_KEY`).
- No CSRF protection and no rate limiting — acceptable for an internal tool behind
  a trusted network. Add both if this is ever exposed publicly.

---

## 7. Running it

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env        # then edit ADMIN_PASSWORD and SECRET_KEY
export $(grep -v '^#' .env | xargs)   # or set the vars however you prefer

uvicorn app:app --reload    # dev
# open http://127.0.0.1:8000
```

Production: run behind a reverse proxy (nginx/Caddy) with TLS, set a strong
`SECRET_KEY` and `ADMIN_PASSWORD`, and drop `--reload`:

```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

The SQLite file is created automatically on first run (`DB_PATH`, default
`inventory.db`). Load initial data via the **Import CSV** button in the admin bar.

### Deploying to Vercel (with Turso)

**Fastest path — the script does all of the below:**
```bash
# turso CLI must be installed first (review before running):
#   curl -sSfL https://get.tur.so/install.sh | bash
ADMIN_PASSWORD='your-password' ./deploy.sh
# optional: also bulk-load a CSV into Turso after deploy
ADMIN_PASSWORD='your-password' SEED_CSV=parts.csv ./deploy.sh
```
`deploy.sh` is idempotent: installs the Vercel CLI if missing, logs into Turso,
creates the DB (or reuses it), mints a token, sets all Vercel env vars, and
deploys to production. Re-run it anytime to redeploy. It never hardcodes secrets —
`ADMIN_PASSWORD` comes from the environment or an interactive prompt, and
`SECRET_KEY` is auto-generated once and reused across deploys.

Vercel is serverless — it has **no persistent disk**, so the app must use Turso
(hosted libSQL) instead of a local SQLite file. The manual steps the script
automates, for reference:

1. **Create the Turso database** (needs a free Turso account + the `turso` CLI):
   ```bash
   turso auth login
   turso db create parts-inventory
   turso db show --url parts-inventory        # -> TURSO_DATABASE_URL
   turso db tokens create parts-inventory     # -> TURSO_AUTH_TOKEN
   ```
2. **Set env vars on Vercel** (Project → Settings → Environment Variables), or
   via CLI:
   ```bash
   vercel env add TURSO_DATABASE_URL     # paste the libsql:// URL
   vercel env add TURSO_AUTH_TOKEN       # paste the token
   vercel env add ADMIN_PASSWORD         # the admin login password
   vercel env add SECRET_KEY             # long random value, signs the cookie
   ```
3. **Deploy:**
   ```bash
   vercel            # preview
   vercel --prod     # production
   ```

Vercel auto-detects the FastAPI `app` in `app.py` (zero-config); `vercel.json`
only raises the function timeout to 60s so large CSV imports don't get cut off.
Tables are created automatically on first request (`init_db()` is idempotent).

**Load initial data:** after the first deploy, log in and use **Import CSV**, or
seed the Turso DB directly from a machine with the env vars set:
```bash
export TURSO_DATABASE_URL=... TURSO_AUTH_TOKEN=...
python -c "import db; db.init_db(); c=db.get_db(); print(db.import_csv(c, open('parts.csv').read()))"
```

> Any host with a persistent disk (Fly.io, a VPS, the homelab) can skip Turso
> entirely and run the local-SQLite mode — leave `TURSO_DATABASE_URL` unset.

---

## 8. Tests

```bash
python test_db.py     # -> prints "ok"
```

Covers the non-trivial logic: quantity coercion, the extras round-trip, and CSV
import (insert, custom-field creation, blank/nameless-row handling, id-based
upsert). No test framework — plain asserts. Runs against the local SQLite path;
the Turso driver (`turso_serverless`) exposes the same DB-API, so the same logic
applies unchanged in production.

---

## 9. Files

```
app.py               FastAPI app: routes, auth, request handling
db.py                SQLite data layer + CSV import/export (no framework)
templates/
  base.html          layout, header, nav
  index.html         the inventory table + admin toolbar
  login.html         admin login form
  edit.html          add/edit part form (incl. custom fields)
  import_result.html import summary page
static/
  style.css          styling
  table.js           client-side search + column sort (progressive enhancement)
test_db.py           runnable checks for the data layer
sample_parts.csv     example import file
requirements.txt     pinned-ish deps
vercel.json          Vercel function config (60s timeout for CSV import)
.env.example         config template
```

---

## 10. Deliberate scope cuts (add when actually needed)

- **No per-user accounts / audit log** — single shared admin password. Add named
  accounts + an `edited_by` column if you need attribution.
- **No CSRF / rate limiting** — internal-network assumption. Add for public exposure.
- **No pagination / server-side search** — unnecessary at ~2k rows. Revisit past
  ~20k rows, when shipping the whole table to the browser starts to hurt.
- **No self-serve column *editor*** — custom fields can be added (via the "Add
  field" button or unknown CSV headers) but not renamed/deleted/reordered in the
  UI. Do that with a one-line SQL change on the rare occasion it's needed.
