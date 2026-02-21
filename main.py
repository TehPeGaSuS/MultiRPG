#!/usr/bin/env python3
"""main.py — Multi IdleRPG entry point. Run from any directory."""
import sys, os
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path: sys.path.insert(0, _HERE)

import asyncio, logging, signal, tomllib
from pathlib import Path
from db.database import Database
from engine.game_engine import GameEngine, Broadcast
from irc.bot import IRCBot

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
log = logging.getLogger("main")

CONFIG_PATH = Path(_HERE) / "config.toml"
DB_PATH     = Path(_HERE) / "multirpg.db"

DEFAULT_CONFIG = """\
[game]
self_clock = 5
limit_pen  = 0

[[networks]]
name    = "swiftirc"
host    = "irc.swiftirc.net"
port    = 6667
channel = "#multirpg"
nick    = "MultiRPG"
use_ssl = true
# nickserv_pass = "yourpassword"
# server_pass   = ""

[[networks]]
name    = "libera"
host    = "irc.libera.chat"
port    = 6697
channel = "#multirpg"
nick    = "MultiRPG"
use_ssl = true

[web]
host = "0.0.0.0"
port = 8080
"""

def load_config():
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(DEFAULT_CONFIG)
        log.info(f"Created config at {CONFIG_PATH} — edit and restart.")
        raise SystemExit(0)
    with open(CONFIG_PATH, "rb") as f: return tomllib.load(f)

class BotManager:
    def __init__(self): self.bots: list[IRCBot] = []
    def register(self, bot: IRCBot):
        self.bots.append(bot)
        bot.broadcast_callback = self.deliver_all   # cross-network routing
    async def deliver_all(self, broadcasts: list[Broadcast]):
        for b in broadcasts:
            for bot in self.bots:
                await bot.deliver(b)

async def game_tick_loop(engine, manager, self_clock):
    log.info(f"Tick loop started (self_clock={self_clock}s)")
    while True:
        try:
            broadcasts = await engine.tick()
            if broadcasts: await manager.deliver_all(broadcasts)
        except Exception as e:
            log.error(f"Tick error: {e}", exc_info=True)
        await asyncio.sleep(self_clock)

async def main():
    config     = load_config()
    db         = Database(DB_PATH)
    await db.connect()
    log.info(f"DB connected ({DB_PATH})")

    game_cfg   = config.get("game", {})
    self_clock = int(game_cfg.get("self_clock", 5))
    limit_pen  = int(game_cfg.get("limit_pen",  0))
    engine     = GameEngine(db, self_clock=self_clock, limit_pen=limit_pen)

    manager = BotManager()
    for net in config.get("networks", []):
        bot = IRCBot(
            network_name  = net["name"],
            host          = net["host"],
            port          = int(net["port"]),
            channel       = net["channel"],
            nick          = net["nick"],
            engine        = engine,
            nickserv_pass = net.get("nickserv_pass"),
            server_pass   = net.get("server_pass"),
            use_ssl       = bool(net.get("use_ssl", False)),
        )
        manager.register(bot)

    if not manager.bots:
        log.error("No networks in config.toml."); await db.close(); return

    web_runner = None
    try:
        from web.app import create_app
        import aiohttp.web as aio_web
        web_cfg    = config.get("web", {})
        web_runner = aio_web.AppRunner(create_app(db, engine, config.get('networks', [])))
        await web_runner.setup()
        await aio_web.TCPSite(web_runner,
            web_cfg.get("host","0.0.0.0"), int(web_cfg.get("port",8080))).start()
        log.info(f"Web server on http://{web_cfg.get('host','0.0.0.0')}:{web_cfg.get('port',8080)}")
    except ImportError as e:
        log.warning(f"Web server disabled ({e})")

    tasks = ([asyncio.create_task(bot.run(), name=f"irc-{bot.network_name}") for bot in manager.bots]
             + [asyncio.create_task(game_tick_loop(engine, manager, self_clock), name="game-tick")])
    log.info(f"Multi IdleRPG running on {len(manager.bots)} network(s), self_clock={self_clock}s")

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: [t.cancel() for t in tasks])
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        log.info("Shutting down...")
    finally:
        await db.close()
        if web_runner: await web_runner.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
