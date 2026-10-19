from __future__ import annotations

import calendar as cal
import hashlib
import json
import logging
import sqlite3
from pathlib import Path
from typing import Dict, Iterator, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import ai, ai_worker, importer, queries
from .db import add_person, get_db, init_db
from .returns import POLICY, fixed_product

logging.basicConfig(level=logging.INFO)
STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Costco Tracker")


@app.on_event("startup")
def startup() -> None:
    init_db()
    with get_db() as con:
        # v1: every warehouse receipt had been imported as gas (costco.com sends fuelUnitQuantity on all items)
        if con.execute("PRAGMA user_version").fetchone()[0] < 1:
            logging.info("Rebuilding purchases from raw receipts: %s", importer.rebuild_from_raw(con))
            con.execute("PRAGMA user_version = 1")
        # v2: keep costco's department + 2nd description line, membership by rule, re-categorise with them
        if con.execute("PRAGMA user_version").fetchone()[0] < 2:
            logging.info("Backfilled item details for %s products", importer.backfill_item_details(con))
            for p in con.execute("SELECT id, receipt_name, description2, department FROM products "
                                 "WHERE ai_status != 'manual'").fetchall():
                fixed = fixed_product(p["receipt_name"], p["description2"], p["department"])
                if fixed:
                    con.execute("UPDATE products SET name=?, brand=?, category=?, return_class=?, return_note=?, "
                                "ai_status='done' WHERE id=?", (*fixed.values(), p["id"]))
            con.execute("""UPDATE products SET ai_status = 'pending'
                           WHERE ai_status IN ('done','failed') AND COALESCE(category,'') NOT IN ('Gas','Membership')""")
            con.execute("PRAGMA user_version = 2")
    with get_db() as con:
        pending = con.execute("SELECT 1 FROM products WHERE ai_status = 'pending' LIMIT 1").fetchone()
    if pending:
        ai_worker.start()
    ai_worker.start_scheduler()


def db() -> Iterator[sqlite3.Connection]:
    with get_db() as con:
        yield con


def _person_id(con: sqlite3.Connection, person_id: int) -> int:
    if not con.execute("SELECT 1 FROM people WHERE id = ?", (person_id,)).fetchone():
        raise HTTPException(400, "Unknown person")
    return person_id


# ---------------------------------------------------------------- health / people

@app.get("/api/health")
def health() -> Dict:
    return {"ok": True}


@app.get("/api/health/llm")
def health_llm() -> Dict:
    return ai.health()


@app.get("/api/people")
def get_people(con=Depends(db)):
    return queries.people(con)


class PersonIn(BaseModel):
    name: str


@app.post("/api/people")
def post_person(body: PersonIn, con=Depends(db)):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Name required")
    try:
        return {"id": add_person(con, name)}
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Already exists")


@app.patch("/api/people/{pid}")
def rename_person(pid: int, body: PersonIn, con=Depends(db)):
    con.execute("UPDATE people SET name = ? WHERE id = ?", (body.name.strip(), pid))
    return {"ok": True}


# ---------------------------------------------------------------- import

@app.post("/api/import/json")
async def import_json(person_id: int = Form(...), file: UploadFile = File(...), con=Depends(db)):
    _person_id(con, person_id)
    try:
        payload = json.loads(await file.read())
        stats = importer.import_payload(con, payload, person_id, "costco-json")
    except (ValueError, json.JSONDecodeError) as e:
        raise HTTPException(400, f"Could not read {file.filename}: {e}")
    con.commit()
    if stats["new_products"]:
        ai_worker.start()
    return stats


@app.post("/api/import/photo")
async def import_photo(person_id: int = Form(...), file: UploadFile = File(...), con=Depends(db)):
    _person_id(con, person_id)
    data = await file.read()
    try:
        extracted = ai.read_receipt_photo(data)
    except ai.AIError as e:
        raise HTTPException(502, str(e))
    except ValueError as e:
        raise HTTPException(502, f"Model returned unreadable output: {e}")
    receipt = ai.photo_to_receipt(extracted, hashlib.sha1(data).hexdigest())
    if not receipt["itemArray"]:
        raise HTTPException(422, "No items found on the photo")
    stats = importer.import_payload(con, [receipt], person_id, "photo")
    con.commit()
    if stats["new_products"]:
        ai_worker.start()
    return {**stats, "extracted": extracted}


