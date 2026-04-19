"""Klient mot MET Frost API (sanntidsobservasjoner fra værstasjoner).

Krever en gratis client_id som settes i miljøvariabelen FROST_CLIENT_ID.
Registrering: https://frost.met.no/auth/requestCredentials.html

Auth er HTTP Basic med client_id som brukernavn og tomt passord.
Dokumentasjon: https://frost.met.no/howto.html
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from dateutil.parser import isoparse

from .config import (
    FROST_LOOKBACK_MIN,
    FROST_MAX_DISTANCE_KM,
    FROST_NEAREST_N,
    FROST_OBSERVATIONS_URL,
    FROST_SOURCES_URL,
    Location,
)

log = logging.getLogger(__name__)


class FrostNotConfigured(RuntimeError):
    """Kastes når FROST_CLIENT_ID ikke er satt."""


@dataclass(frozen=True)
class Station:
    id: str
    name: str
    lat: float
    lon: float
    distance_km: float


@dataclass(frozen=True)
class Observation:
    """Siste observasjon fra én stasjon."""

    station: Station
    time: datetime
    wind_from_direction: float | None
    wind_speed: float | None
    wind_speed_of_gust: float | None


def _client_id() -> str:
    cid = os.environ.get("FROST_CLIENT_ID", "").strip()
    if not cid:
        raise FrostNotConfigured(
            "FROST_CLIENT_ID mangler. Registrer på "
            "https://frost.met.no/auth/requestCredentials.html "
            "og sett som GitHub Secret."
        )
    return cid


def _auth(session: requests.Session) -> tuple[str, str]:
    return (_client_id(), "")


def find_nearest_stations(
    location: Location,
    *,
    n: int = FROST_NEAREST_N,
    session: requests.Session | None = None,
) -> list[Station]:
    """Returner inntil n stasjoner nærmest lokasjonen som har vinddata."""
    sess = session or requests.Session()
    params = {
        "types": "SensorSystem",
        "elements": "wind_speed",
        "geometry": f"nearest(POINT({location.lon} {location.lat}))",
        "nearestmaxcount": str(n),
        "fields": "id,name,geometry,distance",
    }
    resp = sess.get(
        FROST_SOURCES_URL,
        params=params,
        auth=_auth(sess),
        timeout=30,
    )
    if resp.status_code == 401:
        raise FrostNotConfigured("FROST_CLIENT_ID avvist (401 Unauthorized).")
    resp.raise_for_status()
    data = resp.json().get("data", [])

    stations: list[Station] = []
    for item in data:
        geom = item.get("geometry", {})
        coords = geom.get("coordinates") or [None, None]
        lon, lat = coords[0], coords[1]
        if lon is None or lat is None:
            continue
        distance = float(item.get("distance", 0.0))
        if distance > FROST_MAX_DISTANCE_KM:
            continue
        stations.append(
            Station(
                id=item["id"],
                name=item.get("name", item["id"]),
                lat=float(lat),
                lon=float(lon),
                distance_km=distance,
            )
        )
    return stations


def fetch_latest_observations(
    stations: list[Station],
    *,
    lookback_min: int = FROST_LOOKBACK_MIN,
    session: requests.Session | None = None,
) -> list[Observation]:
    """Hent siste observasjon per stasjon for vindretning, -hastighet og kast."""
    if not stations:
        return []

    sess = session or requests.Session()
    now = datetime.now(timezone.utc)
    since = now - timedelta(minutes=lookback_min)

    source_ids = ",".join(s.id for s in stations)
    elements = "wind_from_direction,wind_speed,max(wind_speed_of_gust PT1H)"
    params = {
        "sources": source_ids,
        "elements": elements,
        "referencetime": f"{since.strftime('%Y-%m-%dT%H:%M:%S')}Z/"
        f"{now.strftime('%Y-%m-%dT%H:%M:%S')}Z",
    }
    resp = sess.get(
        FROST_OBSERVATIONS_URL,
        params=params,
        auth=_auth(sess),
        timeout=30,
    )
    if resp.status_code == 404:
        log.info("Ingen observasjoner de siste %d min for valgte stasjoner", lookback_min)
        return []
    if resp.status_code == 401:
        raise FrostNotConfigured("FROST_CLIENT_ID avvist (401 Unauthorized).")
    resp.raise_for_status()

    raw = resp.json().get("data", [])
    station_by_id = {s.id: s for s in stations}
    latest: dict[str, dict[str, Any]] = {}

    for entry in raw:
        src_id = entry.get("sourceId", "").split(":")[0]
        if src_id not in station_by_id:
            continue
        time_str = entry.get("referenceTime")
        if not time_str:
            continue
        obs_time = isoparse(time_str).astimezone(timezone.utc)
        bucket = latest.setdefault(src_id, {"time": obs_time, "values": {}})
        if obs_time > bucket["time"]:
            bucket["time"] = obs_time
            bucket["values"] = {}
        if obs_time == bucket["time"]:
            for obs in entry.get("observations", []):
                element_id = obs.get("elementId")
                value = obs.get("value")
                if element_id and value is not None:
                    bucket["values"][element_id] = float(value)

    results: list[Observation] = []
    for src_id, bucket in latest.items():
        values = bucket["values"]
        gust_key = "max(wind_speed_of_gust PT1H)"
        results.append(
            Observation(
                station=station_by_id[src_id],
                time=bucket["time"],
                wind_from_direction=values.get("wind_from_direction"),
                wind_speed=values.get("wind_speed"),
                wind_speed_of_gust=values.get(gust_key),
            )
        )
    return results
