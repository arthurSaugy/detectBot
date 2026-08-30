"""Repli navigateur : Chromium headless via Playwright, pour passer Cloudflare.

Deux des trois sites (serveur-minecraft.com surtout) servent un « managed
challenge » Cloudflare qu'aucun client HTTP ne peut résoudre, même avec une
empreinte TLS de navigateur (curl_cffi échoue aussi). Seul un vrai moteur de
rendu exécutant le JavaScript du challenge y arrive.

Playwright est une dépendance *optionnelle* : si elle est absente, mcwatch
continue de fonctionner en HTTP pur et signale simplement les sites bloqués.

    pip install playwright && playwright install chromium
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from urllib.parse import urlsplit

from .config import ROOT, Config
from .fetcher import ChallengeError, FetchError

log = logging.getLogger(__name__)

# Chromium est installé dans le dossier applicatif plutôt que dans ~/.cache :
# le service systemd tourne avec ProtectHome=read-only, et un chemin local
# reste valable quel que soit l'utilisateur qui lance la commande.
BUNDLED_BROWSERS = ROOT / ".playwright"

_CHALLENGE_TITLES = ("just a moment", "un instant", "attendez")


class BrowserUnavailable(RuntimeError):
    """Playwright ou Chromium n'est pas installé."""


@dataclass
class BrowserResponse:
    """Imite juste ce que `Site.exists` attend d'une réponse httpx."""

    status_code: int
    url: str
    text: str


class BrowserFetcher:
    """Chromium headless, démarré à la demande et réutilisé pour tout le passage."""

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._pw = None
        self._browser = None
        self._page = None
        self._primed: set[str] = set()

    # -- cycle de vie ------------------------------------------------------
    def start(self) -> None:
        if self._page is not None:
            return
        if BUNDLED_BROWSERS.is_dir():
            os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(BUNDLED_BROWSERS))

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise BrowserUnavailable(
                "playwright n'est pas installé. "
                "pip install playwright && playwright install chromium"
            ) from exc

        try:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            context = self._browser.new_context(
                user_agent=self._cfg.user_agent,
                locale="fr-FR",
                timezone_id="Europe/Zurich",
                viewport={"width": 1366, "height": 768},
            )
            # Masque le marqueur `navigator.webdriver`.
            context.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            )
            self._page = context.new_page()
        except Exception as exc:  # pragma: no cover
            self.close()
            raise BrowserUnavailable(f"Chromium n'a pas pu démarrer : {exc}") from exc

        log.info("navigateur headless démarré (repli Cloudflare)")

    def close(self) -> None:
        for obj, name in ((self._browser, "browser"), (self._pw, "playwright")):
            if obj is None:
                continue
            try:
                obj.close() if name == "browser" else obj.stop()
            except Exception:  # pragma: no cover
                pass
        self._pw = self._browser = self._page = None

    def __enter__(self) -> "BrowserFetcher":
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- navigation --------------------------------------------------------
    def _wait_for_clearance(self, timeout_ms: int) -> None:
        """Attend que la page interstitielle Cloudflare disparaisse."""
        deadline = timeout_ms
        step = 500
        while deadline > 0:
            title = (self._page.title() or "").strip().lower()
            if not any(t in title for t in _CHALLENGE_TITLES):
                return
            self._page.wait_for_timeout(step)
            deadline -= step
        raise ChallengeError("le challenge Cloudflare n'a pas été résolu à temps")

    def _prime(self, url: str) -> None:
        """Première visite d'un domaine : on obtient le cookie cf_clearance."""
        host = urlsplit(url).netloc
        if host in self._primed:
            return
        root = f"{urlsplit(url).scheme}://{host}/"
        try:
            self._page.goto(root, wait_until="domcontentloaded",
                            timeout=int(self._cfg.timeout * 1000))
            self._wait_for_clearance(int(self._cfg.browser_wait * 1000))
        except ChallengeError:
            raise
        except Exception as exc:
            raise FetchError(f"amorçage de {host} impossible : {exc}") from exc
        self._primed.add(host)
        log.info("cookie de clearance obtenu pour %s", host)

    def get(self, url: str) -> BrowserResponse:
        self.start()
        self._prime(url)
        try:
            resp = self._page.goto(
                url, wait_until="domcontentloaded",
                timeout=int(self._cfg.timeout * 1000),
            )
        except Exception as exc:
            raise FetchError(f"navigation vers {url} impossible : {exc}") from exc

        status = resp.status if resp is not None else 0
        if status in (403, 429, 503):
            # Challenge servi malgré le cookie : on attend puis on recharge.
            self._wait_for_clearance(int(self._cfg.browser_wait * 1000))
            resp = self._page.goto(url, wait_until="domcontentloaded",
                                   timeout=int(self._cfg.timeout * 1000))
            status = resp.status if resp is not None else 0

        return BrowserResponse(status_code=status, url=self._page.url,
                               text=self._page.content())
