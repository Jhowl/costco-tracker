"""Background thread that categorises products waiting for AI."""
from __future__ import annotations

import logging
import threading
import time
from typing import Dict

from . import ai
from .config import settings
from .db import get_db
from .returns import apply_rules

log = logging.getLogger(__name__)

RETRIES = 5
RETRY_WAIT_S = 30

_lock = threading.Lock()
_state: Dict = {"running": False, "processed": 0, "failed": 0, "last_error": None}


def state() -> Dict:
    with get_db() as con:
        counts = dict(con.execute("SELECT ai_status, COUNT(*) FROM products GROUP BY ai_status").fetchall())
    return {**_state, "counts": counts}


def start_scheduler(every_s: int = 300) -> None:
    """Resume pending work periodically, e.g. after the Ollama machine was off."""
    def loop() -> None:
        while True:
            time.sleep(every_s)
            try:
                with get_db() as con:
                    pending = con.execute("SELECT 1 FROM products WHERE ai_status = 'pending' LIMIT 1").fetchone()
                if pending and ai.health().get("reachable"):
                    start()
            except Exception as e:  # never let the scheduler die
                log.warning("AI scheduler: %s", e)
    threading.Thread(target=loop, daemon=True, name="ai-scheduler").start()


def start() -> bool:
    with _lock:
        if _state["running"]:
            return False
        _state.update(running=True, processed=0, failed=0, last_error=None)
    threading.Thread(target=_run, daemon=True, name="ai-enrich").start()
    return True


def _run() -> None:
    try:
        while True:
            with get_db() as con:
                rows = [dict(r) for r in con.execute(
                    """SELECT p.id, p.item_number, p.receipt_name, p.description2, p.department,
                              (SELECT pu.unit_price FROM purchases pu WHERE pu.product_id = p.id
                               ORDER BY pu.id DESC LIMIT 1) AS price
                       FROM products p WHERE p.ai_status = 'pending' ORDER BY p.id LIMIT ?""",
                    (settings.ai_batch_size,))]
            if not rows:
                break
            results = None
            for attempt in range(1, RETRIES + 1):
                try:
                    results = ai.enrich_products(rows)
                    break
                except ai.AIError as e:  # Ollama restarting / reloading the model: wait and retry
                    _state["last_error"] = f"{e} (attempt {attempt}/{RETRIES})"
                    log.warning("AI enrichment: %s", _state["last_error"])
                    if attempt < RETRIES:
                        time.sleep(RETRY_WAIT_S)
                except Exception as e:  # bad JSON etc. -> mark this batch failed, go on
                    _state["last_error"] = str(e)
                    results = {}
                    break
            if results is None:  # still unreachable: leave the batch pending for next time
                break
            with get_db() as con:
                for r in rows:
                    res = results.get(r["item_number"])
                    if not res:
                        con.execute("UPDATE products SET ai_status = 'failed' WHERE id = ?", (r["id"],))
                        _state["failed"] += 1
                        continue
                    con.execute(
                        """UPDATE products SET name = ?, brand = ?, category = ?, return_class = ?,
                                  return_note = ?, ai_status = 'done' WHERE id = ? AND ai_status = 'pending'""",
                        (res.get("name") or None, res.get("brand") or None, res.get("category"),
                         apply_rules(res.get("category"), res.get("return_class")),
                         res.get("return_note") or None, r["id"]),
                    )
                    _state["processed"] += 1
    finally:
        _state["running"] = False
