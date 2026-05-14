"""Hovedinngang: kjøres av GitHub Actions hvert 10. minutt.

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

from .cerbo_client import CerboNotConfigured, fetch_cerbo_observation
from .config import (
    ALERT_REPEAT_MIN,
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
        "Config: østvindu %.0f-%.0f°, terskel %.1f m/s, prognose %d-%d t, repeat %.0f min",
        EAST_MIN_DEG,
        EAST_MAX_DEG,
        WIND_THRESHOLD_MS,
        FORECAST_WINDOW_START_H,
        FORECAST_WINDOW_END_H,
        ALERT_REPEAT_MIN,
    )
    state = load_state()
    session = requests.Session()

    any_error = False
    frost_enabled = True
    cerbo_observation = None
    try:
        cerbo_observation = fetch_cerbo_observation()
        if cerbo_observation is None:
            log.info("Ingen fersk Cerbo/Signal K-observasjon tilgjengelig")
        else:
            log.info(
                "Cerbo/Signal K: %s %.1f m/s fra %.0f°",
                cerbo_observation.time.isoformat(),
                cerbo_observation.wind_speed or 0.0,
                cerbo_observation.wind_from_direction or 0.0,
            )
    except CerboNotConfigured as exc:
        log.info("Hopper over Cerbo/Signal K MQTT: %s", exc)
    except Exception:
        log.exception("Cerbo/Signal K MQTT feilet — bruker MET/Frost videre")

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
        elif should_notify_now(state, loc.name, now=now):
            try:
                send_now_alert(loc.name, now_match)
                mark_notified_now(state, loc.name, now_match.time, now=now)
            except Exception as exc:  # noqa: BLE001
                log.error("Feil ved ntfy-varsel (nå) for %s: %s", loc.name, exc)
                any_error = True
        else:
            log.info("Nå-match for %s allerede varslet (%s)", loc.name, now_match.time)

        forecast_event = find_forecast_event(samples, now=now)
        if forecast_event is None:
            clear_forecast(state, loc.name)
        elif should_notify_forecast(state, loc.name, now=now):
            try:
                send_forecast_alert(loc.name, forecast_event)
                mark_notified_forecast(
                    state, loc.name, forecast_event.start, now=now
                )
            except Exception as exc:  # noqa: BLE001
                log.error("Feil ved ntfy-varsel (prognose) for %s: %s", loc.name, exc)
                any_error = True
        else:
            log.info(
                "Prognose-event for %s allerede varslet (%s)",
                loc.name,
                forecast_event.start,
            )

        if cerbo_observation is not None:
            cerbo_match = find_observation_match([cerbo_observation])
            if cerbo_match is None:
                clear_observed(state, loc.name)
            elif should_notify_observed(state, loc.name, now=now):
                try:
                    send_observed_alert(loc.name, cerbo_match)
                    mark_notified_observed(state, loc.name, cerbo_match.time, now=now)
                except Exception:
                    log.exception(
                        "Feil ved ntfy-varsel (Cerbo-observert) for %s",
                        loc.name,
                    )
                    any_error = True
            else:
                log.info(
                    "Cerbo-observert match for %s allerede varslet (%s)",
                    loc.name,
                    cerbo_match.time,
                )
            # Cerbo er på-stedet-data og er derfor primær realtime-kilde.
            continue

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
            ", ".join(
                f"{s.name} ({s.distance_km:.1f} km @ {s.bearing_deg:.0f}°)"
                for s in stations
            ),
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
        elif should_notify_observed(state, loc.name, now=now):
            try:
                send_observed_alert(loc.name, observed_match)
                mark_notified_observed(
                    state, loc.name, observed_match.time, now=now
                )
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
