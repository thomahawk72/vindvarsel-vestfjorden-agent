"""De-duplisering og repetisjonsstyring av varsler.

state.json-strukturen:
{
  "locations": {
    "<location-name>": {
      "last_now_event_start": "ISO-8601 eller null",
      "last_now_notified_at": "ISO-8601 eller null",
      "last_forecast_event_start": "ISO-8601 eller null",
      "last_forecast_notified_at": "ISO-8601 eller null",
      "last_observed_event_start": "ISO-8601 eller null",
      "last_observed_notified_at": "ISO-8601 eller null"
    }
  }
}

Nye events varsles umiddelbart. Samme aktive event kan varsles på nytt etter
ALERT_REPEAT_MIN minutter, og nullstilles når det er en "av"-periode.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import ALERT_REPEAT_MIN, STATE_FILE


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
    entry = state["locations"].setdefault(
        location_name,
        {
            "last_now_event_start": None,
            "last_now_notified_at": None,
            "last_forecast_event_start": None,
            "last_forecast_notified_at": None,
            "last_observed_event_start": None,
            "last_observed_notified_at": None,
        },
    )
    entry.setdefault("last_now_notified_at", None)
    entry.setdefault("last_observed_event_start", None)
    entry.setdefault("last_observed_notified_at", None)
    entry.setdefault("last_forecast_notified_at", None)
    return entry


def _parse_iso(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _should_notify(
    entry: dict[str, Any],
    *,
    notified_key: str,
    now: datetime,
) -> bool:
    last_notified = _parse_iso(entry.get(notified_key))
    if last_notified is None:
        return True
    repeat_after = timedelta(minutes=ALERT_REPEAT_MIN)
    return now.astimezone(timezone.utc) - last_notified >= repeat_after


def _mark_notified(
    entry: dict[str, Any],
    *,
    event_key: str,
    notified_key: str,
    event_time: datetime,
    now: datetime,
) -> None:
    entry[event_key] = event_time.isoformat()
    entry[notified_key] = now.astimezone(timezone.utc).isoformat()


def should_notify_now(
    state: dict[str, Any],
    location_name: str,
    *,
    now: datetime,
) -> bool:
    entry = _loc_entry(state, location_name)
    return _should_notify(entry, notified_key="last_now_notified_at", now=now)


def mark_notified_now(
    state: dict[str, Any],
    location_name: str,
    event_time: datetime,
    *,
    now: datetime,
) -> None:
    _mark_notified(
        _loc_entry(state, location_name),
        event_key="last_now_event_start",
        notified_key="last_now_notified_at",
        event_time=event_time,
        now=now,
    )


def clear_now(state: dict[str, Any], location_name: str) -> None:
    entry = _loc_entry(state, location_name)
    entry["last_now_event_start"] = None
    entry["last_now_notified_at"] = None


def should_notify_forecast(
    state: dict[str, Any],
    location_name: str,
    *,
    now: datetime,
) -> bool:
    entry = _loc_entry(state, location_name)
    return _should_notify(entry, notified_key="last_forecast_notified_at", now=now)


def mark_notified_forecast(
    state: dict[str, Any],
    location_name: str,
    event_start: datetime,
    *,
    now: datetime,
) -> None:
    _mark_notified(
        _loc_entry(state, location_name),
        event_key="last_forecast_event_start",
        notified_key="last_forecast_notified_at",
        event_time=event_start,
        now=now,
    )


def clear_forecast(state: dict[str, Any], location_name: str) -> None:
    entry = _loc_entry(state, location_name)
    entry["last_forecast_event_start"] = None
    entry["last_forecast_notified_at"] = None


def should_notify_observed(
    state: dict[str, Any],
    location_name: str,
    *,
    now: datetime,
) -> bool:
    entry = _loc_entry(state, location_name)
    return _should_notify(entry, notified_key="last_observed_notified_at", now=now)


def mark_notified_observed(
    state: dict[str, Any],
    location_name: str,
    event_time: datetime,
    *,
    now: datetime,
) -> None:
    _mark_notified(
        _loc_entry(state, location_name),
        event_key="last_observed_event_start",
        notified_key="last_observed_notified_at",
        event_time=event_time,
        now=now,
    )


def clear_observed(state: dict[str, Any], location_name: str) -> None:
    entry = _loc_entry(state, location_name)
    entry["last_observed_event_start"] = None
    entry["last_observed_notified_at"] = None
