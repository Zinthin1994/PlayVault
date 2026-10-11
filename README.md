# PlayVault

A controller-first Epic Games Store + GOG library for SteamOS Game Mode (Steam Machine / Steam Deck).
Browse your Epic and GOG games in one place, see Steam ratings, install/uninstall, run them through
Proton (or natively for GOG Linux builds) and play, all from a fullscreen app that lives in your
Steam library. (Formerly EpicVault.)

## Quick install (Steam Deck / Steam Machine)

Switch to **Desktop Mode**, open **Konsole** and paste:
```bash
cd ~/Downloads && curl -L https://github.com/Zinthin1994/PlayVault/releases/latest/download/PlayVault.tar.gz | tar xz && bash PlayVault/install.sh
```
Then go back to **Game Mode** > Library > **Non-Steam** > **PlayVault** and sign in to Epic and/or GOG.
Run the same command again later to update.

## Manual install

1. Download `PlayVault.tar.gz` from the [latest release](https://github.com/Zinthin1994/PlayVault/releases/latest)
   and copy it to the Steam Machine (USB stick, SSH/scp, LocalSend...).
2. Switch to **Desktop Mode** (Steam button > Power > Switch to Desktop).
3. Open **Dolphin**, right-click the archive > *Extract > Extract archive here* (puts a `PlayVault` folder next to it).
4. Open **Konsole** and run:
   ```bash
   cd ~/Downloads/PlayVault        # wherever you extracted it
   bash install.sh
   ```
   It downloads legendary (Epic client), gogdl (GOG client), umu-launcher (Proton runner) and
   Electron (UI runtime), then asks to close Steam for a moment to add the **PlayVault** tile.
5. Go back to **Game Mode** > Library > **Non-Steam** > **PlayVault** and sign in to Epic and/or GOG.

Re-run `bash install.sh` any time to update; your logins, settings and games are kept.
Upgrading from EpicVault: just run the new installer - it moves everything to
`~/.local/share/playvault` and renames your existing Steam tiles (artwork, controller layouts and
playtime are kept).
`bash uninstall.sh` removes the app (games stay; `--all` also deletes data/prefixes).

### Updates
PlayVault updates itself from this repo's [releases](https://github.com/Zinthin1994/PlayVault/releases).
It checks once each time PlayVault opens (no background checks), downloads a new version while
you browse, verifies its SHA-256 checksum, and installs it the next time you open PlayVault (never during a download or while a
game is running). If a new version fails to start twice, it goes back to the previous one.
Change this in Settings > *PlayVault updates* (Download automatically / Ask first / Off).
Versions before 1.2.0 can't update themselves: install 1.2.0 once with the command above.

### Controls
| Button | Action |
|---|---|
| D-pad / left stick | Move |
| A | Select |
| B | Back / close (on the Library: exit) |
| X | Options for the selected game (library) / More (game page) |
| Y | Search (on-screen keyboard) |
| LB / RB | Switch tabs (Library, Downloads, Settings) |
| View | Cycle library filter |
| Menu | Cycle sort order |
| Right stick | Scroll |

If buttons don't respond in Game Mode, open the controller settings for the PlayVault tile and
pick the **Gamepad** template. Keyboard and mouse also work.

## Where things live
| Path | What |
|---|---|
| `~/.local/share/playvault/app` | the app |
| `~/.local/share/playvault/data` | settings, logins (legendary + gog), ratings cache, logs |
| `~/.local/share/playvault/data/prefixes/<game>` | one Proton prefix per game (local saves live here) |
| `~/.local/share/playvault/data/logs` | `backend.log`, `<game>.log` (launch logs), `steam_apply.log` |
| `~/Games/Epic`, `~/Games/GOG` | default install folders (changeable, microSD supported) |

## GOG notes
- GOG games are DRM-free: once installed they launch without signing in, even after you sign out.
- Games that ship both builds can be installed as the **Windows version** (Proton, usually the most
  up to date) or the **native Linux version** - pick it in the install dialog, or set the default
  in Settings.
- GOG cloud saves sync before and after playing (Windows versions; uses GOG's save locations).
- Ratings use the exact Steam id from GOG's catalog where available, so matches are precise.

## Troubleshooting
- **A game won't start**: on its page select *Last launch* (or More > View launch log). Try another
  Proton version in *Properties* (GE-Proton or Proton Experimental), or turn on *Proton debug log*.
- **First launch is slow**: umu downloads the Steam Linux Runtime and Proton once. You can do this
  ahead of time with Settings > *Prepare Proton now*.
- **Online multiplayer with Easy Anti-Cheat/BattlEye**: only works if the developer enabled Linux
  support. The game page shows the AreWeAntiCheatYet status.
- **EA / Ubisoft games on Epic**: Epic only hands off to the EA app / Ubisoft Connect. Those need extra setup in the prefix.
- **New Steam tile doesn't show**: Steam must restart to read new shortcuts. Use the *Restart Steam now* banner.

## Credits & disclaimer
PlayVault is an unofficial fan project, not affiliated with or endorsed by Epic Games, GOG,
Valve or Microsoft. You need your own accounts and only see games you own. The installer
downloads these open-source tools from their official releases: [legendary](https://github.com/derrod/legendary),
[gogdl](https://github.com/Heroic-Games-Launcher/heroic-gogdl), [umu-launcher](https://github.com/Open-Wine-Components/umu-launcher)
and [Electron](https://www.electronjs.org/). Ratings come from Steam, ProtonDB and AreWeAntiCheatYet.
PlayVault itself is MIT licensed (see `LICENSE`).

## How it works
- `evault/server.py`: local HTTP API (127.0.0.1:47821) + serves the web UI
- `evault/legendary.py`: wraps the [legendary](https://github.com/derrod/legendary) CLI for Epic login, library, installs
- `evault/gog.py`: wraps [gogdl](https://github.com/Heroic-Games-Launcher/heroic-gogdl) + GOG's web APIs (library, gamesdb art, builds, cloud saves)
- `evault/jobs.py`: download queue with progress parsed from legendary's / gogdl's output
- `evault/ratings.py`: Steam match + reviews, ProtonDB, Deck status, AreWeAntiCheatYet
- `evault/launcher.py`: cloud-save sync, fresh Epic auth, `umu-run game.exe` with the chosen Proton, playtime
- `evault/steam.py`: writes Steam `shortcuts.vdf` entries + grid/hero/logo artwork
- `electron/main.js`: fullscreen window, Epic / GOG sign-in windows (with built-in keyboard), hides while a game runs
- `web/`: the controller-driven UI (vanilla JS)
