# ⚔ Multi IdleRPG — Multi-Network IdleRPG Python Bot

A faithful Python reimplementation of [IdleRPG](http://idlerpg.net/) v3.0, extended to run simultaneously across multiple IRC networks with a shared game world and a live web interface.

---

## Requirements

- Python 3.11+
- `aiosqlite` and `aiohttp`

```bash
pip install -r requirements.txt
```

---

## Running the Bot

### First run

```bash
python3 main.py
```

This generates a `config.toml` in the current directory. Stop the bot, edit the file to add your IRC networks, then run again.

### Configuration

```toml
[web]
host = "0.0.0.0"
port = 8080

[[networks]]
name       = "PTirc"
host       = "irc.ptirc.org"
port       = 6667
channel    = "#multirpg"
nick       = "MultiRPG"
use_ssl    = false
# nickserv_pass = "yourpass"
# server_pass   = "yourpass"

[[networks]]
name       = "PTnet"
host       = "irc.ptnet.org"
port       = 6667
channel    = "#multirpg"
nick       = "MultiRPG"
use_ssl    = false
```

Add as many `[[networks]]` blocks as you like. All networks share the same game world and player database.

### Keeping it running

With **screen**:
```bash
screen -S multirpg python3 main.py
# detach with Ctrl+A, D — reattach with: screen -r multirpg
```

With **systemd** (`/etc/systemd/system/multirpg.service`):
```ini
[Unit]
Description=Multi IdleRPG Bot
After=network.target

[Service]
WorkingDirectory=/path/to/MultiRPG
ExecStart=/usr/bin/python3 main.py
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

The bot creates `multirpg.db` (SQLite) on first run. Back it up with:
```bash
cp multirpg.db multirpg.db.bak
```
Or add a cron job:
```bash
0 * * * * cp /path/to/multirpg.db /path/to/backups/multirpg-$(date +\%H).db
```

---

## Web Interface

| URL | Description |
|---|---|
| `/` | Leaderboard — auto-refreshes every 10s |
| `/map` | Live world map — terrain, region names, player positions |
| `/info` | Game info and mechanics |
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

`HOG` `PAUSE` `SILENT <0-3>` `CLEARQ` `PUSH <user> <secs>` `CHPASS <user> <pass>` `CHCLASS <user> <class>` `CHUSER <user> <newname>` `DEL <user>` `DELOLD <days>` `MKADMIN <user>` `DELADMIN <user>`

To make yourself admin, first register a character, then run directly against the database:
```bash
sqlite3 multirpg.db "UPDATE players SET is_admin=1 WHERE username='YourName';"
```

---

## Credits

Game design by **jotun**. Original map by **res0** and **Jeb**.  
Python implementation built from scratch, honouring the original v3.0 logic.
