# Multi IdleRPG — Admin Commands

All admin commands are sent via **private message** to the bot. The bot checks the `is_admin` flag on your character. To grant admin, another admin must run `MKADMIN <username>`.

---

## Game Control

### `HOG`
Summon the Hand of God immediately. Randomly helps or hurts one online player by 5–75% of their TTL (80% chance to help, 20% to hinder).

```
/msg MultiRPG HOG
```

### `PAUSE`
Toggle pause mode. When paused, the tick loop stops completely — no TTL countdown, no events, no movement. Use before maintenance. Run again to resume.

```
/msg MultiRPG PAUSE
```

### `SILENT <mode>`
Control how much the bot speaks. Useful for testing without flooding a channel.

| Mode | Effect |
|---|---|
| `0` | All messages enabled (default) |
| `1` | Channel messages disabled — bot goes quiet in channel |
| `2` | Private messages/notices disabled — bot stops PMing players |
| `3` | All messages disabled |

```
/msg MultiRPG SILENT 1
/msg MultiRPG SILENT 0
```

### `CLEARQ`
Clear the outgoing message queue. Use if the bot is backed up with messages (e.g. after a flood or a bug that produced many broadcasts).

```
/msg MultiRPG CLEARQ
```

---

## Player Management

### `PUSH <username> <seconds>`
Push a player toward their next level by subtracting seconds from their TTL. Use to correct bot mistakes. Negative seconds adds time (penalty).

```
/msg MultiRPG PUSH PotHead 3600
```

### `CHPASS <username> <new password>`
Change a player's password. Use when a player has forgotten theirs.

```
/msg MultiRPG CHPASS PotHead newpass123
```

### `CHCLASS <username> <new class>`
Change a player's class name. Class can be up to 30 characters including spaces.

```
/msg MultiRPG CHCLASS PotHead Supreme Overlord of Mischief
```

### `CHUSER <username> <new name>`
Rename a character. The new name must not already be taken across any network. Use sparingly — the player will need to log in again with the new name.

```
/msg MultiRPG CHUSER PotHead HighPotHead
```

### `DEL <username>`
Delete a player's account permanently.

```
/msg MultiRPG DEL PotHead
```

### `DELOLD <days>`
Remove all accounts that have not been online in the last `<days>` days. Useful for periodic cleanup.

```
/msg MultiRPG DELOLD 30
```

---

## Admin Management

### `MKADMIN <username>`
Grant admin privileges to a character. The character must already exist.

```
/msg MultiRPG MKADMIN PotHead
```

### `DELADMIN <username>`
Revoke admin privileges from a character.

```
/msg MultiRPG DELADMIN PotHead
```

---

## Notes

- Admin status is tied to a **character name**, not an IRC nick. If a player renames their character (CHUSER) they retain admin status.
- There is no `PEVAL` command — use direct DB access (`sqlite3 multirpg.db`) for bulk operations.
- There is no `DIE` or `RESTART` command — use your process manager (systemd, screen, etc.) or Ctrl+C.
- Database backups: just `cp multirpg.db multirpg.db.bak` or set up a cron job. No bot command needed.
