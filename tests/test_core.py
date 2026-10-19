import json
from datetime import date
from pathlib import Path

import pytest

from app import db, importer, queries, returns
from app.config import settings
from app.matcher import normalize

SAMPLE = json.loads((Path(__file__).parent / "sample_export.json").read_text())


@pytest.fixture
def con(tmp_path):
    settings.db_path = str(tmp_path / "t.db")
    settings.people = ["Me", "Partner"]
    db.init_db()
    with db.get_db() as c:
        yield c


def test_normalize_expands_abbreviations():
    assert normalize("KS ORG EGGS 24CT") == "KIRKLAND ORGANIC EGGS 24 COUNT"


def test_import_and_dedupe(con):
    s = importer.import_payload(con, SAMPLE, 1, "costco-json")
    assert s["imported"] == 5 and s["duplicates"] == 0 and not s["errors"]
    again = importer.import_payload(con, SAMPLE, 1, "costco-json")
    assert again["imported"] == 0 and again["duplicates"] == 5


def test_instant_savings_attached_to_referenced_item(con):
    importer.import_payload(con, SAMPLE, 1, "costco-json")
    row = con.execute("""SELECT pu.amount, pu.discount FROM purchases pu JOIN products p ON p.id = pu.product_id
                         JOIN receipts r ON r.id = pu.receipt_id
                         WHERE p.item_number = '1234567' AND r.purchase_date = '2026-06-01'""").fetchone()
    assert (row["amount"], row["discount"]) == (7.99, -3.0)
    # discount line is not a product
    assert not con.execute("SELECT 1 FROM products WHERE item_number = '345678'").fetchone()


def test_gas_refund_and_dates(con):
    importer.import_payload(con, SAMPLE, 2, "costco-json")
    gas = con.execute("SELECT * FROM receipts WHERE kind = 'gas'").fetchone()
    assert gas is not None
    gp = con.execute("SELECT p.category, p.return_class, pu.quantity FROM purchases pu JOIN products p ON p.id=pu.product_id WHERE pu.receipt_id=?", (gas["id"],)).fetchone()
    assert gp["category"] == "Gas" and gp["return_class"] == "non_returnable" and gp["quantity"] == pytest.approx(13.274)
    refund = con.execute("SELECT * FROM receipts WHERE is_refund = 1").fetchone()
    assert refund["purchase_date"] == "2026-08-02"
    assert con.execute("SELECT purchase_date FROM receipts WHERE barcode LIKE '%55'").fetchone()[0] == "2026-09-20"


def test_fuzzy_groups_same_product_with_other_item_number(con):
    importer.import_payload(con, SAMPLE, 1, "costco-json")
    a = con.execute("SELECT group_id FROM products WHERE item_number = '1234567'").fetchone()[0]
    b = con.execute("SELECT group_id FROM products WHERE item_number = '7654321'").fetchone()[0]
    assert a == b
    # different size is NOT grouped
    assert con.execute("SELECT group_id FROM products WHERE item_number = '445566'").fetchone()[0] != a


def test_product_stats_and_returned(con):
    importer.import_payload(con, SAMPLE, 1, "costco-json")
    eggs = con.execute("SELECT id FROM products WHERE item_number = '1234567'").fetchone()[0]
    p = queries.product(con, eggs, None)
    assert p["times"] == 2 and p["avg_days_between"] == 32
    towels = con.execute("SELECT id FROM products WHERE item_number = '445566'").fetchone()[0]
    t = queries.product(con, towels, None)
    assert [x.get("returned", False) for x in t["purchases"] if x["quantity"] > 0] == [True]


def test_search_is_fuzzy(con):
    importer.import_payload(con, SAMPLE, 1, "costco-json")
    names = [p["receipt_name"] for p in queries.products(con, None, q="organic eggs")]
    assert "KS ORG EGGS 24CT" in names[:2]
    assert queries.products(con, None, q="paper towels")[0]["receipt_name"] == "KS PPR TWL 12PK"
    # single short words and typos
    assert {p["receipt_name"] for p in queries.products(con, None, q="eggs")} == {"KS ORG EGGS 24CT", "KS ORGANIC EGGS 24 CT"}
    assert queries.products(con, None, q="tv")[0]["item_number"] == "1712345"
    assert queries.products(con, None, q="kirkland towles")[0]["receipt_name"] == "KS PPR TWL 12PK"
    assert not queries.products(con, None, q="kirkland water")


def test_person_filter_and_calendar(con):
    importer.import_payload(con, SAMPLE["receipts"][:2], 1, "costco-json")
    importer.import_payload(con, SAMPLE["receipts"][2:], 2, "costco-json")
    assert queries.summary(con, 1)["totals"]["receipts"] == 2
    assert queries.summary(con, 2)["totals"]["receipts"] == 3
    eggs = con.execute("SELECT id FROM products WHERE item_number = '1234567'").fetchone()[0]
    days = queries.calendar(con, "2026-01-01", "2026-12-31", None, eggs)
    assert [d["date"] for d in days] == ["2026-06-01", "2026-07-03", "2026-09-20"]  # incl. grouped item number


