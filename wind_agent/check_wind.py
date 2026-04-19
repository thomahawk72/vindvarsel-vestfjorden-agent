"""Hovedinngang: kjøres av GitHub Actions hvert 30. minutt.

Henter prognose for hver lokasjon, evaluerer kriteriet og sender
push-varsel via ntfy.sh for (a) nåværende time og (b) prognosevindu
6–24 timer fram.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

import requests

from .config import LOCATIONS
from .evaluator import find_forecast_event, find_now_match
from .met_client import fetch_forecast
from .notifier import send_forecast_alert, send_now_alert
from .state import (
    clear_forecast,
    clear_now,
    load_state,
    mark_notified_forecast,
    mark_notified_now,
    save_state,
    should_notify_forecast,
    should_notify_now,
)

log = logging.getLogger("wind_agent")


def run() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    now = datetime.now(timezone.utc)
    state = load_state()
    session = requests.Session()

    any_error = False

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

    save_state(state)
    return 1 if any_error else 0


if __name__ == "__main__":
    sys.exit(run())
