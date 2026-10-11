#!/usr/bin/env bash
# Removes PlayVault. Your installed games (~/Games/Epic and ~/Games/GOG by default) are NOT deleted.
#   bash uninstall.sh            remove app, keep data (logins, settings, Proton prefixes)
#   bash uninstall.sh --all      also delete data (logins, settings, Proton prefixes / local saves)
set -euo pipefail
ROOT="${PLAYVAULT_HOME:-$HOME/.local/share/playvault}"

if pgrep -x steam >/dev/null; then
  echo "Note: PlayVault tiles stay in your Steam library until you remove them in Steam."
fi
pkill -f "$ROOT/electron/electron" 2>/dev/null || true
systemctl --user stop playvault-backend 2>/dev/null || true
rm -rf "$ROOT/app" "$ROOT/venv" "$ROOT/umu" "$ROOT/gogdl" "$ROOT/electron"
rm -f "$HOME/.local/share/applications/playvault.desktop" "$HOME/.local/share/applications/epicvault.desktop"
[ -L "$HOME/.local/share/epicvault" ] && rm -f "$HOME/.local/share/epicvault"
if [ "${1:-}" = "--all" ]; then
  rm -rf "$ROOT"
  echo "PlayVault and all its data removed."
else
  echo "PlayVault removed. Data kept in $ROOT/data (run with --all to delete it)."
fi
echo "Games were not touched."
