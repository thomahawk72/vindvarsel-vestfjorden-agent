"""Evaluerer når østlig vind > terskel inntreffer, både nå og i prognosevinduet."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .config import (
    EAST_MAX_DEG,
    EAST_MIN_DEG,
    FORECAST_WINDOW_END_H,
    FORECAST_WINDOW_START_H,
    WIND_THRESHOLD_MS,
)
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
