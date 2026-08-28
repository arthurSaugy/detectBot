"""Envoi des notifications via ntfy (https://ntfy.sh)."""

from __future__ import annotations

import json
import logging

import httpx

from .config import Config

log = logging.getLogger(__name__)


class NotifyError(RuntimeError):
    pass


def send(
    cfg: Config,
    *,
    title: str,
    message: str,
    click: str | None = None,
    tags: list[str] | None = None,
    priority: int | None = None,
) -> None:
    """Publie sur ntfy via l'endpoint JSON (gère correctement l'UTF-8)."""
    payload: dict[str, object] = {
        "topic": cfg.ntfy_topic,
        "title": title,
        "message": message,
        "priority": priority if priority is not None else cfg.ntfy_priority,
    }
    if click:
        payload["click"] = click
    if tags:
        payload["tags"] = tags

    headers = {"Content-Type": "application/json; charset=utf-8"}
    if cfg.ntfy_token:
        headers["Authorization"] = f"Bearer {cfg.ntfy_token}"

    try:
        resp = httpx.post(
            cfg.ntfy_server + "/",
            content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            timeout=cfg.timeout,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise NotifyError(f"envoi ntfy impossible : {exc}") from exc

    log.info("notification envoyée : %s", title)
