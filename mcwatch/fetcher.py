"""Client HTTP « façon navigateur » + détection des challenges Cloudflare."""

from __future__ import annotations

import httpx

from .config import Config


class FetchError(RuntimeError):
    """Échec réseau ou réponse inexploitable."""


class ChallengeError(FetchError):
    """Cloudflare (ou équivalent) nous demande de résoudre un challenge navigateur."""


def build_headers(cfg: Config) -> dict[str, str]:
    return {
        "User-Agent": cfg.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
                  "image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }


def make_client(cfg: Config) -> httpx.Client:
    try:
        return httpx.Client(
            headers=build_headers(cfg),
            timeout=cfg.timeout,
            follow_redirects=True,
            http2=True,
        )
    except ImportError:
        # le paquet h2 n'est pas installé : on retombe en HTTP/1.1
        return httpx.Client(
            headers=build_headers(cfg),
            timeout=cfg.timeout,
            follow_redirects=True,
        )


_CHALLENGE_MARKERS = (
    "just a moment",
    "cf-browser-verification",
    "challenges.cloudflare.com",
    "enable javascript and cookies to continue",
)


def _looks_like_challenge(resp: httpx.Response) -> bool:
    if resp.headers.get("cf-mitigated") == "challenge":
        return True
    if resp.status_code not in (403, 429, 503):
        return False
    ctype = resp.headers.get("content-type", "")
    if "html" not in ctype:
        return False
    body = resp.text[:4000].lower()
    return any(marker in body for marker in _CHALLENGE_MARKERS)


def get(client: httpx.Client, url: str) -> httpx.Response:
    """GET avec traduction des erreurs en exceptions maison."""
    try:
        resp = client.get(url)
    except httpx.HTTPError as exc:
        raise FetchError(f"{type(exc).__name__}: {exc}") from exc

    if _looks_like_challenge(resp):
        raise ChallengeError(
            f"challenge anti-bot (HTTP {resp.status_code}) sur {url}"
        )
    if resp.status_code >= 500:
        raise FetchError(f"HTTP {resp.status_code} sur {url}")
    return resp
