"""Local AI through Ollama (gemma4:26b by default)."""
from __future__ import annotations

import base64
import io
import json
from typing import Any, Dict, List, Optional

import httpx

from .config import settings
from .returns import POLICY, RETURN_CLASSES

CATEGORIES = [
    "Produce", "Meat & Seafood", "Dairy & Eggs", "Bakery", "Deli & Prepared Food", "Frozen",
    "Pantry", "Snacks & Candy", "Beverages", "Coffee & Tea", "Alcohol", "Tobacco",
    "Household & Cleaning", "Paper Products", "Health & Beauty", "Vitamins & Medicine", "Baby & Kids",
    "Pet", "Electronics", "Appliances", "Home & Kitchen", "Furniture", "Clothing & Shoes",
    "Office & School", "Toys & Sports", "Garden & Outdoor", "Tools & Auto", "Gas",
    "Food Court", "Membership", "Fees & Deposits", "Gift Cards & Tickets", "Services", "Other",
]

# What Costco (US) department numbers usually hold - hints for the model, learned from real receipts
DEPARTMENT_HINTS = {
    12: "snacks, candy, chips, cookies", 13: "dry grocery / pantry, packaged bread, coffee, tea",
    14: "beverages, water, soda, paper towels, toilet paper, cleaning, laundry, pet food",
    16: "beer and wine", 17: "dairy, eggs, milk, refrigerated drinks", 18: "frozen food",
    19: "deli: cheese, deli meats, refrigerated prepared", 20: "health & beauty, personal care, protein drinks",
    21: "software", 23: "hardware, lighting, batteries, storage", 24: "electronics (phones, watches, games)",
    25: "automotive and tools", 26: "sporting goods, luggage, outdoor, seasonal",
    28: "seasonal, party, office and school supplies", 31: "CLOTHING (women's apparel, bras, loungewear)",
    32: "housewares, kitchen tools, cookware", 33: "small kitchen appliances, coffee machines, fans",
    34: "bedding, sheets, towels (domestics)", 36: "books, media, reading glasses", 38: "furniture",
    39: "CLOTHING (men's / kids' apparel, hoodies, jackets, tees)", 44: "CLOTHING (men's basics, socks, shirts)",
    53: "gasoline", 61: "fresh meat and seafood", 62: "in-store bakery", 63: "deli prepared (rotisserie chicken, take & bake)",
    65: "produce (fruit, vegetables)", 75: "tickets and gift cards", 93: "pharmacy, vitamins, OTC medicine",
    95: "Costco membership fee",
}

RETURN_RULES = "\n".join(f"- {k}: {v}" for k, v in POLICY.items())


class AIError(RuntimeError):
    pass


def _chat(messages: List[Dict], schema: Optional[Dict] = None, temperature: float = 0.0) -> str:
    body: Dict[str, Any] = {
        "model": settings.ollama_model,
        "messages": messages,
        "stream": False,
        "think": False,
        "keep_alive": "15m",
        "options": {"temperature": temperature, "num_ctx": 32768},
    }
    if schema:
        body["format"] = schema
    try:
        resp = httpx.post(f"{settings.ollama_url}/api/chat", json=body, timeout=settings.ollama_timeout)
    except httpx.HTTPError as e:
        raise AIError(f"Ollama not reachable at {settings.ollama_url}: {e}") from e
    if resp.status_code != 200:
        raise AIError(f"Ollama error {resp.status_code}: {resp.text[:300]}")
    return resp.json()["message"]["content"]


def _json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[1]
    return json.loads(text)


def health() -> Dict:
    try:
        resp = httpx.get(f"{settings.ollama_url}/api/tags", timeout=5)
        models = [m["name"] for m in resp.json().get("models", [])]
    except Exception as e:
        return {"reachable": False, "url": settings.ollama_url, "model": settings.ollama_model, "error": str(e)}
    return {"reachable": True, "url": settings.ollama_url, "model": settings.ollama_model,
            "model_present": settings.ollama_model in models}


# ---------------------------------------------------------------- categorise products

ENRICH_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "item_number": {"type": "string"},
            "name": {"type": "string"},
            "brand": {"type": "string"},
            "category": {"type": "string", "enum": CATEGORIES},
            "return_class": {"type": "string", "enum": RETURN_CLASSES},
            "return_note": {"type": "string"},
        },
        "required": ["item_number", "name", "brand", "category", "return_class", "return_note"],
    }}},
    "required": ["items"],
}

ENRICH_PROMPT = f"""You are an expert on Costco Wholesale (USA) products and how they are printed on receipts.
Receipt text is abbreviated, e.g. "KS" = Kirkland Signature, "ORG" = organic, "BNLS" = boneless,
"24CT" = 24 count. For each item, give:
- name: a short readable product name with size (e.g. "Kirkland Signature Organic Eggs, 24 ct")
- brand: the brand ("Kirkland Signature" for KS) or "" when unknown
Each item also has "line2" (Costco's second description line: size, brand, colour, or codes like
"FY24 P240" - FY = fashion/seasonal year, typical for clothing and home goods), the Costco
"department" number with a hint of what it usually holds, and the price. Trust the department hint
over guesses from the abbreviation: e.g. "CKPUFFJACKET" in a clothing department is a Calvin Klein puffer
jacket, not chicken. "Appliances" is ONLY for real appliances (fridges, washers, toasters, vacuums),
never for clothing.
- category: one of the allowed categories. Hints: eggs, milk, cheese, butter, yogurt -> "Dairy & Eggs";
  paper towels, toilet paper, napkins, tissues -> "Paper Products"; "Food Court" ONLY for food-court
  orders (hot dog combo, pizza slice, churro, frozen yogurt); rotisserie chicken -> "Deli & Prepared Food"
- return_class under Costco's return policy:
{RETURN_RULES}
  Small electronics accessories (cables, chargers, batteries, headphones), small kitchen appliances
  and all food are "standard". Bottle deposits / CRV / bag fees are "Fees & Deposits" + non_returnable.
- return_note: one short sentence on returning this particular item.
Return every item_number you were given, unchanged."""


