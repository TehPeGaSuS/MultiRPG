# pylint: disable=E1136
"""engine/game_engine.py — Core IdleRPG game logic."""
import logging, random, time
from dataclasses import dataclass
from typing import Optional
from db.database import Database, ITEM_SLOTS

log = logging.getLogger(__name__)

MAP_X, MAP_Y         = 500, 500
RP_BASE, RP_STEP     = 600, 1.16
RP_PEN_STEP          = 1.14   # used only for penalty calculation


def base_ttl(level: int) -> int:
    if level > 60:
        return int(RP_BASE * (RP_STEP ** 60) + 86400 * (level - 60))
    return int(RP_BASE * (RP_STEP ** level))


def calc_penalty(event: str, level: int, msg_len: int = 0, limit_pen: int = 0) -> int:
    base = {"nick": 30, "part": 200, "quit": 20,
            "logout": 20, "kick": 250}.get(event, msg_len)
    pen  = int(base * (RP_PEN_STEP ** level))
    return min(pen, limit_pen) if limit_pen else pen


# ── Broadcasts ────────────────────────────────────────────────────────────────
@dataclass
class Broadcast:
    scope:   str            # 'all' | 'network' | 'notice'
    network: Optional[str]
    message: str
    nick:    Optional[str] = None

def broadcast_all(msg: str)                        -> Broadcast: return Broadcast("all",    None, msg)
def broadcast_net(net: str, msg: str)              -> Broadcast: return Broadcast("network", net,  msg)
def broadcast_notice(net, nick, msg)               -> Broadcast: return Broadcast("notice",  net,  msg, nick)

# ── Formatting ────────────────────────────────────────────────────────────────
def tag(p) -> str:
    return f"{p['username']}@{p['network']} [{p['pos_x']}/{p['pos_y']}]"

def utag(p) -> str:
    """username@network — used in all channel-visible broadcast messages."""
    return f"{p['username']}@{p['network']}"

def fmt_time(seconds: int) -> str:
    s = abs(int(seconds))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{d} day{'s' if d != 1 else ''}, {h:02d}:{m:02d}:{s:02d}"

# ── Items ─────────────────────────────────────────────────────────────────────
ITEM_SLOT_NAMES = [
    "ring", "amulet", "charm", "weapon", "helm",
    "tunic", "pair of gloves", "shield", "set of leggings", "pair of boots",
]
UNIQUE_ITEMS = [
    ("Mattt's Omniscience Grand Crown",       "helm",           50,  74, 25),
    ("Juliet's Glorious Ring of Sparkliness", "ring",           50,  74, 25),
    ("Res0's Protectorate Plate Mail",        "tunic",          75,  99, 30),
    ("Dwyn's Storm Magic Amulet",             "amulet",        100, 124, 35),
    ("Jotun's Fury Colossal Sword",           "weapon",        150, 174, 40),
    ("Drdink's Cane of Blind Rage",           "weapon",        175, 200, 45),
    ("Mrquick's Magical Boots of Swiftness",  "pair of boots", 250, 300, 48),
    ("Jeff's Cluehammer of Doom",             "weapon",        300, 350, 52),
]
UNIQUE_MSGS = {
    "Mattt's Omniscience Grand Crown":
        "The light of the gods shines down! You found the level {lvl} Mattt's Omniscience Grand Crown! Your enemies fall before you as you anticipate their every move.",
    "Juliet's Glorious Ring of Sparkliness":
        "The light of the gods shines down! You found the level {lvl} Juliet's Glorious Ring of Sparkliness! Your enemies are blinded by its glory and their greed.",
    "Res0's Protectorate Plate Mail":
        "The light of the gods shines down! You found the level {lvl} Res0's Protectorate Plate Mail! Your enemies cower as their attacks have no effect.",
    "Dwyn's Storm Magic Amulet":
        "The light of the gods shines down! You found the level {lvl} Dwyn's Storm Magic Amulet! Your enemies are swept away by elemental fury.",
    "Jotun's Fury Colossal Sword":
        "The light of the gods shines down! You found the level {lvl} Jotun's Fury Colossal Sword! Your enemies are crushed by the blow.",
    "Drdink's Cane of Blind Rage":
        "The light of the gods shines down! You found the level {lvl} Drdink's Cane of Blind Rage! You blindly swing, hitting stuff.",
    "Mrquick's Magical Boots of Swiftness":
        "The light of the gods shines down! You found the level {lvl} Mrquick's Magical Boots of Swiftness! Your enemies choke on your dust.",
    "Jeff's Cluehammer of Doom":
        "The light of the gods shines down! You found the level {lvl} Jeff's Cluehammer of Doom! Your enemies gain sudden clarity... as you relieve them of it.",
}

def roll_item(player_level: int) -> tuple:
    if player_level >= 25:
        for name, slot, min_l, max_l, req in UNIQUE_ITEMS:
            if player_level >= req and random.random() < 1/40:
                return slot, random.randint(min_l, max_l - 1), name, True
    max_level = int(player_level * 1.5)
    level = 1
    for num in range(1, max_level + 1):
        if random.random() < 1 / (1.4 ** (num / 4)):
            level = num
    return random.choice(ITEM_SLOT_NAMES), level, None, False

