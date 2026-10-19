"""Import receipts in the format of costco.com's receipt API (see scripts/costco-export.js).

A receipt looks like:
  {"transactionBarcode": "21134300501862310011", "transactionDateTime": "2024-03-15T14:22:00",
   "warehouseName": "UNION", "transactionType": "Sales", "subTotal": 120.5, "taxes": 3.1,
   "total": 123.6, "membershipNumber": "...",
   "itemArray": [{"itemNumber": "1234567", "itemDescription01": "KS ORG EGGS", "unit": 1,
                  "amount": 6.99, "itemUnitPriceAmount": 6.99}, ...]}

Instant savings are separate lines with a negative amount whose description points
to the discounted item ("/1234567"). On refund receipts every line is a return.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .matcher import ProductIndex, get_or_create_product

DISCOUNT_REF = re.compile(r"^\s*(?:TPD\s*)?/\s*(\d{3,})")


def _f(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_dt(value: str) -> str:
    value = (value or "").strip()
    try:
        return datetime.fromisoformat(value[:19]).isoformat(timespec="minutes")
    except ValueError:
        pass
    for fmt in ("%m/%d/%Y %H:%M", "%m/%d/%Y %I:%M %p", "%m/%d/%Y", "%m/%d/%y %H:%M", "%m/%d/%y"):
        try:
            return datetime.strptime(value, fmt).isoformat(timespec="minutes")
        except ValueError:
            continue
    raise ValueError(f"unrecognised receipt date: {value!r}")


def _kind(r: Dict) -> str:
    text = " ".join(str(r.get(k) or "") for k in ("receiptType", "documentType", "transactionType")).lower()
    items = r.get("itemArray") or []
    if "wash" in text and "gas" not in text:
        return "carwash"
    # costco.com sends fuelUnitQuantity (e.g. 10) on every warehouse item too, so only real
    # fuel fields count: a grade code or a unit of measure such as "GAL"
    if "gas" in text or "fuel" in text or any(i.get("fuelGradeCode") or i.get("fuelUomCode") for i in items):
        return "gas"
    return "warehouse"


def _is_refund(r: Dict) -> bool:
    t = str(r.get("transactionType") or "").lower()
    return "refund" in t or "return" in t or _f(r.get("total")) < 0


def _dept(item: Dict) -> Optional[int]:
    try:
        return int(item.get("itemDepartmentNumber"))
    except (TypeError, ValueError):
        return None


def _description(item: Dict) -> str:
    return (item.get("itemDescription01") or item.get("itemDescription02") or "").strip()


def extract_receipts(payload: Any) -> List[Dict]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if isinstance(payload.get("receipts"), list):
            return payload["receipts"]
        nested = (payload.get("data") or {}).get("receiptsWithCounts") or {}
        if isinstance(nested.get("receipts"), list):
            return nested["receipts"]
        if "itemArray" in payload:
            return [payload]
    raise ValueError("No receipts found: expected a list or {\"receipts\": [...]}")


def _lines(r: Dict, kind: str, refund: bool) -> Tuple[List[Dict], List[Tuple[Optional[str], float]]]:
    """Item lines with their instant savings attached, plus the raw savings lines."""
    lines: List[Dict] = []
    discounts = []
    for item in r.get("itemArray") or []:
        desc = _description(item)
        amount = _f(item.get("amount"))
        ref = DISCOUNT_REF.match(desc)
        if ref or (amount < 0 and not refund):
            discounts.append((ref.group(1) if ref else None, amount))
            continue
        qty = _f(item.get("fuelUnitQuantity")) if kind == "gas" else _f(item.get("unit"))
        qty = abs(qty) or 1.0
        unit_price = abs(_f(item.get("itemUnitPriceAmount"))) or round(abs(amount) / qty, 3)
        if refund:
            qty, amount = -qty, -abs(amount)
        lines.append({"item_number": str(item.get("itemNumber") or "").strip(), "desc": desc,
                      "desc2": (item.get("itemDescription02") or "").strip() or None,
                      "dept": _dept(item), "qty": qty, "unit_price": unit_price, "amount": amount, "discount": 0.0})

    # attach each instant saving to the item it references, else to the line printed before it
    for ref, amount in discounts:
        target = next((l for l in reversed(lines) if ref and l["item_number"] == ref), None)
        target = target or (lines[-1] if lines else None)
        if target:
            target["discount"] += amount
    return lines, discounts


def _write_purchases(con: sqlite3.Connection, index: ProductIndex, rid: int, lines: List[Dict],
                     kind: str) -> None:
    for n, line in enumerate(lines, 1):
        pid = get_or_create_product(con, index, line["item_number"], line["desc"], kind,
                                    line.get("desc2"), line.get("dept"))
        con.execute(
            """INSERT INTO purchases(receipt_id, product_id, line_no, quantity, unit_price, amount, discount)
               VALUES (?,?,?,?,?,?,?)""",
            (rid, pid, n, line["qty"], line["unit_price"], line["amount"], round(line["discount"], 2)),
        )


def import_receipt(con: sqlite3.Connection, index: ProductIndex, r: Dict, person_id: int,
                   source: str) -> Optional[Dict]:
    """Insert one receipt. Returns None when it was imported before (same barcode)."""
    barcode = str(r.get("transactionBarcode") or "").strip()
    if not barcode:
        barcode = "nobarcode-" + hashlib.sha1(json.dumps(r, sort_keys=True).encode()).hexdigest()[:16]
    if con.execute("SELECT 1 FROM receipts WHERE barcode = ?", (barcode,)).fetchone():
        return None

    purchased_at = _parse_dt(r.get("transactionDateTime") or r.get("transactionDate") or "")
    kind, refund = _kind(r), _is_refund(r)

    lines, discounts = _lines(r, kind, refund)

    savings = _f(r.get("instantSavings")) or -sum(a for _, a in discounts if a < 0)
    rid = con.execute(
        """INSERT INTO receipts(barcode, person_id, purchased_at, purchase_date, warehouse, kind, is_refund,
                                subtotal, taxes, total, instant_savings, membership_number, source, raw)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (barcode, person_id, purchased_at, purchased_at[:10],
         r.get("warehouseName") or r.get("warehouseShortName"), kind, int(refund),
         _f(r.get("subTotal")), _f(r.get("taxes")), _f(r.get("total")), abs(savings),
         str(r.get("membershipNumber") or "") or None, source, json.dumps(r)),
    ).lastrowid

    _write_purchases(con, index, rid, lines, kind)
    return {"id": rid, "barcode": barcode, "date": purchased_at[:10], "items": len(lines)}


