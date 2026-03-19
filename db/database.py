"""db/database.py — All SQL lives here."""
import hashlib, random, time
from pathlib import Path
from typing import Optional
import aiosqlite

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
ITEM_SLOTS  = [
    "ring","amulet","charm","weapon","helm",
    "tunic","pair of gloves","shield","set of leggings","pair of boots",
]

class Database:
    def __init__(self, path: Path):
        self.path  = path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self):
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = lambda cur, row: dict(zip([c[0] for c in cur.description], row))
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(SCHEMA_PATH.read_text())
        await self._conn.commit()

    async def close(self):
        if self._conn: await self._conn.close()

    @property
    def conn(self): return self._conn

    @staticmethod
    def hash_password(pw: str) -> str:
        return hashlib.sha256(pw.encode()).hexdigest()

    # ── Players ───────────────────────────────────────────────────────────────

    async def register_player(self, username, network, password,
                               char_class="Adventurer", alignment="n") -> Optional[int]:
        pos_x, pos_y = random.randint(0,499), random.randint(0,499)
        try:
            async with self.conn.execute(
                "INSERT INTO players(username,network,password_hash,class,alignment,pos_x,pos_y,ttl,next_ttl)"
                " VALUES(?,?,?,?,?,?,?,600,600)",
                (username,network,self.hash_password(password),char_class,alignment,pos_x,pos_y)
            ) as cur:
                pid = cur.lastrowid
            for slot in ITEM_SLOTS:
                await self.conn.execute(
                    "INSERT INTO items(player_id,slot,level) VALUES(?,?,0)", (pid,slot))
            await self.conn.commit()
            return pid
        except aiosqlite.IntegrityError:
            return None

    async def get_player(self, username, network):
        async with self.conn.execute(
            "SELECT * FROM players WHERE username=? AND network=?", (username,network)
        ) as c: return await c.fetchone()

    async def get_player_by_id(self, pid):
        async with self.conn.execute("SELECT * FROM players WHERE id=?", (pid,)) as c:
            return await c.fetchone()

    async def get_player_by_nick(self, nick, network):
        async with self.conn.execute(
            "SELECT * FROM players WHERE current_nick=? AND network=?", (nick,network)
        ) as c: return await c.fetchone()

    async def get_online_players(self):
        async with self.conn.execute("SELECT * FROM players WHERE is_online=1") as c:
            return await c.fetchall()

    async def get_all_players(self):
        async with self.conn.execute(
            "SELECT * FROM players ORDER BY level DESC, ttl ASC") as c:
            return await c.fetchall()

    async def set_online(self, pid, nick, channel, userhost=""):
        now = int(time.time())
        await self.conn.execute(
            "UPDATE players SET is_online=1,current_nick=?,channel=?,online_since=?,last_login=?,userhost=? WHERE id=?",
            (nick,channel,now,now,userhost,pid))
        await self.conn.commit()

    async def set_offline(self, pid):
        await self.conn.execute(
            "UPDATE players SET is_online=0,current_nick=NULL WHERE id=?", (pid,))
        await self.conn.commit()

    async def mark_all_offline(self, network):
        await self.conn.execute(
            "UPDATE players SET is_online=0 WHERE network=?", (network,))
        await self.conn.commit()

    async def get_previously_online(self, network):
        async with self.conn.execute(
            "SELECT * FROM players WHERE network=? AND is_online=1 AND userhost IS NOT NULL AND userhost!=''",
            (network,)
        ) as c: return await c.fetchall()

    async def update_nick(self, pid, nick):
        await self.conn.execute("UPDATE players SET current_nick=? WHERE id=?", (nick,pid))
        await self.conn.commit()

    async def update_position(self, pid, x, y):
        await self.conn.execute("UPDATE players SET pos_x=?,pos_y=? WHERE id=?", (x,y,pid))

    async def update_ttl(self, pid, ttl):
        await self.conn.execute("UPDATE players SET ttl=? WHERE id=?", (max(0,ttl),pid))

    async def add_penalty(self, pid, seconds, col=None):
        """Add seconds to TTL. If col is given (e.g. 'pen_mesg'), also update that counter."""
        if col and col in ("pen_mesg","pen_nick","pen_part","pen_kick",
                           "pen_quit","pen_quest","pen_logout"):
            await self.conn.execute(
                f"UPDATE players SET ttl=ttl+?, {col}={col}+? WHERE id=?",
                (max(0,seconds), max(0,seconds), pid))
        else:
            await self.conn.execute(
                "UPDATE players SET ttl=ttl+? WHERE id=?", (max(0,seconds),pid))
        await self.conn.commit()

    async def level_up(self, pid, new_level, new_ttl):
        await self.conn.execute(
            "UPDATE players SET level=?,ttl=?,next_ttl=? WHERE id=?",
            (new_level,new_ttl,new_ttl,pid))
        await self.conn.commit()

    # ── Items ─────────────────────────────────────────────────────────────────

    async def get_items(self, pid):
        async with self.conn.execute(
            "SELECT * FROM items WHERE player_id=? ORDER BY slot", (pid,)) as c:
            return await c.fetchall()

    async def get_item_sum(self, pid) -> int:
        async with self.conn.execute(
            "SELECT COALESCE(SUM(level),0) as t FROM items WHERE player_id=?", (pid,)) as c:
            row = await c.fetchone()
            return row["t"] if row else 0

    async def get_highest_item_sum(self) -> int:
        async with self.conn.execute(
            "SELECT COALESCE(MAX(s.t),0) as m FROM (SELECT SUM(level) as t FROM items GROUP BY player_id) s"
        ) as c:
            row = await c.fetchone()
            return row["m"] if row else 0

    async def set_item(self, pid, slot, level, name=None, is_unique=False):
        await self.conn.execute(
            "UPDATE items SET level=?,name=?,is_unique=? WHERE player_id=? AND slot=?",
            (level,name,int(is_unique),pid,slot))
        await self.conn.commit()

    async def steal_item(self, winner_id, loser_id):
        w = {r["slot"]:r for r in await self.get_items(winner_id)}
        l = {r["slot"]:r for r in await self.get_items(loser_id)}
        candidates = [s for s in ITEM_SLOTS if l[s]["level"] > w[s]["level"]]
        if not candidates: return None
        slot  = random.choice(candidates)
        w_lvl = w[slot]["level"]
        l_lvl = l[slot]["level"]
        await self.conn.execute(
            "UPDATE items SET level=?,name=NULL,is_unique=0 WHERE player_id=? AND slot=?",
            (l_lvl,winner_id,slot))
        await self.conn.execute(
            "UPDATE items SET level=?,name=NULL,is_unique=0 WHERE player_id=? AND slot=?",
            (w_lvl,loser_id,slot))
        await self.conn.commit()
        return slot, l_lvl, w_lvl

    async def modify_item_level(self, pid, slot, delta_pct):
        async with self.conn.execute(
            "SELECT level FROM items WHERE player_id=? AND slot=?", (pid,slot)) as c:
            row = await c.fetchone()
        if not row or row["level"] == 0: return
        await self.conn.execute(
            "UPDATE items SET level=? WHERE player_id=? AND slot=?",
            (max(0, round(row["level"]*(1+delta_pct))), pid, slot))
        await self.conn.commit()

    # ── Events ────────────────────────────────────────────────────────────────

    async def log_event(self, event_type, message, p1=None, p2=None):
        await self.conn.execute(
            "INSERT INTO events(event_type,message,player1_id,player2_id) VALUES(?,?,?,?)",
            (event_type,message,p1,p2))
        await self.conn.commit()

    async def get_recent_events(self, limit=50):
        async with self.conn.execute(
            "SELECT * FROM events ORDER BY created_at DESC LIMIT ?", (limit,)) as c:
            return await c.fetchall()

    # ── Admin ─────────────────────────────────────────────────────────────────

    async def change_password(self, pid, pw):
        await self.conn.execute(
            "UPDATE players SET password_hash=? WHERE id=?", (self.hash_password(pw),pid))
        await self.conn.commit()

    async def set_alignment(self, pid, alignment_char):
        await self.conn.execute(
            "UPDATE players SET alignment=? WHERE id=?", (alignment_char,pid))
        await self.conn.commit()

    async def delete_player(self, pid):
        await self.conn.execute("DELETE FROM players WHERE id=?", (pid,))
        await self.conn.commit()

    async def delete_old_accounts(self, days) -> int:
        cutoff = int(time.time()) - int(days*86400)
        async with self.conn.execute(
            "SELECT COUNT(*) as n FROM players WHERE is_online=0 AND last_login<?", (cutoff,)) as c:
            row = await c.fetchone()
            count = row["n"] if row else 0
        await self.conn.execute(
            "DELETE FROM players WHERE is_online=0 AND last_login<?", (cutoff,))
        await self.conn.commit()
        return count

    async def set_admin(self, username, is_admin):
        await self.conn.execute(
            "UPDATE players SET is_admin=? WHERE username=?", (int(is_admin),username))
        await self.conn.commit()

    async def update_username(self, pid, new_username):
        await self.conn.execute(
            "UPDATE players SET username=? WHERE id=?", (new_username, pid))
        await self.conn.commit()

    async def update_class(self, pid, new_class):
        await self.conn.execute(
            "UPDATE players SET class=? WHERE id=?", (new_class,pid))
        await self.conn.commit()

    async def commit(self):
        """Explicit commit — call after bulk updates."""
        await self.conn.commit()

    async def get_player_any_network(self, username: str):
        """Look up a player by username across all networks (global uniqueness)."""
        async with self.conn.execute(
            "SELECT * FROM players WHERE username=? COLLATE NOCASE LIMIT 1",
            (username,)
        ) as c:
            return await c.fetchone()
