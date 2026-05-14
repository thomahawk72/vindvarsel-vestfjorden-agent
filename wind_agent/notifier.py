"""Push-varsling via ntfy.sh."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

from .config import NTFY_SERVER
from .evaluator import Event, MatchedHour, ObservedMatch
from .http_retry import request_with_retries

log = logging.getLogger(__name__)

OSLO_TZ = ZoneInfo("Europe/Oslo")


@dataclass
class AlertItem:
    """Éin ventande varseloppføring frå éin lokasjon og kjelde."""

    location_name: str
    alert_type: str  # "now", "observed", "forecast"
    data: MatchedHour | ObservedMatch | Event
    source: str  # "MET", "Frost", "Cerbo"


def _topic() -> str:
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        raise RuntimeError(
            "NTFY_TOPIC mangler. Sett miljøvariabel eller GitHub Secret."
        )
    return topic


def _compass(deg: float) -> str:
    points = ["N", "NNØ", "NØ", "ØNØ", "Ø", "ØSØ", "SØ", "SSØ",
              "S", "SSV", "SV", "VSV", "V", "VNV", "NV", "NNV"]
    idx = int((deg % 360) / 22.5 + 0.5) % 16
    return points[idx]


def _fmt_local(t: datetime) -> str:
    return t.astimezone(OSLO_TZ).strftime("%a %d.%m kl %H:%M")


def _send(title: str, body: str, *, priority: str = "default", tags: str = "wind_face") -> None:
    url = f"{NTFY_SERVER.rstrip('/')}/{_topic()}"
    headers = {
        "Title": title.encode("utf-8"),
        "Priority": priority,
        "Tags": tags,
    }
    log.info("Sender ntfy: %s", title)
    session = requests.Session()
    resp = request_with_retries(
        session,
        "POST",
        url,
        data=body.encode("utf-8"),
        headers=headers,
        timeout=20,
        log_url=f"{NTFY_SERVER.rstrip('/')}/<topic>",
    )
    try:
        resp.raise_for_status()
    except requests.HTTPError:
        log.error("ntfy svarte med HTTP %s: %s", resp.status_code, resp.text[:300])
        raise


def send_now_alert(location_name: str, hour: MatchedHour, *, extra_lines: list[str] | None = None) -> None:
    gust_txt = (
        f", kast {hour.wind_speed_of_gust:.1f} m/s"
        if hour.wind_speed_of_gust is not None
        else ""
    )
    title = f"Østlig vind {location_name} NÅ — {hour.wind_speed:.1f} m/s"
    lines = [
        f"{location_name} {_fmt_local(hour.time)}:",
        f"  Retning {hour.wind_from_direction:.0f}° ({_compass(hour.wind_from_direction)}), "
        f"vind {hour.wind_speed:.1f} m/s{gust_txt}",
    ]
    if extra_lines:
        lines.extend(extra_lines)
    _send(title, "\n".join(lines), priority="high", tags="wind_face,warning")


def send_observed_alert(location_name: str, match: ObservedMatch) -> None:
    """Varsel basert på faktisk måling fra nærmeste værstasjon."""
    gust_txt = (
        f", kast {match.wind_speed_of_gust:.1f} m/s"
        if match.wind_speed_of_gust is not None
        else ""
    )
    upstream_note = (
        " — ligger oppstrøms, været er på vei mot dere"
        if match.is_upstream and match.distance_km > 10
        else ""
    )
    title = (
        f"OBS: østlig vind målt {match.wind_speed:.1f} m/s "
        f"ved {match.station_name}"
    )
    body = (
        f"Stasjon {match.station_name} "
        f"({match.distance_km:.1f} km {_compass(match.bearing_deg)} for {location_name}"
        f"{upstream_note})\n"
        f"Måletid {_fmt_local(match.time)}:\n"
        f"  Retning {match.wind_from_direction:.0f}° "
        f"({_compass(match.wind_from_direction)}), "
        f"vind {match.wind_speed:.1f} m/s{gust_txt}"
    )
    _send(title, body, priority="high", tags="wind_face,warning,satellite")


def send_forecast_alert(location_name: str, event: Event) -> None:
    lo, hi = event.dir_range
    peak_gust = event.peak_gust
    gust_txt = f", kast opp til {peak_gust:.1f} m/s" if peak_gust is not None else ""
    title = f"Heads-up: østlig vind {location_name}"
    body = (
        f"Prognose {_fmt_local(event.start)} – {_fmt_local(event.end)} "
        f"(varighet {len(event.hours)} t).\n"
        f"Retning {lo:.0f}–{hi:.0f}° ({_compass((lo + hi) / 2)}), "
        f"topp vind {event.peak_speed:.1f} m/s{gust_txt}."
    )
    _send(title, body, priority="default", tags="wind_face,calendar")


# ---------------------------------------------------------------------------
# Kombinert varsel
# ---------------------------------------------------------------------------

def _fmt_time_short(t: datetime) -> str:
    """Berre klokkeslett i lokal tid."""
    return t.astimezone(OSLO_TZ).strftime("%H:%M")


def _format_now_line(item: AlertItem) -> str:
    hour: MatchedHour = item.data  # type: ignore[assignment]
    gust_txt = (
        f", kast {hour.wind_speed_of_gust:.1f} m/s"
        if hour.wind_speed_of_gust is not None
        else ""
    )
    return (
        f"{item.location_name} NÅ ({item.source}): "
        f"{hour.wind_speed:.1f} m/s frå {_compass(hour.wind_from_direction)} "
        f"({hour.wind_from_direction:.0f}°){gust_txt}"
    )


def _format_observed_line(item: AlertItem) -> str:
    match: ObservedMatch = item.data  # type: ignore[assignment]
    gust_txt = (
        f", kast {match.wind_speed_of_gust:.1f} m/s"
        if match.wind_speed_of_gust is not None
        else ""
    )
    dist_txt = f" {match.distance_km:.1f} km" if match.distance_km > 0.5 else ""
    return (
        f"{item.location_name} ({item.source}{dist_txt}): "
        f"{match.wind_speed:.1f} m/s frå {_compass(match.wind_from_direction)} "
        f"({match.wind_from_direction:.0f}°){gust_txt} "
        f"kl {_fmt_time_short(match.time)}"
    )


def _format_forecast_line(item: AlertItem) -> str:
    event: Event = item.data  # type: ignore[assignment]
    lo, hi = event.dir_range
    gust_txt = (
        f", kast {event.peak_gust:.1f} m/s"
        if event.peak_gust is not None
        else ""
    )
    end_time = _fmt_time_short(event.end)
    return (
        f"{item.location_name} (prognose {item.source}): "
        f"{_fmt_local(event.start)}–{end_time}, "
        f"topp {event.peak_speed:.1f} m/s frå {_compass((lo + hi) / 2)}{gust_txt}"
    )


def send_combined_alert(alerts: list[AlertItem]) -> None:
    """Send éi kombinert ntfy-melding som samlar alle ventande varslar.

    Rekkjefølgje: nå-data og observasjonar fyrst (høg prioritet), deretter
    prognosedata. Dersom ei lokasjon har både nå-match og prognose med
    høgare toppfart, vert det flagga som aukande vind.
    """
    if not alerts:
        return

    high_prio = [a for a in alerts if a.alert_type in ("now", "observed")]
    low_prio = [a for a in alerts if a.alert_type == "forecast"]

    # Finn lokasjonar med aukande vind (nå-match + prognose med høgare toppfart)
    now_speeds: dict[str, float] = {}
    for a in high_prio:
        if a.alert_type == "now":
            hour: MatchedHour = a.data  # type: ignore[assignment]
            now_speeds[a.location_name] = hour.wind_speed
    increasing_locs: set[str] = set()
    for a in low_prio:
        event: Event = a.data  # type: ignore[assignment]
        if a.location_name in now_speeds and event.peak_speed > now_speeds[a.location_name]:
            increasing_locs.add(a.location_name)

    # Tittel og prioritet basert på om det er aktiv vind no
    unique_locs = list(dict.fromkeys(a.location_name for a in alerts))
    if high_prio:
        active_locs = list(dict.fromkeys(a.location_name for a in high_prio))
        title = f"Østlig vind NÅ — {', '.join(active_locs)}"
        priority = "high"
        tags = "wind_face,warning"
    else:
        title = f"Heads-up: østlig vind — {', '.join(unique_locs)}"
        priority = "default"
        tags = "wind_face,calendar"

    lines: list[str] = []
    for a in high_prio:
        if a.alert_type == "now":
            line = _format_now_line(a)
        else:
            line = _format_observed_line(a)
        if a.location_name in increasing_locs:
            line += " ↑ aukande"
        lines.append(line)
    for a in low_prio:
        lines.append(_format_forecast_line(a))

    _send(title, "\n".join(lines), priority=priority, tags=tags)
