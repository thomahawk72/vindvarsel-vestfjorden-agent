"""Push-varsling via ntfy.sh."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

from .config import NTFY_SERVER
from .evaluator import Event, MatchedHour, ObservedMatch
from .http_retry import request_with_retries

log = logging.getLogger(__name__)

OSLO_TZ = ZoneInfo("Europe/Oslo")


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
