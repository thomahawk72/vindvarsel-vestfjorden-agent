"""Klient mot MET Norway Locationforecast 2.0.

Krav fra MET sin ToS:
- Must set a descriptive User-Agent with contact info.
- Should honour Expires / Last-Modified headers to avoid 429s.
  https://api.met.no/doc/TermsOfService
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dateutil.parser import isoparse

from .config import CACHE_DIR, MET_ENDPOINT, Location
from .http_retry import request_with_retries


@dataclass(frozen=True)
class WindSample:
    """Ett tidspunkt i prognosen med de feltene vi bryr oss om."""

    time: datetime
    wind_from_direction: float
    wind_speed: float
    wind_speed_of_gust: float | None


def _user_agent() -> str:
    ua = os.environ.get("MET_USER_AGENT", "").strip()
    if not ua:
        raise RuntimeError(
            "MET_USER_AGENT mangler. Sett miljøvariabel, f.eks. "
            "'VestfjordenVindAgent/1.0 din@epost.no'"
        )
    return ua


def _cache_path(location: Location) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe = f"{location.lat:.4f}_{location.lon:.4f}.json"
    return CACHE_DIR / safe


def _load_cache(location: Location) -> dict[str, Any] | None:
    path = _cache_path(location)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_cache(location: Location, payload: dict[str, Any]) -> None:
    _cache_path(location).write_text(
        json.dumps(payload), encoding="utf-8"
    )


def fetch_forecast(location: Location, *, session: requests.Session | None = None) -> list[WindSample]:
    """Hent prognose for én lokasjon og returner tidsserien som WindSample-liste.

    Bruker If-Modified-Since mot en enkel fil-cache for å minimere API-kall.
    """
    sess = session or requests.Session()
    headers = {
        "User-Agent": _user_agent(),
        "Accept": "application/json",
    }
    cached = _load_cache(location)
    if cached and (last_modified := cached.get("last_modified")):
        headers["If-Modified-Since"] = last_modified

    params = {"lat": f"{location.lat:.4f}", "lon": f"{location.lon:.4f}"}
    resp = request_with_retries(
        sess,
        "GET",
        MET_ENDPOINT,
        params=params,
        headers=headers,
        timeout=30,
    )

    if resp.status_code == 304 and cached:
        body = cached["body"]
    else:
        resp.raise_for_status()
        body = resp.json()
        _save_cache(
            location,
            {
                "last_modified": resp.headers.get("Last-Modified", ""),
                "body": body,
            },
        )

    return _parse_timeseries(body)


def _parse_timeseries(body: dict[str, Any]) -> list[WindSample]:
    samples: list[WindSample] = []
    series = body.get("properties", {}).get("timeseries", [])
    for entry in series:
        time_str = entry.get("time")
        details = (
            entry.get("data", {}).get("instant", {}).get("details", {})
        )
        if not time_str or "wind_from_direction" not in details:
            continue
        samples.append(
            WindSample(
                time=isoparse(time_str).astimezone(timezone.utc),
                wind_from_direction=float(details["wind_from_direction"]),
                wind_speed=float(details.get("wind_speed", 0.0)),
                wind_speed_of_gust=(
                    float(details["wind_speed_of_gust"])
                    if "wind_speed_of_gust" in details
                    else None
                ),
            )
        )
    return samples
