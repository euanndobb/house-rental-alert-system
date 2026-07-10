# Build room areas (m²) and split rent from the floor-plan OCR results.
import json

FT_TO_M = 0.3048       # feet -> metres
M2_PER_FT2 = 0.092903  # square feet -> square metres
TOTAL_RENT = 3250


def bedroom_area_m2(bedroom):
    """Area of one bedroom in m², using the metric dimension if OCR found one,
    otherwise converting the imperial one. Returns None if neither was read.

    `bedroom` is a dict with "m" and/or "ft" as (width, height) or None — the
    shape produced by floorplan_roi_ocr.best_dimension().
    """
    if bedroom.get("m"):
        width, height = bedroom["m"]
        return round(width * height, 2)
    if bedroom.get("ft"):
        width, height = bedroom["ft"]
        return round((width * FT_TO_M) * (height * FT_TO_M), 2)
    return None


def total_area_m2(total):
    """Total floor area in m² (converts from ft² if only that was read).

    `total` is a dict with "m2"/"ft2" (floorplan_roi_ocr.best_area() shape),
    or None.
    """
    if not total:
        return None
    if total.get("m2") is not None:
        return round(total["m2"], 2)
    if total.get("ft2") is not None:
        return round(total["ft2"] * M2_PER_FT2, 2)
    return None


def room_areas_from_results(results):
    """Build the areas summary from a results dict (floorplan_roi_ocr shape):

        {'bedroom 1': m2, ..., 'total_area': m2,
         'bedroom_area_sum': m2, 'communal_area': m2}

    Bedrooms whose dimension could not be OCR'd are skipped; the rest are
    numbered sequentially.
    """
    areas = {}
    n = 0
    for bedroom in results.get("bedrooms", []):
        area = bedroom_area_m2(bedroom)
        if area is None:
            continue  # box OCR couldn't read a dimension from
        n += 1
        areas[f"bedroom {n}"] = area

    areas["total_area"] = total_area_m2(results.get("total_area"))
    areas["bedroom_area_sum"] = round(
        sum(areas[f"bedroom {i}"] for i in range(1, n + 1)), 2)
    areas["communal_area"] = (
        round(areas["total_area"] - areas["bedroom_area_sum"], 2)
        if areas["total_area"] is not None else None)
    return areas


def room_areas_m2(results_path):
    """Same as room_areas_from_results but reads the results from a JSON file."""
    with open(results_path) as f:
        return room_areas_from_results(json.load(f))


def rent_breakdown(areas, total_rent):
    """Split each bedroom's rent into its two components:

        base       – an equal share of the communal cost (same for every room)
        adjustment – an area-weighted share of the remaining rent
        total      – base + adjustment

    Returns {"base_rent": float, "remaining": float,
             "bedrooms": {name: {"base", "adjustment", "total"}}}.
    Generalised to however many bedrooms `areas` contains.
    """
    bedroom_names = [k for k in areas if k.startswith("bedroom ")]
    n = len(bedroom_names)
    if n == 0 or not areas.get("total_area") or not areas.get("bedroom_area_sum"):
        return {"base_rent": 0.0, "remaining": 0.0, "bedrooms": {}}

    base_rent = (areas["communal_area"] / areas["total_area"]) * total_rent / n
    remaining = total_rent - n * base_rent

    bedrooms = {}
    for name in bedroom_names:
        adjustment = areas[name] / areas["bedroom_area_sum"] * remaining
        bedrooms[name] = {
            "base": round(base_rent, 2),
            "adjustment": round(adjustment, 2),
            "total": round(base_rent + adjustment, 2),
        }
    return {"base_rent": round(base_rent, 2),
            "remaining": round(remaining, 2),
            "bedrooms": bedrooms}


def compute_rents(areas, total_rent):
    """Total rent per bedroom (base + area-weighted adjustment). The result
    sums to `total_rent`. See rent_breakdown() for the component split."""
    breakdown = rent_breakdown(areas, total_rent)
    return {name: parts["total"] for name, parts in breakdown["bedrooms"].items()}


if __name__ == "__main__":
    from pprint import pprint

    areas = room_areas_m2("files/43_lydford_road_roi_results.json")
    rents = compute_rents(areas, TOTAL_RENT)
    print("areas:")
    pprint(areas)
    print("\nrents:")
    pprint(rents)
    print(f"\nsum of rents = {sum(rents.values()):.2f}  (total = {TOTAL_RENT})")
