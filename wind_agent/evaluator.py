"""Evaluerer når østlig vind > terskel inntreffer, både nå og i prognosevinduet."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .config import (
    EAST_MAX_DEG,
    EAST_MIN_DEG,
    FORECAST_WINDOW_END_H,
    FORECAST_WINDOW_START_H,
    FROST_DIRECT_ALERT_KM,
    FROST_UPSTREAM_TOLERANCE_DEG,
    WIND_THRESHOLD_MS,
)
from .frost_client import Observation
from .met_client import WindSample


@dataclass(frozen=True)
class MatchedHour:
    """Én time i tidsserien som matcher kriteriet."""

    time: datetime
    wind_from_direction: float
    wind_speed: float
    wind_speed_of_gust: float | None


@dataclass(frozen=True)
class Event:
    """Et sammenhengende vindu av matchende timer."""

    start: datetime
    end: datetime
    hours: tuple[MatchedHour, ...]

    @property
    def peak_speed(self) -> float:
        return max(h.wind_speed for h in self.hours)

    @property
    def peak_gust(self) -> float | None:
        gusts = [h.wind_speed_of_gust for h in self.hours if h.wind_speed_of_gust is not None]
        return max(gusts) if gusts else None

    @property
    def dir_range(self) -> tuple[float, float]:
        dirs = [h.wind_from_direction for h in self.hours]
        return (min(dirs), max(dirs))


def matches(sample: WindSample) -> bool:
    """Kriteriet: retning i [EAST_MIN, EAST_MAX] og vind eller kast > terskel."""
    east = EAST_MIN_DEG <= sample.wind_from_direction <= EAST_MAX_DEG
    gust = sample.wind_speed_of_gust or 0.0
    strong = sample.wind_speed > WIND_THRESHOLD_MS or gust > WIND_THRESHOLD_MS
    return east and strong


def _sample_to_matched(sample: WindSample) -> MatchedHour:
    return MatchedHour(
        time=sample.time,
        wind_from_direction=sample.wind_from_direction,
        wind_speed=sample.wind_speed,
        wind_speed_of_gust=sample.wind_speed_of_gust,
    )


def find_now_match(samples: list[WindSample], *, now: datetime | None = None) -> MatchedHour | None:
    """Returner første matchende time som ligger innen ~1 time fra now."""
    now = now or datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=1, minutes=30)
    for s in samples:
        if s.time < now - timedelta(hours=1):
            continue
        if s.time > cutoff:
            return None
        if matches(s):
            return _sample_to_matched(s)
    return None


@dataclass(frozen=True)
class ObservedMatch:
    """En observasjon fra én stasjon som oppfyller kriteriet."""

    station_id: str
    station_name: str
    distance_km: float
    bearing_deg: float
    time: datetime
    wind_from_direction: float
    wind_speed: float
    wind_speed_of_gust: float | None
    is_upstream: bool


def _angular_diff(a: float, b: float) -> float:
    """Korteste vinkelforskjell mellom to grader (0–180)."""
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def matches_observation(obs: Observation) -> bool:
    """Samme kriterium som for prognose, men på en målt observasjon."""
    if obs.wind_from_direction is None:
        return False
    east = EAST_MIN_DEG <= obs.wind_from_direction <= EAST_MAX_DEG
    spd = obs.wind_speed or 0.0
    gust = obs.wind_speed_of_gust or 0.0
    strong = spd > WIND_THRESHOLD_MS or gust > WIND_THRESHOLD_MS
    return east and strong


def _is_alert_eligible(obs: Observation) -> tuple[bool, bool]:
    """Avgjør om en matchende observasjon skal gi varsel.

    Returnerer (eligible, is_upstream):
    - Nær stasjon (≤ FROST_DIRECT_ALERT_KM): eligible=True, is_upstream=True hvis
      bearing sammenfaller med vindretning (informativt for alertmeldingen).
    - Langt unna: eligible kun hvis stasjonen ligger oppstrøms, dvs. bearing fra
      lokasjon til stasjon er innen FROST_UPSTREAM_TOLERANCE_DEG av
      `wind_from_direction` (retningen vinden kommer FRA).
    """
    if obs.wind_from_direction is None:
        return (False, False)
    diff = _angular_diff(obs.station.bearing_deg, obs.wind_from_direction)
    is_upstream = diff <= FROST_UPSTREAM_TOLERANCE_DEG
    if obs.station.distance_km <= FROST_DIRECT_ALERT_KM:
        return (True, is_upstream)
    return (is_upstream, is_upstream)


def find_observation_match(observations: list[Observation]) -> ObservedMatch | None:
    """Returner første observasjon som matcher kriteriet OG er varselberettiget.

    Stasjoner lengre unna enn FROST_DIRECT_ALERT_KM må ligge oppstrøms for at
    deres observasjon skal trigge varsel. Dette hindrer varsel fra stasjoner
    som måler vær som ikke er på vei mot oss.
    """
    for obs in observations:
        if not matches_observation(obs):
            continue
        eligible, is_upstream = _is_alert_eligible(obs)
        if not eligible:
            continue
        return ObservedMatch(
            station_id=obs.station.id,
            station_name=obs.station.name,
            distance_km=obs.station.distance_km,
            bearing_deg=obs.station.bearing_deg,
            time=obs.time,
            wind_from_direction=obs.wind_from_direction or 0.0,
            wind_speed=obs.wind_speed or 0.0,
            wind_speed_of_gust=obs.wind_speed_of_gust,
            is_upstream=is_upstream,
        )
    return None


def find_forecast_event(
    samples: list[WindSample], *, now: datetime | None = None
) -> Event | None:
    """Første sammenhengende event i vinduet [now + START_H, now + END_H]."""
    now = now or datetime.now(timezone.utc)
    start_window = now + timedelta(hours=FORECAST_WINDOW_START_H)
    end_window = now + timedelta(hours=FORECAST_WINDOW_END_H)

    current: list[MatchedHour] = []
    for s in samples:
        if s.time < start_window or s.time > end_window:
            if current:
                break
            continue
        if matches(s):
            current.append(_sample_to_matched(s))
        elif current:
            break

    if not current:
        return None
    return Event(
        start=current[0].time,
        end=current[-1].time,
        hours=tuple(current),
    )