def enrich_products(rows: List[Dict]) -> Dict[str, Dict]:
    items = []
    for r in rows:
        item = {"item_number": r["item_number"], "receipt_text": r["receipt_name"]}
        if r.get("description2"):
            item["line2"] = r["description2"]
        if r.get("department") is not None:
            item["department"] = r["department"]
            if r["department"] in DEPARTMENT_HINTS:
                item["department_usually"] = DEPARTMENT_HINTS[r["department"]]
        if r.get("price"):
            item["price"] = r["price"]
        items.append(item)
    content = _chat([
        {"role": "system", "content": ENRICH_PROMPT},
        {"role": "user", "content": json.dumps(items)},
    ], schema=ENRICH_SCHEMA)
    out = _json(content).get("items", [])
    return {str(i.get("item_number")): i for i in out if isinstance(i, dict)}


# ---------------------------------------------------------------- receipt photos

RECEIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "date": {"type": "string", "description": "YYYY-MM-DD"},
        "time": {"type": "string", "description": "HH:MM 24h"},
        "warehouse": {"type": "string"},
        "barcode": {"type": "string"},
        "is_refund": {"type": "boolean"},
        "subtotal": {"type": "number"},
        "tax": {"type": "number"},
        "total": {"type": "number"},
        "items": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "item_number": {"type": "string"},
                "description": {"type": "string"},
                "quantity": {"type": "number"},
                "amount": {"type": "number"},
            },
            "required": ["item_number", "description", "quantity", "amount"],
        }},
    },
    "required": ["date", "time", "warehouse", "barcode", "is_refund", "subtotal", "tax", "total", "items"],
}

RECEIPT_PROMPT = """Read this Costco warehouse receipt and transcribe every line item.
Costco lines look like "E 1234567 KS ORG EGGS 6.99 A" -> item_number "1234567", description "KS ORG EGGS",
amount 6.99. Instant savings look like "1234568 /1234567 2.00-": keep them as their own item with
description "/1234567" and a NEGATIVE amount. Quantity is 1 unless a line such as "2 @ 4.99" precedes the item.
Leave item_number "" when none is printed. barcode is the long transaction number under the bar code
(digits only, "" if unreadable). date as YYYY-MM-DD, time as HH:MM. Do not invent items."""


def _prepare_image(data: bytes) -> bytes:
    try:
        from PIL import Image, ImageOps
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except ImportError:
            pass
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
        img.thumbnail((settings.max_image_side, settings.max_image_side))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=90)
        return buf.getvalue()
    except Exception:
        return data


def read_receipt_photo(data: bytes) -> Dict:
    image = base64.b64encode(_prepare_image(data)).decode()
    content = _chat([
        {"role": "system", "content": RECEIPT_PROMPT},
        {"role": "user", "content": "Transcribe this receipt.", "images": [image]},
    ], schema=RECEIPT_SCHEMA)
    return _json(content)


def photo_to_receipt(extracted: Dict, image_hash: str) -> Dict:
    """Convert the model's transcription into the costco.com receipt format the importer reads."""
    barcode = "".join(ch for ch in str(extracted.get("barcode") or "") if ch.isdigit())
    when = f"{extracted.get('date') or ''}T{extracted.get('time') or '00:00'}"
    items = []
    for it in extracted.get("items") or []:
        qty = it.get("quantity") or 1
        amount = it.get("amount") or 0
        items.append({"itemNumber": it.get("item_number") or "", "itemDescription01": it.get("description") or "",
                      "unit": qty, "amount": amount,
                      "itemUnitPriceAmount": round(abs(amount) / abs(qty), 2) if qty else abs(amount)})
    return {
        "transactionBarcode": barcode if len(barcode) >= 10 else f"photo-{image_hash[:16]}",
        "transactionDateTime": when,
        "warehouseName": extracted.get("warehouse") or "",
        "transactionType": "Refund" if extracted.get("is_refund") else "Sales",
        "subTotal": extracted.get("subtotal"), "taxes": extracted.get("tax"), "total": extracted.get("total"),
        "itemArray": items,
    }


# ---------------------------------------------------------------- questions

ASK_PROMPT = f"""You help a couple understand their Costco purchases. Answer the question using ONLY the
data provided (JSON). Be concise and specific: quote dates, prices, counts and who bought it.
If the data doesn't contain the answer, say so. Money is USD.
For return questions use Costco's policy:
{RETURN_RULES}
and the purchase date (today's date is given). Remind them to keep the item/packaging and that
the membership counter has the final say."""


def answer(question: str, context: Dict) -> str:
    return _chat([
        {"role": "system", "content": ASK_PROMPT},
        {"role": "user", "content": f"DATA:\n{json.dumps(context, default=str)}\n\nQUESTION: {question}"},
    ], temperature=0.2)
