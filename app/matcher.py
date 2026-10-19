"""Matching receipt text to products with rapidfuzz.

Costco receipts print short, upper-case, abbreviated names ("KS ORG EGGS 24CT").
They are normalised (abbreviations expanded) and then compared with rapidfuzz to:
  * group the same product sold under different item numbers,
  * find the known product for a line that has no item number (receipt photos),
  * search products by free text ("eggs", "kirkland water").
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from typing import Dict, List, Optional, Tuple

from rapidfuzz import fuzz, process

from .returns import fixed_product

ABBREVIATIONS = {
    "KS": "KIRKLAND", "KIRK": "KIRKLAND", "ORG": "ORGANIC", "ORGNC": "ORGANIC",
    "CHKN": "CHICKEN", "CKN": "CHICKEN", "CHIX": "CHICKEN", "BNLS": "BONELESS", "SKNLS": "SKINLESS",
    "BRST": "BREAST", "THGH": "THIGH", "GRND": "GROUND", "BF": "BEEF", "PRK": "PORK", "SLMN": "SALMON",
    "WTR": "WATER", "SPRKLG": "SPARKLING", "SPKL": "SPARKLING", "OJ": "ORANGE JUICE", "JCE": "JUICE",
    "PPR": "PAPER", "TWL": "TOWEL", "TWLS": "TOWELS", "TP": "TOILET PAPER", "BTH": "BATH", "TISS": "TISSUE",
    "VEG": "VEGETABLE", "VEGS": "VEGETABLES", "FRZ": "FROZEN", "FZN": "FROZEN",
    "CHS": "CHEESE", "CHZ": "CHEESE", "MOZZ": "MOZZARELLA", "PARM": "PARMESAN", "MLK": "MILK",
    "YOG": "YOGURT", "GRK": "GREEK", "BTR": "BUTTER", "CRM": "CREAM", "PNUT": "PEANUT",
    "STRWBRY": "STRAWBERRY", "STRAWB": "STRAWBERRY", "BLUEBRY": "BLUEBERRY", "BLUBRY": "BLUEBERRY",
    "RASPB": "RASPBERRY", "AVOC": "AVOCADO", "BNNA": "BANANA", "BANANS": "BANANAS", "TOM": "TOMATO",
    "DET": "DETERGENT", "LNDRY": "LAUNDRY", "DSHWSHR": "DISHWASHER", "DISHWSH": "DISHWASHER",
    "BRD": "BREAD", "CROISS": "CROISSANT", "MUFF": "MUFFIN",
    "VIT": "VITAMIN", "MULTIVIT": "MULTIVITAMIN", "SUPP": "SUPPLEMENT",
    "PK": "PACK", "CT": "COUNT", "OZ": "OUNCE", "LB": "POUND", "LBS": "POUND", "GAL": "GALLON",
}

_NON_ALNUM = re.compile(r"[^A-Z0-9]+")
_NUMBERS = re.compile(r"\d+(?:\.\d+)?")
# "24CT" -> "24 CT" so the unit is expanded and the number compared on its own
_GLUED_UNIT = re.compile(r"(\d)([A-Z])")


def normalize(text: str) -> str:
    s = _GLUED_UNIT.sub(r"\1 \2", (text or "").upper())
    tokens = _NON_ALNUM.sub(" ", s).split()
    return " ".join(ABBREVIATIONS.get(t, t) for t in tokens)


def numbers(text: str) -> Tuple[str, ...]:
    """Sizes/counts in the text; products with different sizes must not be grouped."""
    return tuple(sorted(_NUMBERS.findall(text or "")))


def synthetic_item_number(text: str) -> str:
    return "N-" + hashlib.sha1(normalize(text).encode()).hexdigest()[:10]


class ProductIndex:
    """In-memory index of all products, built once per import / search."""

    GROUP_CUTOFF = 93   # same product, different item number
    NAME_CUTOFF = 90    # line without item number -> known product

    def __init__(self, con: sqlite3.Connection) -> None:
        self.ids: List[int] = []
        self.norm: List[str] = []
        self.nums: List[Tuple[str, ...]] = []
        self.groups: List[int] = []
        rows = con.execute("SELECT id, receipt_name, group_id FROM products").fetchall()
        for r in rows:
            self.add(r["id"], r["receipt_name"], r["group_id"] or r["id"])

    def add(self, pid: int, receipt_name: str, group_id: int) -> None:
        self.ids.append(pid)
        self.norm.append(normalize(receipt_name))
        self.nums.append(numbers(receipt_name))
        self.groups.append(group_id)

    def _best(self, text: str, cutoff: int) -> Optional[int]:
        """Index of the best match with the same sizes, or None."""
        if not self.norm:
            return None
        query, query_nums = normalize(text), numbers(text)
        for _, score, idx in process.extract(query, self.norm, scorer=fuzz.token_sort_ratio,
                                             score_cutoff=cutoff, limit=5):
            if self.nums[idx] == query_nums:
                return idx
        return None

    def group_for(self, receipt_name: str) -> Optional[int]:
        idx = self._best(receipt_name, self.GROUP_CUTOFF)
        return self.groups[idx] if idx is not None else None

    def product_for_name(self, receipt_name: str) -> Optional[int]:
        idx = self._best(receipt_name, self.NAME_CUTOFF)
        return self.ids[idx] if idx is not None else None


def get_or_create_product(con: sqlite3.Connection, index: ProductIndex, item_number: str,
                          receipt_name: str, kind: str, description2: Optional[str] = None,
                          department: Optional[int] = None) -> int:
    receipt_name = receipt_name.strip() or item_number or "UNKNOWN ITEM"
    if not item_number:
        found = index.product_for_name(receipt_name)
        if found:
            return found
        item_number = synthetic_item_number(receipt_name)

    row = con.execute("SELECT id FROM products WHERE item_number = ?", (item_number,)).fetchone()
    if row:
        if description2 or department is not None:
            con.execute("UPDATE products SET description2 = COALESCE(?, description2), "
                        "department = COALESCE(?, department) WHERE id = ?", (description2, department, row["id"]))
        return row["id"]

    fixed = fixed_product(receipt_name, description2, department)
    if kind == "gas":
        values = (item_number, receipt_name, f"Gasoline – {receipt_name.title()}", "Costco", "Gas",
                  "non_returnable", "Fuel can't be returned.", "done")
    elif fixed:
        values = (item_number, receipt_name, fixed["name"], fixed["brand"], fixed["category"],
                  fixed["return_class"], fixed["return_note"], "done")
    else:
        values = (item_number, receipt_name, None, None, None, None, None, "pending")
    pid = con.execute(
        """INSERT INTO products(item_number, receipt_name, name, brand, category, return_class,
                                return_note, ai_status, description2, department) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (*values, description2, department),
    ).lastrowid
    group_id = index.group_for(receipt_name) or pid
    con.execute("UPDATE products SET group_id = ? WHERE id = ?", (group_id, pid))
    index.add(pid, receipt_name, group_id)
    return pid


def _word_score(query: str, choice: str, **_) -> float:
    """Average over query words of the best fuzzy match among the choice's words.

    Every word must be present for a high score ("kirkland water" doesn't match all Kirkland
    products); prefixes count as exact ("egg" ~ "EGGS") and typos are tolerated ("towles").
    """
    words = choice.split()
    if not words:
        return 0.0
    scores = [max(100.0 if len(t) >= 3 and w.startswith(t) else fuzz.ratio(t, w) for w in words)
              for t in query.split()]
    if not scores:
        return 0.0
    return min(scores) if min(scores) < 70 else sum(scores) / len(scores)


def search(products: List[Dict], query: str, limit: int = 200) -> List[Tuple[Dict, float]]:
    """Fuzzy search over receipt text, readable name, brand, category and item number."""
    q = normalize(query)
    if not q:
        return [(p, 100.0) for p in products]
    choices = {
        i: normalize(" ".join(filter(None, [p.get("receipt_name"), p.get("name"), p.get("brand"),
                                            p.get("category"), p.get("item_number")])))
        for i, p in enumerate(products)
    }
    hits = process.extract(q, choices, scorer=_word_score, score_cutoff=75, limit=limit)
    return [(products[i], score) for _, score, i in hits]
