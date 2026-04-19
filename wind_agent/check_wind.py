"""Hovedinngang: kjøres av GitHub Actions hvert 30. minutt.

Henter prognose og (hvis konfigurert) sanntidsobservasjoner for hver
lokasjon, evaluerer kriteriet og sender push-varsel via ntfy.sh for
(a) nåværende time (prognose), (b) prognosevindu 6–24 t fram og
(c) faktiske målinger fra nærmeste værstasjon.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

import requests

from .config import (
    EAST_MAX_DEG,
    EAST_MIN_DEG,
    FORECAST_WINDOW_END_H,
    FORECAST_WINDOW_START_H,
    LOCATIONS,
    WIND_THRESHOLD_MS,
)
from .evaluator import find_forecast_event, find_now_match, find_observation_match
from .frost_client import (
    FrostNotConfigured,
    fetch_latest_observations,
    find_nearest_stations,
)
from .met_client import fetch_forecast
from .notifier import send_forecast_alert, send_now_alert, send_observed_alert
from .state import (
    clear_forecast,
    clear_now,
    clear_observed,
    load_state,
    mark_notified_forecast,
    mark_notified_now,
    mark_notified_observed,
    save_state,
    should_notify_forecast,
    should_notify_now,
    should_notify_observed,
)

log = logging.getLogger("wind_agent")


def run() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    now = datetime.now(timezone.utc)
    log.info(
        "Config: østvindu %.0f-%.0f°, terskel %.1f m/s, prognose %d-%d t",
        EAST_MIN_DEG,
        EAST_MAX_DEG,
        WIND_THRESHOLD_MS,
        FORECAST_WINDOW_START_H,
        FORECAST_WINDOW_END_H,
    )
    state = load_state()
    session = requests.Session()

    any_error = False
    frost_enabled = True

    for loc in LOCATIONS:
        log.info("Henter prognose for %s (%.4f, %.4f)", loc.name, loc.lat, loc.lon)
        try:
            samples = fetch_forecast(loc, session=session)
        except Exception as exc:  # noqa: BLE001
            log.error("Feil ved henting for %s: %s", loc.name, exc)
            any_error = True
            continue

        if not samples:
            log.warning("Ingen prognosedata for %s", loc.name)
            continue

        now_match = find_now_match(samples, now=now)
        if now_match is None:
            clear_now(state, loc.name)
        elif should_notify_now(state, loc.name, now_match.time):
            try:
                send_now_alert(loc.name, now_match)
                mark_notified_now(state, loc.name, now_match.time)
            except Exception as exc:  # noqa: BLE001
                log.error("Feil ved ntfy-varsel (nå) for %s: %s", loc.name, exc)
                any_error = True
        else:
            log.info("Nå-match for %s allerede varslet (%s)", loc.name, now_match.time)

        forecast_event = find_forecast_event(samples, now=now)
        if forecast_event is None:
            clear_forecast(state, loc.name)
        elif should_notify_forecast(state, loc.name, forecast_event.start):
            try:
                send_forecast_alert(loc.name, forecast_event)
                mark_notified_forecast(state, loc.name, forecast_event.start)
            except Exception as exc:  # noqa: BLE001
                log.error("Feil ved ntfy-varsel (prognose) for %s: %s", loc.name, exc)
                any_error = True
        else:
            log.info(
                "Prognose-event for %s allerede varslet (%s)",
                loc.name,
                forecast_event.start,
            )

        if not frost_enabled:
            continue
        try:
            stations = find_nearest_stations(loc, session=session)
        except FrostNotConfigured as exc:
            log.warning("Hopper over observasjoner: %s", exc)
            frost_enabled = False
            continue
        except Exception:
            log.exception(
                "Frost-stasjonssøk feilet for %s — hopper over observasjoner",
                loc.name,
            )
            continue

        if not stations:
            log.info("Ingen Frost-stasjoner innenfor maks avstand for %s", loc.name)
            clear_observed(state, loc.name)
            continue
        log.info(
            "Frost-stasjoner for %s: %s",
            loc.name,
            ", ".join(f"{s.name} ({s.distance_km:.1f} km)" for s in stations),
        )

        try:
            observations = fetch_latest_observations(stations, session=session)
        except FrostNotConfigured as exc:
            log.warning("Hopper over observasjoner: %s", exc)
            frost_enabled = False
            continue
        except Exception:
            log.exception(
                "Frost-observasjonshenting feilet for %s — hopper over",
                loc.name,
            )
            continue

        observed_match = find_observation_match(observations)
        if observed_match is None:
            clear_observed(state, loc.name)
        elif should_notify_observed(state, loc.name, observed_match.time):
            try:
                send_observed_alert(loc.name, observed_match)
                mark_notified_observed(state, loc.name, observed_match.time)
            except Exception:
                log.exception("Feil ved ntfy-varsel (observert) for %s", loc.name)
                any_error = True
        else:
            log.info(
                "Observert match for %s allerede varslet (%s)",
                loc.name,
                observed_match.time,
            )

    save_state(state)
    return 1 if any_error else 0


if __name__ == "__main__":
    sys.exit(run())
