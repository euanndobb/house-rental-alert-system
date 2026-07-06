"""Real door-to-door travel time via the TfL Journey Planner API (free, no key).

This is the implementation of section 6a of ``rightmove_listings.ipynb`` — replacing
the crow-flies estimate with actual public-transport journey time to each target.

Why TfL: it's free, needs no API key for low volume, and models real London journeys
(tube / bus / national-rail / DLR / walking) — exactly what a commute is. It only
covers London, which is fine here since every target is central London.

Usage
-----
    from travel_utils import travel_times_to_targets, next_weekday_at

    origin = (51.5014, -0.1071)                 # a listing's (lat, lon)
    times = travel_times_to_targets(
        origin, TARGETS,
        arrival_time=next_weekday_at(9),        # arrive by 09:00 on the next weekday
    )
    # -> {"Paddington": {"status": "OK", "minutes": 21, "distance_km": 5.4,
    #                     "text": "21 min (walking → tube → walking)"}, ...}

Notes
-----
* TfL's Journey Planner routes ONE origin → ONE destination per call, so we make one
  request per target (there's no batch matrix endpoint). For a whole listing set that's
  ``n_listings × n_targets`` calls — use ``delay_seconds`` to stay polite, and set a
  ``TFL_APP_KEY`` env var (free from https://api-portal.tfl.gov.uk/) for higher limits.
* ``minutes`` (journey duration) is the reliable figure. ``distance_km`` is summed from
  whichever legs report a distance and may be ``None`` for all-rail journeys.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta

import requests

TFL_BASE = "https://api.tfl.gov.uk"


def _latlon(point) -> str:
    """Format a point as TfL's 'lat,lon' string. Accepts a (lat, lon) pair or a string."""
    if isinstance(point, (tuple, list)):
        return f"{point[0]},{point[1]}"
    return str(point)


def _auth_params(app_key: str | None = None) -> dict:
    """Optional app key (arg or TFL_APP_KEY env var) for higher rate limits."""
    key = app_key or os.environ.get("TFL_APP_KEY")
    return {"app_key": key} if key else {}


def _parse_journeys(data: dict) -> dict:
    """Reduce a TfL JourneyResults payload to the fastest journey, as a tidy dict.

    Pure function (no network) so it can be unit-tested offline.
    """
    journeys = data.get("journeys") or []
    if not journeys:
        return {"status": "NO_JOURNEY", "minutes": None, "distance_km": None, "text": None}

    best = min(journeys, key=lambda j: j.get("duration", float("inf")))
    minutes = best.get("duration")

    legs = best.get("legs", [])
    modes = [(leg.get("mode") or {}).get("name") for leg in legs]
    modes = [m for m in modes if m]

    leg_dists = [leg.get("distance") for leg in legs if leg.get("distance")]
    distance_km = round(sum(leg_dists) / 1000, 2) if leg_dists else None

    text = f"{minutes} min"
    if modes:
        text += f" ({' → '.join(modes)})"

    return {"status": "OK", "minutes": minutes, "distance_km": distance_km, "text": text}


def travel_time(
    origin,
    destination,
    arrival_time: datetime | None = None,
    modes: list[str] | None = None,
    app_key: str | None = None,
    session: requests.Session | None = None,
) -> dict:
    """Fastest public-transport journey time from one origin to one destination.

    Parameters
    ----------
    origin, destination : (lat, lon) tuples, or already-formatted "lat,lon" strings.
                          Coordinates avoid TfL's place-name disambiguation.
    arrival_time        : the datetime you want to *arrive by*. If None, TfL plans
                          from "now".
    modes               : restrict to specific TfL modes, e.g. ["tube", "walking",
                          "national-rail", "bus", "dlr"]. None = all modes.
    app_key             : optional TfL app key (else TFL_APP_KEY env var).
    session             : optional requests.Session for connection reuse.

    Returns
    -------
    ``{"status", "minutes", "distance_km", "text"}`` — status is "OK", "NO_JOURNEY",
    or "AMBIGUOUS" (the last only if a place name couldn't be resolved).
    """
    params: dict = {}
    if arrival_time is not None:
        params["date"] = arrival_time.strftime("%Y%m%d")
        params["time"] = arrival_time.strftime("%H%M")
        params["timeIs"] = "Arriving"
    if modes:
        params["mode"] = ",".join(modes)
    params.update(_auth_params(app_key))

    url = f"{TFL_BASE}/Journey/JourneyResults/{_latlon(origin)}/to/{_latlon(destination)}"
    http = session or requests
    resp = http.get(url, params=params, timeout=30)

    # 300 = TfL couldn't resolve a place unambiguously (shouldn't happen with coords).
    if resp.status_code == 300:
        return {"status": "AMBIGUOUS", "minutes": None, "distance_km": None, "text": None}
    resp.raise_for_status()
    return _parse_journeys(resp.json())


def travel_times_to_targets(
    origin,
    targets: dict,
    arrival_time: datetime | None = None,
    modes: list[str] | None = None,
    app_key: str | None = None,
    delay_seconds: float = 0.0,
) -> dict:
    """Journey time from one origin to every target (one TfL call per target).

    ``targets`` is the TARGETS dict — each value must have ``lat``/``lon``. Returns
    ``{name: {"status", "minutes", "distance_km", "text"}}`` in the same order.

    Set ``delay_seconds`` to space out requests when routing many listings.
    """
    session = requests.Session()
    out: dict = {}
    for i, (name, tgt) in enumerate(targets.items()):
        if i and delay_seconds:
            time.sleep(delay_seconds)
        out[name] = travel_time(
            origin, (tgt["lat"], tgt["lon"]),
            arrival_time=arrival_time, modes=modes, app_key=app_key, session=session,
        )
    return out


def next_weekday_at(hour: int, minute: int = 0, now: datetime | None = None) -> datetime:
    """The next upcoming weekday (Mon–Fri) at ``hour:minute``.

    Handy for a commute ``arrival_time``: 'arrive by 09:00 on the next working day'.
    If today is a weekday and that time is still ahead, returns today; otherwise the
    next weekday. Pass ``now`` to make it deterministic in tests.
    """
    now = now or datetime.now()
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:  # 5=Sat, 6=Sun
        candidate += timedelta(days=1)
    return candidate
