"""Session de scan : httpx d'abord, repli navigateur headless si Cloudflare bloque."""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from .config import Config
from .fetcher import ChallengeError, FetchError, get as http_get, make_client

log = logging.getLogger(__name__)


class Session:
    """Aiguille chaque requête vers le bon transport.

    - `browser_fallback = "never"`  : httpx uniquement ; un challenge = une erreur.
    - `browser_fallback = "auto"`   : httpx, et bascule sur Chromium pour un domaine
                                      dès qu'il renvoie un challenge (défaut).
    - `browser_fallback = "always"` : Chromium pour tout.
    """

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._client = None
        self._browser = None
        self._browser_hosts: set[str] = set()
        self._browser_failed: str | None = None

    def __enter__(self) -> "Session":
        if self._cfg.browser_fallback != "always":
            self._client = make_client(self._cfg)
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._client is not None:
            self._client.close()
        if self._browser is not None:
            self._browser.close()

    # ------------------------------------------------------------------
    def _get_browser(self):
        if self._browser_failed:
            raise ChallengeError(
                f"repli navigateur indisponible ({self._browser_failed})"
            )
        if self._browser is None:
            from .browser import BrowserFetcher, BrowserUnavailable

            try:
                self._browser = BrowserFetcher(self._cfg)
                self._browser.start()
            except BrowserUnavailable as exc:
                self._browser = None
                self._browser_failed = str(exc)
                raise ChallengeError(
                    f"challenge Cloudflare et repli navigateur indisponible : {exc}"
                ) from exc
        return self._browser

    def get(self, url: str):
        host = urlsplit(url).netloc

        if self._cfg.browser_fallback == "always" or host in self._browser_hosts:
            return self._get_browser().get(url)

        try:
            return http_get(self._client, url)
        except ChallengeError as exc:
            if self._cfg.browser_fallback != "auto":
                raise
            log.info("%s : challenge détecté, bascule sur le navigateur headless", host)
            browser = self._get_browser()   # peut relever ChallengeError
            self._browser_hosts.add(host)
            return browser.get(url)
