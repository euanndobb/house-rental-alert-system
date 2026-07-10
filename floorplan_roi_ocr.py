#!/usr/bin/env python3
"""
Interactively select regions of a floor-plan image, then read them with
PaddleOCR.

You draw the boxes yourself, so OCR only runs on the exact areas you care
about — no fragile whole-image label/dimension matching:

  1. BEDROOM DIMENSIONS — draw one box per bedroom dimension (there can be
     several). For each box: drag a rectangle, press ENTER/SPACE to confirm,
     then drag the next. Press ESC when you have drawn them all.
  2. TOTAL AREA — draw a single box around the total floor-area figure, then
     press ENTER/SPACE (ESC to skip).

Each confirmed box stays drawn on screen (labelled BR1, BR2, … and AREA), so
it is always clear what has already been marked, and a preview of all boxes is
shown before OCR runs.

The chosen boxes are saved next to the image as <image>_regions.json so you
can re-run without re-selecting via --regions <that file>.

Outputs:
  - console report (metres and/or feet per bedroom; total area in m² and ft²)
  - <image>_roi_annotated.png : your boxes drawn + the parsed values
  - <image>_roi_results.json  : structured results

Usage:
    uv run python floorplan_roi_ocr.py files/43_lydford_road.jpg
    uv run python floorplan_roi_ocr.py files/43_lydford_road.jpg --regions files/43_lydford_road_regions.json
    uv run python floorplan_roi_ocr.py files/43_lydford_road.jpg --lang en --max-display 1400
"""

import argparse
import json
import re
import sys
from pathlib import Path

import cv2
import numpy as np

# ===========================================================================
# Dimension / area parsing
# ---------------------------------------------------------------------------
# These mirror the rules used elsewhere in the project: bedroom dimensions are
# only kept when expressed in METRES ("3.50m x 4.20m", not mm/cm) and/or FEET
# ("11'6\" x 13'9\""); the total area is reported in m² and/or ft².
# ===========================================================================

# A metre-unit pair: the trailing number must carry a plain "m" (not mm/cm).
METRE_PAIR_RE = re.compile(
    r"(\d[\d.,]*)\s*(?:m(?!m))?\s*[x×X]\s*(\d[\d.,]*)\s*m(?!m)",
    re.IGNORECASE,
)
# Feet + optional inches, e.g. 11'6", 11' 6, 11'.
FEET_INCHES_RE = re.compile(r"(\d+)\s*['’]\s*(\d+)?")

AREA_M2_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:m2|m²|sq\.?\s*m|sqm)\b", re.IGNORECASE)
AREA_FT2_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(?:ft2|ft²|sq\.?\s*ft|sqft)\b", re.IGNORECASE
)

M2_TO_FT2 = 10.7639  # 1 square metre in square feet


def _to_float(s):
    """Parse a number, treating "," as a thousands separator (1,076 -> 1076)
    only when it groups three digits; otherwise as a decimal comma."""
    s = str(s).strip()
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", s):
        s = s.replace(",", "")
    else:
        s = s.replace(",", ".")
    return float(s)


def _feet(feet, inches):
    return int(feet) + (int(inches) if inches else 0) / 12.0


def parse_dimension(text):
    """Parse a dimension string into metric and/or imperial pairs.

    Returns {"m": (w, h) | None, "ft": (w, h) | None} where feet are decimal
    (11'6" -> 11.5). Unit-less numbers, mm and cm return None.
    """
    result = {"m": None, "ft": None}
    m = METRE_PAIR_RE.search(text)
    if m:
        result["m"] = (_to_float(m.group(1)), _to_float(m.group(2)))
    feet = FEET_INCHES_RE.findall(text)
    if len(feet) >= 2:
        result["ft"] = (_feet(*feet[0]), _feet(*feet[1]))
    return result


def _fmt_feet(x):
    feet = int(x)
    inch = round((x - feet) * 12)
    if inch == 12:
        feet, inch = feet + 1, 0
    return f"{feet}'{inch}\""


def fmt_dimension(parsed):
    bits = []
    if parsed["m"]:
        bits.append(f"{parsed['m'][0]:.2f}m x {parsed['m'][1]:.2f}m")
    if parsed["ft"]:
        bits.append(f"{_fmt_feet(parsed['ft'][0])} x {_fmt_feet(parsed['ft'][1])}")
    return " / ".join(bits)


