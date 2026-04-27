"""Konfigurasjon for vindvarsel-agenten.

Juster koordinater, gradintervall, terskler og tidsvinduer her.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _float_from_env(name: str, default: float) -> float:
    """Les flyttall fra miljøvariabel, fall tilbake til default ved feil/mangel."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Location:
    name: str
    lat: float
    lon: float


LOCATIONS: tuple[Location, ...] = (
    Location(name="Konglungen", lat=59.862, lon=10.508),
    Location(name="Bjerkøya", lat=59.849, lon=10.505),
)

# Vindretning fra øst: 45°-135° (NØ til SØ). Retning angir hvor vinden kommer FRA.
EAST_MIN_DEG: float = 45.0
EAST_MAX_DEG: float = 135.0

# Terskel i m/s for både gjennomsnittsvind og kast.
# Kan overstyres via miljøvariabel WIND_THRESHOLD_MS (GitHub Actions Variable).
WIND_THRESHOLD_MS: float = _float_from_env("WIND_THRESHOLD_MS", 4.0)

# Prognosevindu for "heads-up"-varsel (timer fram i tid).
FORECAST_WINDOW_START_H: int = 6
FORECAST_WINDOW_END_H: int = 24

# MET Norway Locationforecast 2.0 compact endpoint.
MET_ENDPOINT: str = "https://api.met.no/weatherapi/locationforecast/2.0/compact"

# MET Frost API (sanntidsobservasjoner fra værstasjoner).
FROST_SOURCES_URL: str = "https://frost.met.no/sources/v0.jsonld"
FROST_OBSERVATIONS_URL: str = "https://frost.met.no/observations/v0.jsonld"

# Antall nærmeste stasjoner vi sjekker per lokasjon.
FROST_NEAREST_N: int = 5

# Søkeradius: hvor langt vi leter etter værstasjoner (km).
FROST_MAX_DISTANCE_KM: float = 25.0

# Direkte-varsel-radius: stasjoner innen denne avstanden kan varsle uansett
# hvor vinden kommer fra. Stasjoner lenger unna varsler kun hvis de ligger
# "oppstrøms" — altså i retningen vinden kommer FRA — og derfor indikerer
# været på vei mot lokasjonen.
FROST_DIRECT_ALERT_KM: float = 15.0

# Toleranse i grader for hva som regnes som "oppstrøms" (±tolerance fra vindretning).
FROST_UPSTREAM_TOLERANCE_DEG: float = 45.0

# Hvor mange minutter tilbake vi ser etter observasjoner.
FROST_LOOKBACK_MIN: int = 60

# Cerbo GX / Signal K MQTT. Hvis CERBO_MQTT_URL ikke er satt, hoppes denne
# datakilden over og agenten bruker MET/Frost som før.
CERBO_MQTT_URL: str = os.environ.get("CERBO_MQTT_URL", "").strip()
CERBO_MQTT_USERNAME: str = os.environ.get("CERBO_MQTT_USERNAME", "").strip()
CERBO_MQTT_PASSWORD: str = os.environ.get("CERBO_MQTT_PASSWORD", "").strip()
CERBO_MQTT_TOPIC_FILTER: str = os.environ.get("CERBO_MQTT_TOPIC_FILTER", "#").strip() or "#"
CERBO_MQTT_TIMEOUT_SEC: float = _float_from_env("CERBO_MQTT_TIMEOUT_SEC", 40.0)
CERBO_MQTT_FRESH_MAX_MIN: float = _float_from_env("CERBO_MQTT_FRESH_MAX_MIN", 15.0)
CERBO_SOURCE_NAME: str = os.environ.get("CERBO_SOURCE_NAME", "Cerbo GX").strip() or "Cerbo GX"

# Signal K headingMagnetic er magnetisk. Sett lokal misvisning i grader øst
# positivt hvis du vil konvertere til tilnærmet sann retning.
CERBO_MAGNETIC_DECLINATION_DEG: float = _float_from_env(
    "CERBO_MAGNETIC_DECLINATION_DEG", 0.0
)

# ntfy.sh server. Kan overstyres hvis man kjører egen instans.
NTFY_SERVER: str = "https://ntfy.sh"

# Filsti for dedup-tilstand. Commites tilbake til repo av GitHub Actions.
STATE_FILE: Path = Path(__file__).parent / "state.json"

# Filsti for enkel HTTP-cache (ETag / Last-Modified per lokasjon).
CACHE_DIR: Path = Path(__file__).parent / ".cache"
