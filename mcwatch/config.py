"""Chargement de la configuration (config.toml + surcharges par variables d'env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # Python >= 3.11
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.8/3.9/3.10
    import tomli as tomllib  # type: ignore[no-redef]

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class ConfigError(RuntimeError):
    pass


@dataclass
class Config:
    # --- notifications
    ntfy_server: str = "https://ntfy.sh"
    ntfy_topic: str = ""
    ntfy_token: str | None = None
    ntfy_priority: int = 4

    # --- scan
    window: int = 3
    sites: list[str] = field(default_factory=list)  # vide = tous
    gap_skip_after_runs: int = 6

    # --- réseau
    timeout: float = 20.0
    delay_min: float = 1.0
    delay_max: float = 3.0
    user_agent: str = DEFAULT_UA
    browser_fallback: str = "auto"   # auto | never | always
    browser_wait: float = 25.0       # patience max face au challenge Cloudflare

    # --- fichiers
    state_path: Path = ROOT / "state.json"

    def validate(self) -> None:
        if not self.ntfy_topic:
            raise ConfigError(
                "ntfy.topic est vide. Renseigne-le dans config.toml "
                "(ou via la variable d'environnement MCWATCH_NTFY_TOPIC)."
            )
        if self.window < 1:
            raise ConfigError("scan.window doit valoir au moins 1.")
        if self.browser_fallback not in ("auto", "never", "always"):
            raise ConfigError(
                "network.browser_fallback doit valoir 'auto', 'never' ou 'always'."
            )


def _config_path(explicit: str | os.PathLike[str] | None = None) -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get("MCWATCH_CONFIG")
    if env:
        return Path(env)
    return ROOT / "config.toml"


def load(explicit: str | os.PathLike[str] | None = None) -> Config:
    path = _config_path(explicit)
    raw: dict = {}
    if path.is_file():
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    elif explicit or os.environ.get("MCWATCH_CONFIG"):
        raise ConfigError(f"Fichier de configuration introuvable : {path}")

    ntfy = raw.get("ntfy", {})
    scan = raw.get("scan", {})
    net = raw.get("network", {})
    storage = raw.get("storage", {})

    cfg = Config(
        ntfy_server=str(ntfy.get("server", "https://ntfy.sh")).rstrip("/"),
        ntfy_topic=str(ntfy.get("topic", "")),
        ntfy_token=ntfy.get("token") or None,
        ntfy_priority=int(ntfy.get("priority", 4)),
        window=int(scan.get("window", 3)),
        sites=list(scan.get("sites", []) or []),
        gap_skip_after_runs=int(scan.get("gap_skip_after_runs", 6)),
        timeout=float(net.get("timeout", 20.0)),
        delay_min=float(net.get("delay_min", 1.0)),
        delay_max=float(net.get("delay_max", 3.0)),
        user_agent=str(net.get("user_agent", DEFAULT_UA)),
        browser_fallback=str(net.get("browser_fallback", "auto")).lower(),
        browser_wait=float(net.get("browser_wait", 25.0)),
        state_path=Path(storage.get("state_path", ROOT / "state.json")),
    )

    # Surcharges d'environnement (pratique pour systemd / Docker / secrets)
    if v := os.environ.get("MCWATCH_NTFY_SERVER"):
        cfg.ntfy_server = v.rstrip("/")
    if v := os.environ.get("MCWATCH_NTFY_TOPIC"):
        cfg.ntfy_topic = v
    if v := os.environ.get("MCWATCH_NTFY_TOKEN"):
        cfg.ntfy_token = v
    if v := os.environ.get("MCWATCH_STATE"):
        cfg.state_path = Path(v)
    if v := os.environ.get("MCWATCH_WINDOW"):
        cfg.window = int(v)
    if v := os.environ.get("MCWATCH_BROWSER_FALLBACK"):
        cfg.browser_fallback = v.lower()

    if not cfg.state_path.is_absolute():
        cfg.state_path = (path.parent if path.is_file() else ROOT) / cfg.state_path

    return cfg
