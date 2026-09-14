"""Runnable checks for the non-trivial data logic: CSV import (upsert + custom
fields) and the extras round-trip. No framework needed — `python test_db.py`.
"""
import os
import tempfile

# Point the module at a throwaway DB before importing it.
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["DB_PATH"] = _tmp.name

import db  # noqa: E402

db.init_db()


def test_create_and_extras_roundtrip():
    conn = db.get_db()
    db.add_custom_field(conn, "Supplier")
    pid = db.create_part(conn, name="Widget", quantity="5", extras={"Supplier": "Acme"})
    p = db.get_part(conn, pid)
    assert p["name"] == "Widget"
    assert p["quantity"] == 5, "quantity should be coerced to int"
    assert p["extras"]["Supplier"] == "Acme"
    conn.close()


def test_import_inserts_and_creates_custom_field():
    conn = db.get_db()
    csv_text = (
        "Name,Qty,Location,Vendor\n"
        "Bolt,10,Shelf A,Acme\n"
        "Nut,,Shelf A,\n"          # blank qty -> 0
        ",5,Nowhere,Ghost\n"        # no name -> skipped
    )
    inserted, updated, new_fields = db.import_csv(conn, csv_text)
    assert inserted == 2, inserted
    assert updated == 0
    assert new_fields == 1, "Vendor should become a custom field"
    assert "Vendor" in db.list_custom_fields(conn)
    names = {p["name"] for p in db.list_parts(conn)}
    assert "Bolt" in names and "Nut" in names
    conn.close()


def test_import_upserts_by_id():
    conn = db.get_db()
    pid = db.create_part(conn, name="Original", quantity=1)
    csv_text = f"id,Name,Qty\n{pid},Renamed,99\n"
    inserted, updated, _ = db.import_csv(conn, csv_text)
    assert inserted == 0 and updated == 1, (inserted, updated)
    p = db.get_part(conn, pid)
    assert p["name"] == "Renamed" and p["quantity"] == 99
    conn.close()


if __name__ == "__main__":
    test_create_and_extras_roundtrip()
    test_import_inserts_and_creates_custom_field()
    test_import_upserts_by_id()
    print("ok")
    os.unlink(_tmp.name)
