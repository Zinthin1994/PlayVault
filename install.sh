#!/usr/bin/env bash
# PlayVault installer for SteamOS (Steam Machine / Steam Deck).
# Run from Desktop Mode in Konsole:   bash install.sh
# Re-running it updates PlayVault and keeps your settings, logins and games.
# Upgrading from EpicVault moves everything over (logins, games, Steam tiles).
#
# Options:  --no-steam   don't add the PlayVault tile to Steam
set -euo pipefail

ELECTRON_VERSION="31.7.7"
UMU_FALLBACK_VERSION="1.4.4"
GOGDL_FALLBACK_VERSION="v1.3.0"
LEGENDARY_URL="https://github.com/derrod/legendary/archive/refs/heads/master.zip"

SRC="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
ROOT="${PLAYVAULT_HOME:-$HOME/.local/share/playvault}"
OLD_ROOT="$HOME/.local/share/epicvault"
ADD_STEAM=1
for a in "$@"; do
  case "$a" in
    --no-steam) ADD_STEAM=0 ;;
  esac
done

bold() { printf '\n\033[1;34m==>\033[0m \033[1m%s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m!! %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mxx %s\033[0m\n' "$*"; exit 1; }

[ "$(uname -s)" = "Linux" ] || die "Run this on the Steam Machine (Linux), not on Windows."
command -v python3 >/dev/null || die "python3 not found"
python3 - <<'EOF' || die "Python 3.9 or newer is required"
import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)
EOF
command -v curl >/dev/null || die "curl not found"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ------------------------------------------------------------------ EpicVault -> PlayVault
if [ -d "$OLD_ROOT" ] && [ ! -L "$OLD_ROOT" ] && [ ! -e "$ROOT" ]; then
  bold "Moving your EpicVault install to PlayVault (logins, settings and games are kept)"
  pkill -f "$OLD_ROOT/electron/electron" 2>/dev/null || true
  systemctl --user stop epicvault-backend 2>/dev/null || true
  sleep 1
  mv "$OLD_ROOT" "$ROOT"
  # old Steam tiles and paths keep working through this link until they're migrated
  ln -s "$ROOT" "$OLD_ROOT"
  # legendary remembers each game's Proton prefix path
  sed -i "s#$OLD_ROOT/#$ROOT/#g" "$ROOT/data/legendary/config.ini" 2>/dev/null || true
  rm -f "$HOME/.local/share/applications/epicvault.desktop"
fi

bold "Installing PlayVault into $ROOT"
mkdir -p "$ROOT/data/logs"
pkill -f "$ROOT/electron/electron" 2>/dev/null || true
systemctl --user stop playvault-backend epicvault-backend 2>/dev/null || true

# ------------------------------------------------------------------ app files
bold "Copying app files"
rm -rf "$ROOT/app.new"
cp -r "$SRC/app" "$ROOT/app.new"
# strip Windows line endings in case the folder came via a Windows PC
find "$ROOT/app.new" -type f \( -name '*.py' -o -name '*.js' -o -name '*.json' -o -name '*.html' -o -name '*.css' \) \
  -exec sed -i 's/\r$//' {} +
sed -i 's/\r$//' "$ROOT/app.new/bin/"*
chmod +x "$ROOT/app.new/bin/"*
rm -rf "$ROOT/app"
mv "$ROOT/app.new" "$ROOT/app"

# ------------------------------------------------------------------ python + legendary
bold "Setting up Python environment with legendary (Epic client)"
if [ ! -x "$ROOT/venv/bin/python" ]; then
  if ! python3 -m venv "$ROOT/venv"; then
    warn "venv with pip failed, bootstrapping pip manually"
    rm -rf "$ROOT/venv"
    python3 -m venv --without-pip "$ROOT/venv" || die "Could not create a Python venv"
    curl -fsSL https://bootstrap.pypa.io/get-pip.py -o "$TMP/get-pip.py" || die "Could not download get-pip.py"
    "$ROOT/venv/bin/python" "$TMP/get-pip.py" --quiet || die "Could not install pip"
  fi
fi
"$ROOT/venv/bin/python" -m pip install --quiet --upgrade pip || warn "pip self-upgrade failed (continuing)"
if ! "$ROOT/venv/bin/python" -m pip install --quiet --upgrade "$LEGENDARY_URL"; then
  warn "Installing legendary from GitHub failed, trying PyPI"
  "$ROOT/venv/bin/python" -m pip install --quiet --upgrade legendary-gl || die "Could not install legendary"
fi
"$ROOT/venv/bin/legendary" --version || die "legendary did not install correctly"

# ------------------------------------------------------------------ gogdl (GOG client)
bold "Installing gogdl (GOG client from Heroic)"
GOGDL_URL="$(curl -fsSL https://api.github.com/repos/Heroic-Games-Launcher/heroic-gogdl/releases/latest 2>/dev/null \
  | python3 -c 'import sys,json
try:
    d=json.load(sys.stdin); print(next(a["browser_download_url"] for a in d["assets"] if a["name"]=="gogdl_linux_x86_64"))
except Exception: pass' || true)"
if [ -z "$GOGDL_URL" ]; then
  GOGDL_URL="https://github.com/Heroic-Games-Launcher/heroic-gogdl/releases/download/${GOGDL_FALLBACK_VERSION}/gogdl_linux_x86_64"
