"""Interface en ligne de commande de mcwatch."""

from __future__ import annotations

import argparse
import logging
import sys

from . import notify, scanner, state as state_mod
from .config import Config, ConfigError, load as load_config
from .fetcher import ChallengeError, FetchError
from .session import Session
from .sites import BY_KEY, SITES


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


# --------------------------------------------------------------------------- run
def cmd_run(cfg: Config, args: argparse.Namespace) -> int:
    reports = scanner.run(cfg, dry_run=args.dry_run)

    total_new = sum(len(r.new) for r in reports)
    failed = [r for r in reports if r.error]

    for r in reports:
        if r.error:
            print(f"[!] {r.site.label} : {r.error}")
        else:
            print(
                f"[ok] {r.site.label} : curseur {r.cursor_before} -> {r.cursor_after}, "
                f"{len(r.found)} trouvé(s), {len(r.new)} nouveau(x)"
            )
        for f in r.new:
            print(f"     + #{f.server_id} {f.name} — {f.url}")

    if args.dry_run:
        print("\n(dry-run : ni état sauvegardé, ni notification envoyée)")

    if failed and len(failed) == len(reports):
        return 1
    return 0


# ------------------------------------------------------------------------ status
def cmd_status(cfg: Config, args: argparse.Namespace) -> int:
    st_all = state_mod.load(cfg.state_path)
    print(f"état      : {cfg.state_path}")
    print(f"ntfy      : {cfg.ntfy_server}/{cfg.ntfy_topic or '(non configuré)'}")
    print(f"fenêtre   : {cfg.window} ID(s) après le curseur\n")
    for site in SITES:
        st = st_all.sites.get(site.key)
        if st is None:
            print(f"  {site.label:<28} curseur non initialisé")
            continue
        flag = "" if st.consecutive_errors == 0 else f"  [!] {st.consecutive_errors} erreur(s)"
        print(f"  {site.label:<28} curseur #{st.cursor}{flag}")
        print(f"  {'':<28} dernier succès : {st.last_success or '—'}")
        if st.last_error:
            print(f"  {'':<28} dernière erreur : {st.last_error}")
        if st.gap_strikes:
            print(f"  {'':<28} trou en cours : {st.gap_strikes} passage(s)")
    return 0


# -------------------------------------------------------------------- set-cursor
def cmd_set_cursor(cfg: Config, args: argparse.Namespace) -> int:
    if args.site not in BY_KEY:
        print(f"Site inconnu : {args.site}. Connus : {', '.join(BY_KEY)}", file=sys.stderr)
        return 2
    st_all = state_mod.load(cfg.state_path)
    st = st_all.for_site(args.site)
    old = st.cursor
    st.cursor = args.server_id
    st.gap_strikes = 0
    state_mod.save(cfg.state_path, st_all)
    print(f"{BY_KEY[args.site].label} : curseur {old} -> {args.server_id}")
    return 0


# --------------------------------------------------------------------------- init
DEFAULT_CURSORS = {
    "serveursminecraft_org": 7719,
    "serveur_minecraft_com": 5907,
    "serveur_minecraft_vote_fr": 2787,
}


def cmd_init(cfg: Config, args: argparse.Namespace) -> int:
    st_all = state_mod.load(cfg.state_path)
    for key, value in DEFAULT_CURSORS.items():
        st = st_all.for_site(key)
        if st.cursor and not args.force:
            print(f"  {BY_KEY[key].label:<28} déjà à #{st.cursor} (--force pour écraser)")
            continue
        st.cursor = value
        print(f"  {BY_KEY[key].label:<28} initialisé à #{value}")
    state_mod.save(cfg.state_path, st_all)
    print(f"\nÉtat écrit dans {cfg.state_path}")
    return 0


# -------------------------------------------------------------------- test-notify
def cmd_test_notify(cfg: Config, args: argparse.Namespace) -> int:
    cfg.validate()
    try:
        notify.send(
            cfg,
            title="mcwatch — test",
            message="Si tu lis ceci, les notifications fonctionnent.",
            tags=["white_check_mark"],
            priority=3,
        )
    except notify.NotifyError as exc:
        print(f"Échec : {exc}", file=sys.stderr)
        return 1
    print(f"Notification envoyée sur {cfg.ntfy_server}/{cfg.ntfy_topic}")
    return 0


# ----------------------------------------------------------------------- selftest
SELFTEST_CASES = [
    # (site key, id, doit exister)
    ("serveursminecraft_org", 7719, True),
    ("serveursminecraft_org", 9999999, False),
    ("serveur_minecraft_com", 5907, True),
    ("serveur_minecraft_com", 9999999, False),
    ("serveur_minecraft_vote_fr", 2787, True),
    ("serveur_minecraft_vote_fr", 9999999, False),
]


def cmd_selftest(cfg: Config, args: argparse.Namespace) -> int:
    """Vérifie la détection contre des IDs dont on connaît la réponse."""
    ok = True
    with Session(cfg) as session:
        for key, server_id, expected in SELFTEST_CASES:
            site = BY_KEY[key]
            url = site.url(server_id)
            try:
                resp = session.get(url)
            except ChallengeError as exc:
                print(f"  CHALLENGE  {site.label:<26} #{server_id} — {exc}")
                ok = False
                continue
            except FetchError as exc:
                print(f"  ERREUR     {site.label:<26} #{server_id} — {exc}")
                ok = False
                continue
            actual = site.exists(resp)
            name = site.server_name(resp.text) if actual else ""
            verdict = "OK  " if actual == expected else "ÉCHEC"
            if actual != expected:
                ok = False
            print(
                f"  {verdict}       {site.label:<26} #{server_id} "
                f"attendu={'existe' if expected else 'absent'} "
                f"obtenu={'existe' if actual else 'absent'} "
                f"(HTTP {resp.status_code}) {name}"
            )
    print()
    if ok:
        print("Selftest réussi : la détection est correcte sur les 3 sites.")
        return 0
    print("Selftest en échec — voir les lignes ci-dessus.", file=sys.stderr)
    return 1


# --------------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mcwatch",
        description="Détecte l'apparition de nouveaux serveurs sur 3 annuaires Minecraft.",
    )
    p.add_argument("-c", "--config", help="chemin du config.toml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="un passage de scan (commande du timer systemd)")
    r.add_argument("--dry-run", action="store_true",
                   help="ne rien sauvegarder ni notifier")
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("status", help="afficher l'état courant")
    s.set_defaults(func=cmd_status)

    i = sub.add_parser("init", help="initialiser les curseurs aux derniers IDs connus")
    i.add_argument("--force", action="store_true", help="écraser les curseurs existants")
    i.set_defaults(func=cmd_init)

    sc = sub.add_parser("set-cursor", help="fixer manuellement le curseur d'un site")
    sc.add_argument("site", choices=list(BY_KEY))
    sc.add_argument("server_id", type=int)
    sc.set_defaults(func=cmd_set_cursor)

    tn = sub.add_parser("test-notify", help="envoyer une notification de test")
    tn.set_defaults(func=cmd_test_notify)

    stt = sub.add_parser("selftest", help="vérifier la détection sur des IDs connus")
    stt.set_defaults(func=cmd_selftest)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"Configuration : {exc}", file=sys.stderr)
        return 2
    try:
        return args.func(cfg, args)
    except ConfigError as exc:
        print(f"Configuration : {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