def eff_sum(raw: int, alignment: str) -> int:
    if alignment == "g": return int(raw * 1.1)
    if alignment == "e": return int(raw * 0.9)
    return raw

# ── Battle ────────────────────────────────────────────────────────────────────
async def resolve_battle(db: Database, challenger, opponent,
                         collision: bool = False) -> list:
    # Always re-fetch for current TTL
    c = await db.get_player_by_id(challenger["id"]) or challenger
    o = await db.get_player_by_id(opponent["id"])   or opponent
    if c["id"] == o["id"]:
        return []   # never battle yourself

    c_sum  = eff_sum(await db.get_item_sum(c["id"]), c["alignment"])
    o_sum  = eff_sum(await db.get_item_sum(o["id"]), o["alignment"])
    c_roll = random.randint(0, max(c_sum - 1, 0))
    o_roll = random.randint(0, max(o_sum - 1, 0))
    won    = c_roll >= o_roll
    winner, loser = (c, o) if won else (o, c)
    msgs   = []

    if collision:
        verb = (f"{tag(challenger)} has come upon {tag(opponent)} and "
                f"{'taken them in' if won else 'been defeated in'} combat!")
    else:
        verb = (f"{tag(challenger)} has challenged {tag(opponent)} in "
                f"combat and {'won' if won else 'lost'}!")

    if won:
        gain    = int(max(loser["level"] / 4, 7) / 100 * winner["ttl"])
        new_ttl = max(0, winner["ttl"] - gain)
        await db.update_ttl(winner["id"], new_ttl)
        msg = f"{verb} {fmt_time(gain)} is removed from {utag(winner)}'s clock."
        msgs += [broadcast_all(msg),
                 broadcast_all(f"{utag(winner)} reaches next level in {fmt_time(new_ttl)}.")]
        cs = 50 if c["alignment"] == "g" else 20 if c["alignment"] == "e" else 35
        if random.randint(0, cs - 1) < 1:
            crit = int(((5 + random.randint(0, 19)) / 100) * loser["ttl"])
            await db.add_penalty(loser["id"], crit)
            cm = (f"{utag(winner)} dealt {utag(loser)} a Critical Strike! "
                  f"{fmt_time(crit)} added to {utag(loser)}'s clock.")
            msgs.append(broadcast_all(cm))
            await db.log_event("critical", cm, winner["id"], loser["id"])
        elif random.randint(0, 24) < 1 and winner["level"] > 19:
            result = await db.steal_item(winner["id"], loser["id"])
            if result:
                slot, sl, ol = result
                sm = (f"In battle, {utag(loser)} dropped their level {sl} {slot}! "
                      f"{utag(winner)} picks it up, tossing their old level {ol} {slot}.")
                msgs.append(broadcast_all(sm))
                await db.log_event("steal", sm, winner["id"], loser["id"])
    else:
        pen = int(max(loser["level"] / 7, 7) / 100 * c["ttl"])
        await db.add_penalty(c["id"], pen)
        msg = f"{verb} {fmt_time(pen)} is added to {utag(c)}'s clock."
        msgs += [broadcast_all(msg),
                 broadcast_all(f"{utag(c)} reaches next level in {fmt_time(c['ttl'] + pen)}.")]

    await db.log_event("battle", msg, c["id"], o["id"])
    await db.commit()
    return msgs