@app.delete("/api/receipts/{rid}")
def delete_receipt(rid: int, con=Depends(db)):
    con.execute("DELETE FROM receipts WHERE id = ?", (rid,))
    con.execute("DELETE FROM products WHERE id NOT IN (SELECT product_id FROM purchases)")
    return {"ok": True}


# ---------------------------------------------------------------- views

@app.get("/api/summary")
def get_summary(person: Optional[int] = None, con=Depends(db)):
    return queries.summary(con, person)


@app.get("/api/home")
def get_home(person: Optional[int] = None, con=Depends(db)):
    return queries.home(con, person)


@app.get("/api/period")
def get_period(start: str, end: str, person: Optional[int] = None, vs_start: Optional[str] = None,
               vs_end: Optional[str] = None, con=Depends(db)):
    return queries.period_summary(con, start, end, person, vs_start, vs_end)


@app.get("/api/categories")
def get_categories(person: Optional[int] = None, con=Depends(db)):
    return queries.categories_overview(con, person)


@app.get("/api/category")
def get_category(name: str, person: Optional[int] = None, con=Depends(db)):
    return queries.category_detail(con, name, person)


@app.get("/api/calendar")
def get_calendar(month: str, person: Optional[int] = None, product: Optional[int] = None, con=Depends(db)):
    """month = YYYY-MM, or YYYY for the whole year."""
    if len(month) == 4:
        start, end = f"{month}-01-01", f"{month}-12-31"
    else:
        y, m = map(int, month.split("-"))
        start, end = f"{y:04d}-{m:02d}-01", f"{y:04d}-{m:02d}-{cal.monthrange(y, m)[1]:02d}"
    return queries.calendar(con, start, end, person, product)


@app.get("/api/receipts")
def get_receipts(person: Optional[int] = None, start: Optional[str] = None, end: Optional[str] = None,
                 limit: int = 200, offset: int = 0, con=Depends(db)):
    return queries.receipts(con, person, start, end, limit, offset)


@app.get("/api/receipts/{rid}")
def get_receipt(rid: int, con=Depends(db)):
    r = queries.receipt(con, rid)
    if not r:
        raise HTTPException(404)
    return r


@app.get("/api/products")
def get_products(person: Optional[int] = None, q: Optional[str] = None, category: Optional[str] = None,
                 sort: str = "last", con=Depends(db)):
    return queries.products(con, person, q, category, sort)


@app.get("/api/products/{pid}")
def get_product(pid: int, person: Optional[int] = None, con=Depends(db)):
    p = queries.product(con, pid, person)
    if not p:
        raise HTTPException(404)
    return p


class ProductPatch(BaseModel):
    name: Optional[str] = None
    brand: Optional[str] = None
    category: Optional[str] = None
    return_class: Optional[str] = None
    return_note: Optional[str] = None


@app.patch("/api/products/{pid}")
def patch_product(pid: int, body: ProductPatch, con=Depends(db)):
    queries.update_product(con, pid, body.dict(exclude_none=True))
    return queries.product(con, pid, None)


@app.get("/api/returns")
def get_returns(person: Optional[int] = None, con=Depends(db)):
    return queries.returns_view(con, person)


@app.get("/api/meta")
def meta():
    return {"categories": ai.CATEGORIES, "return_policy": POLICY}


# ---------------------------------------------------------------- AI

@app.get("/api/ai/status")
def ai_status():
    return ai_worker.state()


@app.post("/api/ai/categorize")
def ai_categorize(retry_failed: bool = False, redo_all: bool = False, con=Depends(db)):
    if redo_all:
        con.execute("UPDATE products SET ai_status = 'pending' WHERE ai_status IN ('done','failed') AND COALESCE(category, '') NOT IN ('Gas', 'Membership')")
    elif retry_failed:
        con.execute("UPDATE products SET ai_status = 'pending' WHERE ai_status = 'failed'")
    con.commit()
    return {"started": ai_worker.start()}


class Question(BaseModel):
    question: str
    person: Optional[int] = None


@app.post("/api/ask")
def ask(body: Question, con=Depends(db)):
    context = queries.ask_context(con, body.question, body.person)
    try:
        text = ai.answer(body.question, context)
    except ai.AIError as e:
        raise HTTPException(502, str(e))
    return {"answer": text, "products_used": [p["product"] for p in context["matching_products"]]}


# ---------------------------------------------------------------- static UI

@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/costco-export.js")
def export_script():
    return FileResponse(Path(__file__).parent.parent / "scripts" / "costco-export.js", media_type="text/javascript")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
