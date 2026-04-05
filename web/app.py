"""web/app.py — Leaderboard, live world map, and game info page."""
import json, time, collections
from pathlib import Path
from aiohttp import web
from db.database import Database

# ── Real IP (Cloudflare tunnel forwards CF-Connecting-IP) ─────────────────────
def get_ip(req) -> str:
    return (req.headers.get("CF-Connecting-IP")
            or req.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or req.transport.get_extra_info("peername", ("unknown",))[0])

# ── Rate limiter — sliding window, in-memory ──────────────────────────────────
class RateLimiter:
    def __init__(self, limit: int = 60, window: int = 60):
        """limit requests per window seconds per IP."""
        self.limit  = limit
        self.window = window
        self._hits: dict[str, collections.deque] = {}

    def is_allowed(self, ip: str) -> bool:
        now    = time.monotonic()
        dq     = self._hits.setdefault(ip, collections.deque())
        cutoff = now - self.window
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= self.limit:
            return False
        dq.append(now)
        return True

    def response_429(self):
        return web.Response(status=429, text="Too many requests — slow down.",
                            content_type="text/plain")

_rl = RateLimiter()  # configured in create_app() from config

STATIC = Path(__file__).parent / "static"

async def handle_favicon(req):
    import os
    favicon = os.path.join(os.path.dirname(__file__), "..", "favicon.svg")
    with open(favicon, "r") as f:
        return web.Response(text=f.read(), content_type="image/svg+xml")

def create_app(db: Database, engine=None, networks=None, web_cfg=None) -> web.Application:
    web_cfg = web_cfg or {}
    rl_limit  = int(web_cfg.get("rate_limit",  60))
    rl_window = int(web_cfg.get("rate_window", 60))
    rl = RateLimiter(limit=rl_limit, window=rl_window)

    @web.middleware
    async def _middleware(req, handler):
        import logging
        _log = logging.getLogger("web.access")
        ip = get_ip(req)
        if not rl.is_allowed(ip):
            _log.warning("%s RATE LIMITED %s %s", ip, req.method, req.path)
            return rl.response_429()
        resp = await handler(req)
        ver = f"{req.version.major}.{req.version.minor}"
        _log.info('%s "%s %s HTTP/%s" %s', ip, req.method, req.path, ver, resp.status)
        return resp

    app = web.Application(middlewares=[_middleware])
    app["db"] = db
    app["engine"] = engine
    app["networks"] = networks or []
    app["hof_type"] = engine.hof_type if engine else "level"
    app.router.add_get("/",            handle_index)
    app.router.add_get("/favicon.svg",   handle_favicon)
    app.router.add_get("/map",         handle_map)
    app.router.add_get("/info",        handle_info)
    app.router.add_get("/admin",       handle_admin)
    app.router.add_get("/quest",       handle_quest)
    app.router.add_get("/player/{username}", handle_player)
    app.router.add_get("/play",        handle_play)
    if (engine and engine.hof_type != "none"):
        app.router.add_get("/hof",         handle_hof)
    app.router.add_get("/api/quest",   handle_api_quest)
    app.router.add_get("/api/players", handle_api_players)
    app.router.add_get("/api/events",  handle_api_events)
    if STATIC.exists():
        app.router.add_static("/static", STATIC, name="static")
    return app

# ── Shared ────────────────────────────────────────────────────────────────────

def make_nav(show_hof: bool = True) -> str:
    hof_link = '  <a href="/hof">🏆 Hall of Fame</a>' if show_hof else ""
    return f"""<nav>
  <a href="/">🏆 Leaderboard</a>
  <a href="/map">🌐 World Map</a>
  <a href="/info">📖 Game Info</a>
  <a href="/quest">🧭 Quest</a>
  <a href="/play">🕹️ Where to Play</a>
{hof_link}
  <a href="/admin">🔑 Admin</a>
</nav>"""

