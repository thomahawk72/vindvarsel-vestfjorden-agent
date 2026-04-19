"""De-duplisering av varsler.

state.json-strukturen:
{
  "locations": {
    "<location-name>": {
      "last_now_event_start": "ISO-8601 eller null",
      "last_forecast_event_start": "ISO-8601 eller null"
    }
  }
}

Et nytt "now"-varsel sendes kun når vi har en match og matchens time-stempel er
ulik det sist varslede. Vi nullstiller når det er en "av"-periode, slik at det
samme eventet ikke varsles flere ganger, men nye events varsles.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import STATE_FILE


def load_state(path: Path = STATE_FILE) -> dict[str, Any]:
    if not path.exists():
        return {"locations": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"locations": {}}
    data.setdefault("locations", {})
    return data


def save_state(state: dict[str, Any], path: Path = STATE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _loc_entry(state: dict[str, Any], location_name: str) -> dict[str, Any]:
    return state["locations"].setdefault(
        location_name,
        {"last_now_event_start": None, "last_forecast_event_start": None},
    )


def should_notify_now(state: dict[str, Any], location_name: str, event_time: datetime) -> bool:
    entry = _loc_entry(state, location_name)
    return entry.get("last_now_event_start") != event_time.isoformat()


def mark_notified_now(state: dict[str, Any], location_name: str, event_time: datetime) -> None:
    _loc_entry(state, location_name)["last_now_event_start"] = event_time.isoformat()


def clear_now(state: dict[str, Any], location_name: str) -> None:
    _loc_entry(state, location_name)["last_now_event_start"] = None


def should_notify_forecast(
    state: dict[str, Any], location_name: str, event_start: datetime
) -> bool:
    entry = _loc_entry(state, location_name)
    return entry.get("last_forecast_event_start") != event_start.isoformat()


def mark_notified_forecast(
    state: dict[str, Any], location_name: str, event_start: datetime
) -> None:
    _loc_entry(state, location_name)["last_forecast_event_start"] = event_start.isoformat()


def clear_forecast(state: dict[str, Any], location_name: str) -> None:
    _loc_entry(state, location_name)["last_forecast_event_start"] = None
