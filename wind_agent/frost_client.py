"""Klient mot MET Frost API (sanntidsobservasjoner fra værstasjoner).

Krever en gratis client_id som settes i miljøvariabelen FROST_CLIENT_ID.
Registrering: https://frost.met.no/auth/requestCredentials.html

Auth er HTTP Basic med client_id som brukernavn og tomt passord.
Dokumentasjon: https://frost.met.no/howto.html
"""
from __future__ import annotations

import logging
import math
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
    # Retning i grader fra lokasjonen til stasjonen (0° = nord, 90° = øst).
    # Brukes til å vurdere om stasjonen ligger "oppstrøms" for vinden.
    bearing_deg: float


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


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Stor-sirkel-avstand i km mellom to punkter."""
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(a))


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initiell peiling fra (lat1, lon1) mot (lat2, lon2), 0°=N, 90°=Ø."""
    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    dlon_r = math.radians(lon2 - lon1)
    y = math.sin(dlon_r) * math.cos(lat2_r)
    x = math.cos(lat1_r) * math.sin(lat2_r) - math.sin(lat1_r) * math.cos(
        lat2_r
    ) * math.cos(dlon_r)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _bbox_polygon(lat: float, lon: float, radius_km: float) -> str:
    """BBOX-polygon rundt et punkt (grov konvertering — god nok for stasjonssøk)."""
    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * math.cos(math.radians(lat)) or 1.0)
    lat_min, lat_max = lat - dlat, lat + dlat
    lon_min, lon_max = lon - dlon, lon + dlon
    return (
        f"POLYGON(("
        f"{lon_min} {lat_min}, "
        f"{lon_min} {lat_max}, "
        f"{lon_max} {lat_max}, "
        f"{lon_max} {lat_min}, "
        f"{lon_min} {lat_min}"
        f"))"
    )


def find_nearest_stations(
    location: Location,
    *,
    n: int = FROST_NEAREST_N,
    session: requests.Session | None = None,
) -> list[Station]:
    """Returner inntil n stasjoner nærmest lokasjonen som har vinddata.

    Bruker POLYGON-søk + selvberegnet avstand (Frost v0 støtter ikke
    `nearestmaxcount`, og `nearest(POINT(...))` gir kun én stasjon).
    """
    sess = session or requests.Session()
    polygon = _bbox_polygon(location.lat, location.lon, FROST_MAX_DISTANCE_KM)
    params = {
        "types": "SensorSystem",
        "elements": "wind_speed",
        "geometry": polygon,
        "stationholder": "MET.NO",
        "fields": "id,name,geometry",
    }
    resp = sess.get(
        FROST_SOURCES_URL,
        params=params,
        auth=_auth(sess),
        timeout=30,
    )
    if resp.status_code == 401:
        raise FrostNotConfigured("FROST_CLIENT_ID avvist (401 Unauthorized).")
    if resp.status_code == 404:
        log.info("Ingen stasjoner funnet i boks rundt %s", location.name)
        return []
    resp.raise_for_status()
    data = resp.json().get("data", [])

    stations: list[Station] = []
    for item in data:
        geom = item.get("geometry") or {}
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        lon, lat = float(coords[0]), float(coords[1])
        distance = _haversine_km(location.lat, location.lon, lat, lon)
        if distance > FROST_MAX_DISTANCE_KM:
            continue
        station_id = item.get("id")
        if not station_id:
            continue
        stations.append(
            Station(
                id=station_id,
                name=item.get("name") or station_id,
                lat=lat,
                lon=lon,
                distance_km=distance,
                bearing_deg=_bearing_deg(location.lat, location.lon, lat, lon),
            )
        )
    stations.sort(key=lambda s: s.distance_km)
    return stations[:n]


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
    # Instantaneous-målinger: direkteverdier uten aggregering. Gust er valgfri
    # og vil mangle for stasjoner som ikke måler kast — det er OK.
    elements = "wind_from_direction,wind_speed,wind_speed_of_gust"
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
    # 404 = ingen data i perioden. 412 = alle etterspurte elementer mangler for
    # alle stasjoner i intervallet (Frost's måte å si "tomt"). Begge er OK.
    if resp.status_code in (404, 412):
        log.info(
            "Ingen observasjoner de siste %d min for valgte stasjoner (HTTP %d)",
            lookback_min,
            resp.status_code,
        )
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
        results.append(
            Observation(
                station=station_by_id[src_id],
                time=bucket["time"],
                wind_from_direction=values.get("wind_from_direction"),
                wind_speed=values.get("wind_speed"),
                wind_speed_of_gust=values.get("wind_speed_of_gust"),
            )
        )
    return results
