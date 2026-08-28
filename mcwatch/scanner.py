"""Cœur de la logique : sonder la fenêtre d'IDs, avancer le curseur, notifier."""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass

from . import notify, state as state_mod
from .config import Config
from .fetcher import ChallengeError, FetchError
from .session import Session
from .sites import Site, selected

log = logging.getLogger(__name__)


@dataclass
class Found:
    site: Site
    server_id: int
    name: str
    url: str


@dataclass
class SiteReport:
    site: Site
    probed: dict[int, bool]
    found: list[Found]
    new: list[Found]
    cursor_before: int
    cursor_after: int
    error: str | None = None


def _sleep(cfg: Config) -> None:
    if cfg.delay_max > 0:
        time.sleep(random.uniform(cfg.delay_min, cfg.delay_max))


def scan_site(
    cfg: Config,
    session: Session,
    site: Site,
    st: state_mod.SiteState,
    *,
    dry_run: bool = False,
) -> SiteReport:
    cursor_before = st.cursor
    probed: dict[int, bool] = {}
    found: list[Found] = []
    error: str | None = None

    for offset in range(1, cfg.window + 1):
        server_id = cursor_before + offset
        url = site.url(server_id)
        try:
            resp = session.get(url)
        except ChallengeError as exc:
            error = str(exc)
            log.warning("[%s] %s", site.label, exc)
            break
        except FetchError as exc:
            error = str(exc)
            log.warning("[%s] id=%s : %s", site.label, server_id, exc)
            break

        exists = site.exists(resp)
        probed[server_id] = exists
        log.info(
            "[%s] id=%s -> %s (HTTP %s)",
            site.label, server_id, "EXISTE" if exists else "absent", resp.status_code,
        )
        if exists:
            found.append(Found(site, server_id, site.server_name(resp.text), str(resp.url)))

        if offset < cfg.window:
            _sleep(cfg)

    cursor_after = cursor_before

    if error is not None and not probed:
        # Rien n'a pu être sondé : on ne touche à rien.
        state_mod.mark_error(st, error)
        return SiteReport(site, probed, found, [], cursor_before, cursor_after, error)

    # 1) On avance tant que les IDs sont contigus.
    while probed.get(cursor_after + 1) is True:
        cursor_after += 1

    # 2) Gestion des trous (serveur supprimé -> ID définitivement manquant).
    if cursor_after == cursor_before and any(probed.values()):
        st.gap_strikes += 1
        if st.gap_strikes >= cfg.gap_skip_after_runs:
            jump = max(i for i, ok in probed.items() if ok)
            log.warning(
                "[%s] trou persistant depuis %s passages : le curseur saute de %s à %s",
                site.label, st.gap_strikes, cursor_after, jump,
            )
            cursor_after = jump
            st.gap_strikes = 0
    else:
        st.gap_strikes = 0

    new = [f for f in found if f.server_id not in st.notified_ids]

    if not dry_run:
        st.cursor = cursor_after
        for f in new:
            st.remember(f.server_id)
        if error is None:
            state_mod.mark_success(st)
        else:
            state_mod.mark_error(st, error)

    return SiteReport(site, probed, found, new, cursor_before, cursor_after, error)


def _notify_new(cfg: Config, f: Found) -> None:
    notify.send(
        cfg,
        title=f"Nouveau serveur — {f.site.label}",
        message=f"#{f.server_id} · {f.name}\n{f.url}",
        click=f.url,
        tags=["video_game", "new"],
    )


def _notify_error(cfg: Config, site: Site, st: state_mod.SiteState, message: str) -> None:
    notify.send(
        cfg,
        title=f"mcwatch en échec — {site.label}",
        message=(
            f"{message}\n"
            f"{st.consecutive_errors} passage(s) consécutif(s) en erreur. "
            f"Le curseur reste à #{st.cursor}."
        ),
        tags=["warning"],
        priority=3,
    )


def run(cfg: Config, *, dry_run: bool = False, notify_errors: bool = True) -> list[SiteReport]:
    cfg.validate()
    st_all = state_mod.load(cfg.state_path)
    sites = selected(cfg.sites)
    reports: list[SiteReport] = []

    with Session(cfg) as session:
        for i, site in enumerate(sites):
            st = st_all.for_site(site.key)
            if st.cursor <= 0:
                log.error(
                    "[%s] curseur non initialisé. Lance : "
                    "python -m mcwatch set-cursor %s <dernier_id_connu>",
                    site.label, site.key,
                )
                reports.append(
                    SiteReport(site, {}, [], [], 0, 0, "curseur non initialisé")
                )
                continue

            report = scan_site(cfg, session, site, st, dry_run=dry_run)
            reports.append(report)

            if not dry_run:
                for f in report.new:
                    try:
                        _notify_new(cfg, f)
                    except notify.NotifyError as exc:
                        log.error("notification échouée pour #%s : %s", f.server_id, exc)

                if report.error and notify_errors:
                    # 1re erreur, puis rappel toutes les 24 erreurs consécutives.
                    if st.consecutive_errors == 1 or st.consecutive_errors % 24 == 0:
                        try:
                            _notify_error(cfg, site, st, report.error)
                        except notify.NotifyError as exc:
                            log.error("alerte d'erreur non envoyée : %s", exc)

            if i < len(sites) - 1:
                _sleep(cfg)

    if not dry_run:
        state_mod.save(cfg.state_path, st_all)

    return reports
