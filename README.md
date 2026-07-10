# House Rental Alert System

Two self-contained tools in one repo, each in its own folder:

1. **`rental_alerts/`** — scrape Rightmove listings and rank them by real commute time to your target locations (notebook-driven).
2. **`rent_splitter/`** — upload a floor-plan image, box the bedroom dimensions and the total area, and split the rent by room size (Flask app).

Both tools share a single virtual-env and dependency set defined at the repo root, so you only run `uv sync` once.

## Project structure

```
house-rental-alert-system/
├── pyproject.toml            # shared dependencies for both tools
├── uv.lock                   # locked dependency versions
├── README.md
├── .gitignore
├── rental_alerts/            # Tool 1 — Rightmove scraper + commute ranking
│   ├── rightmove_listings.ipynb   # main notebook: scrape, filter, rank
│   ├── travel_utils.py            # door-to-door travel time via TfL API
│   ├── test_travel_utils.ipynb    # tests / scratch for travel_utils
│   └── config.yaml                # search params + commute targets (edit this)
└── rent_splitter/            # Tool 2 — floor-plan rent-splitter website
    ├── app.py                     # Flask server
    ├── rent_splitter.py           # rent-splitting maths (area-weighted)
    ├── floorplan_roi_ocr.py       # PaddleOCR region reader
    ├── templates/
    │   └── index.html             # single-page browser UI
    ├── files/                     # bundled sample floor plan + OCR results
    └── uploads/                   # user-submitted floor plans (git-ignored, runtime)
```

## Prerequisites

- Python ≥ 3.11
- [`uv`](https://docs.astral.sh/uv/) for dependency management

## Setup

```bash
uv sync            # creates .venv and installs everything from uv.lock
```

> First run pulls PaddleOCR + PaddlePaddle (large) and downloads OCR models on first use.

---

## 1. Rental alerts (`rental_alerts/`)

Edit [rental_alerts/config.yaml](rental_alerts/config.yaml) — no code changes needed:

- `search.locationIdentifier` — copy from a Rightmove search URL (`locationIdentifier=REGION%5E85216` → `REGION^85216`).
- `search.*` — bedrooms, price, radius, recency, etc.
- `targets` — your commute destinations. **The lat/lon in the sample are best-guesses — verify them** (an inaccurate coordinate silently skews every travel time).

Then run:

```bash
uv run jupyter lab      # open rental_alerts/rightmove_listings.ipynb and run it
```

The notebook loads `config.yaml` from its own folder, so run the cells with `rental_alerts/` as the working directory (Jupyter does this automatically when you open the notebook there).

Commute times use the free [TfL Journey Planner API](https://api-portal.tfl.gov.uk/) (London only, no key needed at low volume). For higher rate limits set a key:

```bash
export TFL_APP_KEY=your_key_here
```

---

## 2. Rent splitter website (`rent_splitter/`, optional)

```bash
uv run python rent_splitter/app.py
```

Open http://localhost:5000. Upload a floor plan (or click the bundled sample), draw a box over each bedroom's dimension text and one over the total area, then **Analyse**.

To share it temporarily:

```bash
cloudflared tunnel --url http://localhost:5000
```

### Security notes ⚠️

Read these before exposing the site to anyone else:

- **Keep `debug=False`.** It is off in [rent_splitter/app.py](rent_splitter/app.py) already. Flask's debugger exposes an interactive console that lets a visitor run arbitrary code on your machine — never turn it on for a shared/tunnelled instance.
- **`host="0.0.0.0"` binds to all interfaces**, so anyone on your network (or through the tunnel) can reach it. There is **no authentication** — only share the tunnel URL with people you trust, and stop the server when you're done.
- **Use a production server for anything beyond a quick demo.** The Flask dev server isn't hardened:
  ```bash
  cd rent_splitter && uv run waitress-serve --port 5000 app:app
  ```
- **Uploads are user-supplied files** written to `rent_splitter/uploads/` (git-ignored). Uploads are capped at 25 MB and restricted to image extensions, and filenames are sanitised — keep those checks in place, and periodically clear the folder.
- **Don't commit secrets.** `.env*` and `uploads/` are already git-ignored; keep `TFL_APP_KEY` and any keys in the environment, not in `config.yaml`.
