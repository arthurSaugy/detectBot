"""Persistance de l'état : dernier ID connu par site, compteurs, IDs déjà notifiés."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

STATE_VERSION = 1
MAX_NOTIFIED = 200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class SiteState:
    cursor: int = 0               # dernier ID contigu confirmé existant
    gap_strikes: int = 0          # passages consécutifs bloqués par un trou
    consecutive_errors: int = 0   # passages consécutifs en erreur (challenge, réseau)
    notified_ids: list[int] = field(default_factory=list)
    last_success: str | None = None
    last_error: str | None = None

    def remember(self, server_id: int) -> None:
        if server_id not in self.notified_ids:
            self.notified_ids.append(server_id)
            del self.notified_ids[:-MAX_NOTIFIED]


@dataclass
class State:
    version: int = STATE_VERSION
    sites: dict[str, SiteState] = field(default_factory=dict)

    def for_site(self, key: str) -> SiteState:
        return self.sites.setdefault(key, SiteState())


def load(path: Path) -> State:
    if not path.is_file():
        return State()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(f"state.json illisible ({path}): {exc}") from exc
    sites = {k: SiteState(**v) for k, v in (raw.get("sites") or {}).items()}
    return State(version=raw.get("version", STATE_VERSION), sites=sites)


def save(path: Path, state: State) -> None:
    """Écriture atomique : on n'abîme jamais un state.json existant."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": state.version,
        "saved_at": _now(),
        "sites": {k: asdict(v) for k, v in state.sites.items()},
    }
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".state-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def mark_success(st: SiteState) -> None:
    st.consecutive_errors = 0
    st.last_error = None
    st.last_success = _now()


def mark_error(st: SiteState, message: str) -> None:
    st.consecutive_errors += 1
    st.last_error = f"{_now()} — {message}"