def test_return_rules():
    assert returns.apply_rules("Alcohol", "standard") == "non_returnable"
    assert returns.apply_rules("Electronics", "electronics_90") == "electronics_90"
    s = returns.status("electronics_90", "2026-08-01", today=date(2026, 10, 14))
    assert s["state"] == "open" and s["deadline"] == "2026-10-30" and s["days_left"] == 16
    assert returns.status("electronics_90", "2026-06-01", today=date(2026, 10, 14))["state"] == "expired"
    assert returns.status("standard", "2020-01-01")["state"] == "anytime"


def test_returns_view(con):
    importer.import_payload(con, SAMPLE, 1, "costco-json")
    tv = con.execute("SELECT id FROM products WHERE item_number = '1712345'").fetchone()[0]
    queries.update_product(con, tv, {"category": "Electronics", "return_class": "electronics_90"})
    v = queries.returns_view(con, None, today=date(2026, 8, 15))
    assert [r["item_number"] for r in v["open"]] == ["1712345"]
    v = queries.returns_view(con, None, today=date(2026, 9, 15))
    assert [r["item_number"] for r in v["expired"]] == ["1712345"]


def test_photo_conversion_reuses_importer(con):
    from app.ai import photo_to_receipt
    r = photo_to_receipt({"date": "2026-10-01", "time": "11:30", "warehouse": "UNION", "barcode": "", "is_refund": False,
                          "subtotal": 10, "tax": 0, "total": 10,
                          "items": [{"item_number": "", "description": "KS ORG EGGS 24CT", "quantity": 1, "amount": 12.0},
                                    {"item_number": "", "description": "/1234567", "quantity": 1, "amount": -2.0}]}, "abc123")
    importer.import_payload(con, SAMPLE, 1, "costco-json")
    s = importer.import_payload(con, [r], 1, "photo")
    assert s["imported"] == 1 and s["new_products"] == 0  # matched the known eggs by name
    row = con.execute("SELECT discount FROM purchases pu JOIN receipts r ON r.id=pu.receipt_id WHERE r.source='photo'").fetchone()
    assert row["discount"] == -2.0


REAL_WAREHOUSE = {  # shape returned by costco.com: fuelUnitQuantity is set on ordinary items too
    "warehouseName": "UNION", "receiptType": "In-Warehouse", "documentType": "WarehouseReceiptDetail",
    "transactionDateTime": "2026-10-07T10:12:00", "transactionType": "Sales", "transactionBarcode": "210320203002826",
    "total": 8.89, "subTotal": 8.89, "taxes": 0, "instantSavings": 2.7,
    "itemArray": [
        {"itemNumber": "1902857", "itemDescription01": "WATERLOO", "itemDescription02": "LL/RN/BL 24PK/12OZ  P=100",
         "unit": 1, "amount": 11.59, "fuelUnitQuantity": 10, "fuelGradeCode": None, "itemUnitPriceAmount": 11.59,
         "fuelUomCode": None},
        {"itemNumber": "390121", "itemDescription01": "/1902857", "unit": -1, "amount": -2.7,
         "fuelUnitQuantity": None, "fuelGradeCode": None, "itemUnitPriceAmount": 0},
    ]}
REAL_GAS = {
    "warehouseName": "Union", "receiptType": "Gas Station", "documentType": "FuelReceipts",
    "transactionDateTime": "2026-09-01T08:00:00", "transactionType": "Sales", "transactionBarcode": "999000111",
    "total": 38.22,
    "itemArray": [{"itemNumber": "800877", "itemDescription01": "PREMIUM", "unit": 1, "amount": 38.22,
                   "fuelUnitQuantity": 12.332, "fuelGradeCode": "2", "itemUnitPriceAmount": 3.099,
                   "fuelUomCode": "GAL", "fuelGradeDescription": "Premium"}]}


def test_real_costco_shape_is_not_gas(con):
    importer.import_payload(con, [REAL_WAREHOUSE, REAL_GAS], 1, "costco-json")
    kinds = dict(con.execute("SELECT barcode, kind FROM receipts").fetchall())
    assert kinds == {"210320203002826": "warehouse", "999000111": "gas"}
    w = con.execute("SELECT p.name, p.category, p.ai_status, pu.quantity, pu.discount FROM purchases pu "
                    "JOIN products p ON p.id = pu.product_id WHERE p.item_number = '1902857'").fetchone()
    assert (w["name"], w["category"], w["ai_status"], w["quantity"], w["discount"]) == (None, None, "pending", 1, -2.7)
    g = con.execute("SELECT p.category, pu.quantity FROM purchases pu JOIN products p ON p.id = pu.product_id "
                    "WHERE p.item_number = '800877'").fetchone()
    assert g["category"] == "Gas" and g["quantity"] == pytest.approx(12.332)