COMMON_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@400;700&family=Inter:wght@400;500&display=swap');
:root{--gold:#b8953f;--dark:#111318;--panel:#1c1f26;--panel2:#22262f;--border:#2e3340;--text:#d4d8e0;--muted:#7a8194;--accent:#4a7fa5}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--dark);color:var(--text);font-family:'Inter',sans-serif;min-height:100vh;
     background-image:radial-gradient(ellipse at 50% 0%,#181c24 0%,#111318 70%)}
header{text-align:center;padding:1.6rem 1rem 0.7rem;border-bottom:1px solid var(--border)}
header h1{font-family:'Cinzel',serif;font-size:2rem;color:var(--gold);letter-spacing:0.15em;
           text-shadow:0 0 20px rgba(184,149,63,0.3)}
header p{color:var(--muted);margin-top:0.3rem;font-style:italic;font-size:0.9rem}
nav{text-align:center;padding:0.6rem 0.5rem;border-bottom:1px solid var(--border);
     background:var(--panel);display:flex;flex-wrap:wrap;justify-content:center;gap:0.2rem 0}
nav a{color:var(--muted);text-decoration:none;padding:0.25rem 0.7rem;
       font-family:'Cinzel',serif;font-size:0.78rem;letter-spacing:0.07em;
       transition:color 0.2s;white-space:nowrap}
nav a:hover{color:var(--gold)}
.container{max-width:1100px;margin:2rem auto;padding:0 1.5rem}
"""

def page(title, body, extra_css="", extra_head="", show_hof=True):
    return f"""<!DOCTYPE html><html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" type="image/svg+xml" href="/favicon.svg"><title>Multi IdleRPG — {title}</title>
<style>{COMMON_CSS}{extra_css}</style>{extra_head}</head>
<body>
<header><h1>⚔ Multi IdleRPG ⚔</h1><p>The ancient art of doing absolutely nothing</p></header>
{make_nav(show_hof)}
{body}
</body></html>"""

def _show_hof(req) -> bool:
    """Return True if HoF is enabled (not hof_type=none)."""
    return req.app.get("hof_type", "level") != "none"

# ── API ───────────────────────────────────────────────────────────────────────

async def handle_api_players(req):
    db = req.app["db"]
    players = await db.get_all_players()
    data = []
    for p in players:
        isum = await db.get_item_sum(p["id"])
        data.append({
            "username":  p["username"],
            "network":   p["network"],
            "level":     p["level"],
            "char_class": p["class"],
            "alignment": p["alignment"],
            "x":         p["pos_x"],
            "y":         p["pos_y"],
            "is_online": bool(p["is_online"]),
            "ttl":       p["ttl"],
            "item_sum":  isum,
        })
    return web.Response(text=json.dumps(data), content_type="application/json")

async def handle_api_events(req):
    db = req.app["db"]
    events = await db.get_recent_events(50)
    data = [{"type": e["event_type"], "message": e["message"],
             "ts": e["created_at"]} for e in events]
    return web.Response(text=json.dumps(data), content_type="application/json")

# ── Leaderboard ───────────────────────────────────────────────────────────────

async def handle_index(req):
    db = req.app["db"]
    current_round = await db.get_round()
    players = await db.get_all_players()
    rows = ""
    for p in players:
        isum   = await db.get_item_sum(p["id"])
        amap   = {"g": "good", "e": "evil", "n": "neutral"}
        status = "🟢" if p["is_online"] else "⚫"
        s      = abs(int(p["ttl"]))
        d, s   = divmod(s, 86400); h, s = divmod(s, 3600); m, s = divmod(s, 60)
        ttl_s  = f"{d}d {h:02d}:{m:02d}:{s:02d}"
        rows += f"""<tr>
          <td>{status} <a href="/player/{p['username']}">{p['username']}</a></td><td>{p['network']}</td>
          <td>{p['level']}</td><td>{p['class']}</td>
          <td>{amap.get(p['alignment'],'neutral')}</td>
          <td>{ttl_s}</td><td>{isum}</td>
        </tr>"""
    css = """
table{width:100%;border-collapse:collapse;background:var(--panel);
      border:1px solid var(--border);border-radius:4px;overflow:hidden}
.table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch;border-radius:4px}
thead tr{background:linear-gradient(180deg,#2a1f0a,#1a1408)}
th{font-family:'Cinzel',serif;color:var(--gold);padding:0.85rem 1rem;text-align:left;
   font-size:0.8rem;letter-spacing:0.12em;border-bottom:1px solid var(--border)}
td{padding:0.75rem 1rem;border-bottom:1px solid #231b09;font-size:0.95rem}
tr:hover td{background:rgba(201,168,76,0.05)}
td a{color:var(--text);text-decoration:none;border-bottom:1px solid var(--border);transition:color 0.2s,border-color 0.2s}
td a:hover{color:var(--gold);border-bottom-color:var(--gold)}
td a:visited{color:var(--muted)}
tr:last-child td{border-bottom:none}
"""
    body = """<div class="container"><div class="table-wrap"><table id="lb">
  <thead><tr>
    <th>Player</th><th>Network</th><th>Level</th><th>Class</th>
    <th>Alignment</th><th>Next Level</th><th>Item Sum</th>
  </tr></thead>
  <tbody id="lb-body"><tr><td colspan="7" style="text-align:center;color:var(--muted)">Loading...</td></tr></tbody>
</table></div></div>
<script>
const AMAP = {g:'good', e:'evil', n:'neutral'};
function fmtTTL(s) {
  s = Math.abs(Math.round(s));
  const d = Math.floor(s/86400); s %= 86400;
  const h = Math.floor(s/3600);  s %= 3600;
  const m = Math.floor(s/60);    s %= 60;
  return `${d}d ${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
}
async function refreshLeaderboard() {
  try {
    const players = await fetch('/api/players').then(r => r.json());
    const tbody = document.getElementById('lb-body');
    tbody.innerHTML = players.map(p => `
      <tr>
        <td>${p.is_online ? '🟢' : '⚫'} <a href="/player/${p.username}">${p.username}</a></td>
        <td>${p.network}</td>
        <td>${p.level}</td>
        <td>${p.char_class}</td>
        <td>${AMAP[p.alignment] || 'neutral'}</td>
        <td>${fmtTTL(p.ttl)}</td>
        <td>${p.item_sum}</td>
      </tr>`).join('');
  } catch(e) { console.warn('Leaderboard refresh failed', e); }
}
refreshLeaderboard();
setInterval(refreshLeaderboard, 10000);
</script>"""
    return web.Response(text=page("Leaderboard", body, css, show_hof=_show_hof(req)),
        content_type="text/html")

# ── World Map ─────────────────────────────────────────────────────────────────

# Original IdleRPG map region labels (from res0 & Jeb's basemap.png).
# The basemap is 500x500. These coords place labels at the visual centre of
# each region as they appear on the original basemap, reproduced faithfully.
# 4x4 invisible grid zones — each cell is 125x125px on the 500x500 world map
# Geography-matched pirate names, pins nudged off-grid for organic feel
GRID_ZONES = [
    "The Roaring Swell",  "Deadman's Cove",    "Serpent's Current", "Whaler's Notch",
    "Kraken Deep",        "Smuggler's Run",     "Mermaid's Lagoon",  "Freeport",
    "Cutthroat Cove",     "The Howling Gale",   "Marauder's Bay",    "The Devil's Passage",
    "The Abyssal Plain",  "The Frozen South",   "Antarctica's Edge", "The Endless Deep",
]

# Pin positions — nudged off cell-center for organic feel (matches rendered map)
GRID_PINS = [
    ( 54,  72), (193,  70), (307,  74), (446,  68),
    ( 72, 180), (180, 196), (320, 179), (428, 194),
    ( 69, 302), (179, 320), (321, 306), (430, 321),
    ( 70, 429), (181, 444), (319, 428), (428, 445),
]

MAP_REGIONS = [
    [GRID_ZONES[i], GRID_PINS[i][0], GRID_PINS[i][1], "region"]
    for i in range(16)
]

def nearest_landmark(x, y):
    col = min(int(x // 125), 3)
    row = min(int(y // 125), 3)
    return GRID_ZONES[row * 4 + col]


async def handle_map(req):
    regions_js    = json.dumps(MAP_REGIONS)
    grid_zones_js = json.dumps(GRID_ZONES)
    current_round = await req.app["db"].get_round()
    css = """
body{overflow:hidden;height:100vh;display:flex;flex-direction:column}
header{flex-shrink:0}nav{flex-shrink:0}
.map-area{flex:1;display:flex;overflow:hidden;min-height:0}
.map-wrap{flex:1;display:flex;align-items:center;justify-content:center;
           padding:0.75rem;overflow:hidden;min-height:0}
canvas{border:1px solid var(--border);border-radius:4px;display:block;
       box-shadow:0 0 40px rgba(201,168,76,0.1);cursor:crosshair;
       max-width:100%;max-height:100%}
.sidebar{width:235px;flex-shrink:0;border-left:1px solid var(--border);
          padding:1rem;display:flex;flex-direction:column;gap:0.8rem;overflow-y:auto}
.sidebar h3{font-family:'Cinzel',serif;color:var(--gold);font-size:0.75rem;
             letter-spacing:0.1em;margin-bottom:0.15rem;text-transform:uppercase}
#hover-info{background:var(--panel);border:1px solid var(--border);border-radius:3px;
             padding:0.6rem;font-size:0.82rem;min-height:90px;color:#a08050;line-height:1.8}
#hover-info b{color:var(--text)}
#hover-info .region-name{color:var(--gold);font-style:italic;font-family:'Cinzel',serif;font-size:0.8rem}
#status-bar{font-family:'Cinzel',serif;font-size:0.68rem;color:#6a5030;letter-spacing:0.08em;
             text-align:center;margin-top:auto;padding-top:0.5rem;border-top:1px solid var(--border)}
#legend{font-size:0.82rem;line-height:2.3}
.legend-row{display:flex;align-items:center;gap:0.5rem}
.dot{width:10px;height:10px;border-radius:50%;display:inline-block;flex-shrink:0}
.dot-on{background:#ff44cc;box-shadow:0 0 6px #ff44cc}
.dot-off{background:#cc2222;box-shadow:0 0 3px #cc2222}
.dot-off{background:#dc2828;box-shadow:0 0 8px rgba(220,40,40,0.8)}
#tooltip{position:fixed;background:rgba(13,10,6,0.95);border:1px solid var(--border);
          color:var(--text);padding:0.25rem 0.55rem;border-radius:3px;font-size:0.75rem;
          pointer-events:none;display:none;z-index:100;font-family:'Cinzel',serif;
          white-space:nowrap;box-shadow:0 3px 10px rgba(0,0,0,0.6)}
"""
    body = f"""
<div class="map-area">
  <div class="map-wrap"><canvas id="map" width="500" height="500"></canvas></div>
  <div class="sidebar">
    <div>
      <h3>Hover Info</h3>
      <div id="hover-info">Hover over the map<br>to inspect players<br>and terrain.</div>
    </div>
    <div>
      <h3>Legend</h3>
      <div id="legend">
        <div class="legend-row"><span class="dot dot-on"></span> Online player</div>
        <div class="legend-row"><span class="dot dot-off"></span> Offline player</div>
      </div>
    </div>
    <div id="status-bar">Round {current_round} — Loading…</div>
  </div>
</div>
<div id="tooltip"></div>
<script>
const canvas    = document.getElementById('map');
const ctx       = canvas.getContext('2d');
const W = 500, H = 500;
let players = [];

// ── Region data ─────────────────────────────────────────────────────────────
const REGIONS = {regions_js};
const GRID_ZONES = {grid_zones_js};

// ── Load map image as background ──────────────────────────────────────────────
const off = document.createElement('canvas'); off.width=W; off.height=H;
const offCtx = off.getContext('2d');
const mapImg = new Image();
mapImg.src = '/static/map.png';
mapImg.onload = () => {{
  offCtx.drawImage(mapImg, 0, 0, W, H);
  drawReady = true;
  draw();
}};
let drawReady = false;

// ── Render ────────────────────────────────────────────────────────────────────
function drawDot(ctx, x, y, online) {{
  const color  = online ? [255, 68, 204] : [220, 40, 40];
  const [r,g,b] = color;
  const outerR = online ? 12 : 10;
  const innerR = online ? 4.5 : 3.5;
  // Outer glow
  const g2 = ctx.createRadialGradient(x, y, 0, x, y, outerR);
  g2.addColorStop(0, `rgba(${{r}},${{g}},${{b}},0.75)`);
  g2.addColorStop(1, `rgba(${{r}},${{g}},${{b}},0)`);
  ctx.beginPath(); ctx.arc(x, y, outerR, 0, Math.PI*2);
  ctx.fillStyle = g2; ctx.fill();
  // Inner dot
  ctx.beginPath(); ctx.arc(x, y, innerR, 0, Math.PI*2);
  ctx.fillStyle = `rgba(${{r}},${{g}},${{b}},0.95)`; ctx.fill();
  ctx.strokeStyle = online ? '#ff44cc' : '#ff2020';
  ctx.lineWidth = 1; ctx.stroke();
}}

function draw() {{
  if (!drawReady) return;
  ctx.drawImage(off, 0, 0);
  // Offline first (behind online)
  for (const p of players.filter(p => !p.is_online)) drawDot(ctx, p.x, p.y, false);
  // Online on top
  for (const p of players.filter(p =>  p.is_online)) drawDot(ctx, p.x, p.y, true);
}}

// ── Data refresh ──────────────────────────────────────────────────────────────
async function fetchPlayers() {{
  try {{
    players = await (await fetch('/api/players')).json();
    const n = players.filter(p => p.is_online).length;
    document.getElementById('status-bar').textContent =
      `${{n}} online · ${{players.length}} total`;
    draw();
  }} catch(e) {{ document.getElementById('status-bar').textContent = 'Connection error'; }}
}}

// ── Grid-based zone lookup — always returns a zone name ──────────────────────
const CELL = 500 / 4;
function regionAt(mx, my) {{
  const col = Math.min(Math.floor(mx / CELL), 3);
  const row = Math.min(Math.floor(my / CELL), 3);
  return GRID_ZONES[row * 4 + col] || null;
}}

// ── Hover ─────────────────────────────────────────────────────────────────────
const hoverEl = document.getElementById('hover-info');
const tooltip  = document.getElementById('tooltip');

canvas.addEventListener('mousemove', e => {{
  const r  = canvas.getBoundingClientRect();
  const mx = (e.clientX - r.left) * (W / r.width);
  const my = (e.clientY - r.top)  * (H / r.height);
  const hit    = players.find(p => Math.hypot(p.x - mx, p.y - my) < 8);
  const region = regionAt(mx, my);
  const locLine = region
    ? `<span class="region-name">${{region}}</span>`
    : `<b>[${{Math.round(mx)}}, ${{Math.round(my)}}]</b>`;

  if (hit) {{
    hoverEl.innerHTML =
      `<b>${{hit.username}}</b> @${{hit.network}}<br>` +
      `Lv.${{hit.level}} ${{hit.char_class}}<br>` +
      `[${{hit.x}}, ${{hit.y}}] · ${{hit.is_online ? '🟢' : '🔴'}}<br>` +
      locLine;
    tooltip.style.cssText = `display:block;left:${{e.clientX+14}}px;top:${{e.clientY-10}}px`;
    tooltip.textContent = `${{hit.username}} lv.${{hit.level}}`;
  }} else {{
    hoverEl.innerHTML = locLine;
    tooltip.style.display = 'none';
  }}
}});

canvas.addEventListener('mouseleave', () => {{
  hoverEl.innerHTML = 'Hover over the map<br>to inspect players<br>and regions.';
  tooltip.style.display = 'none';
}});

fetchPlayers();
setInterval(fetchPlayers, 5000);
</script>"""

    return web.Response(text=page("World Map", body, css, show_hof=_show_hof(req)), content_type="text/html")

# ── Game Info ─────────────────────────────────────────────────────────────────
# Faithful reproduction of the original idlerpg.net/info.php page,
# adapted for this multi-network implementation.

async def handle_info(req):
    engine   = req.app.get("engine")
    rp_base  = engine.rp_base  if engine and hasattr(engine, "rp_base")  else 600
    rp_step  = engine.rp_step  if engine and hasattr(engine, "rp_step")  else 1.21
    win_level = engine.win_level if engine and hasattr(engine, "win_level") else 40
    css = """
.info{max-width:820px;margin:2rem auto;padding:0 1.5rem 3rem}
h2{font-family:'Cinzel',serif;color:var(--gold);font-size:1.1rem;letter-spacing:0.08em;
   margin:2.2rem 0 0.65rem;padding-bottom:0.3rem;border-bottom:1px solid var(--border)}
h2:first-of-type{margin-top:0}
p{margin:0.55rem 0;line-height:1.8}
a{color:var(--gold)}a:hover{text-decoration:underline}
pre{background:var(--panel);border:1px solid var(--border);border-radius:3px;
    padding:0.55rem 1rem;margin:0.5rem 0;font-family:monospace;font-size:0.92rem;
    color:#ffe88a;overflow-x:auto}
code{font-family:monospace;color:#ffe88a;font-size:0.92rem}
table{border-collapse:collapse;margin:0.7rem 0;width:100%}
th,td{padding:0.4rem 0.9rem;border:1px solid var(--border);font-size:0.88rem;text-align:left}
th{font-family:'Cinzel',serif;color:var(--gold);background:var(--panel);
   font-size:0.75rem;letter-spacing:0.07em}
td:first-child{color:#ffe88a;font-family:monospace}
ul{margin:0.4rem 0 0.4rem 1.5rem;line-height:1.9}
.note{color:#a08050;font-style:italic;font-size:0.88rem}
"""
    body = f"""<div class="info">

<h2>What is Multi IdleRPG?</h2>
<p>The Multi IdleRPG is just what it sounds like: an RPG in which the players idle. In addition
to merely gaining levels, players can find items and battle other players. However, this is
all done for you; you just idle. There are no set classes; you can name your character anything
you like, and have its class be anything you like, as well.</p>

<h2>Registering</h2>
<p>To register, simply:</p>
<pre>/msg MultiRPG REGISTER &lt;char name&gt; &lt;password&gt; &lt;char class&gt;</pre>
<p>Where <code>char name</code> can be up to 16 chars long, <code>password</code> up to 8
characters, and <code>char class</code> up to 30 chars. Character names are unique across
all networks — you cannot register the same name on two different networks.</p>

<h2>Logging In</h2>
<pre>/msg MultiRPG LOGIN &lt;char name&gt; &lt;password&gt;</pre>
<p class="note">This is a p0 (see <a href="#penalties">Penalties</a>) command.</p>

<h2>Logging Out</h2>
<pre>/msg MultiRPG LOGOUT</pre>
<p class="note">This is a p20 (see <a href="#penalties">Penalties</a>) command.</p>

<h2>Changing Your Password</h2>
<pre>/msg MultiRPG NEWPASS &lt;new password&gt;</pre>
<p class="note">This is a p0 command.</p>
<p>If you have forgotten your password, message an op in the channel — they can use the
admin <code>CHPASS</code> command to reset it for you.</p>

<h2>Removing Your Account</h2>
<pre>/msg MultiRPG REMOVEME</pre>
<p class="note">This is a p0 command :^)</p>

<h2>Changing Your Alignment</h2>
<pre>/msg MultiRPG ALIGN &lt;good|neutral|evil&gt;</pre>
<p class="note">This is a p0 command.</p>
<p>Your alignment can affect certain aspects of the game. You may align with good, neutral,
or evil. <em>Good</em> users have a 10% boost to their item sum for battles, and a 1/12
chance each day that they, along with a good friend, will have the light of their god shine
upon them, accelerating them 5–12% toward their next level. <em>Evil</em> users have a 10%
detriment to their item sum for battles, but have a 1/8 chance each day that they will either
a) attempt to steal an item from a good user, or b) be forsaken (for 1–5% of their TTL) by
their evil god. Good users have a 1/50 chance of landing a
<a href="#critstrike">Critical Strike</a> when battling, while evil users have a 1/20 chance.
All users start as neutral.</p>

<h2>Other Commands</h2>
<pre>/msg MultiRPG WHOAMI</pre>
<p>Shows whether you are logged in, and your time to next level. p0.</p>
<pre>/msg MultiRPG STATUS [username]</pre>
<p>Shows full stats for yourself or another player. p0.</p>
<pre>/msg MultiRPG QUEST</pre>
<p>Shows the active quest, its participants, and time remaining. p0.</p>
<pre>/msg MultiRPG TOP</pre>
<p>Shows the top 5 players by level. p0.</p>

<h2>Levelling</h2>
<p>To gain levels, you must only be logged in and idle. The time between levels is based on
your character level, calculated by the formula:</p>
<pre>{rp_base} × ({rp_step} ^ YOUR_LEVEL)  seconds</pre>
<p>Very high levels (above 60) are calculated differently:</p>
<pre>(time to level at 60) + (86400 × (level − 60))  seconds</pre>

<a name="penalties"></a>
<h2>Penalties</h2>
<p>If you do something other than idle — part, quit, talk in the channel, change your nick,
or notice the channel — you are penalized. The penalties are time in seconds added to your
next time to level, based on your character level:</p>
<table>
  <tr><th>Event</th><th>Formula</th></tr>
  <tr><td>Nick change</td><td>30 × (1.14 ^ YOUR_LEVEL)</td></tr>
  <tr><td>Part</td><td>200 × (1.14 ^ YOUR_LEVEL)</td></tr>
  <tr><td>Quit</td><td>20 × (1.14 ^ YOUR_LEVEL)</td></tr>
  <tr><td>LOGOUT command</td><td>20 × (1.14 ^ YOUR_LEVEL)</td></tr>
  <tr><td>Being Kicked</td><td>250 × (1.14 ^ YOUR_LEVEL)</td></tr>
  <tr><td>Channel privmsg</td><td>[message length] × (1.14 ^ YOUR_LEVEL)</td></tr>
  <tr><td>Channel notice</td><td>[message length] × (1.14 ^ YOUR_LEVEL)</td></tr>
</table>
<p>So, a level 25 character changing their nick would be penalized
<code>30 × (1.14^25) = 793 seconds</code> towards their next level.</p>
<p class="note">Penalty shorthand is p[num]. Nick change = p30, part = p200, quit = p20.
Messages and notices are p[length of message in characters].</p>

<h2>Items</h2>
<p>Each time you level, you find an item. You can find an item as high as
<code>1.5 × YOUR_LEVEL</code> (unless you find a <a href="#uniqueitems">unique item</a>).
There are 10 types of items: trinket, amulet, idol, cutlass, tricorn, coat, gauntlets,
buckler, breeches, and sea boots. When you find an item with a level higher than
your current item of that type, you equip it. The exact item level formula is:</p>
<pre>for each number from 1 to YOUR_LEVEL×1.5:
    you have a 1 / (1.4 ^ number) chance to find an item at this level</pre>

<h2>Battle</h2>
<p>Each time you level, if your level is less than 25, you have a 25% chance to challenge
someone to combat. If your level is ≥ 25, you always challenge someone. A random online
opponent is chosen. Victory is decided like so:</p>
<ul>
  <li>Your item levels are summed (good: +10%, evil: −10%).</li>
  <li>Their item levels are summed (same modifiers).</li>
  <li>A random number between 0 and your sum is taken.</li>
  <li>A random number between 0 and their sum is taken.</li>
  <li>The higher roll wins.</li>
</ul>
<p>If you win, your time to next level is reduced by:</p>
<pre>max(OPPONENT_LEVEL/4, 7) / 100  ×  YOUR_TTL</pre>
<p>If you lose, you are penalized:</p>
<pre>max(OPPONENT_LEVEL/7, 7) / 100  ×  YOUR_TTL</pre>
<p>As of v3.0, if more than 15% of online players are level 45+, a random level 45+ user
will battle another random player every 20 minutes to speed up levelling among veterans.</p>
<p>Also as of v3.0, the <a href="#grid">grid system</a> can cause collisions between
players, which may also trigger battle.</p>

<a name="uniqueitems"></a>
<h2>Unique Items</h2>
<p>After level 25, you have a 1/40 chance per level-up to find a unique item:</p>
<table>
  <tr><th>Name</th><th>Slot</th><th>Item Level</th><th>Required Level</th></tr>
  <tr><td>The Admiral's Grand Tricorn</td><td>tricorn</td><td>50–74</td><td>25+</td></tr>
  <tr><td>Davy Jones' Cursed Trinket</td><td>trinket</td><td>50–74</td><td>25+</td></tr>
  <tr><td>The Kraken Hunter's Coat</td><td>coat</td><td>75–99</td><td>30+</td></tr>
  <tr><td>The Sea Witch's Amulet</td><td>amulet</td><td>100–124</td><td>35+</td></tr>
  <tr><td>Blackbeard's Fury Cutlass</td><td>cutlass</td><td>150–174</td><td>40+</td></tr>
  <tr><td>The Dead Man's Cutlass of Ruin</td><td>cutlass</td><td>175–200</td><td>45+</td></tr>
  <tr><td>Navigator's Enchanted Sea Boots</td><td>sea boots</td><td>250–300</td><td>48+</td></tr>
  <tr><td>The Cannon of Doom</td><td>cutlass</td><td>300–350</td><td>52+</td></tr>
</table>

<h2>The Hand of God</h2>
<p>Every online user has a roughly 1/20 chance per day of a Hand of God affecting them.
A HoG can help or hurt your character by carrying it 5–75% towards or away from its next
time to level. The odds are in your favor: 80% chance to help, 20% chance to smite.</p>
<p>Admins may also summon the HoG at their whim via the <code>HOG</code> command.</p>

<a name="critstrike"></a>
<h2>Critical Strike</h2>
<p>If a challenger wins a battle, they have a 1/35 chance (1/50 for good, 1/20 for evil)
of landing a Critical Strike. The opponent is penalized:</p>
<pre>((random number from 5 to 25) / 100)  ×  OPPONENT'S_TTL</pre>

<h2>Team Battles</h2>
<p>Every online user has roughly a 1/4 chance per day of being in a team battle. Three random
online players battle three others. If the first group wins, 20% of the lowest member's TTL is
removed from all three clocks. If they lose, 20% is added.</p>

<h2>Calamities</h2>
<p>Each online user has roughly a 1/8 chance per day of a calamity: either a) slowed 5–12%
of their TTL, or b) one item loses 10% of its value.</p>

<h2>Godsends</h2>
<p>Each online user has roughly a 1/8 chance per day of a godsend: either a) accelerated
5–12% toward their next level, or b) one item gains 10% of its value.</p>

<h2>Quests</h2>
<p>Four level {win_level}+ users that have been online for more than 10 hours are chosen to go on a
quest. There are two types: <em>time-based</em> (lasting 12–24 hours) and
<em>grid-based</em> (questers must walk to two map coordinates). On success, all four
questers have 25% of their TTL removed. If any quester is penalized before the quest ends,
all online users suffer a p15 punishment.</p>

<a name="grid"></a>
<h2>Grid System</h2>
<p>The IRPG has a 500×500 grid on which players walk. Every second, each player steps up,
down, or neither, and left, right, or neither, with equal chance. If two players occupy the
same tile, there is a 1/(number of online players) chance they battle. Grid-based quests
require questers to walk to specific coordinates — the bot walks for you, though at a slower
pace to avoid accidents.</p>

<a name="stealing"></a>
<h2>Item Stealing</h2>
<p>After winning a battle, a challenger has a slightly less than 2% chance of stealing one
of the loser's items — but only if the loser's item of that type is higher level. The
challenger's old item is given to the loser in a moment of pity.</p>

<h2>Credits</h2>
<p>Idle RPG was created by <strong>jotun</strong>. Original map by <strong>res0</strong>
and <strong>Jeb</strong>.</p>
<p>This pirate-themed multi-network Python implementation was built from scratch,
honouring the original game logic as closely as possible.</p>

</div>"""
    return web.Response(text=page("Game Info", body, css, show_hof=_show_hof(req)), content_type="text/html")


# ── Admin Commands page ───────────────────────────────────────────────────────

async def handle_admin(req):
    css = """
.info{max-width:820px;margin:2rem auto;padding:0 1.5rem 3rem}
h2{font-family:'Cinzel',serif;color:var(--gold);font-size:1.1rem;letter-spacing:0.08em;
   margin:2.2rem 0 0.65rem;padding-bottom:0.3rem;border-bottom:1px solid var(--border)}
h2:first-of-type{margin-top:0}
h3{font-family:'Cinzel',serif;color:#c09840;font-size:0.9rem;letter-spacing:0.06em;
   margin:1.4rem 0 0.4rem}
p{margin:0.55rem 0;line-height:1.8}
pre{background:var(--panel);border:1px solid var(--border);border-radius:3px;
    padding:0.55rem 1rem;margin:0.4rem 0;font-family:monospace;font-size:0.9rem;
    color:#ffe88a;overflow-x:auto}
code{font-family:monospace;color:#ffe88a;font-size:0.9rem}
table{border-collapse:collapse;margin:0.6rem 0;width:100%}
th,td{padding:0.4rem 0.9rem;border:1px solid var(--border);font-size:0.88rem;text-align:left}
th{font-family:'Cinzel',serif;color:var(--gold);background:var(--panel);
   font-size:0.75rem;letter-spacing:0.07em}
td:first-child{color:#ffe88a;font-family:monospace;white-space:nowrap}
ul{margin:0.4rem 0 0.4rem 1.5rem;line-height:1.9}
.note{background:rgba(201,168,76,0.07);border-left:3px solid var(--gold);
      padding:0.5rem 0.8rem;margin:0.8rem 0;font-size:0.88rem;color:#c09840;line-height:1.7}
"""
    body = """<div class="info">

<h2>Game Control</h2>

<h3>HOG — Hand of God</h3>
<p>Summon the Hand of God immediately. Randomly helps or hurts one online player
by 5-75% of their TTL (80% chance to help, 20% to hinder).</p>
<pre>/msg MultiRPG HOG</pre>

<h3>FORCEQUEST — Force-Start a Quest</h3>
<p>Force-start a quest immediately, ignoring the quest timer and eligibility checks.
Useful for testing or manually kicking off a quest when the queue seems stuck.</p>
<pre>/msg MultiRPG FORCEQUEST</pre>
<p class="note">Regular users can use <code>QUEST</code> to check the active quest status.
<code>FORCEQUEST</code> is the admin-only command to force-start one.</p>

<h3>PAUSE — Toggle Pause Mode</h3>
<p>Stops the tick loop completely — no TTL countdown, no events, no movement.
Run again to resume. Use before maintenance or when investigating issues.</p>
<pre>/msg MultiRPG PAUSE</pre>

<h3>SILENT &lt;mode&gt; — Silence Control</h3>
<p>Control how much the bot speaks.</p>
<table>
  <tr><th>Mode</th><th>Effect</th></tr>
  <tr><td>0</td><td>All messages enabled (default)</td></tr>
  <tr><td>1</td><td>Channel messages disabled</td></tr>
  <tr><td>2</td><td>Private messages and notices disabled</td></tr>
  <tr><td>3</td><td>All messages disabled</td></tr>
</table>
<pre>/msg MultiRPG SILENT 1
/msg MultiRPG SILENT 0</pre>

<h3>CLEARQ — Clear Message Queue</h3>
<p>Clears the outgoing message queue. Use if the bot is backed up after a flood
or a runaway event.</p>
<pre>/msg MultiRPG CLEARQ</pre>

<h2>Player Management</h2>

<h3>PUSH &lt;username&gt; &lt;seconds&gt;</h3>
<p>Subtract seconds from a player's TTL, pushing them toward their next level.
Use to correct erroneous penalties. Negative values add time.</p>
<pre>/msg MultiRPG PUSH PotHead 3600</pre>

<h3>CHPASS &lt;username&gt; &lt;new password&gt;</h3>
<p>Change a player's password. Use when a player has forgotten theirs.</p>
<pre>/msg MultiRPG CHPASS PotHead newpass123</pre>

<h3>CHCLASS &lt;username&gt; &lt;new class&gt;</h3>
<p>Change a player's class name (up to 30 characters, spaces allowed).</p>
<pre>/msg MultiRPG CHCLASS PotHead Supreme Overlord of Mischief</pre>

<h3>CHUSER &lt;username&gt; &lt;new name&gt;</h3>
<p>Rename a character. The new name must not already exist on any network.
The player will need to log in again with the new name.</p>
<pre>/msg MultiRPG CHUSER PotHead HighPotHead</pre>

<h3>DEL &lt;username&gt;</h3>
<p>Permanently delete a player's account.</p>
<pre>/msg MultiRPG DEL PotHead</pre>

<h3>DELOLD &lt;days&gt;</h3>
<p>Remove all accounts not logged in within the last <code>&lt;days&gt;</code> days.</p>
<pre>/msg MultiRPG DELOLD 30</pre>

<h2>Admin Management</h2>

<h3>MKADMIN &lt;username&gt;</h3>
<p>Grant admin privileges to a character.</p>
<pre>/msg MultiRPG MKADMIN PotHead</pre>

<h3>DELADMIN &lt;username&gt;</h3>
<p>Revoke admin privileges from a character.</p>
<pre>/msg MultiRPG DELADMIN PotHead</pre>

<h2>Notes</h2>
<div class="note">
  Admin status is tied to a <strong>character name</strong>, not an IRC nick.
  A renamed character retains admin status.<br><br>
  No <code>PEVAL</code>, <code>DIE</code>, or <code>RESTART</code> commands exist.
  Use <code>sqlite3 multirpg.db</code> for bulk DB operations and your
  process manager (systemd, screen, etc.) to control the bot process.<br><br>
  For backups: <code>cp multipg.db multirpg.db.bak</code> or a cron job.
</div>

</div>"""
    return web.Response(text=page("Admin Commands", body, css, show_hof=_show_hof(req)), content_type="text/html")


# ── Quest API ─────────────────────────────────────────────────────────────────

async def handle_api_quest(req):
    engine = req.app.get("engine")
    if not engine:
        return web.Response(text=json.dumps({"active": False}),
                            content_type="application/json")
    import time
    q   = engine._quest
    now = int(time.time())
    if not q["questers"]:
        data = {"active": False}
    elif q["type"] == 1:
        data = {
            "active":    True,
            "type":      "time",
            "text":      q["text"],
            "time_left": max(0, q["qtime"] - now),
            "questers":  [{"username": x["username"], "network": x["network"],
                           "level": x["level"], "char_class": x["class"]}
                          for x in q["questers"]],
        }
    else:
        target = q["p1"] if q["stage"] == 1 else q["p2"]
        data = {
            "active":   True,
            "type":     "grid",
            "text":     q["text"],
            "stage":    q["stage"],
            "target":   target,
            "questers": [{"username": x["username"], "network": x["network"],
                          "level": x["level"], "char_class": x["class"],
                          "x": x.get("pos_x", 0), "y": x.get("pos_y", 0)}
                         for x in q["questers"]],
            "p1name": f"{q.get('p1name', '')} [{target[0]}, {target[1]}]".strip(),
            "p2name": f"{q.get('p2name', '')} [{q['p2'][0]}, {q['p2'][1]}]".strip() if q.get('p2') else "",
        }
    return web.Response(text=json.dumps(data), content_type="application/json")


# ── Quest page ────────────────────────────────────────────────────────────────

async def handle_quest(req):
    css = """
.quest-wrap{max-width:680px;margin:3rem auto;padding:0 1.5rem}
.no-quest{text-align:center;padding:4rem 2rem;color:#6a5030;font-style:italic;font-size:1.1rem}
.no-quest span{display:block;font-size:2.5rem;margin-bottom:1rem;opacity:0.4}
.quest-card{background:var(--panel);border:1px solid var(--border);border-radius:6px;
             overflow:hidden;box-shadow:0 4px 30px rgba(0,0,0,0.4)}
.quest-header{background:linear-gradient(180deg,#2a1f0a,#1a1408);
               padding:1.2rem 1.5rem;border-bottom:1px solid var(--border)}
.quest-header h2{font-family:'Cinzel',serif;color:var(--gold);font-size:1.1rem;
                  letter-spacing:0.12em;margin-bottom:0.4rem}
.quest-header p{color:#c09840;font-style:italic;line-height:1.7;font-size:0.95rem}
.quest-meta{padding:1rem 1.5rem;border-bottom:1px solid var(--border);
             display:flex;gap:2rem;flex-wrap:wrap}
.meta-item{font-size:0.82rem;color:#a08050}
.meta-item b{display:block;font-family:'Cinzel',serif;color:var(--gold);
              font-size:0.7rem;letter-spacing:0.1em;margin-bottom:0.2rem}
.meta-item span{color:var(--text);font-size:0.95rem}
#countdown{font-family:monospace;font-size:1.1rem;color:#ffe88a;letter-spacing:0.05em}
.questers{padding:1rem 1.5rem}
.questers h3{font-family:'Cinzel',serif;color:var(--gold);font-size:0.75rem;
              letter-spacing:0.12em;text-transform:uppercase;margin-bottom:0.8rem}
.quester{display:flex;align-items:center;gap:1rem;padding:0.65rem 0;
          border-bottom:1px solid #1f1709}
.quester:last-child{border-bottom:none}
.quester-num{font-family:'Cinzel',serif;color:#6a5030;font-size:0.8rem;
              width:1.5rem;flex-shrink:0;text-align:center}
.quester-info b{color:var(--text);display:block;font-size:0.95rem}
.quester-info span{color:#a08050;font-size:0.82rem}
.tag{display:inline-block;background:rgba(201,168,76,0.1);border:1px solid var(--border);
     border-radius:2px;padding:0.1rem 0.4rem;font-size:0.75rem;
     font-family:'Cinzel',serif;color:#c09840;margin-left:0.4rem}
.grid-coords{background:rgba(201,168,76,0.05);border:1px solid var(--border);
              border-radius:3px;padding:0.5rem 1rem;margin:0 1.5rem 1rem;
              font-size:0.85rem;color:#a08050}
.grid-coords b{color:var(--gold)}
"""
    body = """
<div class="quest-wrap">
  <div id="quest-content">
    <div class="no-quest"><span>⚔</span>Loading quest status…</div>
  </div>
</div>
<script>
function fmtTime(s) {
  if (s <= 0) return '00:00:00';
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sc = s % 60;
  const hms = `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(sc).padStart(2,'0')}`;
  return d > 0 ? `${d} day${d>1?'s':''}, ${hms}` : hms;
}

let _timeLeft = 0;
let _timer = null;

function renderQuest(q) {
  const el = document.getElementById('quest-content');
  if (!q.active) {
    el.innerHTML = `<div class="no-quest"><span>⚔</span>There is currently no active quest.</div>`;
    if (_timer) { clearInterval(_timer); _timer = null; }
    return;
  }

  const typeLabel = q.type === 'time' ? 'Time-based Quest' : 'Grid-based Quest';
  const questText = q.text.charAt(0).toUpperCase() + q.text.slice(1);

  let metaHtml = '';
  if (q.type === 'time') {
    _timeLeft = q.time_left;
    metaHtml = `
      <div class="meta-item">
        <b>Type</b><span>${typeLabel}</span>
      </div>
      <div class="meta-item">
        <b>Time to Completion</b><span id="countdown">${fmtTime(_timeLeft)}</span>
      </div>`;
  } else {
    const destName = q.stage === 1 ? q.p1name : q.p2name;
    metaHtml = `
      <div class="meta-item">
        <b>Type</b><span>${typeLabel}</span>
      </div>
      <div class="meta-item">
        <b>Stage</b><span>${q.stage} of 2</span>
      </div>
      <div class="meta-item">
        <b>Destination</b><span>${destName}</span>
      </div>`;
  }

  const questersHtml = q.questers.map((p, i) => `
    <div class="quester">
      <div class="quester-num">${i+1}</div>
      <div class="quester-info">
        <b>${p.username}<span class="tag">${p.network}</span></b>
        <span>Level ${p.level} ${p.char_class}${q.type==='grid' ? ` · [${p.x}, ${p.y}]` : ''}</span>
      </div>
    </div>`).join('');

  el.innerHTML = `
    <div class="quest-card">
      <div class="quest-header">
        <h2>⚔ Active Quest</h2>
        <p>To ${questText}.</p>
      </div>
      <div class="quest-meta">${metaHtml}</div>
      <div class="questers">
        <h3>Questers</h3>
        ${questersHtml}
      </div>
    </div>`;

  // Live countdown for time-based quests
  if (_timer) clearInterval(_timer);
  if (q.type === 'time') {
    _timer = setInterval(() => {
      _timeLeft = Math.max(0, _timeLeft - 1);
      const el = document.getElementById('countdown');
      if (el) el.textContent = fmtTime(_timeLeft);
    }, 1000);
  }
}

async function fetchQuest() {
  try {
    const q = await (await fetch('/api/quest')).json();
    renderQuest(q);
  } catch(e) {
    document.getElementById('quest-content').innerHTML =
      '<div class="no-quest"><span>⚠</span>Could not load quest data.</div>';
  }
}

fetchQuest();
setInterval(fetchQuest, 15000);
</script>"""

    return web.Response(text=page("Quest", body, css, show_hof=_show_hof(req)),
                        content_type="text/html")


# ── Where to Play ─────────────────────────────────────────────────────────────

async def handle_play(req):
    networks = req.app.get("networks", [])
    css = """
.play-wrap{max-width:680px;margin:3rem auto;padding:0 1.5rem 3rem}
.play-wrap h2{font-family:'Cinzel',serif;color:var(--gold);font-size:1rem;
               letter-spacing:0.1em;margin:0 0 1.5rem;padding-bottom:0.5rem;
               border-bottom:1px solid var(--border)}
.network-card{background:var(--panel);border:1px solid var(--border);border-radius:6px;
               margin-bottom:1.2rem;overflow:hidden;
               box-shadow:0 2px 15px rgba(0,0,0,0.3)}
.network-name{background:linear-gradient(180deg,#2a1f0a,#1a1408);
               padding:0.85rem 1.2rem;border-bottom:1px solid var(--border);
               font-family:'Cinzel',serif;color:var(--gold);font-size:1rem;
               letter-spacing:0.12em}
.network-fields{padding:0.5rem 0}
.field{display:flex;align-items:baseline;padding:0.45rem 1.2rem;
        border-bottom:1px solid #1a1206}
.field:last-child{border-bottom:none}
.field-label{font-family:'Cinzel',serif;font-size:0.7rem;letter-spacing:0.1em;
              color:#6a5030;text-transform:uppercase;width:6rem;flex-shrink:0}
.field-value{color:var(--text);font-size:0.92rem}
.field-value a{color:var(--gold);text-decoration:none}
.field-value a:hover{text-decoration:underline}
.tls-yes{color:#5c9e5c}
.tls-no{color:#9e5c5c}
.hint{text-align:center;color:#6a5030;font-style:italic;font-size:0.85rem;
       margin-top:2rem;line-height:1.8}
"""
    cards = ""
    for net in networks:
        name    = net.get("name", "Unknown")
        host    = net.get("host", "")
        port    = net.get("port", 6667)
        channel = net.get("channel", "")
        nick    = net.get("nick", "MultiRPG")
        tls     = net.get("use_ssl", False)
        tls_str = '<span class="tls-yes">Yes ✓</span>' if tls else '<span class="tls-no">No</span>'
        # Build an irc:// link
        scheme  = "ircs" if tls else "irc"
        chan_url = channel.lstrip("#")
        irc_url = f"{scheme}://{host}:{port}/{chan_url}"
        cards += f"""
<div class="network-card">
  <div class="network-name">{name}</div>
  <div class="network-fields">
    <div class="field"><span class="field-label">Host</span>
      <span class="field-value">{host}</span></div>
    <div class="field"><span class="field-label">Port</span>
      <span class="field-value">{port}</span></div>
    <div class="field"><span class="field-label">TLS</span>
      <span class="field-value">{tls_str}</span></div>
    <div class="field"><span class="field-label">Channel</span>
      <span class="field-value"><a href="{irc_url}">{channel}</a></span></div>
    <div class="field"><span class="field-label">Bot Nick</span>
      <span class="field-value">{nick}</span></div>
  </div>
</div>"""

    if not cards:
        cards = '<div class="no-quest"><span>🕹️</span>No networks configured.</div>'

    body = f"""<div class="play-wrap">
  <h2>🕹️ Where to Play</h2>
  {cards}
  <p class="hint">Click a channel name to open it in your IRC client.<br>
  All commands are sent via <strong>private message</strong> to the bot.<br>
  Example: Type <code>"/msg {networks[0].get("nick","MultiRPG") if networks else "MultiRPG"} HELP"</code> to get started.</p>
</div>"""

    return web.Response(text=page("Where to Play", body, css, show_hof=_show_hof(req)),
                        content_type="text/html")


# ── Player profile ────────────────────────────────────────────────────────────

ITEM_SLOTS = [
    "amulet", "idol", "tricorn", "gauntlets", "sea boots",
    "trinket", "breeches", "buckler", "coat", "cutlass",
]
SLOT_LABEL = {
    "amulet":"Amulet","idol":"Idol","tricorn":"Tricorn",
    "gauntlets":"Gauntlets","sea boots":"Sea Boots",
    "trinket":"Trinket","breeches":"Breeches",
    "buckler":"Buckler","coat":"Coat","cutlass":"Cutlass",
}

async def handle_player(req):
    import time as _time, datetime
    db       = req.app["db"]
    username = req.match_info["username"]
    p        = await db.get_player_any_network(username)
    if not p:
        body = f'<div class="container" style="padding:2rem"><p>Player <b>{username}</b> not found. <a href="/" style="color:var(--gold)">Back to leaderboard</a></p></div>'
        return web.Response(text=page("Not Found", body, show_hof=_show_hof(req)), content_type="text/html")

    items  = {r["slot"]: r for r in await db.get_items(p["id"])}
    isum   = sum(r["level"] for r in items.values())

    def fmt_ts(ts):
        if not ts: return "—"
        return datetime.datetime.utcfromtimestamp(ts).strftime("%a %-d %b %Y at %H:%M:%S UTC")

    def fmt_ttl(s):
        s = abs(int(s))
        d, s = divmod(s, 86400); h, s = divmod(s, 3600); m, s = divmod(s, 60)
        return f"{d} days, {h:02d}:{m:02d}:{s:02d}"

    def fmt_pen(s):
        return fmt_ttl(s) if s else "None"

    amap      = {"g":"Good","e":"Evil","n":"Neutral"}
    status    = "Online" if p["is_online"] else "Offline"
    alignment = amap.get(p["alignment"],"Neutral")
    total_pen = (p["pen_mesg"]+p["pen_nick"]+p["pen_part"]+
                 p["pen_kick"]+p["pen_quit"]+p["pen_quest"]+p["pen_logout"])

    def row(label, value, cls=""):
        return f'<tr><th>{label}</th><td class="{cls}">{value}</td></tr>'

    def item_row(slot):
        r     = items.get(slot)
        label = SLOT_LABEL.get(slot, slot.title())
        if not r or r["level"] == 0:
            return f'<tr><th>{label}</th><td class="muted">—</td></tr>'
        name  = f' <span class="iname">({r["name"]})</span>' if r.get("name") else ""
        return f'<tr><th>{label}</th><td><span class="ilvl">{r["level"]}</span>{name}</td></tr>'

    def pen_row(label, val):
        if not val:
            return f'<tr><th>{label}</th><td class="muted">None</td></tr>'
        return f'<tr><th>{label}</th><td class="pen">{fmt_pen(val)}</td></tr>'

    css = """
.pw{max-width:960px;margin:2rem auto;padding:0 1.5rem 3rem}
.pname{font-family:'Cinzel',serif;color:var(--gold);font-size:1.3rem;
        letter-spacing:0.08em;margin-bottom:0.3rem}
.psub{color:var(--muted);font-style:italic;font-size:0.9rem;margin-bottom:1.2rem}
.ptop{display:grid;grid-template-columns:1fr 1fr;gap:1.2rem;margin-bottom:1.2rem}
.pbot{display:grid;grid-template-columns:1fr 1fr;gap:1.2rem}
@media(max-width:750px){.ptop,.pbot{grid-template-columns:1fr}}
.card{background:var(--panel);border:1px solid var(--border);border-radius:5px;overflow:hidden}
.ct{font-family:'Cinzel',serif;color:var(--gold);font-size:0.68rem;letter-spacing:0.1em;
     text-transform:uppercase;padding:0.5rem 1rem;background:var(--panel2);
     border-bottom:1px solid var(--border)}
table{width:100%;border-collapse:collapse}
th{font-size:0.78rem;color:var(--muted);text-align:left;padding:0.38rem 1rem;
    width:9.5rem;border-bottom:1px solid var(--border);font-weight:500;white-space:nowrap}
td{font-size:0.86rem;color:var(--text);padding:0.38rem 1rem;
    border-bottom:1px solid var(--border);word-break:break-all}
tr:last-child th,tr:last-child td{border-bottom:none}
.online{color:#4caf6e}.offline{color:#cf6060}
.ilvl{color:#d4b86a;font-family:monospace;margin-right:0.4rem;font-weight:600}
.iname{color:var(--muted);font-style:italic;font-size:0.8rem}
.muted{color:#3d4455;font-style:italic}
.pen{color:#c0854a;font-family:monospace;font-size:0.85rem}
.pen-total{color:var(--text);font-family:monospace;font-size:0.85rem;font-weight:600}
.map-pad{padding:0.6rem;background:#0a0c10}
/* Canvas always 500x500 internally; CSS scales it to fill the card */
#mm{display:block;width:100%;height:auto;border-radius:2px}
"""

    px, py = p["pos_x"], p["pos_y"]

    body = f"""<div class="pw">
  <div class="pname">{p['username']}</div>
  <div class="psub">{p['class']} &middot; {p['network']}</div>

  <div class="ptop">
    <div class="card">
      <div class="ct">Character</div>
      <table>
        {row('User', p['username'])}
        {row('Class', p['class'])}
        {row('Level', p['level'])}
        {row('Next Level', fmt_ttl(p['ttl']))}
        {row('Status', '<span class="' + ('online' if p['is_online'] else 'offline') + '">' + status + '</span>')}
        {row('Host', '<span style="font-size:0.78rem;word-break:break-all">' + (p['userhost'] or '—') + '</span>')}
        {row('Account Created', fmt_ts(p['created_at']))}
        {row('Last Login', fmt_ts(p['last_login']))}
        {row('Total Idled', fmt_ttl(p['idled']))}
        {row('Position', f"{px}, {py}")}
        {row('Alignment', alignment)}
        {row('Item Sum', isum)}
      </table>
    </div>
    <div class="card">
      <div class="ct">Map — [{px}, {py}]</div>
      <div class="map-pad">
        <canvas id="mm" width="500" height="500"></canvas>
      </div>
    </div>
  </div>

  <div class="pbot">
    <div class="card">
      <div class="ct">Items</div>
      <table>{''.join(item_row(s) for s in ITEM_SLOTS)}</table>
    </div>
    <div class="card">
      <div class="ct">Penalties</div>
      <table>
        {pen_row('Kick', p['pen_kick'])}
        {pen_row('Logout', p['pen_logout'])}
        {pen_row('Message', p['pen_mesg'])}
        {pen_row('Nick', p['pen_nick'])}
        {pen_row('Part', p['pen_part'])}
        {pen_row('Quest', p['pen_quest'])}
        {pen_row('Quit', p['pen_quit'])}
        {row('Total', '<span class="' + ('pen-total' if total_pen else 'muted') + '">' + fmt_pen(total_pen) + '</span>')}
      </table>
    </div>
  </div>
</div>

<script>
const cv=document.getElementById('mm');
const ctx=cv.getContext('2d');
const W=500,H=500;
const PX={px},PY={py};

// Load pirate map image as background
const mapImg = new Image();
mapImg.src = '/static/map.png';
mapImg.onload = () => {{
  ctx.drawImage(mapImg, 0, 0, W, H);
  drawDot();
}};

function drawDot() {{
// Player dot — glowing magenta
const g2=ctx.createRadialGradient(PX,PY,0,PX,PY,12);
g2.addColorStop(0,'rgba(255,68,204,0.85)');
g2.addColorStop(1,'rgba(255,68,204,0)');
ctx.beginPath();ctx.arc(PX,PY,12,0,Math.PI*2);
ctx.fillStyle=g2;ctx.fill();
ctx.beginPath();ctx.arc(PX,PY,4,0,Math.PI*2);
ctx.fillStyle='#ff99ee';ctx.fill();
ctx.strokeStyle='#ff44cc';ctx.lineWidth=1.5;ctx.stroke();

// Name label — large, readable, with background box
const lbl='{p['username']}';
ctx.font='bold 16px sans-serif';
ctx.textAlign='center';
const lw=ctx.measureText(lbl).width;
// Clamp label so it never goes off edge
const lx=Math.min(Math.max(PX,lw/2+6),W-lw/2-6);
const ly=PY>24?PY-12:PY+26;
ctx.fillStyle='rgba(0,0,0,0.82)';
ctx.fillRect(lx-lw/2-5,ly-15,lw+10,20);
ctx.fillStyle='#ffccee';
ctx.fillText(lbl,lx,ly);
}}
</script>
"""

    return web.Response(text=page(f"{p['username']} — Profile", body, css, show_hof=_show_hof(req)),
                        content_type="text/html")


# ── Hall of Fame ──────────────────────────────────────────────────────────────

async def handle_hof(req):
    import datetime
    db      = req.app["db"]
    entries = await db.get_hof()
    engine  = req.app.get("engine")
    current_round = await db.get_round()

    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    css = """
.hof{max-width:820px;margin:2rem auto;padding:0 1.5rem 3rem}
.round-block{margin-bottom:2rem}
.round-title{font-family:'Cinzel',serif;color:var(--gold);font-size:0.9rem;
              letter-spacing:0.1em;margin-bottom:0.6rem;padding-bottom:0.4rem;
              border-bottom:1px solid var(--border)}
.hof-card{background:var(--panel);border:1px solid var(--border);border-radius:5px;
           overflow:hidden;margin-bottom:0.5rem;display:flex;align-items:center;gap:1rem;
           padding:0.7rem 1.2rem}
.medal{font-size:1.4rem;flex-shrink:0;width:2rem;text-align:center}
.hof-name{font-family:'Cinzel',serif;color:var(--gold);font-size:0.95rem}
.hof-detail{color:var(--muted);font-size:0.82rem;margin-top:0.2rem}
.hof-meta{margin-left:auto;text-align:right;font-size:0.8rem;color:var(--muted)}
.current-round{background:var(--panel);border:1px solid var(--border);border-radius:5px;
                padding:1rem 1.2rem;margin-bottom:1.5rem;font-family:'Cinzel',serif;
                color:var(--muted);font-size:0.85rem;letter-spacing:0.08em}
.current-round span{color:var(--gold);font-size:1.1rem}
.empty{color:var(--muted);font-style:italic;padding:2rem;text-align:center}
"""

    # Group by round
    rounds = {}
    for e in entries:
        r = e["round"]
        if r not in rounds:
            rounds[r] = []
        rounds[r].append(e)

    body_parts = [f'<div class="hof">']
    body_parts.append(
        f'<div class="current-round">Currently on <span>Round {current_round}</span></div>')

    if not rounds:
        body_parts.append('<div class="empty">No completed rounds yet — be the first to reach level 40!</div>')
    else:
        for round_num in sorted(rounds.keys(), reverse=True):
            winners = rounds[round_num]
            body_parts.append(f'<div class="round-block">')
            body_parts.append(f'<div class="round-title">⚔ Round {round_num}</div>')
            for w in sorted(winners, key=lambda x: x["rank"]):
                medal   = medals.get(w["rank"], "")
                date    = datetime.datetime.utcfromtimestamp(
                    w["finished_at"]).strftime("%d %b %Y")
                body_parts.append(f'''<div class="hof-card">
  <div class="medal">{medal}</div>
  <div>
    <div class="hof-name">{w['username']}</div>
    <div class="hof-detail">{w['class']} · {w['network']} · Level {w['level']} · Item Sum {w['item_sum']}</div>
  </div>
  <div class="hof-meta">{date}</div>
</div>''')
            body_parts.append('</div>')

    body_parts.append('</div>')
    return web.Response(text=page("Hall of Fame", '\n'.join(body_parts), css, show_hof=True),
                        content_type="text/html")
