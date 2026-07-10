# House Rental Alert System

Two tools in one repo:

1. **Rental alerts** — scrape Rightmove listings and rank them by real commute time to your target locations (notebook-driven).
2. **Rent splitter website** — upload a floor-plan image, box the bedroom dimensions and the total area, and split the rent by room size (Flask app).

## Prerequisites

- Python ≥ 3.11
- [`uv`](https://docs.astral.sh/uv/) for dependency management

## Setup

```bash
uv sync            # creates .venv and installs everything from uv.lock
```

> First run pulls PaddleOCR + PaddlePaddle (large) and downloads OCR models on first use.

---

## 1. Rental alerts

Edit [config.yaml](config.yaml) — no code changes needed:

- `search.locationIdentifier` — copy from a Rightmove search URL (`locationIdentifier=REGION%5E85216` → `REGION^85216`).
- `search.*` — bedrooms, price, radius, recency, etc.
- `targets` — your commute destinations. **The lat/lon in the sample are best-guesses — verify them** (an inaccurate coordinate silently skews every travel time).

Then run:

```bash
uv run jupyter lab      # open rightmove_listings.ipynb and run it
```

Commute times use the free [TfL Journey Planner API](https://api-portal.tfl.gov.uk/) (London only, no key needed at low volume). For higher rate limits set a key:

```bash
export TFL_APP_KEY=your_key_here
```

---

## 2. Rent splitter website (optional)

```bash
uv run python app.py
```

Open http://localhost:5000. Upload a floor plan (or click the bundled sample), draw a box over each bedroom's dimension text and one over the total area, then **Analyse**.

To share it temporarily:

```bash
cloudflared tunnel --url http://localhost:5000
```

### Security notes ⚠️

Read these before exposing the site to anyone else:

- **Keep `debug=False`.** It is off in [app.py](app.py) already. Flask's debugger exposes an interactive console that lets a visitor run arbitrary code on your machine — never turn it on for a shared/tunnelled instance.
- **`host="0.0.0.0"` binds to all interfaces**, so anyone on your network (or through the tunnel) can reach it. There is **no authentication** — only share the tunnel URL with people you trust, and stop the server when you're done.
- **Use a production server for anything beyond a quick demo.** The Flask dev server isn't hardened:
  ```bash
  uv run waitress-serve --port 5000 app:app
  ```
- **Uploads are user-supplied files** written to `uploads/` (git-ignored). Uploads are capped at 25 MB and restricted to image extensions, and filenames are sanitised — keep those checks in place, and periodically clear the folder.
- **Don't commit secrets.** `.env*` and `uploads/` are already git-ignored; keep `TFL_APP_KEY` and any keys in the environment, not in `config.yaml`.