def parse_area(text):
    """Return {"m2": float|None, "ft2": float|None} extracted from `text`."""
    result = {"m2": None, "ft2": None}
    m = AREA_M2_RE.search(text)
    if m:
        result["m2"] = _to_float(m.group(1))
    f = AREA_FT2_RE.search(text)
    if f:
        result["ft2"] = _to_float(f.group(1))
    return result


def fmt_area(parsed):
    """Format area in both units; the missing one is converted (~)."""
    m2, ft2 = parsed["m2"], parsed["ft2"]
    bits = []
    if m2 is not None:
        bits.append(f"{m2:.1f} m²")
    elif ft2 is not None:
        bits.append(f"~{ft2 / M2_TO_FT2:.1f} m²")
    if ft2 is not None:
        bits.append(f"{ft2:.0f} ft²")
    elif m2 is not None:
        bits.append(f"~{m2 * M2_TO_FT2:.0f} ft²")
    return " / ".join(bits)


# ===========================================================================
# PaddleOCR
# ===========================================================================

def make_ocr(lang):
    """Build a PaddleOCR reader, tolerating API changes across versions."""
    try:
        from paddleocr import PaddleOCR
    except ImportError:
        sys.exit(
            "PaddleOCR is not installed. Install with:\n"
            "  uv add paddleocr paddlepaddle"
        )
    for kwargs in (
        {"use_angle_cls": True, "lang": lang, "show_log": False},
        {"use_angle_cls": True, "lang": lang},
        {"lang": lang},
    ):
        try:
            return PaddleOCR(**kwargs)
        except TypeError:
            continue
    # Last resort: default constructor.
    return PaddleOCR()


def _flatten_texts(result):
    """Pull plain text strings out of a PaddleOCR result across 2.x/3.x shapes.

    2.x: [[ [box, (text, conf)], ... ]]
    3.x: [ {'rec_texts': [...], 'rec_scores': [...]} ] (dict-like OCRResult)
    """
    texts = []
    for page in result or []:
        if page is None:
            continue
        # 3.x dict / OCRResult
        rec = None
        try:
            rec = page["rec_texts"]
        except (TypeError, KeyError, IndexError):
            rec = getattr(page, "rec_texts", None)
        if rec:
            texts.extend(str(t) for t in rec)
            continue
        # 2.x list of lines
        if isinstance(page, (list, tuple)):
            for line in page:
                try:
                    texts.append(str(line[1][0]))
                except (TypeError, KeyError, IndexError):
                    pass
    return texts


def ocr_texts(ocr, crop):
    """Run OCR on an image crop and return the list of detected text lines."""
    result = None
    for call in (
        lambda: ocr.ocr(crop, cls=True),
        lambda: ocr.ocr(crop),
        lambda: ocr.predict(crop),
    ):
        try:
            result = call()
            break
        except TypeError:
            continue
        except Exception:
            continue
    return _flatten_texts(result)


def best_dimension(texts):
    """Choose the text (joined, then per-line) that parses to a metre/feet
    dimension. Returns (raw_text, parsed)."""
    candidates = [" ".join(texts)] + list(texts)
    for t in candidates:
        parsed = parse_dimension(t)
        if parsed["m"] or parsed["ft"]:
            return t, parsed
    return " ".join(texts), {"m": None, "ft": None}


def best_area(texts):
    candidates = [" ".join(texts)] + list(texts)
    for t in candidates:
        parsed = parse_area(t)
        if parsed["m2"] is not None or parsed["ft2"] is not None:
            return t, parsed
    return " ".join(texts), {"m2": None, "ft2": None}


# ===========================================================================
# Interactive region selection
# ===========================================================================

def _display_scale(img, max_display):
    h, w = img.shape[:2]
    longest = max(h, w)
    return min(1.0, max_display / longest) if longest > max_display else 1.0


def _to_original(rect, scale):
    x, y, w, h = rect
    inv = 1.0 / scale
    return (int(round(x * inv)), int(round(y * inv)),
            int(round(w * inv)), int(round(h * inv)))