def test_rebuild_repairs_receipts_imported_as_gas(con):
    importer.import_payload(con, [REAL_WAREHOUSE, REAL_GAS], 1, "costco-json")
    # simulate the old bug's result
    con.execute("UPDATE receipts SET kind = 'gas'")
    con.execute("UPDATE purchases SET quantity = 10 WHERE product_id = (SELECT id FROM products WHERE item_number='1902857')")
    con.execute("UPDATE products SET name = 'Gasoline – Waterloo', category = 'Gas', return_class = 'non_returnable', "
                "ai_status = 'done' WHERE item_number = '1902857'")
    res = importer.rebuild_from_raw(con)
    assert res == {"receipts": 2, "products_reset": 1}
    w = con.execute("SELECT p.name, p.category, p.ai_status, pu.quantity FROM purchases pu "
                    "JOIN products p ON p.id = pu.product_id WHERE p.item_number = '1902857'").fetchone()
    assert (w["name"], w["category"], w["ai_status"], w["quantity"]) == (None, None, "pending", 1)
    assert con.execute("SELECT category FROM products WHERE item_number = '800877'").fetchone()[0] == "Gas"
    assert dict(con.execute("SELECT barcode, kind FROM receipts").fetchall())["210320203002826"] == "warehouse"


def test_membership_by_rule_and_item_details_kept(con):
    renew = {"transactionBarcode": "777", "transactionDateTime": "2026-10-01T10:00:00", "transactionType": "Sales",
             "total": 99.99, "itemArray": [
                 {"itemNumber": "731024", "itemDescription01": "EXGS RENEW", "itemDescription02": "MEMBERSHIP FEE",
                  "itemDepartmentNumber": 95, "unit": 1, "amount": 99.99, "itemUnitPriceAmount": 99.99},
                 {"itemNumber": "1791589", "itemDescription01": "CKPUFFJACKET", "itemDescription02": "FY25    P100",
                  "itemDepartmentNumber": 31, "unit": 1, "amount": 59.99, "itemUnitPriceAmount": 59.99}]}
    importer.import_payload(con, [renew], 1, "costco-json")
    m = dict(con.execute("SELECT * FROM products WHERE item_number = '731024'").fetchone())
    assert (m["category"], m["return_class"], m["ai_status"]) == ("Membership", "standard", "done")
    assert m["name"] == "Costco Executive membership renewal"
    j = dict(con.execute("SELECT * FROM products WHERE item_number = '1791589'").fetchone())
    assert (j["description2"], j["department"], j["ai_status"]) == ("FY25    P100", 31, "pending")


def test_backfill_item_details(con):
    importer.import_payload(con, [REAL_WAREHOUSE], 1, "costco-json")
    con.execute("UPDATE products SET description2 = NULL, department = NULL")
    assert importer.backfill_item_details(con) == 1
    row = con.execute("SELECT description2 FROM products WHERE item_number = '1902857'").fetchone()
    assert row[0] == "LL/RN/BL 24PK/12OZ  P=100"


def test_period_categories_and_detail(con):
    importer.import_payload(con, SAMPLE, 1, "costco-json")
    eggs = con.execute("SELECT id FROM products WHERE item_number = '1234567'").fetchone()[0]
    queries.update_product(con, eggs, {"category": "Dairy & Eggs"})
    p = queries.period_summary(con, "2026-07-01", "2026-07-31", None, "2026-06-01", "2026-06-30")
    assert p["totals"]["receipts"] == 2 and p["vs"]["receipts"] == 1
    assert p["gas"]["fills"] == 1 and p["gas"]["gallons"] == pytest.approx(13.3)
    assert p["new_count"] == 0  # eggs were first bought in June
    assert [c["category"] for c in p["categories"]][:1] == ["Gas"]
    june = queries.period_summary(con, "2026-06-01", "2026-06-30", None)
    assert june["new_count"] == 3  # eggs, TV, towels
    assert queries.period_summary(con, "2026-07-01", "2026-07-31", None)["price_changes"][0]["pct"] == 6.3  # 7.99 -> 8.49
    c = queries.categories_overview(con, None, today=date(2026, 10, 14))
    dairy = next(x for x in c["categories"] if x["category"] == "Dairy & Eggs")
    assert dairy["trips"] == 2 and dairy["spent"] == 21.97 and dairy["top_product"]["id"] == eggs
    d = queries.category_detail(con, "Dairy & Eggs", None, today=date(2026, 10, 14))
    assert d["ytd"] == 21.97 and [m["spent"] for m in d["monthly"] if m["spent"]] == [4.99, 16.98]
    assert d["products"][0]["id"] == eggs