def import_payload(con: sqlite3.Connection, payload: Any, person_id: int, source: str) -> Dict:
    receipts = extract_receipts(payload)
    index = ProductIndex(con)
    products_before = con.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    stats = {"received": len(receipts), "imported": 0, "duplicates": 0, "items": 0, "errors": []}
    for r in receipts:
        try:
            con.execute("SAVEPOINT rec")
            res = import_receipt(con, index, r, person_id, source)
            con.execute("RELEASE rec")
        except Exception as e:  # keep importing the others
            con.execute("ROLLBACK TO rec")
            con.execute("RELEASE rec")
            index = ProductIndex(con)  # drop products added by the rolled-back receipt
            stats["errors"].append(f"{r.get('transactionBarcode') or '?'}: {e}")
            continue
        if res is None:
            stats["duplicates"] += 1
        else:
            stats["imported"] += 1
            stats["items"] += res["items"]
    stats["new_products"] = con.execute("SELECT COUNT(*) FROM products").fetchone()[0] - products_before
    return stats


def rebuild_from_raw(con: sqlite3.Connection) -> Dict:
    """Re-derive receipt kind and all purchase lines from the stored raw JSON.

    Products keep their ids (same item numbers); ones wrongly treated as fuel go back to the AI.
    """
    index = ProductIndex(con)
    rows = con.execute("SELECT id, raw FROM receipts WHERE raw IS NOT NULL").fetchall()
    for row in rows:
        r = json.loads(row["raw"])
        kind, refund = _kind(r), _is_refund(r)
        con.execute("UPDATE receipts SET kind = ?, is_refund = ? WHERE id = ?", (kind, int(refund), row["id"]))
        con.execute("DELETE FROM purchases WHERE receipt_id = ?", (row["id"],))
        lines, _ = _lines(r, kind, refund)
        _write_purchases(con, index, row["id"], lines, kind)
    reset = con.execute(
        """UPDATE products SET name = NULL, brand = NULL, category = NULL, return_class = NULL,
                  return_note = NULL, ai_status = 'pending'
           WHERE category = 'Gas' AND ai_status = 'done' AND id NOT IN (
               SELECT pu.product_id FROM purchases pu JOIN receipts r ON r.id = pu.receipt_id
               WHERE r.kind = 'gas')""").rowcount
    con.execute("DELETE FROM products WHERE id NOT IN (SELECT product_id FROM purchases)")
    return {"receipts": len(rows), "products_reset": reset}


def backfill_item_details(con: sqlite3.Connection) -> int:
    """Fill products.description2 / department from the stored receipts (latest receipt wins)."""
    details: Dict[str, Tuple[Optional[str], Optional[int]]] = {}
    for row in con.execute("SELECT raw FROM receipts WHERE raw IS NOT NULL ORDER BY purchased_at"):
        for item in json.loads(row["raw"]).get("itemArray") or []:
            number = str(item.get("itemNumber") or "").strip()
            if number and not DISCOUNT_REF.match(_description(item)):
                details[number] = ((item.get("itemDescription02") or "").strip() or None, _dept(item))
    n = 0
    for number, (desc2, dept) in details.items():
        n += con.execute("UPDATE products SET description2 = ?, department = ? WHERE item_number = ?",
                         (desc2, dept, number)).rowcount
    return n
