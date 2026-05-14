"""Henter ferske vinddata fra Cerbo GX / Signal K via MQTT."""
from __future__ import annotations

import json
import logging
import math
import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import paho.mqtt.client as mqtt
from dateutil.parser import isoparse

from .config import (
    CERBO_MAGNETIC_DECLINATION_DEG,
    CERBO_MQTT_FRESH_MAX_MIN,
    CERBO_MQTT_PASSWORD,
    CERBO_MQTT_TIMEOUT_SEC,
    CERBO_MQTT_TOPIC_FILTER,
    CERBO_MQTT_URL,
    CERBO_MQTT_USERNAME,
    CERBO_SOURCE_NAME,
)
from .frost_client import Observation, Station

log = logging.getLogger(__name__)

PATH_SPEED_APPARENT = "environment.wind.speedApparent"
PATH_ANGLE_APPARENT = "environment.wind.angleApparent"
PATH_HEADING_MAGNETIC = "navigation.headingMagnetic"
REQUIRED_PATHS = {
    PATH_SPEED_APPARENT,
    PATH_ANGLE_APPARENT,
    PATH_HEADING_MAGNETIC,
}


class CerboNotConfigured(RuntimeError):
    """Kastes når Cerbo MQTT ikke er konfigurert."""


@dataclass
class _PathValue:
    value: float
    timestamp: datetime


def fetch_cerbo_observation() -> Observation | None:
    """Returner én syntetisk Frost-lignende observasjon fra Cerbo MQTT-data.

    Signal K sender `speedApparent`, `angleApparent` og `headingMagnetic` i SI:
    m/s og radianer. Vi konverterer apparent vind til kompassretning fra der
    vinden kommer, slik eksisterende varslingslogikk kan gjenbrukes.
    """
    if not CERBO_MQTT_URL:
        raise CerboNotConfigured("CERBO_MQTT_URL mangler.")

    values = _read_signal_k_values()
    missing = REQUIRED_PATHS - values.keys()
    if missing:
        log.info("Cerbo MQTT mangler path(s): %s", ", ".join(sorted(missing)))
        return None

    newest_time = max(v.timestamp for v in values.values())
    oldest_time = min(v.timestamp for v in values.values())
    now = datetime.now(timezone.utc)
    max_age = timedelta(minutes=CERBO_MQTT_FRESH_MAX_MIN)
    if now - newest_time > max_age:
        log.info("Cerbo MQTT-data er for gammel: %s", newest_time.isoformat())
        return None

    speed = values[PATH_SPEED_APPARENT].value
    angle_deg = math.degrees(values[PATH_ANGLE_APPARENT].value)
    heading_deg = math.degrees(values[PATH_HEADING_MAGNETIC].value)
    wind_from_direction = (
        heading_deg + angle_deg + CERBO_MAGNETIC_DECLINATION_DEG
    ) % 360.0

    observation_hour = oldest_time.replace(minute=0, second=0, microsecond=0)
    return Observation(
        station=Station(
            id="cerbo-gx",
            name=CERBO_SOURCE_NAME,
            lat=0.0,
            lon=0.0,
            distance_km=0.0,
            bearing_deg=wind_from_direction,
        ),
        time=observation_hour,
        wind_from_direction=wind_from_direction,
        wind_speed=speed,
        wind_speed_of_gust=None,
    )


def _read_signal_k_values() -> dict[str, _PathValue]:
    parsed_url = urlparse(CERBO_MQTT_URL)
    if parsed_url.scheme not in {"mqtt", "mqtts"}:
        raise ValueError("CERBO_MQTT_URL må starte med mqtt:// eller mqtts://")
    if not parsed_url.hostname:
        raise ValueError("CERBO_MQTT_URL mangler host.")

    port = parsed_url.port or (8883 if parsed_url.scheme == "mqtts" else 1883)
    client = _make_client()
    values: dict[str, _PathValue] = {}
    connected = False

    def on_connect(
        client: mqtt.Client,
        _userdata: Any,
        _flags: Any,
        reason_code: int,
        _properties: Any = None,
    ) -> None:
        nonlocal connected
        code = getattr(reason_code, "value", reason_code)
        if code != 0:
            log.warning("MQTT-tilkobling feilet med kode %s", reason_code)
            return
        connected = True
        client.subscribe(CERBO_MQTT_TOPIC_FILTER)

    def on_message(_client: mqtt.Client, _userdata: Any, msg: mqtt.MQTTMessage) -> None:
        for path, path_value in _extract_values(msg.topic, msg.payload):
            if path in REQUIRED_PATHS:
                values[path] = path_value

    client.on_connect = on_connect
    client.on_message = on_message
    if CERBO_MQTT_USERNAME:
        client.username_pw_set(CERBO_MQTT_USERNAME, CERBO_MQTT_PASSWORD or None)
    if parsed_url.scheme == "mqtts":
        client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
        client.tls_insecure_set(False)

    try:
        client.connect(parsed_url.hostname, port, keepalive=30)
        client.loop_start()
        deadline = time.monotonic() + CERBO_MQTT_TIMEOUT_SEC
        while time.monotonic() < deadline:
            if connected and REQUIRED_PATHS.issubset(values):
                break
            time.sleep(0.1)
    finally:
        client.loop_stop()
        client.disconnect()

    return values


def _make_client() -> mqtt.Client:
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    except AttributeError:
        return mqtt.Client()


def _extract_values(topic: str, payload: bytes) -> list[tuple[str, _PathValue]]:
    now = datetime.now(timezone.utc)
    text = payload.decode("utf-8", errors="replace").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        path = _path_from_topic(topic)
        return [] if path is None else [(path, _PathValue(float(text), now))]

    extracted: list[tuple[str, _PathValue]] = []
    if isinstance(data, int | float):
        path = _path_from_topic(topic)
        if path is not None:
            extracted.append((path, _PathValue(float(data), now)))
        return extracted

    if not isinstance(data, dict):
        return extracted

    # Signal K delta format: {"updates": [{"timestamp": "...", "values": [...]}]}
    for update in data.get("updates", []):
        if not isinstance(update, dict):
            continue
        ts = _parse_time(update.get("timestamp"), now)
        for item in update.get("values", []):
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            value = item.get("value")
            if isinstance(path, str) and isinstance(value, int | float):
                extracted.append((path, _PathValue(float(value), ts)))

    # Enklere plugin-format: {"path": "...", "value": 1.23, "timestamp": "..."}
    path = data.get("path") or _path_from_topic(topic)
    value = data.get("value")
    if isinstance(path, str) and isinstance(value, int | float):
        extracted.append(
            (
                path,
                _PathValue(float(value), _parse_time(data.get("timestamp"), now)),
            )
        )

    return extracted


def _path_from_topic(topic: str) -> str | None:
    normalized = topic.strip("/").replace("/", ".")
    for path in REQUIRED_PATHS:
        if normalized == path or normalized.endswith(f".{path}"):
            return path
    return None


def _parse_time(raw: Any, default: datetime) -> datetime:
    if not isinstance(raw, str) or not raw:
        return default
    try:
        return isoparse(raw).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return default
