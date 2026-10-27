# pylint: disable=E1136
"""engine/game_engine.py — Core IdleRPG game logic."""
import logging, random, re, time
from dataclasses import dataclass
from typing import Optional
from db.database import Database, ITEM_SLOTS
from engine.locations import LOCATIONS

log = logging.getLogger(__name__)

MAP_X, MAP_Y         = 500, 500
RP_BASE, RP_STEP     = 600, 1.16   # IdleRPG 3.0 defaults: TTL = 600 * 1.16^level
RP_PEN_STEP          = 1.14   # used only for penalty calculation
QUEST_GRACE          = 600    # secs an absent quester gets to come back before leaving the quest


def _sentence(text: str) -> str:
    """Upper-case the first letter only (str.capitalize() lower-cases place names)."""
    return text[:1].upper() + text[1:]


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
    "trinket", "amulet", "idol", "cutlass", "tricorn",
    "coat", "gauntlets", "buckler", "breeches", "sea boots",
]
UNIQUE_ITEMS = [
    ("The Admiral's Grand Tricorn",            "tricorn",        50,  74, 25),
    ("Davy Jones' Cursed Trinket",             "trinket",        50,  74, 25),
    ("The Kraken Hunter's Coat",               "coat",           75,  99, 30),
    ("The Sea Witch's Amulet",                 "amulet",        100, 124, 35),
    ("Blackbeard's Fury Cutlass",              "cutlass",       150, 174, 40),
    ("The Dead Man's Cutlass of Ruin",         "cutlass",       175, 200, 45),
    ("Navigator's Enchanted Sea Boots",        "sea boots",     250, 300, 48),
    ("The Cannon of Doom",                      "cutlass",       300, 350, 52),
]
UNIQUE_MSGS = {
    "The Admiral's Grand Tricorn":
        "The seas part and fortune smiles upon you! You found the level {lvl} Admiral's Grand Tricorn! Your enemies flee at the sight of your authority.",
    "Davy Jones' Cursed Trinket":
        "Something glints in the deep! You found the level {lvl} Davy Jones' Cursed Trinket! The curse of the deep binds your enemies in fear.",
    "The Kraken Hunter's Coat":
        "A coat stitched from kraken hide washes ashore! You found the level {lvl} Kraken Hunter's Coat! Your enemies' blades glance off harmlessly.",
    "The Sea Witch's Amulet":
        "A glowing amulet drifts up from the depths! You found the level {lvl} Sea Witch's Amulet! Your enemies are swept away by the tides of magic.",
    "Blackbeard's Fury Cutlass":
        "A fury-forged blade materialises in your grip! You found the level {lvl} Blackbeard's Fury Cutlass! Your enemies are cut down where they stand.",
    "The Dead Man's Cutlass of Ruin":
        "A spectral blade rises from the deep! You found the level {lvl} Dead Man's Cutlass of Ruin! You swing wildly, hitting everything in sight.",
    "Navigator's Enchanted Sea Boots":
        "Enchanted boots lace themselves onto your feet! You found the level {lvl} Navigator's Enchanted Sea Boots! Your enemies choke on your wake.",
    "The Cannon of Doom":
        "An impossibly large cannon washes up beside you! You found the level {lvl} Cannon of Doom! Your enemies scatter as you light the fuse.",
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
# The bot itself can be picked as an opponent (original: $primnick). It is
# named after the challenger's network, and its nick differs per network, so
# broadcasts carry "{BOT:<network>}" and IRCBot.deliver() swaps in that
# network's current nick (as nick@network). BOT_NAME is the fallback and what
# the DB event log records.
BOT_NAME = "MultiRPG"
BOT_RE   = re.compile(r"\{BOT:([^}]*)\}")

def bot_tag(network: str) -> str:
    return "{BOT:%s}" % network

def resolve_bot_tags(text: str, nick_for) -> str:
    """Replace {BOT:net} with nick@net; nick_for(net) -> bot nick or None."""
    return BOT_RE.sub(lambda m: f"{nick_for(m[1]) or BOT_NAME}@{m[1]}", text)

def pick_opponent(opponents, bot_networks=()):
    """Random opponent from the online list; 1 in len+1 a bot instead (original:
    $primnick). Which network's bot is chosen at random, so a SwiftIRC player
    can meet the DALnet bot."""
    if random.randrange(len(opponents) + 1) < 1:
        return {"bot_network": random.choice(list(bot_networks) or [None])}
    return random.choice(opponents)

async def resolve_battle(db: Database, challenger, opponent,
                         collision: bool = False) -> list:
    """Port of challenge_opp / collision_fight. An opponent of the form
    {"bot_network": net} (see pick_opponent) means the bot on that network."""
    # Always re-fetch for current TTL
    c = await db.get_player_by_id(challenger["id"]) or challenger
    bot = opponent is None or "bot_network" in opponent
    if bot:
        best = 0
        for pl in await db.get_all_players():
            best = max(best, await db.get_item_sum(pl["id"]))
        o = {"id": None, "username": BOT_NAME, "network": "", "level": 0,
             "alignment": "", "ttl": 0, "pos_x": 0, "pos_y": 0}
        o_sum = best + 1
    else:
        o = await db.get_player_by_id(opponent["id"]) or opponent
        if c["id"] == o["id"]:
            return []   # never battle yourself
        o_sum = eff_sum(await db.get_item_sum(o["id"]), o["alignment"])

    c_sum  = eff_sum(await db.get_item_sum(c["id"]), c["alignment"])
    c_roll = random.randint(0, max(c_sum - 1, 0))
    o_roll = random.randint(0, max(o_sum - 1, 0))
    won    = c_roll >= o_roll
    msgs   = []

    outag = ("the bot " + bot_tag((opponent or {}).get('bot_network') or c['network'])
             if bot else utag(o))
    rolls = f"[{c_roll}/{c_sum}]"
    orolls = f"[{o_roll}/{o_sum}]"
    if collision:
        verb = (f"{utag(c)} {rolls} has come upon {outag} {orolls} and "
                f"{'taken them in' if won else 'been defeated in'} combat!")
    else:
        verb = (f"{utag(c)} {rolls} has challenged {outag} {orolls} in "
                f"combat and {'won' if won else 'lost'}!")

    if won:
        # opponent's level drives the gain (bot: flat 20% in a challenge)
        base = 20 if (bot and not collision) else o["level"] // 4
        gain    = int(max(base, 7) / 100 * c["ttl"])
        new_ttl = max(0, c["ttl"] - gain)
        await db.update_ttl(c["id"], new_ttl)
        msg = f"{verb} {fmt_time(gain)} is removed from {utag(c)}'s clock."
        msgs += [broadcast_all(msg),
                 broadcast_all(f"{utag(c)} reaches next level in {fmt_time(new_ttl)}.")]
        if not bot:
            # alignment shifts crit odds in challenges only; collisions are flat 1/35
            cs = 35 if collision else (50 if c["alignment"] == "g"
                                       else 20 if c["alignment"] == "e" else 35)
            if random.randrange(cs) < 1:
                crit = int(((5 + random.randint(0, 19)) / 100) * o["ttl"])
                await db.add_penalty(o["id"], crit)
                cm = (f"{utag(c)} has dealt {utag(o)} a Critical Strike! "
                      f"{fmt_time(crit)} is added to {utag(o)}'s clock.")
                msgs.append(broadcast_all(cm))
                msgs.append(broadcast_all(
                    f"{utag(o)} reaches next level in {fmt_time(o['ttl'] + crit)}."))
                await db.log_event("critical", cm, c["id"], o["id"])
            elif random.randrange(25) < 1 and c["level"] > 19:
                result = await db.steal_item(c["id"], o["id"])
                if result:
                    slot, sl, ol = result
                    sm = (f"In the fierce battle, {utag(o)} dropped their level {sl} {slot}! "
                          f"{utag(c)} picks it up, tossing their old level {ol} {slot} "
                          f"to {utag(o)}.")
                    msgs.append(broadcast_all(sm))
                    await db.log_event("steal", sm, c["id"], o["id"])
    else:
        base = 10 if bot else o["level"] // 7
        pen = int(max(base, 7) / 100 * c["ttl"])
        await db.add_penalty(c["id"], pen)
        msg = f"{verb} {fmt_time(pen)} is added to {utag(c)}'s clock."
        msgs += [broadcast_all(msg),
                 broadcast_all(f"{utag(c)} reaches next level in {fmt_time(c['ttl'] + pen)}.")]

    await db.log_event("battle", resolve_bot_tags(msg, lambda n: None), c["id"], o["id"])
    await db.commit()
    return msgs


# ── Game Engine ───────────────────────────────────────────────────────────────
class GameEngine:
    def __init__(self, db: Database, self_clock: int = 5, limit_pen: int = 0,
                 hof_type: str = "level", win_level: int = 40,
                 round_cron: str = "0 0 1 1,4,7,10 *", quest_min_questers: int = 1):
        self.db         = db
        self.bot_networks: list = []   # filled by BotManager.register
        self.self_clock = self_clock
        self.limit_pen  = limit_pen
        self.rp_base    = RP_BASE
        self.rp_step    = RP_STEP
        self.hof_type   = hof_type    # "level", "cron", or "none"
        self.win_level  = win_level   # used when hof_type="level"
        self.round_cron = round_cron  # used when hof_type="cron"
        self.quest_min_questers = max(1, quest_min_questers)  # fewer left → quest cancelled
        self._quest_absent: dict = {}  # quester id -> deadline to come back by
        self._last_cron_check = 0     # last timestamp cron was evaluated
        self._lasttime  = 0          # 0 = not joined yet
        self._rpreport  = self_clock  # start at sc so tick 1 doesn't trigger modulo-zero events
        self.paused          = False  # PAUSE command stops tick processing
        self._reset_pending  = False  # True during 60s end-of-round grace period
        self._reset_at       = 0      # timestamp when reset fires
        self._post_reset_who = False  # signal bots to re-WHO after round reset
        self._relogin_who    = False  # signal bots to re-WHO on admin RELOGIN command
        self.silent     = 0          # 0=all, 1=no chan, 2=no pm, 3=no both
        self._quest = {
            "questers": [], "type": 1, "stage": 1,
            "p1": None, "p2": None,
            "qtime": int(time.time()) + random.randint(3600, 7200),
            "text": "",
        }

    async def load_persisted_quest(self):
        """Restore any in-progress quest from DB after restart."""
        q = await self.db.load_quest()
        if q["questers"]:
            self._quest = q
            names = ", ".join(f"{x['username']}@{x['network']}" for x in q["questers"])
            log.info(f"Restored active quest: {q['text']!r} — questers: {names}")

    async def check_win_condition(self) -> list:
        """Called on startup — if any player is already at win_level, trigger reset."""
        if self.hof_type != "level":
            return []
        all_players = await self.db.get_all_players()
        winners = [p for p in all_players if p["level"] >= self.win_level]
        if winners and not self._reset_pending and not self._quest["questers"]:
            self._reset_pending = True
            self._reset_at      = int(time.time()) + 60
            round_num           = await self.db.get_round()
            top                 = max(winners, key=lambda p: p["level"])
            log.warning(f"Startup: found level {top['level']} player — "
                        f"triggering end of Round {round_num} in 60s")
            return [broadcast_all(
                f"⚠️ {top['username']}@{top['network']} reached level "
                f"{self.win_level} — Round {round_num} ending in 60 seconds! "
                f"Your characters will be reset. Keep idling to continue!"
            )]
        return []

    def mark_joined(self):
        self._lasttime = int(time.time())
        log.info(f"Engine: mark_joined at {self._lasttime}")

    # ── IRC events ────────────────────────────────────────────────────────────

    async def on_login(self, username, network, nick, channel,
                       password, userhost="") -> tuple:
        """Returns (ok, private_msg, [Broadcast]). Only a successful login is
        announced; failures and "already logged in" stay private."""
        p = await self.db.get_player(username, network)
        if not p:
            # Try any network — usernames are globally unique
            p = await self.db.get_player_any_network(username)
            if not p:
                return False, "No such account. Use REGISTER to create one.", []
            # Allow login from any network
        if not self.db.verify_password(password, p["password_hash"]):
            return False, "Wrong password.", []
        if self.db.needs_rehash(p["password_hash"]):
            await self.db.change_password(p["id"], password)
        if p["is_online"]:
            return False, "You are already logged in.", []
        await self.db.set_online(p["id"], nick, channel, userhost)
        priv = (
            f"Logon successful. {username}, the level {p['level']} "
            f"{p['class']}. Next level in {fmt_time(p['ttl'])}."
        )
        chan = broadcast_all(
            f"{utag(p)}, the level {p['level']} {p['class']}, is now online "
            f"from nickname {nick}. Next level in {fmt_time(p['ttl'])}."
        )
        return True, priv, [chan]

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

    def cmd_relogin(self) -> str:
        """Admin: signal all bots to re-WHO their channels and restore online status."""
        self._relogin_who = True
        return "Re-login WHO sent to all networks."

    async def cmd_endround(self) -> list:
        """Admin: manually end the current round. Schedules the same 60-second
        grace + end-of-round reset the automatic HoF paths use, so players are
        kept logged in and the winners/reset announcement broadcasts to every
        network. Works regardless of any active quest (the reset clears it)."""
        if self._reset_pending:
            return []   # already scheduled — bot replies privately, no channel spam
        now                 = int(time.time())
        self._reset_pending = True
        self._reset_at      = now + 60
        round_num           = await self.db.get_round()
        return [broadcast_all(
            f"⏰ Round {round_num} is being ended by an admin! "
            f"The realm will be reborn in 60 seconds."
        )]

    async def cmd_forcequest(self) -> list:
        """Admin: force-start a quest immediately, ignoring timer and eligibility."""
        if self._quest["questers"]:
            return [broadcast_all("A quest is already in progress.")]
        online = await self.db.get_online_players()
        if len(online) < 4:
            return [broadcast_all(
                f"Not enough online players to start a quest "
                f"(need 4, have {len(online)}).")]
        result = await self._start_quest(online)
        if result:
            return result
        questers = random.sample(online, min(4, len(online)))
        self._quest["questers"] = questers
        names = ", ".join(f"{q['username']}@{q['network']}" for q in questers)
        avg_x     = sum(q["pos_x"] for q in questers) // len(questers)
        avg_y     = sum(q["pos_y"] for q in questers) // len(questers)
        lm_sorted = sorted(LOCATIONS, key=lambda l: (l[1]-avg_x)**2+(l[2]-avg_y)**2)
        lm_pool   = lm_sorted[:max(4, len(lm_sorted)//2)]
        lm1, lm2  = random.sample(lm_pool, 2)
        self._quest["type"]   = 2
        self._quest["stage"]  = 1
        self._quest["p1"]     = (lm1[1], lm1[2])
        self._quest["p2"]     = (lm2[1], lm2[2])
        self._quest["p1name"] = lm1[0]
        self._quest["p2name"] = lm2[0]
        self._quest["text"]   = "chart the treacherous waters between two pirate strongholds"
        names_coords = ", ".join(
            f"{q['username']}@{q['network']} [{q['pos_x']}, {q['pos_y']}]"
            for q in questers)
        msg1 = f"⚓ QUEST: {names_coords} have been chosen by the gods."
        msg2 = (f"⚓ QUEST: {_sentence(self._quest['text'])}. "
                f"First reach {lm1[0]} [{lm1[1]}, {lm1[2]}], "
                f"then {lm2[0]} [{lm2[1]}, {lm2[2]}].")
        msg = f"{msg1} {msg2}"
        await self.db.log_event("quest", msg)
        await self.db.save_quest(self._quest)
        return [broadcast_all(msg1), broadcast_all(msg2)]

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

    async def cmd_del(self, admin_nick, target) -> tuple:
        """Admin: permanently delete a player's account."""
        p = await self.db.get_player_any_network(target)
        if not p: return f"No such username {target}.", []
        if p["is_admin"]:
            return (f"{p['username']} is an admin. Run DELADMIN {p['username']} "
                    "first if you really want to delete it."), []
        removed = f"{utag(p)}'s account was removed by an admin."
        if any(x["id"] == p["id"] for x in self._quest["questers"]):
            broadcasts = await self._drop_questers({p["id"]}, removed)
        else:
            broadcasts = [broadcast_all(removed)]
        await self.db.delete_player(p["id"])
        return f"Account {p['username']} deleted.", broadcasts

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

    # ── Questers who are gone through no fault of their own ──────────────────

    async def _drop_questers(self, ids, lead) -> list:
        """Take players off the active quest. It isn't the others' doing, so no
        penalties: the quest carries on with whoever is left, and is only
        cancelled (and the next one delayed) once fewer than quest_min_questers
        remain. `lead` opens the announcement."""
        q = self._quest
        q["questers"] = [x for x in q["questers"] if x["id"] not in ids]
        for i in ids: self._quest_absent.pop(i, None)
        what = f"The quest to {q['text']}" if q.get("text") else "The quest"
        n    = len(q["questers"])
        if n >= self.quest_min_questers:
            await self.db.save_quest(q)
            names = ", ".join(f"{x['username']}@{x['network']}" for x in q["questers"])
            return [broadcast_all(
                f"{lead} {what} now has {n} quester{'s' if n != 1 else ''}: {names}.")]
        q["questers"] = []
        q["qtime"]    = int(time.time()) + 43200
        self._quest_absent.clear()
        await self.db.clear_quest()
        why = "no questers are" if not n else "too few questers are"
        # A reset may have been deferred waiting for the quest to end
        return ([broadcast_all(f"{lead} {what} has been cancelled, since {why} left.")]
                + await self.check_win_condition())

    def quest_absent(self, players, channel) -> list:
        """Questers found missing from the channel (the WHO after the bot
        rejoins) get QUEST_GRACE seconds to come back before leaving the quest."""
        ids = {x["id"] for x in self._quest["questers"]}
        gone = [p for p in players if p["id"] in ids and p["id"] not in self._quest_absent]
        if not gone: return []
        for p in gone:
            self._quest_absent[p["id"]] = int(time.time()) + QUEST_GRACE
        names = ", ".join(utag(p) for p in gone)
        verb  = "is" if len(gone) == 1 else "are"
        return [broadcast_all(
            f"{names} {verb} on the quest but not in {channel}. Rejoin {channel} "
            f"(or LOGIN) within {QUEST_GRACE // 60} minutes to stay on it.")]

    async def _check_quest_absent(self) -> list:
        """Forgive questers who came back; drop those whose grace ran out."""
        if not self._quest_absent: return []
        now, late = int(time.time()), set()
        questers  = {x["id"] for x in self._quest["questers"]}
        for pid, deadline in list(self._quest_absent.items()):
            p = await self.db.get_player_by_id(pid)
            if pid not in questers or not p or p["is_online"]:
                del self._quest_absent[pid]
            elif now >= deadline:
                late.add(pid)
        if not late: return []
        names = ", ".join(f"{x['username']}@{x['network']}"
                          for x in self._quest["questers"] if x["id"] in late)
        verb  = "has" if len(late) == 1 else "have"
        return await self._drop_questers(late, f"{names} didn't come back in time and {verb} left the quest.")

    # ── Quest penalty check ───────────────────────────────────────────────────

    async def _qpc(self, player) -> list:
        q = self._quest
        if not q["questers"]: return []
        if not any(x["id"] == player["id"] for x in q["questers"]): return []
        q["questers"] = []
        q["qtime"]    = int(time.time()) + 43200
        for p in await self.db.get_online_players():
            await self.db.add_penalty(p["id"], int(15 * (RP_PEN_STEP ** p["level"])), "pen_quest")
        await self.db.clear_quest()
        await self.db.commit()
        # Check if reset was deferred waiting for quest to end
        await self.check_win_condition()
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

        # ── End-of-round reset ────────────────────────────────────────────────
        if self._reset_pending and int(time.time()) >= self._reset_at:
            return await self._do_round_reset()

        # ── Cron-based round end ──────────────────────────────────────────────
        if self.hof_type == "cron" and not self._reset_pending:
            from croniter import croniter
            now = int(time.time())
            
            # On first check, initialize to the last cron fire time to catch missed fires
            if self._last_cron_check == 0:
                cron_init = croniter(self.round_cron, now)
                self._last_cron_check = cron_init.get_prev(float)
            
            # Find the most recent cron fire time
            cron = croniter(self.round_cron, now)
            last_fire = cron.get_prev(float)
            
            # Check if a new cron event has fired since last check. The scheduled
            # boundary always wins: an active quest does not defer it (the reset
            # clears the quest anyway), so rounds end on the date they should.
            if last_fire > self._last_cron_check:
                quest_active = bool(self._quest["questers"])
                self._reset_pending = True
                self._reset_at      = now + 60
                round_num           = await self.db.get_round()
                self._last_cron_check = last_fire
                note = " The active quest will be cancelled." if quest_active else ""
                return [broadcast_all(
                    f"⏰ Scheduled end of Round {round_num}! "
                    f"The realm will be reborn in 60 seconds.{note}"
                )]
            self._last_cron_check = last_fire

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
                await self.db.update_ttl(p["id"], 0)
            else:
                await self.db.update_ttl(p["id"], new_ttl)
            # Accumulate total idle time
            await self.db.conn.execute(
                "UPDATE players SET idled=idled+? WHERE id=?", (elapsed, p["id"]))

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
        if self._rpreport % 3600  == 0: msgs.extend(await self._high_level_battle(online))

        # ── Quest ─────────────────────────────────────────────────────────────
        try:
            msgs.extend(await self._check_quest_absent())
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

        # ── End of round check ────────────────────────────────────────────────
        if (self.hof_type == "level"
                and new_level >= self.win_level
                and not self._reset_pending
                and not self._quest["questers"]):
            self._reset_pending = True
            self._reset_at      = int(time.time()) + 60
            round_num           = await self.db.get_round()
            return [broadcast_all(
                f"⚠️ {utag(p)} has reached level {self.win_level} "
                f"and ended Round {round_num}! "
                f"The realm will be reborn in 60 seconds. "
                f"Prepare to re-register for Round {round_num + 1}!"
            )]

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
                    self.db, fresh, pick_opponent(opponents, self.bot_networks)))
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
        # Track which pairs have already battled this tick (across all movement steps)
        battled_pairs = set()

        for _ in range(self.self_clock):
            positions = {}

            for p in online:
                pid = p["id"]
                x   = player_state[pid]["pos_x"]
                y   = player_state[pid]["pos_y"]

                if pid in qids and q["type"] == 2 and random.random() < 0.33:
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
                if key in positions:
                    other_pid = positions[key]
                    pair = (min(pid, other_pid), max(pid, other_pid))
                    # No collision battles during grid quests — questers converge
                    # on the same cell intentionally, battles would be unfair
                    is_quest_collision = (q["type"] == 2 and q["questers"]
                                          and (pid in qids or other_pid in qids))
                    if (other_pid != pid and n > 1 and pair not in battled_pairs
                            and not is_quest_collision
                            and random.random() < 1 / n):
                        battled_pairs.add(pair)
                        msgs.extend(await resolve_battle(
                            self.db,
                            player_state[pid],
                            player_state[other_pid],
                            collision=True))
                else:
                    positions[key] = pid

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
                    await self.db.save_quest(self._quest)
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
                    await self.db.clear_quest()
                    await self.db.log_event("quest", msg)
                    msgs.extend(await self.check_win_condition())
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
            await self.db.clear_quest()
            await self.db.log_event("quest", msg)
            msgs = [broadcast_all(msg)]
            msgs.extend(await self.check_win_condition())
            return msgs
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
            ("Q1", "sail through the Roaring Swell and survive the tempest unscathed"),
            ("Q1", "retrieve the cursed treasure chest from Deadman's Cove before the tide turns"),
            ("Q1", "escort the merchant fleet safely past Wreckers' Shoals to Freeport"),
            ("Q1", "break the sea witch's curse haunting the waters of Mermaid's Lagoon"),
            ("Q1", "defend Freeport from the incoming pirate armada until dawn"),
            ("Q1", "recover the Pirate King's stolen crown from Blackbeard's Cove"),
            ("Q1", "navigate the Serpent's Current and chart a safe passage for the fleet"),
            ("Q1", "rescue the prisoners held captive at Gallows Reef before high tide"),
            ("Q1", "destroy the kraken's eggs hidden in the depths of Kraken Deep"),
            ("Q1", "barter passage through Cutthroat Cove without firing a single shot"),
            ("Q1", "smuggle a hold of contraband rum through Smuggler's Run without alerting the navy"),
            ("Q1", "steal the harpoons of the whaling fleet at Whaler's Notch before the hunt begins"),
            ("Q1", "thread the fleet through The Devil's Passage before the tide closes the channel"),
            ("Q1", "ride out the storm in The Howling Gale and bring every ship home"),
            ("Q1", "ambush the rival crew hiding in Marauder's Bay and seize their plunder"),
            ("Q1", "lower the diving bell into The Abyssal Plain and recover the sunken galleon's gold"),
            ("Q1", "recover the cursed skull from the peak of Bonecrown Isle before it wakes the dead"),
            ("Q1", "chart the hidden channels of Tempest Archipelago before the next storm hits"),
            ("Q1", "answer the ghostly distress call drifting across The Endless Deep"),
            ("Q2", "brave the open seas to reach the lost ruins"),
            ("Q2", "chart the treacherous waters between two pirate strongholds"),
            ("Q2", "deliver the sealed orders across enemy-controlled waters"),
            ("Q2", "follow the ancient map to uncover the buried treasure"),
        ]
        qtype, text = random.choice(QUESTS)
        self._quest["text"] = text
        if qtype == "Q1":
            self._quest["type"]  = 1
            dur = random.randint(43200, 86400)
            self._quest["qtime"] = now + dur
            names_coords = ", ".join(
                f"{q['username']}@{q['network']} [{q['pos_x']}, {q['pos_y']}]"
                for q in questers)
            msg1 = f"⚓ QUEST: {names_coords} have been chosen by the gods."
            msg2 = f"⚓ QUEST: {_sentence(text)}. Quest ends in {fmt_time(dur)}."
            msg  = f"{msg1} {msg2}"
        else:
            self._quest["type"]   = 2
            self._quest["stage"]  = 1
            avg_x = sum(q["pos_x"] for q in questers) // len(questers)
            avg_y = sum(q["pos_y"] for q in questers) // len(questers)
            lm_sorted = sorted(LOCATIONS, key=lambda l: (l[1]-avg_x)**2+(l[2]-avg_y)**2)
            lm_pool   = lm_sorted[:max(4, len(lm_sorted)//2)]
            lm1, lm2  = random.sample(lm_pool, 2)
            self._quest["p1"]     = (lm1[1], lm1[2])
            self._quest["p2"]     = (lm2[1], lm2[2])
            self._quest["p1name"] = lm1[0]
            self._quest["p2name"] = lm2[0]
            names_coords = ", ".join(
                f"{q['username']}@{q['network']} [{q['pos_x']}, {q['pos_y']}]"
                for q in questers)
            msg1 = f"⚓ QUEST: {names_coords} have been chosen by the gods."
            msg2 = (f"⚓ QUEST: {_sentence(text)}. "
                    f"First reach {lm1[0]} [{lm1[1]}, {lm1[2]}], "
                    f"then {lm2[0]} [{lm2[1]}, {lm2[2]}].")
            msg  = f"{msg1} {msg2}"
        await self.db.log_event("quest", msg)
        await self.db.save_quest(self._quest)
        return [broadcast_all(msg1), broadcast_all(msg2)]

    # ── Daily events ──────────────────────────────────────────────────────────

    async def _do_round_reset(self) -> list:
        """Record top 3 in HoF, announce winners, reset all players for new round."""
        round_num   = await self.db.get_round()
        all_players = await self.db.get_all_players()

        # Rank like the rest of the game (leaderboard, "Top Players"): highest
        # level first, ties broken by the least time left to the next level.
        scored = []
        for p in all_players:
            isum = await self.db.get_item_sum(p["id"])
            scored.append((p, isum))
        scored.sort(key=lambda x: (-x[0]["level"], x[0]["ttl"]))

        # Record top 3 in HoF
        top3 = scored[:3]
        for rank, (p, isum) in enumerate(top3, 1):
            await self.db.record_hof(round_num, rank, dict(p), isum)

        # Build the ranking line from pre-reset levels (top3 dicts predate reset).
        # Use plain "#1/#2/#3" — NOT medal emojis. UnrealIRCd's antimixedutf8
        # module blocks messages with several emojis, so keep at most the one
        # leading ⚔️ in the announcement below.
        if top3:
            ranking = ", ".join(
                f"#{i + 1} - {p['username']}@{p['network']} (lv.{p['level']}, {p['class']})"
                for i, (p, _isum) in enumerate(top3))
        else:
            ranking = "no ranked players"

        # Clear quest
        self._quest = {"questers": [], "type": 1, "stage": 1,
                       "p1": None, "p2": None, "qtime": 0, "text": "",
                       "p1name": "", "p2name": ""}
        await self.db.clear_quest()

        # Reset all players (keeps them logged in; scatters grid positions)
        await self.db.reset_round()

        # Clear reset flag
        self._reset_pending  = False
        self._reset_at       = 0
        # NOTE: no post-reset re-WHO. reset_round() now preserves login sessions,
        # so players stay logged in across the reset — re-WHO'ing here would log
        # everyone out/in again and spam "N users automatically logged in".

        # ── Announce to every network/channel ─────────────────────────────────
        broadcasts = [broadcast_all(
            f"⚔️ End of Round {round_num}! {ranking} :: "
            f"The realm has been reborn — Round {round_num + 1} begins now! "
            f"Your characters have been reset. Keep idling to continue!")]

        # Players are carried into the new round still logged in. Summarise who's
        # online per network (grouped), mirroring the auto-login summary format.
        online = await self.db.get_online_players()
        by_net: dict = {}
        for p in online:
            by_net.setdefault(p["network"], []).append(p.get("current_nick") or p["username"])
        for net in sorted(by_net):
            nicks = by_net[net]
            n     = len(nicks)
            # Each network's login summary goes only to that network's channel.
            broadcasts.append(broadcast_net(
                net,
                f"{n} user{'s' if n != 1 else ''} automatically logged in on {net}. "
                + ", ".join(nicks)))

        await self.db.log_event("system", f"Round {round_num} ended. "
                                f"Winner: {top3[0][0]['username'] if top3 else 'none'}")
        return broadcasts

    async def cmd_forcelogin(self, username: str, nick: str, network: str, channel: str, userhost: str = "") -> tuple:
        """Admin command: Force a character to be logged in with a specific nick on a network.
        
        If user is already logged in, updates their nick/userhost.
        If userhost not provided, returns (ok, needs_who) to signal bot to capture via WHO.
        
        Returns (ok, (message_string, broadcasts)) or (ok, "needs_who") to wait for userhost.
        """
        # Check if character exists
        p = await self.db.get_player_any_network(username)
        if not p:
            return False, f"No character '{username}' exists."
        
        # If no userhost provided, preserve existing one or mark as needing WHO
        if not userhost and p.get("userhost"):
            userhost = p["userhost"]
        elif not userhost:
            # Will capture via WHO - return special signal
            return True, ("needs_who", nick, username, channel, network)
        
        # Log them in with userhost (updates if already online)
        await self.db.set_online(p["id"], nick, channel, userhost=userhost)
        
        msg = f"{username} has been forcefully logged in as {nick}@{network} in {channel}."
        if userhost:
            msg += f" (userhost: {userhost})"
        
        # Broadcast to the network
        bcast = broadcast_net(network, f"{username} has been forcefully logged in from {nick} on {network}.")
        
        return True, (msg, [bcast])

    async def _hand_of_god(self, online) -> list:
        if not online: return []
        p       = random.choice(online)
        helping = random.randint(0, 4) > 0
        pct     = (5 + random.randint(0, 70)) / 100
        t       = int(pct * p["ttl"])
        if helping:
            await self.db.update_ttl(p["id"], max(0, p["ttl"] - t))
            HOG_HELP = [
                f"The seas parted and a divine wind carried {utag(p)}'s vessel {fmt_time(t)} toward level {p['level'] + 1}.",
                f"Neptune himself rose from the deep and blessed {utag(p)}, carrying them {fmt_time(t)} toward level {p['level'] + 1}.",
                f"A legendary kraken cleared the path ahead for {utag(p)}, saving them {fmt_time(t)} toward level {p['level'] + 1}.",
            ]
            msg = random.choice(HOG_HELP)
        else:
            await self.db.add_penalty(p["id"], t)
            HOG_HURT = [
                f"The Hand of Davy Jones reached up and dragged {utag(p)} down, slowing them {fmt_time(t)} from level {p['level'] + 1}.",
                f"A furious storm conjured by the sea gods battered {utag(p)}'s ship, costing them {fmt_time(t)} from level {p['level'] + 1}.",
                f"The Kraken rose from Kraken Deep and smashed {utag(p)}'s hull, slowing them {fmt_time(t)} from level {p['level'] + 1}.",
            ]
            msg = random.choice(HOG_HURT)
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
            "amulet":   f"{utag(p)}'s amulet was lost overboard in a storm",
            "idol":     f"{utag(p)}'s idol was stolen by a port pickpocket",
            "cutlass":  f"{utag(p)} left their cutlass to rust in the bilge",
            "coat":     f"{utag(p)}'s coat was shredded by the Kraken's tentacle",
            "buckler":  f"{utag(p)}'s buckler was crushed under a falling mast",
            "breeches": f"{utag(p)}'s breeches were eaten by ship rats",
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
            f"{utag(p)} was press-ganged into swabbing the poop deck",
            f"{utag(p)} fell into the hold chasing a runaway barrel",
            f"{utag(p)} got tangled in the rigging during a squall",
            f"{utag(p)} was marooned briefly on a sandbar",
            f"{utag(p)} drank a jug of bad rum and saw sea monsters",
            f"{utag(p)} was chased across three ports by an angry harbourmaster",
            f"{utag(p)} lost their sea charts in a tavern brawl",
            f"{utag(p)} sailed into the Howling Gale without a compass",
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
            "amulet":   f"{utag(p)}'s amulet was blessed by a sea witch",
            "idol":     f"{utag(p)}'s idol glowed after surviving the Howling Gale",
            "cutlass":  f"{utag(p)} had their cutlass reforged by a Freeport blacksmith",
            "coat":     f"A mermaid wove enchanted silk into {utag(p)}'s coat",
            "buckler":  f"{utag(p)}'s buckler was reinforced with kraken bone",
            "breeches": f"{utag(p)}'s breeches were stitched with enchanted sailcloth",
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
            f"{utag(p)} discovered a chest of gold doubloons washed ashore",
            f"{utag(p)} caught a legendary wind and made record time",
            f"{utag(p)} was blessed by the ghost of a friendly buccaneer",
            f"{utag(p)} found a mermaid's pearl that grants swift passage",
            f"{utag(p)} rode a pod of dolphins through Mermaid's Lagoon",
            f"{utag(p)} deciphered an ancient sea chart leading to a shortcut",
            f"{utag(p)} was hailed as a hero at Port Royal and feasted royally",
            f"{utag(p)} survived the Kraken's gaze and emerged stronger",
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
        msg     = (f"{utag(players[0])} and {utag(players[1])} sailed together "
                   f"under a blessed flag and the sea gods smiled upon them. "
                   f"{gain}% of their time is removed.")
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
        msg  = f"{utag(me)} is forsaken by their dark patron and cast adrift. {fmt_time(t)} added to their clock."
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
        return await resolve_battle(self.db, c, pick_opponent(others, self.bot_networks))

    async def _announce_top(self) -> list:
        players = await self.db.get_all_players()
        if not players: return []
        msgs = [broadcast_all("Idle RPG Top Players:")]
        for i, p in enumerate(players[:3], 1):
            msgs.append(broadcast_all(
                f"{utag(p)}, the level {p['level']} {p['class']}, "
                f"is #{i}! Next level in {fmt_time(p['ttl'])}."))
        return msgs
