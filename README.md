[![Pylint](https://github.com/TehPeGaSuS/MultiRPG/actions/workflows/pylint.yml/badge.svg)](https://github.com/TehPeGaSuS/MultiRPG/actions/workflows/pylint.yml) [![Python application](https://github.com/TehPeGaSuS/MultiRPG/actions/workflows/python-app.yml/badge.svg)](https://github.com/TehPeGaSuS/MultiRPG/actions/workflows/python-app.yml) [![CodeQL Advanced](https://github.com/TehPeGaSuS/MultiRPG/actions/workflows/codeql.yml/badge.svg)](https://github.com/TehPeGaSuS/MultiRPG/actions/workflows/codeql.yml)

---

# ⚔ Multi IdleRPG ⚔

An idle RPG for IRC, written in Python. You register a character, then play by doing absolutely nothing: levels come from idling, and talking, parting, quitting or changing your nick sets you back.

It started as a Python port of [IdleRPG](http://idlerpg.net/) 3.0 by jotun and still plays by the same core rules (time to next level, penalties, battles, calamities, quests), but it has grown well beyond a port:

- **Many networks, one world.** A single bot connects to as many IRC networks as you configure. All of them share one game world and one player database.
- **Web interface.** Live leaderboard, world map, quest status, game info and a Hall of Fame.
- **Rounds.** A round can end when someone reaches a target level, on a cron schedule, or never. The top three go into the Hall of Fame and everyone starts over (see [Rounds](#rounds-and-the-hall-of-fame)).
- **Stays logged in.** Players are logged back in automatically by their host after a bot restart or a reconnect, and they stay logged in across round resets.
- **SQLite storage** and **salted scrypt** password hashes (hashes from older versions are upgraded when the player next logs in).

---

## Requirements

- Python 3.11+
- `aiosqlite`, `aiohttp` and `croniter` (`pip install -r requirements.txt`; `croniter` is only used when `hof_type = "cron"`)

---

## Running the Bot

### First run

Recent Ubuntu releases (22.04+) will refuse `pip install` at the system level with an "externally managed environment" error. Use a virtual environment instead:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 main.py
```

This generates a `config.toml` in the current directory. Stop the bot, edit the file to add your IRC networks, then run again:

```bash
python3 main.py
```

> **Note:** You need to `source venv/bin/activate` in every new shell session before running the bot, or use the full path to the venv Python directly (see systemd example below).

### Configuration

```toml
[game]
self_clock  = 5
limit_pen   = 0
hof_type    = "level"           # "level", "cron", or "none"
win_level   = 40                # used when hof_type = "level"
round_cron  = "0 0 1 1,4,7,10 *"  # used when hof_type = "cron" (quarterly)

[web]
host = "0.0.0.0"
port = 8080

[[networks]]
name       = "SwiftIRC"
host       = "irc.swiftirc.net"
port       = 6697
channel    = "#multirpg"
nick       = "MultiRPG"
use_ssl    = true
# nickserv_pass = "yourpass"
# server_pass   = "yourpass"

[[networks]]
name       = "Libera"
host       = "irc.libera.chat"
port       = 6697
channel    = "#multirpg"
nick       = "MultiRPG"
use_ssl    = true
```

Add as many `[[networks]]` blocks as you like. All networks share the same game world and player database.

### Keeping it running

With **screen**:
```bash
source venv/bin/activate
screen -S multirpg python3 main.py
# detach with Ctrl+A, D — reattach with: screen -r multirpg
```

With **systemd** (`/etc/systemd/system/multirpg.service`) — note the venv Python path:
```ini
[Unit]
Description=Multi IdleRPG Bot
After=network.target

[Service]
WorkingDirectory=/path/to/MultiRPG
ExecStart=/path/to/MultiRPG/venv/bin/python3 main.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```
```bash
systemctl enable --now multirpg
journalctl -u multirpg -f   # follow logs
```

### Database

The bot creates `multirpg.db` (SQLite, WAL mode) on first run. The safe way to back it up while the bot is running is SQLite's own backup command, because a plain `cp` can miss recent writes that are still in the `-wal` file:
```bash
sqlite3 multirpg.db ".backup multirpg.db.bak"
```
Or add a cron job:
```bash
0 * * * * sqlite3 /path/to/multirpg.db ".backup /path/to/backups/multirpg-$(date +\%H).db"
```

---

## Rounds and the Hall of Fame

`hof_type` in `config.toml` decides how rounds end:

| `hof_type` | A round ends… |
|---|---|
| `"level"` | when a player reaches `win_level` |
| `"cron"` | on the schedule in `round_cron` (standard cron syntax, **UTC**), e.g. `"0 0 1 1,4,7,10 *"` for quarterly |
| `"none"` | never automatically (no Hall of Fame page); an admin can still run `ENDROUND` |

When a round ends, the bot announces it in every channel and waits 60 seconds. Then the top three are saved to the Hall of Fame: highest level first, ties broken by the least time left to the next level, the same order as the leaderboard and `TOP`. After that every character is reset (level, TTL, items, penalties, alignment and position) and a new round begins. Accounts, passwords and login sessions are kept, so nobody has to log in again. A scheduled (`cron`) round end always happens on time, and any quest in progress is cancelled by the reset.

A bot that is down when a scheduled round end passes does not make it up when it comes back. An admin can run `ENDROUND` to reset on demand.

---

## Versioning

The version lives in `version.py` (`__version__`). It is shown in the footer of every web page and logged when the bot starts. When releasing, bump it and tag the commit to match (`git tag v1.0.1`).

---

## Web Interface

| URL | Description |
|---|---|
| `/` | Leaderboard — auto-refreshes every 10s |
| `/map` | Live world map — terrain, region names, player positions |
| `/info` | Game info and mechanics |
| `/quest` | Active quest status |
| `/play` | Where to play — IRC networks and channels |
| `/hof` | Hall of Fame — top players from past rounds (hidden when `hof_type = none`) |
| `/admin` | Admin command reference |

---

## User Commands

All commands are sent via **private message** to the bot. Talking in the channel, parting, quitting, changing your nick, or noticing the channel all incur time penalties.

### Account

| Command | Description |
|---|---|
| `REGISTER <name> <pass> <class>` | Create a character. Name ≤16 chars, class ≤30 chars. |
| `LOGIN <name> <pass>` | Log in. |
| `LOGOUT` | Log out (penalty). |
| `NEWPASS <password>` | Change your password. |
| `ALIGN <good\|neutral\|evil>` | Change alignment. |
| `REMOVEME` | Permanently delete your account. |

### Info

| Command | Description |
|---|---|
| `WHOAMI` | Your name, level, class, time to next level. |
| `STATUS [username]` | Full stats for yourself or another player. |
| `QUEST` | Active quest info. |
| `TOP` | Top 5 players by level. |
| `HELP` | Full command list. |

### Penalties

| Event | Formula |
|---|---|
| Nick change | `30 × (1.14 ^ level)` seconds |
| Part | `200 × (1.14 ^ level)` seconds |
| Quit | `20 × (1.14 ^ level)` seconds |
| LOGOUT | `20 × (1.14 ^ level)` seconds |
| Kicked | `250 × (1.14 ^ level)` seconds |
| Channel message | `message_length × (1.14 ^ level)` seconds |

---

## Admin Commands

See [ADMIN.md](ADMIN.md) for the full reference. Quick list:

`HOG` `FORCEQUEST` `ENDROUND` `PAUSE` `SILENT <0-3>` `CLEARQ` `PUSH <user> <secs>` `CHPASS <user> <pass>` `CHCLASS <user> <class>` `CHUSER <user> <newname>` `DEL <user>` `DELOLD <days>` `MKADMIN <user>` `DELADMIN <user>` `RELOGIN` `FORCELOGIN <character> <nick> <network> [userhost]`

To make yourself admin, first register a character, then run directly against the database:
```bash
sqlite3 multirpg.db "UPDATE players SET is_admin=1 WHERE username='YourName';"
```

---

## Credits

Game design by **jotun**. Original map by **res0** and **Jeb**.  
The bot began as a Python port of IdleRPG 3.0 and has since been extended well beyond it.
