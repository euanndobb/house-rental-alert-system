#!/usr/bin/env python3
"""
Flask website for the floor-plan rent splitter.

Workflow (all in the browser — no desktop OpenCV window):
  1. Upload a floor-plan image (or load the bundled sample).
  2. Draw a box around each bedroom's dimension text — boxes are auto-numbered
     1, 2, 3, … so it is clear from the start which bedroom is which.
  3. Draw one box around the total-area figure.
  4. Analyse: PaddleOCR reads each region, dimensions/areas are parsed, and the
     rent is split across the bedrooms.

Run:
    uv run python app.py
    # then, to share:  cloudflared tunnel --url http://localhost:5000

Debug is OFF so this is safe to expose through a Cloudflare tunnel. For a real
public deployment prefer a WSGI server, e.g.:
    uv run waitress-serve --port 5000 app:app
"""

import uuid
from pathlib import Path

import cv2
from flask import (Flask, jsonify, render_template, request,
                   send_from_directory)
from werkzeug.utils import secure_filename

import floorplan_roi_ocr as fp
import rent_splitter as rs

BASE = Path(__file__).parent
UPLOAD_DIR = BASE / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
SAMPLE = BASE / "files" / "43_lydford_road.jpg"
ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25 MB upload cap

# PaddleOCR is heavy to build, so create it once and reuse across requests.
_ocr = None


def get_ocr():
    global _ocr
    if _ocr is None:
        _ocr = fp.make_ocr("en")
    return _ocr


def _clean_rect(box):
    """Coerce a [x, y, w, h] from the browser into safe ints."""
    x, y, w, h = (int(round(float(v))) for v in box)
    return [max(0, x), max(0, y), max(1, w), max(1, h)]


def _image_meta(path):
    img = cv2.imread(str(path))
    if img is None:
        return None
    h, w = img.shape[:2]
    return {"width": int(w), "height": int(h)}


@app.route("/")
def index():
    return render_template("index.html", default_rent=rs.TOTAL_RENT)


@app.route("/uploads/<path:name>")
def uploaded_file(name):
    return send_from_directory(UPLOAD_DIR, name)


@app.route("/upload", methods=["POST"])
def upload():
    file = request.files.get("image")
    if not file or file.filename == "":
        return jsonify(error="No file provided"), 400
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXT:
        return jsonify(error=f"Unsupported file type '{ext}'"), 400

    name = f"{uuid.uuid4().hex[:12]}{ext}"
    dest = UPLOAD_DIR / name
    file.save(str(dest))
    meta = _image_meta(dest)
    if meta is None:
        return jsonify(error="Could not read that image"), 400
    return jsonify(name=name, url=f"/uploads/{name}", **meta)


@app.route("/sample", methods=["POST"])
def sample():
    if not SAMPLE.exists():
        return jsonify(error="No sample image is available"), 404
    name = f"sample_{uuid.uuid4().hex[:8]}.jpg"
    dest = UPLOAD_DIR / name
    cv2.imwrite(str(dest), cv2.imread(str(SAMPLE)))
    meta = _image_meta(dest)
    return jsonify(name=name, url=f"/uploads/{name}", **meta)


@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.get_json(silent=True) or {}
    name = secure_filename(data.get("image", ""))
    path = UPLOAD_DIR / name
    if not name or not path.exists():
        return jsonify(error="Image not found — please upload it again"), 400
    img = cv2.imread(str(path))
    if img is None:
        return jsonify(error="Could not read the image"), 400

    total_rent = float(data.get("total_rent") or rs.TOTAL_RENT)
    ocr = get_ocr()

    # --- bedrooms (numbering follows the order they were drawn) ---
    bedrooms_out, results_bedrooms = [], []
    for i, box in enumerate(data.get("bedrooms", []), 1):
        rect = _clean_rect(box)
        texts = fp.ocr_texts(ocr, fp.crop(img, rect))
        _, parsed = fp.best_dimension(texts)
        bedrooms_out.append({
            "label": f"bedroom {i}",
            "box": rect,
            "dimension": fp.fmt_dimension(parsed) or None,
            "area_m2": rs.bedroom_area_m2(parsed),
            "ocr_text": texts,
        })
        results_bedrooms.append({"box": rect, "m": parsed["m"], "ft": parsed["ft"]})

    # --- total area ---
    total_out, results_total = None, None
    if data.get("area"):
        rect = _clean_rect(data["area"])
        texts = fp.ocr_texts(ocr, fp.crop(img, rect))
        _, parsed = fp.best_area(texts)
        total_out = {
            "box": rect,
            "value": fp.fmt_area(parsed) or None,
            "area_m2": rs.total_area_m2(parsed),
            "ocr_text": texts,
        }
        results_total = {"m2": parsed["m2"], "ft2": parsed["ft2"]}

    # areas dict preserving the user's 1,2,3 labelling
    areas = {b["label"]: b["area_m2"] for b in bedrooms_out}
    areas["total_area"] = total_out["area_m2"] if total_out else None

    # Split the rent only when every bedroom AND the total area were read.
    unreadable = [b["label"] for b in bedrooms_out if b["area_m2"] is None]
    summary, rents, breakdown = None, {}, None
    if bedrooms_out and not unreadable and areas["total_area"]:
        summary = rs.room_areas_from_results(
            {"bedrooms": results_bedrooms, "total_area": results_total})
        breakdown = rs.rent_breakdown(summary, total_rent)
        rents = rs.compute_rents(summary, total_rent)

    return jsonify(
        bedrooms=bedrooms_out,
        total_area=total_out,
        areas=areas,
        summary=summary,
        rents=rents,
        breakdown=breakdown,
        total_rent=total_rent,
        unreadable=unreadable,
    )


if __name__ == "__main__":
    # debug=False so it's safe to expose through a Cloudflare tunnel.
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
