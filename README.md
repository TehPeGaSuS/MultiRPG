[![Python application](https://github.com/TehPeGaSuS/MultiRPG/actions/workflows/python-app.yml/badge.svg)](https://github.com/TehPeGaSuS/MultiRPG/actions/workflows/python-app.yml)  [![Pylint](https://github.com/TehPeGaSuS/MultiRPG/actions/workflows/pylint.yml/badge.svg)](https://github.com/TehPeGaSuS/MultiRPG/actions/workflows/pylint.yml)  [![CodeQL Advanced](https://github.com/TehPeGaSuS/MultiRPG/actions/workflows/codeql.yml/badge.svg)](https://github.com/TehPeGaSuS/MultiRPG/actions/workflows/codeql.yml)

---

# ⚔ Multi IdleRPG — Multi-Network IdleRPG Python Bot ⚔

A faithful Python reimplementation of [IdleRPG](http://idlerpg.net/) v3.0, extended to run simultaneously across multiple IRC networks with a shared game world and a live web interface.

---

## Requirements

- Python 3.11+
- `aiosqlite` and `aiohttp`

---

## Running the Bot

### First run

Recent Ubuntu releases (22.04+) will refuse `pip install` at the system level. Use a virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 main.py
```

This generates a `config.toml`. Stop the bot, edit it to add your IRC networks, then run again:

```bash
python3 main.py
```

> **Note:** You need to `source venv/bin/activate` in every new shell session, or use the full venv path in systemd (see below).

### Configuration

```toml
[game]
self_clock = 5
limit_pen  = 0

[web]
host         = "0.0.0.0"
port         = 8080
rate_limit   = 60   # max requests per IP per window
rate_window  = 60   # window size in seconds

[[networks]]
name       = "PTirc"
host       = "irc.ptirc.org"
port       = 6697
channel    = "#multirpg"
nick       = "MultiRPG"
use_ssl    = true
# nickserv_pass = "yourpass"
# server_pass   = "yourpass"
```

Add as many `[[networks]]` blocks as you like. All networks share the same game world and database.

### Keeping it running

With **screen**:
```bash
source venv/bin/activate
screen -S multirpg python3 main.py
# detach: Ctrl+A, D — reattach: screen -r multirpg
```

With **systemd** (`/etc/systemd/system/multirpg.service`):
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
journalctl -u multirpg -f
```

### Database

The bot creates `multirpg.db` (SQLite) on first run. To apply schema updates to an existing database without wiping it:
```bash
sqlite3 multirpg.db < db/schema.sql
```

Back it up with:
```bash
cp multirpg.db multirpg.db.bak
# or via cron:
0 * * * * cp /path/to/multirpg.db /path/to/backups/multirpg-$(date +\%H).db
```

---

## Web Interface

| URL | Description |
|---|---|
| `/` | Leaderboard — live, auto-refreshes every 10s |
| `/map` | World map — player positions on a vintage map |
| `/info` | Game info and mechanics |
| `/quest` | Active quest status |
| `/hof` | Hall of Fame — all-time round winners |
| `/play` | Where to play — IRC networks and channels |
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

## Game Mechanics

### Rounds & Hall of Fame

The game runs in rounds. The first player to reach **level 40** ends the round. The top 3 players by level (item sum as tiebreaker) are recorded in the Hall of Fame. All player stats reset automatically — usernames, passwords, and admin flags are preserved. Players already in the channel are re-logged in automatically.

### Quests

Four level 40+ players who have been online for at least 2 hours are chosen for a quest. There are two types:

- **Time-based** — lasts 12-24 hours. All questers must stay penalty-free until the timer expires.
- **Grid-based** — questers must walk to two landmark coordinates on the map. No fixed duration.

If any quester receives a penalty during a quest, the quest fails and everyone is penalised.

### Daily Events

- **Hand of God** — random player carried toward or away from next level (5-75% of TTL)
- **Calamities** — bad luck slows a player 5-12% or degrades an item by 10%
- **Godsends** — good luck accelerates a player 5-12% or upgrades an item by 10%
- **Goodness** — two good-aligned players pray together, removing 5-12% of their TTL
- **Evilness** — evil-aligned player steals an item or is forsaken by their dark patron

---

## Admin Commands

See [ADMIN.md](ADMIN.md) for the full reference. Quick list:

`HOG` `FORCEQUEST` `RELOGIN` `PAUSE` `SILENT <0-3>` `CLEARQ` `PUSH <user> <secs>` `CHPASS <user> <pass>` `CHCLASS <user> <class>` `CHUSER <user> <newname>` `DEL <user>` `DELOLD <days>` `MKADMIN <user>` `DELADMIN <user>`

To make yourself admin, first register a character, then:
```bash
sqlite3 multirpg.db "UPDATE players SET is_admin=1 WHERE username='YourName';"
```

---

## Credits

Game design by **jotun**. Original map by **res0** and **Jeb**.
Python implementation built from scratch, honouring the original v3.0 logic.
