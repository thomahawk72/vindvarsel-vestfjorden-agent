"""Små HTTP-retries for eksterne API-kall."""
from __future__ import annotations

import logging
import time
from typing import Any

import requests

log = logging.getLogger(__name__)

RETRY_STATUS_CODES = {429, 500, 502, 503, 504}


def request_with_retries(
    session: requests.Session,
    method: str,
    url: str,
    *,
    attempts: int = 3,
    backoff_seconds: tuple[float, ...] = (2.0, 5.0),
    log_url: str | None = None,
    **kwargs: Any,
) -> requests.Response:
    """Kjør HTTP-kall med retry på midlertidige feil.

    Permanente klientfeil (andre 4xx enn 429) returneres uten retry slik at
    kalleren kan bruke vanlig `raise_for_status()` og få riktig feilmelding.
    """
    last_exc: requests.RequestException | None = None
    display_url = log_url or url
    for attempt in range(1, attempts + 1):
        try:
            resp = session.request(method, url, **kwargs)
        except requests.RequestException as exc:
            last_exc = exc
            if attempt >= attempts:
                log.error(
                    "HTTP %s %s feilet etter %d forsøk: %s",
                    method,
                    display_url,
                    attempt,
                    exc,
                )
                raise
            _sleep_before_retry(method, display_url, attempt, backoff_seconds, error=str(exc))
            continue

        if resp.status_code not in RETRY_STATUS_CODES or attempt >= attempts:
            if resp.status_code in RETRY_STATUS_CODES:
                log.error(
                    "HTTP %s %s ga %s etter %d forsøk: %s",
                    method,
                    display_url,
                    resp.status_code,
                    attempt,
                    _short_response_text(resp),
                )
            return resp

        _sleep_before_retry(
            method,
            display_url,
            attempt,
            backoff_seconds,
            status_code=resp.status_code,
            response_text=_short_response_text(resp),
        )

    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"HTTP {method} {url} feilet uten respons")


def _sleep_before_retry(
    method: str,
    url: str,
    attempt: int,
    backoff_seconds: tuple[float, ...],
    *,
    status_code: int | None = None,
    response_text: str | None = None,
    error: str | None = None,
) -> None:
    delay = backoff_seconds[min(attempt - 1, len(backoff_seconds) - 1)]
    detail = f"status {status_code}: {response_text}" if status_code else error
    log.warning(
        "HTTP %s %s feilet på forsøk %d (%s). Prøver igjen om %.0f s.",
        method,
        url,
        attempt,
        detail,
        delay,
    )
    time.sleep(delay)


def _short_response_text(resp: requests.Response) -> str:
    text = resp.text.strip().replace("\n", " ")
    return text[:300] if text else "<tom respons>"