def _with_banner(canvas, lines, color):
    """Return a copy of `canvas` with a translucent instruction banner across
    the top so the current step is clear on the image itself."""
    view = canvas.copy()
    height = 16 + 30 * len(lines)
    overlay = view.copy()
    cv2.rectangle(overlay, (0, 0), (view.shape[1], height), color, -1)
    cv2.addWeighted(overlay, 0.55, view, 0.45, 0, view)
    y = 32
    for line in lines:
        cv2.putText(view, line, (14, y), cv2.FONT_HERSHEY_SIMPLEX, 0.75,
                    (255, 255, 255), 2, cv2.LINE_AA)
        y += 30
    return view


def select_regions(img, max_display):
    """Interactively mark several bedroom dimension boxes and one total-area
    box in a single window.

    Every confirmed box is drawn onto the canvas and STAYS visible (labelled
    BR1, BR2, … and AREA). A coloured banner on the image shows the current
    step, and a full-screen prompt separates the two steps so the switch from
    bedrooms to total area is unmistakable.

    Returns (bedroom_rects, area_rect_or_None) in ORIGINAL image coordinates.
    """
    scale = _display_scale(img, max_display)
    base = (cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            if scale != 1.0 else img.copy())
    win = "Floorplan region selection"
    GREEN, RED, GREY = (0, 140, 0), (0, 0, 170), (50, 50, 50)

    print("\n[1/2] Mark EACH bedroom dimension: drag a box + ENTER to keep it,")
    print("      then drag the next. Press ESC when all bedrooms are marked.")
    bedrooms = []
    while True:
        view = _with_banner(base, [
            "STEP 1 of 2:  MARK BEDROOM DIMENSIONS  (green)",
            "Drag a box, ENTER = keep  |  ESC = done with bedrooms",
            f"Bedrooms marked so far: {len(bedrooms)}",
        ], GREEN)
        rect = cv2.selectROI(win, view, showCrosshair=True, fromCenter=False)
        x, y, w, h = (int(v) for v in rect)
        if w == 0 or h == 0:  # ESC / empty selection -> done with bedrooms
            break
        draw_labeled_box(base, (x, y, w, h), (0, 180, 0), f"BR{len(bedrooms) + 1}")
        bedrooms.append(_to_original((x, y, w, h), scale))

    # Explicit interstitial so it is obvious we are switching steps.
    print("\n  -> Bedrooms done. Next: mark the TOTAL AREA box.")
    prompt = _with_banner(base, [
        f"BEDROOMS DONE  ({len(bedrooms)} marked, shown in green).",
        "NEXT: mark the TOTAL AREA box.",
        "Press any key to continue…",
    ], RED)
    cv2.imshow(win, prompt)
    cv2.waitKey(0)

    print("\n[2/2] Mark the TOTAL AREA box (drag + ENTER), or ESC to skip.")
    view = _with_banner(base, [
        "STEP 2 of 2:  MARK TOTAL AREA  (red)",
        "Drag one box, ENTER = keep  |  ESC = skip",
    ], RED)
    rect = cv2.selectROI(win, view, showCrosshair=True, fromCenter=False)
    x, y, w, h = (int(v) for v in rect)
    area = None
    if w > 0 and h > 0:
        draw_labeled_box(base, (x, y, w, h), (0, 0, 255), "AREA")
        area = _to_original((x, y, w, h), scale)

    # Final confirmation of everything marked before OCR runs.
    print("\nMarked regions shown — press any key in the image window to run OCR.")
    final = _with_banner(base, ["ALL REGIONS MARKED — press any key to run OCR"], GREY)
    cv2.imshow(win, final)
    cv2.waitKey(0)
    cv2.destroyWindow(win)
    cv2.waitKey(1)  # let the window actually close on macOS

    return bedrooms, area


def load_regions(path):
    data = json.loads(Path(path).read_text())
    bedrooms = [tuple(r) for r in data.get("bedrooms", [])]
    area = tuple(data["total_area"]) if data.get("total_area") else None
    return bedrooms, area


def save_regions(path, image_path, bedrooms, area):
    Path(path).write_text(json.dumps({
        "image": str(image_path),
        "bedrooms": [list(r) for r in bedrooms],
        "total_area": list(area) if area else None,
    }, indent=2))


# ===========================================================================
# Drawing / reporting
# ===========================================================================