# ── Game Engine ───────────────────────────────────────────────────────────────
class GameEngine:
    def __init__(self, db: Database, self_clock: int = 5, limit_pen: int = 0):
        self.db         = db
        self.self_clock = self_clock
        self.limit_pen  = limit_pen
        self._lasttime  = 0          # 0 = not joined yet
        self._rpreport  = 0
        self.paused     = False      # PAUSE command stops tick processing
        self.silent     = 0          # 0=all, 1=no chan, 2=no pm, 3=no both
        self._quest = {
            "questers": [], "type": 1, "stage": 1,
            "p1": None, "p2": None,
            "qtime": int(time.time()) + random.randint(3600, 7200),
            "text": "",
        }

    def mark_joined(self):
        self._lasttime = int(time.time())
        log.info(f"Engine: mark_joined at {self._lasttime}")

    # ── IRC events ────────────────────────────────────────────────────────────

    async def on_login(self, username, network, nick, channel,
                       password, userhost="") -> tuple:
        p = await self.db.get_player(username, network)
        if not p:
            # Try any network — usernames are globally unique
            p = await self.db.get_player_any_network(username)
            if not p:
                return False, "No such account. Use REGISTER to create one."
            # Allow login from any network
        if p["password_hash"] != self.db.hash_password(password):
            return False, "Wrong password."
        if p["is_online"]:
            return False, "You are already logged in."
        await self.db.set_online(p["id"], nick, channel, userhost)
        return True, (
            f"Logon successful. {username}, the level {p['level']} "
            f"{p['class']}. Next level in {fmt_time(p['ttl'])}."
        )

    async def on_register(self, username, network, nick, channel,
                          password, char_class, userhost="") -> tuple:
        """Returns (ok, private_msg, [Broadcast]). All players start neutral."""
        if not (1 <= len(username) <= 16):
            return False, "Character names must be 1-16 chars.", []
        if username.startswith("#"):
            return False, "Character names may not begin with #.", []
        if len(char_class) > 30:
            return False, "Character classes must be < 31 chars.", []

        # Global uniqueness — username must be unique across ALL networks
        existing = await self.db.get_player_any_network(username)
        if existing:
            return False, f"Sorry, the name {username} is already taken.", []

        pid = await self.db.register_player(username, network, password, char_class)
        if pid is None:
            return False, "Sorry, that character name is already in use.", []

        await self.db.set_online(pid, nick, channel, userhost)
        priv = (
            f"Success! Account {username} created. You have "
            f"{fmt_time(RP_BASE)} until level 1. "
            "NOTE: The point of the game is to idle. Talking, parting, "
            "quitting, and nick changes all penalize you!"
        )
        # Issue #4: Welcome message includes nick@network
        chan = broadcast_all(
            f"Welcome {nick}@{network}'s new player {username}, the "
            f"{char_class}! Next level in {fmt_time(RP_BASE)}."
        )
        return True, priv, [chan]

    async def on_logout(self, nick, network) -> list:
        p = await self.db.get_player_by_nick(nick, network)
        if not p or not p["is_online"]: return []
        pen = calc_penalty("logout", p["level"], limit_pen=self.limit_pen)
        await self.db.add_penalty(p["id"], pen, "pen_logout")
        await self.db.set_offline(p["id"])
        return [broadcast_notice(network, nick,
            f"Penalty of {fmt_time(pen)} added to your timer for LOGOUT."
        )] + await self._qpc(p)

    async def on_nick_change(self, old_nick, new_nick, network) -> list:
        p = await self.db.get_player_by_nick(old_nick, network)
        if not p or not p["is_online"]: return []
        pen = calc_penalty("nick", p["level"], limit_pen=self.limit_pen)
        await self.db.add_penalty(p["id"], pen, "pen_nick")
        await self.db.update_nick(p["id"], new_nick)
        return [broadcast_notice(network, new_nick,
            f"Penalty of {fmt_time(pen)} added to your timer for nick change."
        )] + await self._qpc(p)

    async def on_part(self, nick, network) -> list:
        p = await self.db.get_player_by_nick(nick, network)
        if not p or not p["is_online"]: return []
        pen = calc_penalty("part", p["level"], limit_pen=self.limit_pen)
        await self.db.add_penalty(p["id"], pen, "pen_part")
        await self.db.set_offline(p["id"])
        return [broadcast_net(network,
            f"{utag(p)} has parted. Penalty: {fmt_time(pen)}."
        )] + await self._qpc(p)

    async def on_quit(self, nick, network) -> list:
        p = await self.db.get_player_by_nick(nick, network)
        if not p or not p["is_online"]: return []
        pen = calc_penalty("quit", p["level"], limit_pen=self.limit_pen)
        await self.db.add_penalty(p["id"], pen, "pen_quit")
        await self.db.set_offline(p["id"])
        return await self._qpc(p)

    async def on_kick(self, nick, network) -> list:
        p = await self.db.get_player_by_nick(nick, network)
        if not p or not p["is_online"]: return []
        pen = calc_penalty("kick", p["level"], limit_pen=self.limit_pen)
        await self.db.add_penalty(p["id"], pen, "pen_kick")
        await self.db.set_offline(p["id"])
        return [broadcast_net(network,
            f"{utag(p)} was kicked! Penalty: {fmt_time(pen)}."
        )] + await self._qpc(p)

    async def on_message(self, nick, network, message) -> list:
        p = await self.db.get_player_by_nick(nick, network)
        if not p or not p["is_online"]: return []
        pen = calc_penalty("privmsg", p["level"],
                           msg_len=len(message), limit_pen=self.limit_pen)
        await self.db.add_penalty(p["id"], pen, "pen_mesg")
        return [broadcast_notice(network, nick,
            f"Penalty of {fmt_time(pen)} added to your timer for talking."
        )] + await self._qpc(p)

    async def on_notice(self, nick, network, message) -> list:
        return await self.on_message(nick, network, message)

    # ── Player commands ───────────────────────────────────────────────────────

    async def cmd_status(self, nick, network, target=None) -> str:
        if target:
            p = await self.db.get_player_any_network(target)
        else:
            p = await self.db.get_player_by_nick(nick, network)
        if not p:
            return "No such user." if target else "You are not logged in."
        amap = {"g": "good", "e": "evil", "n": "neutral"}
        isum = await self.db.get_item_sum(p["id"])
        return (
            f"{p['username']}@{p['network']} | "
            f"Level {p['level']} {p['class']} ({amap.get(p['alignment'],'neutral')}) | "
            f"{'Online' if p['is_online'] else 'Offline'} | "
            f"TTL: {fmt_time(p['ttl'])} | "
            f"Pos: [{p['pos_x']}/{p['pos_y']}] | Items: {isum}"
        )

    async def cmd_whoami(self, nick, network) -> str:
        p = await self.db.get_player_by_nick(nick, network)
        if not p:
            return "You are not logged in."
        return (
            f"You are {p['username']}, the level {p['level']} {p['class']}. "
            f"Next level in {fmt_time(p['ttl'])}."
        )

    async def cmd_quest(self) -> str:
        q = self._quest
        if not q["questers"]:
            return "There is no active quest."
        names = ", ".join(f"{x['username']}@{x['network']}" for x in q["questers"])
        if q["type"] == 1:
            return (f"{names} are questing to {q['text']}. "
                    f"Ends in {fmt_time(q['qtime'] - time.time())}.")
        t = q["p1"] if q["stage"] == 1 else q["p2"]
        return (f"{names} are questing to {q['text']}. "
                f"Must reach [{q['p1'][0]},{q['p1'][1]}] then "
                f"[{q['p2'][0]},{q['p2'][1]}]. Heading to [{t[0]},{t[1]}].")

    async def cmd_newpass(self, nick, network, pw) -> str:
        p = await self.db.get_player_by_nick(nick, network)
        if not p: return "You are not logged in."
        await self.db.change_password(p["id"], pw)
        return "Password changed."

    async def cmd_align(self, nick, network, alignment) -> tuple:
        if alignment not in ("good", "neutral", "evil"):
            return "Usage: ALIGN <good|neutral|evil>", []
        p = await self.db.get_player_by_nick(nick, network)
        if not p: return "You are not logged in.", []
        await self.db.set_alignment(p["id"], alignment[0])
        return (f"Your alignment is now {alignment}.",
                [broadcast_all(f"{utag(p)} changed alignment to: {alignment}.")])

    async def cmd_removeme(self, nick, network) -> tuple:
        p = await self.db.get_player_by_nick(nick, network)
        if not p: return "You are not logged in.", []
        await self.db.delete_player(p["id"])
        return (f"Account {p['username']} removed.",
                [broadcast_all(f"{nick} removed their account, "
                               f"{utag(p)}, the {p['class']}.")])

    async def cmd_push(self, admin_nick, network, target, seconds) -> tuple:
        p = await self.db.get_player_any_network(target)
        if not p: return f"No such username {target}.", []
        seconds = min(seconds, p["ttl"])
        new_ttl = max(0, p["ttl"] - seconds)
        await self.db.update_ttl(p["id"], new_ttl)
        await self.db.commit()
        msg = (f"{admin_nick} pushed {utag(p)} {fmt_time(seconds)} "
               f"toward level {p['level'] + 1}. Next level in {fmt_time(new_ttl)}.")
        return "Done.", [broadcast_all(msg)]

    async def cmd_hog(self, admin_nick, network) -> list:
        return await self._hand_of_god(await self.db.get_online_players())

    async def cmd_delold(self, days: float) -> str:
        n = await self.db.delete_old_accounts(days)
        return f"{n} accounts removed."

    async def cmd_chpass(self, target, pw) -> str:
        p = await self.db.get_player_any_network(target)
        if not p: return f"No such username {target}."
        await self.db.change_password(p["id"], pw)
        return f"Password for {target} changed."

    async def cmd_chclass(self, target, new_class) -> str:
        p = await self.db.get_player_any_network(target)
        if not p: return f"No such username {target}."
        await self.db.update_class(p["id"], new_class)
        return f"Class for {target} changed to {new_class}."

    async def cmd_chuser(self, target, new_name) -> str:
        if not (1 <= len(new_name) <= 16):
            return "New name must be 1-16 characters."
        p = await self.db.get_player_any_network(target)
        if not p: return f"No such username {target}."
        existing = await self.db.get_player_any_network(new_name)
        if existing: return f"The name {new_name} is already taken."
        await self.db.update_username(p["id"], new_name)
        return f"Username changed from {target} to {new_name}."

    def cmd_pause(self) -> str:
        self.paused = not self.paused
        return f"Game {'PAUSED — tick loop suspended.' if self.paused else 'RESUMED — tick loop running.'}"

    def cmd_silentmode(self, mode: int) -> str:
        self.silent = mode
        labels = {
            0: "all messages enabled",
            1: "channel messages disabled",
            2: "private messages disabled",
            3: "all messages disabled",
        }
        return f"Silent mode {mode}: {labels.get(mode, 'unknown mode')}."

    # ── Quest penalty check ───────────────────────────────────────────────────

    async def _qpc(self, player) -> list:
        q = self._quest
        if not q["questers"]: return []
        if not any(x["id"] == player["id"] for x in q["questers"]): return []
        q["questers"] = []
        q["qtime"]    = int(time.time()) + 43200
        for p in await self.db.get_online_players():
            await self.db.add_penalty(p["id"], int(15 * (RP_PEN_STEP ** p["level"])), "pen_quest")
        await self.db.commit()
        return [broadcast_all(
            f"{utag(player)}'s actions have brought the wrath of the gods "
            "upon the realm. Hell rains down upon you all."
        )]

    # ── Main tick ─────────────────────────────────────────────────────────────

    async def tick(self) -> list:
        if not self._lasttime:
            return []   # bot hasn't joined channel yet
        if self.paused:
            return []   # PAUSE mode active

        msgs    = []
        online  = await self.db.get_online_players()
        if not online:
            return []

        n       = len(online)
        n_evil  = sum(1 for p in online if p["alignment"] == "e")
        n_good  = sum(1 for p in online if p["alignment"] == "g")
        cur     = int(time.time())
        elapsed = cur - self._lasttime
        sc      = self.self_clock

        if elapsed <= 0:
            return []

        log.debug(f"Tick: {n} online, elapsed={elapsed}s")

        # ── TTL countdown ─────────────────────────────────────────────────────
        levelled_ids = set()
        for p in online:
            new_ttl = p["ttl"] - elapsed
            if new_ttl < 1:
                levelled_ids.add(p["id"])
                # Zero out TTL so next tick doesn't see stale value
                await self.db.update_ttl(p["id"], 0)
            else:
                await self.db.update_ttl(p["id"], new_ttl)

        # Commit TTL changes immediately so DB is never stale
        await self.db.commit()

        # Process level-ups after committing TTL changes
        for pid in levelled_ids:
            lu_msgs = await self._do_level_up(pid)
            log.info(f"Level-up for pid={pid} produced {len(lu_msgs)} broadcasts")
            msgs.extend(lu_msgs)

        # ── Daily random events ───────────────────────────────────────────────
        try:
            if random.random() < n      / ((20 * 86400) / sc): msgs.extend(await self._hand_of_god(online))
            if random.random() < n      / ((24 * 86400) / sc): msgs.extend(await self._team_battle(online))
            if random.random() < n      / ((8  * 86400) / sc): msgs.extend(await self._calamity(online))
            if random.random() < n      / ((4  * 86400) / sc): msgs.extend(await self._godsend(online))
            if random.random() < n_evil / ((8  * 86400) / sc): msgs.extend(await self._evilness(online))
            if random.random() < n_good / ((12 * 86400) / sc): msgs.extend(await self._goodness(online))
        except Exception as e:
            log.error(f"Daily event error: {e}", exc_info=True)

        # ── Movement ──────────────────────────────────────────────────────────
        try:
            msgs.extend(await self._move_players(online))
        except Exception as e:
            log.error(f"Movement error: {e}", exc_info=True)

        # ── Periodic announcements ────────────────────────────────────────────
        self._rpreport += sc
        if self._rpreport % 36000 == 0: msgs.extend(await self._announce_top())
        if self._rpreport % 1200  == 0: msgs.extend(await self._high_level_battle(online))

        # ── Quest ─────────────────────────────────────────────────────────────
        try:
            msgs.extend(await self._check_quest(online))
        except Exception as e:
            log.error(f"Quest error: {e}", exc_info=True)

        await self.db.commit()
        self._lasttime = cur
        if msgs:
            log.info(f"Tick returning {len(msgs)} broadcasts")
        return msgs

    # ── Level up ──────────────────────────────────────────────────────────────

    async def _do_level_up(self, player_id: int) -> list:
        """Re-fetches player from DB so TTL is always current."""
        p = await self.db.get_player_by_id(player_id)
        if not p:
            return []
        new_level = p["level"] + 1
        new_ttl   = base_ttl(new_level)
        await self.db.level_up(p["id"], new_level, new_ttl)

        # Issue #5: Level-up message format
        msgs = [broadcast_all(
            f"{utag(p)}, the {p['class']}, has attained "
            f"level {new_level}! Next level in {fmt_time(new_ttl)}."
        )]
        log.info(f"Level up: {p['username']} -> level {new_level}")
        await self.db.log_event("levelup",
            f"{p['username']} reached level {new_level}", p["id"])

        msgs.extend(await self._find_item(p, new_level))

        # Battle on level-up
        online    = await self.db.get_online_players()
        opponents = [x for x in online if x["id"] != p["id"]]
        if opponents and (new_level >= 25 or random.random() < 0.25):
            fresh = await self.db.get_player_by_id(p["id"])
            if fresh:
                msgs.extend(await resolve_battle(
                    self.db, fresh, random.choice(opponents)))
        return msgs

    async def _find_item(self, player, level) -> list:
        slot, item_lvl, uname, is_unique = roll_item(level)
        items   = {r["slot"]: dict(r) for r in await self.db.get_items(player["id"])}
        cur_lvl = items.get(slot, {}).get("level", 0)
        nick    = player.get("current_nick") or player["username"]
        net     = player["network"]
        if is_unique and item_lvl > cur_lvl:
            await self.db.set_item(player["id"], slot, item_lvl, uname, True)
            return [broadcast_notice(net, nick,
                UNIQUE_MSGS.get(uname, "").format(lvl=item_lvl))]
        elif item_lvl > cur_lvl:
            await self.db.set_item(player["id"], slot, item_lvl)
            return [broadcast_notice(net, nick,
                f"You found a level {item_lvl} {slot}! "
                f"Your current {slot} is only level {cur_lvl}, "
                f"so it seems Luck is with you!")]
        else:
            return [broadcast_notice(net, nick,
                f"You found a level {item_lvl} {slot}. "
                f"Your current {slot} is level {cur_lvl}, "
                f"so it seems Luck is against you. You toss the {slot}.")]

    # ── Movement ──────────────────────────────────────────────────────────────

    async def _move_players(self, online) -> list:
        msgs = []
        n    = len(online)
        q    = self._quest
        qids = {x["id"] for x in q["questers"]}

        # Build state once OUTSIDE the loop — updates carry forward each step
        player_state = {p["id"]: dict(p) for p in online}

        for _ in range(self.self_clock):
            positions = {}

            for p in online:
                pid = p["id"]
                x   = player_state[pid]["pos_x"]
                y   = player_state[pid]["pos_y"]

                if pid in qids and q["type"] == 2 and random.random() < 0.01:
                    t  = q["p1"] if q["stage"] == 1 else q["p2"]
                    dx = 1 if x < t[0] else -1 if x > t[0] else 0
                    dy = 1 if y < t[1] else -1 if y > t[1] else 0
                else:
                    dx = random.randint(-1, 1)
                    dy = random.randint(-1, 1)

                nx = (x + dx) % MAP_X
                ny = (y + dy) % MAP_Y
                player_state[pid]["pos_x"] = nx
                player_state[pid]["pos_y"] = ny
                await self.db.update_position(pid, nx, ny)

                key = (nx, ny)
                if key in positions and not positions[key]["battled"]:
                    # Only battle if it's a different player
                    other_pid = positions[key]["pid"]
                    if other_pid != pid and n > 1 and random.random() < 1 / n:
                        positions[key]["battled"] = True
                        msgs.extend(await resolve_battle(
                            self.db,
                            player_state[pid],
                            player_state[other_pid],
                            collision=True))
                else:
                    positions[key] = {"pid": pid, "battled": False}

        # Grid quest completion check
        if q["questers"] and q["type"] == 2:
            fresh  = {p["id"]: p for p in await self.db.get_online_players()}
            target = q["p1"] if q["stage"] == 1 else q["p2"]
            if all(
                fresh.get(x["id"], {}).get("pos_x") == target[0] and
                fresh.get(x["id"], {}).get("pos_y") == target[1]
                for x in q["questers"]
            ):
                if q["stage"] == 1:
                    q["stage"] = 2
                else:
                    names = ", ".join(
                        f"{x['username']}@{x['network']}" for x in q["questers"])
                    msg   = (f"{names} have completed their journey! "
                             "25% of their burden is eliminated.")
                    msgs.append(broadcast_all(msg))
                    for x in q["questers"]:
                        fp = fresh.get(x["id"])
                        if fp:
                            await self.db.update_ttl(x["id"], int(fp["ttl"] * 0.75))
                    q["questers"] = []
                    q["qtime"]    = int(time.time()) + 3600
                    await self.db.log_event("quest", msg)
        return msgs

    # ── Quest ─────────────────────────────────────────────────────────────────

    async def _check_quest(self, online) -> list:
        q, now = self._quest, int(time.time())
        if not q["questers"] and now > q["qtime"]:
            return await self._start_quest(online)
        if q["questers"] and q["type"] == 1 and now > q["qtime"]:
            names = ", ".join(
                f"{x['username']}@{x['network']}" for x in q["questers"])
            msg   = (f"{names} have blessed the realm by completing their quest! "
                     "25% of their burden is eliminated.")
            for x in q["questers"]:
                fp = await self.db.get_player_by_id(x["id"])
                if fp:
                    await self.db.update_ttl(x["id"], int(fp["ttl"] * 0.75))
            q["questers"] = []
            q["qtime"]    = now + 21600
            await self.db.log_event("quest", msg)
            return [broadcast_all(msg)]
        return []

    async def _start_quest(self, online) -> list:
        now      = int(time.time())
        eligible = [p for p in online
                    if p["level"] > 39
                    and p["online_since"]
                    and (now - p["online_since"]) >= 36000]
        if len(eligible) < 4:
            return []
        questers = random.sample(eligible, 4)
        self._quest["questers"] = questers
        names = ", ".join(f"{q['username']}@{q['network']}" for q in questers)
        QUESTS = [
            ("Q1", "slay the dragon terrorising the realm"),
            ("Q1", "retrieve the sacred chalice from the dark temple"),
            ("Q1", "escort the princess safely across the mountains"),
            ("Q2", "cleanse the Temple of the Shadow God"),
            ("Q2", "recover the Lost Tome of Forbidden Knowledge"),
        ]
        qtype, text = random.choice(QUESTS)
        self._quest["text"] = text
        if qtype == "Q1":
            self._quest["type"]  = 1
            dur = random.randint(43200, 86400)
            self._quest["qtime"] = now + dur
            msg = (f"{names} have been chosen by the gods to {text}. "
                   f"Quest ends in {fmt_time(dur)}.")
        else:
            self._quest["type"]  = 2
            self._quest["stage"] = 1
            p1 = (random.randint(0, MAP_X - 1), random.randint(0, MAP_Y - 1))
            p2 = (random.randint(0, MAP_X - 1), random.randint(0, MAP_Y - 1))
            self._quest["p1"] = p1
            self._quest["p2"] = p2
            msg = (f"{names} have been chosen by the gods to {text}. "
                   f"First reach [{p1[0]},{p1[1]}], then [{p2[0]},{p2[1]}].")
        await self.db.log_event("quest", msg)
        return [broadcast_all(msg)]

    # ── Daily events ──────────────────────────────────────────────────────────

    async def _hand_of_god(self, online) -> list:
        if not online: return []
        p       = random.choice(online)
        helping = random.randint(0, 4) > 0
        pct     = (5 + random.randint(0, 70)) / 100
        t       = int(pct * p["ttl"])
        if helping:
            await self.db.update_ttl(p["id"], max(0, p["ttl"] - t))
            msg = (f"Verily I say unto thee, the Heavens have burst forth, and the "
                   f"blessed hand of God carried {utag(p)} {fmt_time(t)} "
                   f"toward level {p['level'] + 1}.")
        else:
            await self.db.add_penalty(p["id"], t)
            msg = (f"Thereupon He stretched out His little finger among them and "
                   f"consumed {utag(p)} with fire, slowing the heathen "
                   f"{fmt_time(t)} from level {p['level'] + 1}.")
        fp   = await self.db.get_player_by_id(p["id"])
        msgs = [broadcast_all(msg)]
        if fp:
            msgs.append(broadcast_all(
                f"{utag(p)} reaches next level in {fmt_time(fp['ttl'])}."))
        await self.db.log_event("hog", msg, p["id"])
        return msgs

    async def _calamity(self, online) -> list:
        if not online: return []
        p  = random.choice(online)
        IE = {
            "amulet":          f"{utag(p)} fell, chipping their amulet",
            "charm":           f"{utag(p)} dropped their charm in a bog",
            "weapon":          f"{utag(p)} left their weapon out in the rain",
            "tunic":           f"{utag(p)} spilled a shrinking potion on their tunic",
            "shield":          f"{utag(p)}'s shield was scorched by dragon fire",
            "set of leggings": f"{utag(p)} burned a hole in their leggings while ironing",
        }
        if random.random() < 0.1:
            slot = random.choice(list(IE.keys()))
            await self.db.modify_item_level(p["id"], slot, -0.10)
            msg  = f"{IE[slot]}! {utag(p)}'s {slot} loses 10% effectiveness."
            await self.db.log_event("calamity", msg, p["id"])
            return [broadcast_all(msg)]
        pct = (5 + random.randint(0, 7)) / 100
        t   = int(pct * p["ttl"])
        await self.db.add_penalty(p["id"], t)
        TEXTS = [
            f"{utag(p)} tripped over their own feet",
            f"{utag(p)} was startled by a loud noise",
            f"{utag(p)} drank a potion of Extreme Clumsiness by mistake",
            f"{utag(p)} got lost in the Enchanted Woods",
        ]
        fp   = await self.db.get_player_by_id(p["id"])
        msg  = (f"{random.choice(TEXTS)}. This calamity slowed them "
                f"{fmt_time(t)} from level {p['level'] + 1}.")
        msgs = [broadcast_all(msg)]
        if fp:
            msgs.append(broadcast_all(
                f"{utag(p)} reaches next level in {fmt_time(fp['ttl'])}."))
        await self.db.log_event("calamity", msg, p["id"])
        return msgs

    async def _godsend(self, online) -> list:
        if not online: return []
        p  = random.choice(online)
        IE = {
            "amulet":          f"{utag(p)}'s amulet was blessed by a cleric",
            "charm":           f"{utag(p)}'s charm absorbed a bolt of lightning",
            "weapon":          f"{utag(p)} sharpened their weapon",
            "tunic":           f"A magician cast Rigidity on {utag(p)}'s tunic",
            "shield":          f"{utag(p)} reinforced their shield with dragon scales",
            "set of leggings": f"A wizard imbued {utag(p)}'s leggings with Fortitude",
        }
        if random.random() < 0.1:
            slot = random.choice(list(IE.keys()))
            await self.db.modify_item_level(p["id"], slot, +0.10)
            msg  = f"{IE[slot]}! {utag(p)}'s {slot} gains 10% effectiveness."
            await self.db.log_event("godsend", msg, p["id"])
            return [broadcast_all(msg)]
        pct = (5 + random.randint(0, 7)) / 100
        t   = int(pct * p["ttl"])
        await self.db.update_ttl(p["id"], max(0, p["ttl"] - t))
        TEXTS = [
            f"{utag(p)} found a four-leaf clover",
            f"{utag(p)} received a blessing from a wandering priest",
            f"{utag(p)} stumbled upon an enchanted spring",
            f"{utag(p)} was touched by an angel",
        ]
        fp   = await self.db.get_player_by_id(p["id"])
        msg  = (f"{random.choice(TEXTS)}! This godsend accelerated them "
                f"{fmt_time(t)} towards level {p['level'] + 1}.")
        msgs = [broadcast_all(msg)]
        if fp:
            msgs.append(broadcast_all(
                f"{utag(p)} reaches next level in {fmt_time(fp['ttl'])}."))
        await self.db.log_event("godsend", msg, p["id"])
        return msgs

    async def _goodness(self, online) -> list:
        good = [p for p in online if p["alignment"] == "g"]
        if len(good) < 2: return []
        players = random.sample(good, 2)
        gain    = 5 + random.randint(0, 7)
        msg     = (f"{utag(players[0])} and {utag(players[1])} have "
                   f"prayed together. {gain}% of their time is removed.")
        msgs = [broadcast_all(msg)]
        for p in players:
            new_ttl = int(p["ttl"] * (1 - gain / 100))
            await self.db.update_ttl(p["id"], new_ttl)
            msgs.append(broadcast_all(
                f"{utag(p)} reaches next level in {fmt_time(new_ttl)}."))
        await self.db.log_event("godsend", msg)
        return msgs

    async def _evilness(self, online) -> list:
        evil = [p for p in online if p["alignment"] == "e"]
        if not evil: return []
        me = random.choice(evil)
        if random.random() < 0.5:
            good = [p for p in online if p["alignment"] == "g"]
            if not good: return []
            target = random.choice(good)
            result = await self.db.steal_item(me["id"], target["id"])
            if result:
                slot, sl, ol = result
                msg = (f"{utag(me)} stole {utag(target)}'s level "
                       f"{sl} {slot}! Leaves their old level {ol} {slot} behind.")
                await self.db.log_event("steal", msg, me["id"], target["id"])
                return [broadcast_all(msg)]
            return []
        t = int(me["ttl"] * (1 + random.randint(0, 4)) / 100)
        await self.db.add_penalty(me["id"], t)
        msg  = f"{utag(me)} is forsaken by their evil god. {fmt_time(t)} added to their clock."
        fp   = await self.db.get_player_by_id(me["id"])
        msgs = [broadcast_all(msg)]
        if fp:
            msgs.append(broadcast_all(
                f"{utag(me)} reaches next level in {fmt_time(fp['ttl'])}."))
        await self.db.log_event("calamity", msg, me["id"])
        return msgs

    async def _team_battle(self, online) -> list:
        if len(online) < 6: return []
        sample = random.sample(online, 6)
        a, b   = sample[:3], sample[3:]
        sa = sum(eff_sum(await self.db.get_item_sum(p["id"]), p["alignment"]) for p in a)
        sb = sum(eff_sum(await self.db.get_item_sum(p["id"]), p["alignment"]) for p in b)
        ra = random.randint(0, max(sa - 1, 0))
        rb = random.randint(0, max(sb - 1, 0))
        won  = ra >= rb
        gain = int(min(p["ttl"] for p in a) * 0.20)
        msg  = (f"{', '.join(utag(p) for p in a)} [{ra}/{sa}] team battled "
                f"{', '.join(utag(p) for p in b)} [{rb}/{sb}] and "
                f"{'won' if won else 'lost'}! "
                f"{fmt_time(gain)} {'removed from' if won else 'added to'} their clocks.")
        for p in a:
            if won: await self.db.update_ttl(p["id"], max(0, p["ttl"] - gain))
            else:   await self.db.add_penalty(p["id"], gain)
        await self.db.log_event("team_battle", msg)
        return [broadcast_all(msg)]

    async def _high_level_battle(self, online) -> list:
        high = [p for p in online if p["level"] >= 45]
        if not high or len(high) / len(online) <= 0.15: return []
        c      = random.choice(high)
        others = [p for p in online if p["id"] != c["id"]]
        if not others: return []
        return await resolve_battle(self.db, c, random.choice(others))

    async def _announce_top(self) -> list:
        players = await self.db.get_all_players()
        if not players: return []
        msgs = [broadcast_all("Idle RPG Top Players:")]
        for i, p in enumerate(players[:3], 1):
            msgs.append(broadcast_all(
                f"{utag(p)}, the level {p['level']} {p['class']}, "
                f"is #{i}! Next level in {fmt_time(p['ttl'])}."))
        return msgs