fi
echo "    $GOGDL_URL"
mkdir -p "$ROOT/gogdl"
if curl -fL --progress-bar "$GOGDL_URL" -o "$TMP/gogdl"; then
  mv "$TMP/gogdl" "$ROOT/gogdl/gogdl"
  chmod +x "$ROOT/gogdl/gogdl"
  "$ROOT/gogdl/gogdl" --version || warn "gogdl did not start - GOG support will be unavailable"
else
  warn "Downloading gogdl failed - GOG support will be unavailable until you re-run the installer"
fi

# ------------------------------------------------------------------ umu-launcher
bold "Installing umu-launcher (runs games with Proton outside of Steam)"
UMU_URL="$(curl -fsSL https://api.github.com/repos/Open-Wine-Components/umu-launcher/releases/latest 2>/dev/null \
  | python3 -c 'import sys,json
try:
    d=json.load(sys.stdin); print(next(a["browser_download_url"] for a in d["assets"] if a["name"].endswith("zipapp.tar")))
except Exception: pass' || true)"
if [ -z "$UMU_URL" ]; then
  UMU_URL="https://github.com/Open-Wine-Components/umu-launcher/releases/download/${UMU_FALLBACK_VERSION}/umu-launcher-${UMU_FALLBACK_VERSION}-zipapp.tar"
fi
echo "    $UMU_URL"
curl -fL --progress-bar "$UMU_URL" -o "$TMP/umu.tar" || die "Downloading umu-launcher failed"
mkdir -p "$TMP/umu"
tar -xf "$TMP/umu.tar" -C "$TMP/umu"
UMU_BIN="$(find "$TMP/umu" -type f -name umu-run | head -n1)"
[ -n "$UMU_BIN" ] || die "umu-run not found in the umu archive"
rm -rf "$ROOT/umu"
mkdir -p "$ROOT/umu"
cp "$UMU_BIN" "$ROOT/umu/umu-run"
chmod +x "$ROOT/umu/umu-run"

# ------------------------------------------------------------------ electron
if [ "$(cat "$ROOT/electron/.version" 2>/dev/null || true)" != "$ELECTRON_VERSION" ]; then
  bold "Downloading Electron $ELECTRON_VERSION (UI runtime, ~100 MB)"
  curl -fL --progress-bar \
    "https://github.com/electron/electron/releases/download/v${ELECTRON_VERSION}/electron-v${ELECTRON_VERSION}-linux-x64.zip" \
    -o "$TMP/electron.zip" || die "Downloading Electron failed"
  rm -rf "$ROOT/electron"
  mkdir -p "$ROOT/electron"
  python3 - "$TMP/electron.zip" "$ROOT/electron" <<'EOF'
import os, stat, sys, zipfile
src, dst = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(src) as z:
    for info in z.infolist():
        z.extract(info, dst)
        mode = (info.external_attr >> 16) & 0o777
        if mode:
            os.chmod(os.path.join(dst, info.filename), mode)
EOF
  chmod +x "$ROOT/electron/electron" "$ROOT/electron/chrome_crashpad_handler" 2>/dev/null || true
  echo "$ELECTRON_VERSION" > "$ROOT/electron/.version"
else
  bold "Electron $ELECTRON_VERSION already installed"
fi

# ------------------------------------------------------------------ desktop entry
bold "Creating desktop menu entry"
mkdir -p "$HOME/.local/share/applications"
rm -f "$HOME/.local/share/applications/epicvault.desktop"
cat > "$HOME/.local/share/applications/playvault.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=PlayVault
Comment=Epic Games + GOG library for Game Mode
Exec=$ROOT/app/bin/playvault
Icon=$ROOT/app/art/icon.png
Categories=Game;
Terminal=false
EOF

# ------------------------------------------------------------------ Steam tile
STEAM_PY() { PLAYVAULT_HOME="$ROOT" PYTHONPATH="$ROOT/app" "$ROOT/venv/bin/python" -m evault.steam "$@"; }
if [ "$ADD_STEAM" = "1" ]; then
  bold "Adding PlayVault to your Steam library"
  RESTART_STEAM=0
  if pgrep -x steam >/dev/null; then
    echo "Steam has to close for a moment so the library tiles stick."
    read -r -p "Close Steam now? [Y/n] " ans || ans="y"
    if [ -z "$ans" ] || [[ "$ans" =~ ^[Yy] ]]; then
      steam -shutdown >/dev/null 2>&1 || true
      for _ in $(seq 1 60); do pgrep -x steam >/dev/null || break; sleep 1; done
      RESTART_STEAM=1
    fi
  fi
  # with Steam closed this writes the tiles now; otherwise it stages them and PlayVault
  # shows a "Restart Steam" banner (EpicVault tiles are renamed in the same step)
  STEAM_PY add-self || warn "Could not add the Steam tile (log into Steam once, then use Settings > Re-add PlayVault to Steam)"
  if [ "$RESTART_STEAM" = "1" ]; then
    (setsid steam >/dev/null 2>&1 &)
  fi
else
  STEAM_PY migrate >/dev/null 2>&1 || true
fi

bold "Done!"
cat <<EOF

  PlayVault is installed.

  * Game Mode:     find "PlayVault" in your Library (Non-Steam tab).
  * Desktop Mode:  Application menu > Games > PlayVault
  * First start:   sign in with your Epic and/or GOG account.

  Logs: $ROOT/data/logs
  Update later by running this installer again.
EOF
