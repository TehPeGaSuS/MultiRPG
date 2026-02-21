"""irc/bot.py — IRC adapter. All player commands via PM only."""
import asyncio, logging
from typing import Optional
from engine.game_engine import GameEngine, Broadcast

log = logging.getLogger(__name__)

class IRCBot:
    def __init__(self, network_name, host, port, channel, nick, engine,
                 nickserv_pass=None, server_pass=None, use_ssl=False,
                 reconnect_delay=30, modes="+i"):
        self.network_name    = network_name
        self.host, self.port = host, port
        self.channel         = channel
        self.nick            = nick
        self.current_nick    = nick
        self.engine          = engine
        self.nickserv_pass   = nickserv_pass
        self.server_pass     = server_pass
        self.use_ssl         = use_ssl
        self.reconnect_delay = reconnect_delay
        self.modes           = modes
        self._reader         = None
        self._writer         = None
        self._connected      = False
        self._send_queue     = asyncio.Queue()
        self.broadcast_callback = None   # set by BotManager
        self._prev_online: dict = {}     # userhost -> username for auto-login

    async def run(self):
        while True:
            try:
                await self._connect()
                await asyncio.gather(self._recv_loop(), self._send_loop())
            except Exception as e:
                log.error(f"[{self.network_name}] {e}", exc_info=True)
            self._connected = False
            log.info(f"[{self.network_name}] Reconnecting in {self.reconnect_delay}s...")
            await asyncio.sleep(self.reconnect_delay)

    async def _connect(self):
        log.info(f"[{self.network_name}] Connecting to {self.host}:{self.port}")
        if self.use_ssl:
            import ssl as _ssl
            ctx = _ssl.create_default_context()
            self._reader, self._writer = await asyncio.open_connection(
                self.host, self.port, ssl=ctx)
        else:
            self._reader, self._writer = await asyncio.open_connection(
                self.host, self.port)
        self._connected = True
        if self.server_pass: await self._raw(f"PASS {self.server_pass}")
        await self._raw(f"NICK {self.nick}")
        await self._raw(f"USER multirpg 0 * :Multi IdleRPG Bot")

    async def _recv_loop(self):
        while self._connected:
            try:
                line = await self._reader.readline()
                if not line: break
                await self._handle_line(line.decode("utf-8", "replace").rstrip("\r\n"))
            except asyncio.CancelledError: break
            except Exception as e:
                log.error(f"[{self.network_name}] recv: {e}"); break

    async def _send_loop(self):
        log.info(f"[{self.network_name}] _send_loop started")
        while self._connected:
            try:
                line = await asyncio.wait_for(self._send_queue.get(), timeout=1.0)
                log.info(f"[{self.network_name}] _send_loop sending: {line[:80]}")
                await self._raw(line)
                await asyncio.sleep(0.5)
            except asyncio.TimeoutError: continue
            except asyncio.CancelledError: break
        log.info(f"[{self.network_name}] _send_loop exited")

    async def _raw(self, line):
        if self._writer:
            self._writer.write((line + "\r\n").encode("utf-8"))
            await self._writer.drain()

    async def say(self, msg):
        if self.engine.silent in (1, 3): return   # channel msgs suppressed
        for chunk in _split(msg):
            await self._send_queue.put(f"PRIVMSG {self.channel} :{chunk}")

    async def notice_nick(self, nick, msg):
        if self.engine.silent in (2, 3): return   # private msgs suppressed
        for chunk in _split(msg):
            await self._send_queue.put(f"NOTICE {nick} :{chunk}")

    async def privmsg_nick(self, nick, msg):
        if self.engine.silent in (2, 3): return   # private msgs suppressed
        for chunk in _split(msg):
            await self._send_queue.put(f"PRIVMSG {nick} :{chunk}")

    async def deliver(self, b: Broadcast):
        """Called by BotManager to route cross-network broadcasts."""
        if b.scope in ("all", "network") and (b.scope == "all" or b.network == self.network_name):
            log.info(f"[{self.network_name}] deliver -> say: {b.message[:80]}")
            await self.say(b.message)
        elif b.scope == "notice" and b.network == self.network_name and b.nick:
            await self.notice_nick(b.nick, b.message)

    # ── IRC line parser ───────────────────────────────────────────────────────

    async def _handle_line(self, line):
        parts = line.split()
        if not parts: return

        # PING
        if parts[0].upper() == "PING":
            await self._raw(f"PONG {parts[1]}" if len(parts) > 1 else "PONG")
            return

        if len(parts) > 1:
            cmd2 = parts[1]

            # 001 — welcome / registered
            if cmd2 == "001":
                log.info(f"[{self.network_name}] Registered. Joining {self.channel}")
                if self.nickserv_pass:
                    await self._raw(f"PRIVMSG NickServ :IDENTIFY {self.nickserv_pass}")
                    await asyncio.sleep(2)
                if self.modes:
                    await self._raw(f"MODE {self.current_nick} {self.modes}")
                await self._raw(f"JOIN {self.channel}")
                return

            # 433 — nick in use
            if cmd2 == "433":
                self.current_nick += "_"
                await self._raw(f"NICK {self.current_nick}")
                return

            # 352 — WHO reply: :server 352 botnick #chan user host server nick H :0 realname
            if cmd2 == "352" and self._prev_online and len(parts) >= 8:
                who_nick = parts[7]
                who_user = parts[4]
                who_host = parts[5]
                uh       = f"{who_nick}!{who_user}@{who_host}"
                uah      = f"{who_user}@{who_host}"
                for saved_uh, uname in list(self._prev_online.items()):
                    if "!" in saved_uh and saved_uh.split("!", 1)[1] == uah:
                        p = await self.engine.db.get_player(uname, self.network_name)
                        if p:
                            await self.engine.db.set_online(
                                p["id"], who_nick, self.channel, uh)
                            log.info(f"[{self.network_name}] Auto-login: {uname} ({uh})")
                        del self._prev_online[saved_uh]
                        break
                return

            # 315 — end of WHO
            if cmd2 == "315":
                # Anyone still in _prev_online wasn't in the channel — log them out
                for uname in self._prev_online.values():
                    p = await self.engine.db.get_player(uname, self.network_name)
                    if p:
                        await self.engine.db.set_offline(p["id"])
                        log.info(f"[{self.network_name}] {uname} not in channel — logged out")
                self._prev_online = {}
                # Announce single summary of how many were auto-logged in
                online = await self.engine.db.get_online_players()
                net_online = [p for p in online if p["network"] == self.network_name]
                if net_online:
                    n = len(net_online)
                    await self.say(
                        f"{n} user{'s' if n != 1 else ''} automatically logged in on "
                        f"{self.network_name}."
                    )
                return

        # Lines below need a proper :prefix
        if not line.startswith(":"):
            return

        prefix   = parts[0][1:]   # strip leading :
        command  = parts[1].upper() if len(parts) > 1 else ""
        usernick = prefix.split("!")[0] if "!" in prefix else prefix

        # ── JOIN ─────────────────────────────────────────────────────────────
        # IMPORTANT: handle our own JOIN *before* the early-return below
        if command == "JOIN":
            if usernick == self.current_nick:
                log.info(f"[{self.network_name}] Joined {self.channel}.")
                # Build prev_online map for WHO-based auto-login
                prev = await self.engine.db.get_previously_online(self.network_name)
                self._prev_online = {
                    p["userhost"]: p["username"]
                    for p in prev if p["userhost"]
                }
                if self._prev_online:
                    log.info(f"[{self.network_name}] {len(self._prev_online)} previously online — sending WHO")
                    await self._raw(f"WHO {self.channel}")
                else:
                    await self.engine.db.mark_all_offline(self.network_name)
                # mark_joined AFTER setting up prev_online, so tick starts
                self.engine.mark_joined()
            return

        # Ignore our own messages for all other commands
        if usernick == self.current_nick:
            return

        # ── PRIVMSG ───────────────────────────────────────────────────────────
        if command == "PRIVMSG":
            target = parts[2] if len(parts) > 2 else ""
            text   = " ".join(parts[3:])[1:] if len(parts) > 3 else ""
            uh     = prefix if "!" in prefix else usernick
            if target.lower() == self.current_nick.lower():
                # PM to bot → command handler
                await self._handle_pm(usernick, text, userhost=uh)
            elif target == self.channel:
                # Channel message from player → penalty
                await self._deliver_local(
                    await self.engine.on_message(usernick, self.network_name, text))

        elif command == "NOTICE":
            target = parts[2] if len(parts) > 2 else ""
            text   = " ".join(parts[3:])[1:] if len(parts) > 3 else ""
            if target == self.channel:
                await self._deliver_local(
                    await self.engine.on_notice(usernick, self.network_name, text))

        elif command == "PART":
            await self._deliver_local(
                await self.engine.on_part(usernick, self.network_name))

        elif command == "QUIT":
            await self._deliver_local(
                await self.engine.on_quit(usernick, self.network_name))

        elif command == "NICK":
            new_nick = parts[2].lstrip(":") if len(parts) > 2 else ""
            if new_nick:
                await self._deliver_local(
                    await self.engine.on_nick_change(
                        usernick, new_nick, self.network_name))

        elif command == "KICK":
            kicked = parts[3] if len(parts) > 3 else ""
            if kicked:
                await self._deliver_local(
                    await self.engine.on_kick(kicked, self.network_name))

    # ── PM command dispatcher ─────────────────────────────────────────────────

    async def _handle_pm(self, nick, text, userhost=""):
        parts = text.strip().split()
        if not parts: return
        cmd, args = parts[0].upper(), parts[1:]

        async def reply(msg): await self.privmsg_nick(nick, msg)

        # ── Account ───────────────────────────────────────────────────────────

        if cmd == "REGISTER":
            if len(args) < 3:
                await reply("Usage: REGISTER <username> <password> <class>")
                await reply("Example: REGISTER PotHead toke420 420th Level Puffmage")
                return
            ok, priv, broadcasts = await self.engine.on_register(
                args[0], self.network_name, nick, self.channel,
                args[1], " ".join(args[2:]), userhost=userhost)
            await reply(priv)
            await self._deliver_local(broadcasts)

        elif cmd == "LOGIN":
            if len(args) < 2:
                await reply("Usage: LOGIN <username> <password>"); return
            ok, msg = await self.engine.on_login(
                args[0], self.network_name, nick, self.channel,
                args[1], userhost=userhost)
            await reply(msg)

        elif cmd == "LOGOUT":
            broadcasts = await self.engine.on_logout(nick, self.network_name)
            await self._deliver_local(broadcasts)
            if not broadcasts: await reply("You are not logged in.")

        elif cmd == "NEWPASS":
            if not args: await reply("Usage: NEWPASS <password>"); return
            await reply(await self.engine.cmd_newpass(nick, self.network_name, args[0]))

        elif cmd == "ALIGN":
            if not args: await reply("Usage: ALIGN <good|neutral|evil>"); return
            msg, broadcasts = await self.engine.cmd_align(
                nick, self.network_name, args[0].lower())
            await reply(msg)
            await self._deliver_local(broadcasts)

        elif cmd == "REMOVEME":
            msg, broadcasts = await self.engine.cmd_removeme(nick, self.network_name)
            await reply(msg)
            await self._deliver_local(broadcasts)

        # ── Info ──────────────────────────────────────────────────────────────

        elif cmd == "STATUS":
            target = args[0] if args else None
            await reply(await self.engine.cmd_status(nick, self.network_name, target))

        elif cmd == "WHOAMI":
            await reply(await self.engine.cmd_whoami(nick, self.network_name))

        elif cmd == "QUEST":
            await reply(await self.engine.cmd_quest())

        elif cmd == "TOP":
            players = await self.engine.db.get_all_players()
            if not players: await reply("No players yet."); return
            from engine.game_engine import fmt_time
            for i, p in enumerate(players[:5], 1):
                isum = await self.engine.db.get_item_sum(p["id"])
                await reply(
                    f"{i}. {p['username']}@{p['network']} — "
                    f"Lv.{p['level']} {p['class']} | "
                    f"Items: {isum} | TTL: {fmt_time(p['ttl'])}")

        elif cmd == "HELP":
            for line in [
                "MultiRPG commands (all via PM to the bot):",
                "  REGISTER <username> <password> <class>  — Create account",
                "  LOGIN <username> <password>              — Log in",
                "  LOGOUT                                   — Log out (penalty!)",
                "  STATUS [username]                        — Show stats",
                "  WHOAMI                                   — Short status",
                "  QUEST                                    — Active quest info",
                "  TOP                                      — Top 5 players",
                "  NEWPASS <password>                       — Change password",
                "  ALIGN <good|neutral|evil>                — Change alignment",
                "  REMOVEME                                 — Delete account",
                "Talking in channel, parting, quitting, nick changes = penalty!",
                "Admin commands: HOG PUSH CHPASS CHCLASS CHUSER PAUSE SILENT CLEARQ DELOLD MKADMIN DELADMIN",
            ]: await reply(line)

        # ── Admin ─────────────────────────────────────────────────────────────

        elif cmd == "HOG":
            if not await self._is_admin(nick): await reply("Access denied."); return
            await self._deliver_local(await self.engine.cmd_hog(nick, self.network_name))

        elif cmd == "PUSH":
            if not await self._is_admin(nick): await reply("Access denied."); return
            if len(args) < 2 or not args[1].lstrip("-").isdigit():
                await reply("Usage: PUSH <username> <seconds>"); return
            msg, broadcasts = await self.engine.cmd_push(
                nick, self.network_name, args[0], int(args[1]))
            await reply(msg)
            await self._deliver_local(broadcasts)

        elif cmd == "CHPASS":
            if not await self._is_admin(nick): await reply("Access denied."); return
            if len(args) < 2: await reply("Usage: CHPASS <username> <password>"); return
            await reply(await self.engine.cmd_chpass(args[0], args[1]))

        elif cmd == "CHCLASS":
            if not await self._is_admin(nick): await reply("Access denied."); return
            if len(args) < 2: await reply("Usage: CHCLASS <username> <class>"); return
            await reply(await self.engine.cmd_chclass(args[0], " ".join(args[1:])))

        elif cmd == "DELOLD":
            if not await self._is_admin(nick): await reply("Access denied."); return
            if not args or not args[0].replace(".", "").isdigit():
                await reply("Usage: DELOLD <days>"); return
            await reply(await self.engine.cmd_delold(float(args[0])))

        elif cmd == "MKADMIN":
            if not await self._is_admin(nick): await reply("Access denied."); return
            if not args: await reply("Usage: MKADMIN <username>"); return
            await self.engine.db.set_admin(args[0], True)
            await reply(f"{args[0]} is now an admin.")

        elif cmd == "DELADMIN":
            if not await self._is_admin(nick): await reply("Access denied."); return
            if not args: await reply("Usage: DELADMIN <username>"); return
            await self.engine.db.set_admin(args[0], False)
            await reply(f"{args[0]} is no longer an admin.")

        elif cmd == "CHUSER":
            if not await self._is_admin(nick): await reply("Access denied."); return
            if len(args) < 2: await reply("Usage: CHUSER <username> <new name>"); return
            await reply(await self.engine.cmd_chuser(args[0], args[1]))

        elif cmd == "PAUSE":
            if not await self._is_admin(nick): await reply("Access denied."); return
            await reply(self.engine.cmd_pause())

        elif cmd == "SILENT":
            if not await self._is_admin(nick): await reply("Access denied."); return
            if not args or args[0] not in ("0","1","2","3"):
                await reply("Usage: SILENT <0|1|2|3>  (0=all on, 1=no chan, 2=no pm, 3=all off)")
                return
            await reply(self.engine.cmd_silentmode(int(args[0])))

        elif cmd == "CLEARQ":
            if not await self._is_admin(nick): await reply("Access denied."); return
            count = self._send_queue.qsize()
            self._send_queue = __import__('asyncio').Queue()
            await reply(f"Send queue cleared ({count} messages dropped).")

        else:
            await reply(f"Unknown command '{cmd}'. Send HELP for a list of commands.")

    async def _is_admin(self, nick):
        p = await self.engine.db.get_player_by_nick(nick, self.network_name)
        return bool(p and p["is_admin"])

    async def _deliver_local(self, broadcasts: list):
        """
        broadcast_all  → escalate to BotManager so ALL networks get it
        broadcast_net  → only this network's channel
        broadcast_notice → NOTICE to specific nick on this network
        """
        if not broadcasts: return
        all_bc   = [b for b in broadcasts if b.scope == "all"]
        local_bc = [b for b in broadcasts if b.scope != "all"]
        if all_bc:
            if self.broadcast_callback:
                await self.broadcast_callback(all_bc)
            else:
                for b in all_bc: await self.say(b.message)
        for b in local_bc:
            if b.scope == "network" and b.network == self.network_name:
                await self.say(b.message)
            elif b.scope == "notice" and b.network == self.network_name and b.nick:
                await self.notice_nick(b.nick, b.message)


def _split(msg, max_len=400):
    chunks = []
    while len(msg) > max_len:
        cut = msg.rfind(" ", 0, max_len)
        if cut == -1: cut = max_len
        chunks.append(msg[:cut])
        msg = msg[cut:].lstrip()
    if msg: chunks.append(msg)
    return chunks