def draw_labeled_box(img, rect, color, label, thickness=3):
    x, y, w, h = rect
    cv2.rectangle(img, (x, y), (x + w, y + h), color, thickness)
    if label:
        cv2.putText(img, label, (x, max(0, y - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2, cv2.LINE_AA)


def crop(img, rect, pad=6):
    """Crop `rect` (x, y, w, h) from `img`, with a few pixels of padding so a
    box drawn slightly tight still captures trailing units (the "m" in
    "4.20m", the closing " on feet), which OCR needs to classify the value."""
    x, y, w, h = rect
    height, width = img.shape[:2]
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(width, x + w + pad), min(height, y + h + pad)
    return img[y0:y1, x0:x1]


def run(image_path, lang="en", max_display=1200, regions_path=None, out_path=None):
    """Select regions (or reuse a saved regions file), OCR them and parse the
    values. Writes an annotated image + results JSON, and RETURNS the results
    dict for programmatic use:

        {
          "image": str,
          "bedrooms": [{"box", "ocr_text", "raw", "m": (w,h)|None, "ft": (w,h)|None}, ...],
          "total_area": {"box", "ocr_text", "raw", "m2": float|None, "ft2": float|None} | None,
        }
    """
    image_path = Path(image_path)
    if out_path is None:
        out_path = image_path.with_name(image_path.stem + "_roi_annotated.png")

    img = cv2.imread(str(image_path))
    if img is None:
        sys.exit(f"Could not read image: {image_path}")

    if regions_path and Path(regions_path).exists():
        print(f"Loading regions from {regions_path}")
        bedrooms, area = load_regions(regions_path)
    else:
        bedrooms, area = select_regions(img, max_display)
        regions_out = image_path.with_name(image_path.stem + "_regions.json")
        save_regions(regions_out, image_path, bedrooms, area)
        print(f"\nSaved selected regions -> {regions_out}")

    if not bedrooms and not area:
        sys.exit("No regions selected — nothing to OCR.")

    print("\nInitialising PaddleOCR (first run downloads models)…")
    ocr = make_ocr(lang)

    annotated = img.copy()
    results = {"image": str(image_path), "bedrooms": [], "total_area": None}

    print("\n=== BEDROOMS (dimensions in metres and/or feet) ===")
    for i, rect in enumerate(bedrooms, 1):
        texts = ocr_texts(ocr, crop(img, rect))
        raw, parsed = best_dimension(texts)
        pretty = fmt_dimension(parsed) or "no metre/feet dimension found"
        print(f"  Bedroom {i}: {pretty}   (OCR: {texts})")
        results["bedrooms"].append({
            "box": list(rect), "ocr_text": texts, "raw": raw,
            "m": parsed["m"], "ft": parsed["ft"],
        })
        draw_labeled_box(annotated, rect, (0, 180, 0),
                         f"BR{i}: {fmt_dimension(parsed) or '?'}")

    print("\n=== TOTAL FLOOR AREA (ft² and/or m²) ===")
    if area:
        texts = ocr_texts(ocr, crop(img, area))
        raw, parsed = best_area(texts)
        pretty = fmt_area(parsed) or "no area found"
        print(f"  {pretty}   (OCR: {texts})")
        results["total_area"] = {
            "box": list(area), "ocr_text": texts, "raw": raw,
            "m2": parsed["m2"], "ft2": parsed["ft2"],
        }
        draw_labeled_box(annotated, area, (0, 0, 255),
                         f"AREA: {fmt_area(parsed) or '?'}")
    else:
        print("  no area box selected")

    cv2.imwrite(str(out_path), annotated)
    results_out = image_path.with_name(image_path.stem + "_roi_results.json")
    Path(results_out).write_text(json.dumps(results, indent=2))

    print(f"\nAnnotated image -> {out_path}")
    print(f"Results JSON    -> {results_out}")

    return results


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", help="path to the floor-plan image")
    ap.add_argument("--lang", default="en", help="PaddleOCR language (default: en)")
    ap.add_argument("--max-display", type=int, default=1200,
                    help="max window size for selection (default: 1200px)")
    ap.add_argument("--regions", default=None,
                    help="path to a regions JSON to reuse (skips selection)")
    ap.add_argument("--out", default=None,
                    help="annotated output path (default: <image>_roi_annotated.png)")
    args = ap.parse_args()

    image_path = Path(args.image)
    out_path = Path(args.out) if args.out else \
        image_path.with_name(image_path.stem + "_roi_annotated.png")

    run(image_path, args.lang, args.max_display, args.regions, out_path)


if __name__ == "__main__":
    main()
