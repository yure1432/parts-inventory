"""Data layer for the parts inventory.

Core fields are real, typed columns. Admin-added ("custom") fields are stored
per-row in the `extras` JSON blob and registered in the `custom_fields` table so
every row shows a consistent set of columns.

Dual-mode by design:
  * If TURSO_DATABASE_URL is set  -> talk to Turso/libSQL over HTTP
    (turso_serverless, pure-Python DB-API — required for serverless like Vercel,
    which has no persistent disk for a local SQLite file).
  * Otherwise                     -> stdlib sqlite3 against a local file
    (zero-config local dev + tests, no cloud account needed).

Both drivers expose the same DB-API surface (execute, executescript, lastrowid,
`?` params, sqlite3.Row-style rows), so the rest of this file is driver-agnostic.
"""
import csv
import io
import json
import os
import sqlite3

DB_PATH = os.environ.get("DB_PATH", "inventory.db")
TURSO_URL = os.environ.get("TURSO_DATABASE_URL")
TURSO_TOKEN = os.environ.get("TURSO_AUTH_TOKEN")

# Core columns as (db_column, human_label). "user of part" -> part_user because
# `user` is a reserved-ish word and confusing next to login users.
CORE_FIELDS = [
    ("name", "Name"),
    ("details", "Details"),
    ("location", "Location"),
    ("quantity", "Quantity"),
    ("part_user", "User"),
]
CORE_COLUMNS = [c for c, _ in CORE_FIELDS]


def get_db():
    if TURSO_URL:
        import turso_serverless  # lazy: only needed in Turso mode
        conn = turso_serverless.connect(TURSO_URL, auth_token=TURSO_TOKEN)
        conn.row_factory = turso_serverless.Row
        # No PRAGMAs: Turso is a managed server, it handles concurrency itself.
        return conn
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Local sqlite concurrency: WAL lets readers run during a write; busy_timeout
    # makes a blocked writer wait up to 5s instead of raising "database is locked".
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db():
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS parts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            details    TEXT NOT NULL DEFAULT '',
            location   TEXT NOT NULL DEFAULT '',
            quantity   INTEGER NOT NULL DEFAULT 0,
            part_user  TEXT NOT NULL DEFAULT '',
            extras     TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS custom_fields (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );
        """
    )
    conn.commit()
    conn.close()


# ---- custom fields --------------------------------------------------------

def list_custom_fields(conn):
    return [r["name"] for r in conn.execute("SELECT name FROM custom_fields ORDER BY name")]


def add_custom_field(conn, name):
    name = (name or "").strip()
    if not name:
        return
    # ponytail: INSERT OR IGNORE dedupes on the UNIQUE constraint, no pre-check.
    conn.execute("INSERT OR IGNORE INTO custom_fields (name) VALUES (?)", (name,))
    conn.commit()


# ---- parts ----------------------------------------------------------------

def _row_to_part(row, custom_fields):
    part = dict(row)
    extras = json.loads(part.pop("extras") or "{}")
    part["extras"] = {f: extras.get(f, "") for f in custom_fields}
    return part


def list_parts(conn):
    fields = list_custom_fields(conn)
    rows = conn.execute("SELECT * FROM parts ORDER BY name COLLATE NOCASE").fetchall()
    return [_row_to_part(r, fields) for r in rows]


def get_part(conn, part_id):
    row = conn.execute("SELECT * FROM parts WHERE id = ?", (part_id,)).fetchone()
    if row is None:
        return None
    return _row_to_part(row, list_custom_fields(conn))


def list_locations(conn):
    rows = conn.execute(
        "SELECT DISTINCT location FROM parts WHERE location <> '' ORDER BY location COLLATE NOCASE"
    )
    return [r["location"] for r in rows]


def _clean_quantity(value):
    try:
        return int(str(value).strip() or 0)
    except (TypeError, ValueError):
        return 0


def create_part(conn, name, details="", location="", quantity=0, part_user="", extras=None):
    name = (name or "").strip()
    if not name:
        raise ValueError("name is required")
    extras_json = json.dumps(extras or {})
    cur = conn.execute(
        """INSERT INTO parts (name, details, location, quantity, part_user, extras)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (name, details or "", location or "", _clean_quantity(quantity), part_user or "", extras_json),
    )
    conn.commit()
    return cur.lastrowid


def update_part(conn, part_id, name, details="", location="", quantity=0, part_user="", extras=None):
    name = (name or "").strip()
    if not name:
        raise ValueError("name is required")
    extras_json = json.dumps(extras or {})
    conn.execute(
        """UPDATE parts
              SET name=?, details=?, location=?, quantity=?, part_user=?, extras=?,
                  updated_at=datetime('now')
            WHERE id=?""",
        (name, details or "", location or "", _clean_quantity(quantity), part_user or "", extras_json, part_id),
    )
    conn.commit()


def delete_part(conn, part_id):
    conn.execute("DELETE FROM parts WHERE id = ?", (part_id,))
    conn.commit()


# ---- CSV import -----------------------------------------------------------

# Accepted header aliases (case-insensitive) -> core column.
_HEADER_ALIASES = {
    "name": "name", "part": "name", "part name": "name",
    "details": "details", "detail": "details", "description": "details", "desc": "details",
    "location": "location", "loc": "location",
    "quantity": "quantity", "qty": "quantity", "count": "quantity",
    "user": "part_user", "user of part": "part_user", "owner": "part_user", "assigned to": "part_user",
    "id": "id",
}


def import_csv(conn, text):
    """Import CSV text. Rows with a matching `id` update in place; others insert.

    Unknown headers become custom fields automatically and land in `extras`.
    Returns (inserted, updated, new_field_count).
    """
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        return (0, 0, 0)

    # Map each CSV header to a core column or mark it as a custom field.
    col_for = {}
    custom_headers = []
    for h in reader.fieldnames:
        if h is None:
            continue
        key = _HEADER_ALIASES.get(h.strip().lower())
        if key:
            col_for[h] = key
        else:
            col_for[h] = None
            custom_headers.append(h.strip())

    existing = set(list_custom_fields(conn))
    new_fields = 0
    for h in custom_headers:
        if h and h not in existing:
            add_custom_field(conn, h)
            existing.add(h)
            new_fields += 1

    inserted = updated = 0
    for raw in reader:
        core = {"name": "", "details": "", "location": "", "quantity": 0, "part_user": ""}
        extras = {}
        row_id = None
        for header, value in raw.items():
            target = col_for.get(header)
            if target == "id":
                row_id = (value or "").strip() or None
            elif target in core:
                core[target] = value or ""
            elif target is None and header:  # custom field
                extras[header.strip()] = value or ""

        if not (core["name"] or "").strip():
            continue  # skip rows without a name

        if row_id and conn.execute("SELECT 1 FROM parts WHERE id=?", (row_id,)).fetchone():
            update_part(conn, row_id, extras=extras, **core)
            updated += 1
        else:
            create_part(conn, extras=extras, **core)
            inserted += 1

    return (inserted, updated, new_fields)


def export_csv(conn):
    """Serialize all parts to CSV text (id + core + custom fields)."""
    fields = list_custom_fields(conn)
    out = io.StringIO()
    header = ["id"] + [label for _, label in CORE_FIELDS] + fields
    writer = csv.writer(out)
    writer.writerow(header)
    for p in list_parts(conn):
        row = [p["id"], p["name"], p["details"], p["location"], p["quantity"], p["part_user"]]
        row += [p["extras"].get(f, "") for f in fields]
        writer.writerow(row)
    return out.getvalue()
