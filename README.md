# Costco Tracker

Self-hosted tracker for your household's Costco receipts: import them once, then browse spending, prices, returns and a shopping calendar, all on your own server with a local AI.

- **Receipts → database**: every receipt, line item, instant saving, refund and gas fill-up in SQLite.
- **Products indexed**: each item number becomes a product; [rapidfuzz](https://github.com/rapidfuzz/RapidFuzz) groups the same product sold under different item numbers, links instant-savings lines to the right item, matches photo-scanned lines to known products and powers the fuzzy search (`eggs`, `kirkland towles`, `1234567`).
- **Local AI (Ollama, `gemma4:26b`)**: turns `KS ORG EGGS 24CT` into *Kirkland Signature Organic Eggs, 24 ct*, picks a category and the Costco return rule, reads **photos of paper receipts**, and answers questions ("how many times did we buy eggs this year?").
- **Calendar**: every day you shopped, per person, with totals; click a day for the receipts. Filter it to one product to see each date you bought it.
- **Prices**: last / lowest / highest price, price-history chart, what you paid after instant savings, how often you buy it.
- **Returns**: per purchase, *return anytime*, *return by DATE (90-day electronics/appliances)*, *not returnable* (gas, alcohol, fees…) or *check conditions*; plus a Returns page with open 90-day windows.
- **Two people**: every receipt belongs to a person; the "Showing" selector filters every page to one person or everyone.

## Run on the home server

```bash
# from your computer: rsync to the server over SSH and run docker compose there
HOST=192.168.1.10 ./deploy.sh                 # SSH user defaults to your local username
HOST=192.168.1.10 SSH_USER=admin ./deploy.sh  # or pick one

# or on the server itself:
cp .env.example .env && docker compose up -d --build
```

Re-run `deploy.sh` any time to ship updates. The server's `.env` and `./data` are never overwritten, and a free port is picked automatically if `APP_PORT` is taken.

Open `http://<server>:8096`. The database is `./data/costco.db` — back that folder up.
`http://<server>:8096/api/health/llm` checks the Ollama connection (`OLLAMA_URL` in `.env`, default `http://localhost:11434`).

Requires [Ollama](https://ollama.com) reachable from the server with a vision-capable model pulled (`ollama pull gemma4:26b`).

### Configuration (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `APP_PORT` | `8096` | Port the app is published on |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama server (the container must be able to reach it; use the host's LAN IP, not `localhost`, if Ollama runs outside the container) |
| `OLLAMA_MODEL` | `gemma4:26b` | Model for categorising, receipt photos and questions |
| `OLLAMA_TIMEOUT_S` | `600` | Per-request timeout |
| `AI_BATCH_SIZE` | `20` | Products categorised per request |
| `LLM_MAX_IMAGE_SIDE` | `3000` | Long side (px) of receipt photos sent to the model |
| `PEOPLE` | `Me,Partner` | People created on first start; rename or add more on the **Import** page |
| `APP_UID` / `APP_GID` | `1000` | User the container runs as (owner of `./data`) |
| `TZ` | `America/New_York` | Container timezone |

There is no login: run it on a trusted network or behind a reverse proxy with authentication.

## Getting the receipts

costco.com has no public API, so the export runs **in your own browser** with your logged-in session:

1. Sign in at costco.com → Account → **Orders & Purchases** → **Warehouse** tab.
2. Open the console (`F12` / `⌘⌥J`; Chrome may require typing `allow pasting`).
3. Paste [`scripts/costco-export.js`](scripts/costco-export.js) (the Import page has a copy button) → a `costco-receipts-YYYY-MM-DD.json` downloads.
4. In the app: **Import** → choose the person → upload the file. Re-importing is safe: known receipts are skipped.
5. Anyone else in the household repeats 1–4 signed in with their own account, choosing their name on the Import page.

costco.com keeps roughly 2–3 years of warehouse receipts. For older or paper receipts, upload a photo (≈20–60 s each with gemma4:26b).

> The script uses the same internal API costco.com's receipts page uses. If Costco changes it, the console shows the error; the importer also accepts any JSON list of receipts in that format.

New products are categorised in the background right after an import (banner at the top). Fix anything by hand on the product page — manual edits are never overwritten.

## Return rules (Costco US, summary)

| Rule | Applies to |
|---|---|
| Return anytime | Almost everything, food included (satisfaction guarantee) |
| 90 days | TVs, projectors, computers, tablets, smartwatches, cameras, drones, VR, phones, major appliances |
| Check conditions | Diamonds ≥1 ct, tires, hearing aids, prescriptions, special orders |
| Not returnable | Gas, tobacco, alcohol (where state law prohibits), bullion, gift cards, tickets, fees/deposits |

Gas, Alcohol, Tobacco, Fees and Gift Cards are forced to *not returnable* whatever the model says. The membership counter has the final word.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest -q
DB_PATH=data/dev.db .venv/bin/uvicorn app.main:app --reload --port 8096
```

API docs at `/docs`. Layout: `app/importer.py` (receipt JSON → rows), `app/matcher.py` (rapidfuzz),
`app/ai.py` + `app/ai_worker.py` (Ollama), `app/returns.py` (policy), `app/queries.py` (views), `app/static/` (UI).
