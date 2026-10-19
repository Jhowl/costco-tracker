"""Costco (US) return policy as rules, applied per purchase.

Summary of costco.com/return-policy: most items are returnable at any time under the
satisfaction guarantee; a set of electronics and major appliances must come back
within 90 days; a few things can't be returned at all. Always confirm at the
membership counter: state law and special cases apply.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, Optional

RETURN_CLASSES = ["standard", "electronics_90", "limited", "non_returnable"]

POLICY = {
    "standard": "Satisfaction guarantee: return at any time for a full refund (food included).",
    "electronics_90": "Televisions, projectors, computers, tablets, smartwatches, cameras, drones, "
                      "VR headsets, cell phones and major appliances: within 90 days of purchase.",
    "limited": "Special conditions apply (e.g. diamonds 1 ct+ need their certificate, tires depend on "
               "wear/warranty, hearing aids 180 days, prescriptions and special orders vary).",
    "non_returnable": "Not returnable: gasoline, tobacco, alcohol where state law prohibits, gold/silver "
                      "bullion, gift/cash cards, tickets, deposits, fees and services.",
}

# categories that are never returnable, whatever the model says
FORCED_NON_RETURNABLE = {"Gas", "Alcohol", "Tobacco", "Fees & Deposits", "Gift Cards & Tickets"}


def apply_rules(category: Optional[str], return_class: Optional[str]) -> str:
    if category in FORCED_NON_RETURNABLE:
        return "non_returnable"
    return return_class if return_class in RETURN_CLASSES else "standard"


def status(return_class: Optional[str], purchase_date: str, today: Optional[date] = None,
           returned: bool = False) -> Dict:
    today = today or date.today()
    bought = date.fromisoformat(purchase_date[:10])
    out = {"class": return_class, "deadline": None, "days_left": None}
    if returned:
        return {**out, "state": "returned", "label": "Returned"}
    if return_class is None:
        return {**out, "state": "unknown", "label": "Not categorised yet"}
    if return_class == "non_returnable":
        return {**out, "state": "no", "label": "Not returnable"}
    if return_class == "limited":
        return {**out, "state": "check", "label": "Check conditions"}
    if return_class == "electronics_90":
        deadline = bought + timedelta(days=90)
        left = (deadline - today).days
        out.update(deadline=deadline.isoformat(), days_left=left)
        if left < 0:
            return {**out, "state": "expired", "label": f"Window closed {deadline.isoformat()}"}
        return {**out, "state": "open", "label": f"Return by {deadline.isoformat()} ({left} days left)"}
    return {**out, "state": "anytime", "label": "Return anytime"}


def fixed_product(receipt_name: str, description2: Optional[str], department: Optional[int]) -> Optional[Dict]:
    """Products identified by rule, without asking the model."""
    text = f"{receipt_name} {description2 or ''}".upper()
    if department == 95 or "MEMBERSHIP" in text:
        kind = "renewal" if "RENEW" in text else "sign-up" if "S/U" in text or "SIGN" in text else "fee"
        level = "Executive " if text.startswith("EX") or "EXEC" in text else ""
        return {"name": f"Costco {level}membership {kind}", "brand": "Costco", "category": "Membership",
                "return_class": "standard",
                "return_note": "The membership fee is refundable at any time: cancel at the membership counter."}
    return None
