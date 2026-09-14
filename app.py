"""Parts inventory web app.

Public read-only table for everyone; login-gated add/edit/delete/import for admins.
Single shared admin password (ADMIN_PASSWORD). No per-user accounts by design.
"""
import os

from fastapi import FastAPI, Form, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import db

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-change-me")

# Absolute paths so templates/static resolve regardless of the process cwd
# (Vercel's serverless bundle does not run from the repo root).
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="Parts Inventory")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

db.init_db()


def is_admin(request: Request) -> bool:
    return bool(request.session.get("admin"))


def require_admin(request: Request):
    if not is_admin(request):
        # 303 so the browser redirects a rejected POST to the login page.
        raise HTTPException(status_code=303, headers={"Location": "/login"})


# ---- read-only (public) ---------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    conn = db.get_db()
    try:
        ctx = {
            "request": request,
            "parts": db.list_parts(conn),
            "core_fields": db.CORE_FIELDS,
            "custom_fields": db.list_custom_fields(conn),
            "is_admin": is_admin(request),
        }
    finally:
        conn.close()
    return templates.TemplateResponse(request, "index.html", ctx)


@app.get("/export")
def export(request: Request):
    require_admin(request)
    conn = db.get_db()
    try:
        csv_text = db.export_csv(conn)
    finally:
        conn.close()
    return PlainTextResponse(
        csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=inventory.csv"},
    )


# ---- auth -----------------------------------------------------------------

@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, error: str = ""):
    return templates.TemplateResponse(request, "login.html", {"error": error})


@app.post("/login")
def login(request: Request, password: str = Form(...)):
    if password == ADMIN_PASSWORD:
        request.session["admin"] = True
        return RedirectResponse("/", status_code=303)
    return RedirectResponse("/login?error=1", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


# ---- admin writes ---------------------------------------------------------

def _extras_from_form(form, custom_fields):
    return {f: (form.get(f"extra_{f}") or "") for f in custom_fields}


@app.get("/parts/new", response_class=HTMLResponse)
def new_part_form(request: Request):
    require_admin(request)
    conn = db.get_db()
    try:
        ctx = {
            "request": request,
            "part": None,
            "core_fields": db.CORE_FIELDS,
            "custom_fields": db.list_custom_fields(conn),
            "locations": db.list_locations(conn),
        }
    finally:
        conn.close()
    return templates.TemplateResponse(request, "edit.html", ctx)


@app.post("/parts")
async def create_part(request: Request):
    require_admin(request)
    form = await request.form()
    conn = db.get_db()
    try:
        extras = _extras_from_form(form, db.list_custom_fields(conn))
        db.create_part(
            conn,
            name=form.get("name", ""),
            details=form.get("details", ""),
            location=form.get("location", ""),
            quantity=form.get("quantity", 0),
            part_user=form.get("part_user", ""),
            extras=extras,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()
    return RedirectResponse("/", status_code=303)


@app.get("/parts/{part_id}/edit", response_class=HTMLResponse)
def edit_part_form(request: Request, part_id: int):
    require_admin(request)
    conn = db.get_db()
    try:
        part = db.get_part(conn, part_id)
        if part is None:
            raise HTTPException(status_code=404, detail="part not found")
        ctx = {
            "request": request,
            "part": part,
            "core_fields": db.CORE_FIELDS,
            "custom_fields": db.list_custom_fields(conn),
            "locations": db.list_locations(conn),
        }
    finally:
        conn.close()
    return templates.TemplateResponse(request, "edit.html", ctx)


@app.post("/parts/{part_id}")
async def update_part(request: Request, part_id: int):
    require_admin(request)
    form = await request.form()
    conn = db.get_db()
    try:
        if db.get_part(conn, part_id) is None:
            raise HTTPException(status_code=404, detail="part not found")
        extras = _extras_from_form(form, db.list_custom_fields(conn))
        db.update_part(
            conn,
            part_id,
            name=form.get("name", ""),
            details=form.get("details", ""),
            location=form.get("location", ""),
            quantity=form.get("quantity", 0),
            part_user=form.get("part_user", ""),
            extras=extras,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()
    return RedirectResponse("/", status_code=303)


@app.post("/parts/{part_id}/delete")
def remove_part(request: Request, part_id: int):
    require_admin(request)
    conn = db.get_db()
    try:
        db.delete_part(conn, part_id)
    finally:
        conn.close()
    return RedirectResponse("/", status_code=303)


@app.post("/fields")
def add_field(request: Request, name: str = Form(...)):
    require_admin(request)
    conn = db.get_db()
    try:
        db.add_custom_field(conn, name)
    finally:
        conn.close()
    return RedirectResponse("/", status_code=303)


@app.post("/import", response_class=HTMLResponse)
async def import_parts(request: Request, file: UploadFile = File(...)):
    require_admin(request)
    raw = (await file.read()).decode("utf-8-sig")
    conn = db.get_db()
    try:
        inserted, updated, new_fields = db.import_csv(conn, raw)
    finally:
        conn.close()
    msg = f"Imported: {inserted} added, {updated} updated, {new_fields} new field(s)."
    return templates.TemplateResponse(request, "import_result.html", {"message": msg})
