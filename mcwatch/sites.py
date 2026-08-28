"""Définition des 3 annuaires surveillés et de leur logique de détection.

Comportement observé le 2026-08-28 :

| Site                      | ID valide | ID jamais attribué | ID supprimé      |
|---------------------------|-----------|--------------------|------------------|
| serveursminecraft.org     | 200       | 302 vers "/"       | 302 vers "/"     |
| serveur-minecraft.com     | 200       | 404                | (supposé 404/"/")|
| serveur-minecraft-vote.fr | 200       | 404                | 301 vers "/"     |

Un simple `status == 200` ne suffit donc pas : serveur-minecraft-vote.fr renvoie
bien 200 pour un serveur supprimé, mais après redirection vers l'accueil (vu sur
les IDs 2788 et 2789). La règle appliquée aux trois sites est la même :

    le serveur existe  <=>  HTTP 200  ET  l'URL finale est toujours une fiche

Pour serveur-minecraft-vote.fr le slug (nom du serveur) dans l'URL est
facultatif : /serveur/2787 redirige de lui-même vers /serveur/2787/craft4fight.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

import httpx

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def page_title(text: str) -> str:
    m = _TITLE_RE.search(text)
    if not m:
        return ""
    return html.unescape(re.sub(r"\s+", " ", m.group(1))).strip()


@dataclass(frozen=True)
class Site:
    key: str
    label: str
    url_template: str          # {id} sera remplacé
    exists: Callable[[httpx.Response], bool]
    _name_re: re.Pattern[str] | None = None

    def url(self, server_id: int) -> str:
        return self.url_template.format(id=server_id)

    def server_name(self, text: str) -> str:
        """Nom du serveur extrait du <title>, sinon le titre brut."""
        title = page_title(text)
        if self._name_re is not None:
            m = self._name_re.match(title)
            if m and m.group(1).strip():
                return m.group(1).strip()
        return title or "(nom inconnu)"


def _exists_if_path_matches(pattern: str) -> Callable[[httpx.Response], bool]:
    """Fabrique un test « 200 + URL finale toujours sur une fiche serveur ».

    Couvre d'un coup les trois façons dont ces sites disent « ça n'existe pas » :
    un 404, une redirection vers l'accueil, ou un 200 sur une page générique.
    """
    path_re = re.compile(pattern)

    def _exists(resp: httpx.Response) -> bool:
        if resp.status_code != 200:
            return False
        return bool(path_re.match(urlsplit(str(resp.url)).path))

    return _exists


SITES: list[Site] = [
    Site(
        key="serveursminecraft_org",
        label="serveursminecraft.org",
        url_template="https://www.serveursminecraft.org/serveur/{id}",
        exists=_exists_if_path_matches(r"^/serveur/\d+"),
        # "Serveur Minecraft PantheonMC | Liste de Serveur Minecraft"
        _name_re=re.compile(r"^Serveur Minecraft\s+(.*?)\s*\|", re.IGNORECASE),
    ),
    Site(
        key="serveur_minecraft_com",
        label="serveur-minecraft.com",
        url_template="https://serveur-minecraft.com/{id}",
        exists=_exists_if_path_matches(r"^/\d+(?:[/-]|$)"),
        # "PantheonMC - Serveur Minecraft"
        _name_re=re.compile(r"^(.*?)\s+-\s+Serveur Minecraft", re.IGNORECASE),
    ),
    Site(
        key="serveur_minecraft_vote_fr",
        label="serveur-minecraft-vote.fr",
        url_template="https://serveur-minecraft-vote.fr/serveur/{id}",
        exists=_exists_if_path_matches(r"^/serveur/\d+"),
        # "Craft4Fight - serveur Minecraft - Serveur Minecraft Vote"
        _name_re=re.compile(r"^(.*?)\s+-\s+serveur Minecraft", re.IGNORECASE),
    ),
]

BY_KEY = {s.key: s for s in SITES}


def selected(keys: list[str] | None) -> list[Site]:
    if not keys:
        return list(SITES)
    unknown = [k for k in keys if k not in BY_KEY]
    if unknown:
        raise KeyError(f"Site(s) inconnu(s) : {', '.join(unknown)}. Connus : {', '.join(BY_KEY)}")
    return [BY_KEY[k] for k in keys]
