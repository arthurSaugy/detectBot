#!/usr/bin/env bash
# Installe mcwatch comme timer systemd. À lancer en root (ou avec sudo)
# depuis le dossier du dépôt : sudo ./deploy/install.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="${SUDO_USER:-$(id -un)}"
UNIT_DIR="/etc/systemd/system"

if [[ $EUID -ne 0 ]]; then
  echo "Ce script doit être lancé en root : sudo $0" >&2
  exit 1
fi

echo "==> Dossier applicatif : $APP_DIR"
echo "==> Utilisateur du service : $RUN_USER"

# --- 1. Environnement virtuel -----------------------------------------------
# On teste l'interpréteur, pas le dossier : un venv à moitié créé (ensurepip
# manquant) laisse un .venv vide qui ferait échouer la suite silencieusement.
if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
  if [[ -d "$APP_DIR/.venv" ]]; then
    echo "==> Virtualenv incomplet, on repart de zéro"
    rm -rf "$APP_DIR/.venv"
  fi
  echo "==> Création du virtualenv"
  if ! sudo -u "$RUN_USER" python3 -m venv "$APP_DIR/.venv"; then
    PYVER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    cat >&2 <<MSG

Échec de la création du virtualenv.
Sur Debian/Ubuntu, le module venv est dans un paquet séparé :

    sudo apt update && sudo apt install -y python${PYVER}-venv

puis relance ce script.
MSG
    rm -rf "$APP_DIR/.venv"
    exit 1
  fi
fi
echo "==> Installation des dépendances"
sudo -u "$RUN_USER" "$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
sudo -u "$RUN_USER" "$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

# --- 2. Configuration --------------------------------------------------------
if [[ ! -f "$APP_DIR/config.toml" ]]; then
  cp "$APP_DIR/config.example.toml" "$APP_DIR/config.toml"
  chown "$RUN_USER" "$APP_DIR/config.toml"
  chmod 600 "$APP_DIR/config.toml"
  echo
  echo "!! config.toml créé à partir de l'exemple."
  echo "!! Renseigne le topic ntfy dans $APP_DIR/config.toml, puis relance ce script."
  exit 0
fi

if grep -q "CHANGE-MOI" "$APP_DIR/config.toml"; then
  echo "!! config.toml contient encore un placeholder CHANGE-MOI. Corrige-le d'abord." >&2
  exit 1
fi

# --- 3. État initial ---------------------------------------------------------
if [[ ! -f "$APP_DIR/state.json" ]]; then
  echo "==> Initialisation des curseurs"
  sudo -u "$RUN_USER" "$APP_DIR/.venv/bin/python" -m mcwatch init
fi

# --- 4. Unités systemd -------------------------------------------------------
echo "==> Installation des unités systemd"
sed -e "s|@APP_DIR@|$APP_DIR|g" -e "s|@USER@|$RUN_USER|g" \
    "$APP_DIR/deploy/mcwatch.service" > "$UNIT_DIR/mcwatch.service"
cp "$APP_DIR/deploy/mcwatch.timer" "$UNIT_DIR/mcwatch.timer"

systemctl daemon-reload
systemctl enable --now mcwatch.timer

echo
echo "==> Terminé."
systemctl list-timers mcwatch.timer --no-pager || true
echo
echo "Commandes utiles :"
echo "  systemctl start mcwatch.service      # forcer un passage maintenant"
echo "  journalctl -u mcwatch.service -n 50  # voir les logs"
echo "  $APP_DIR/.venv/bin/python -m mcwatch status"
