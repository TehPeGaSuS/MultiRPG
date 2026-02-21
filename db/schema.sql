PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS players (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT    NOT NULL,
    network         TEXT    NOT NULL,
    password_hash   TEXT    NOT NULL,
    is_admin        INTEGER NOT NULL DEFAULT 0,
    is_online       INTEGER NOT NULL DEFAULT 0,
    current_nick    TEXT,
    channel         TEXT,
    userhost        TEXT,
    level           INTEGER NOT NULL DEFAULT 0,
    ttl             INTEGER NOT NULL DEFAULT 600,
    next_ttl        INTEGER NOT NULL DEFAULT 600,
    pos_x           INTEGER NOT NULL DEFAULT 0,
    pos_y           INTEGER NOT NULL DEFAULT 0,
    alignment       TEXT    NOT NULL DEFAULT 'n',
    class           TEXT    NOT NULL DEFAULT 'Adventurer',
    pen_mesg        INTEGER NOT NULL DEFAULT 0,
    pen_nick        INTEGER NOT NULL DEFAULT 0,
    pen_part        INTEGER NOT NULL DEFAULT 0,
    pen_kick        INTEGER NOT NULL DEFAULT 0,
    pen_quit        INTEGER NOT NULL DEFAULT 0,
    pen_quest       INTEGER NOT NULL DEFAULT 0,
    pen_logout      INTEGER NOT NULL DEFAULT 0,
    idled           INTEGER NOT NULL DEFAULT 0,
    online_since    INTEGER,
    created_at      INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    last_login      INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    UNIQUE(username COLLATE NOCASE)  -- globally unique across all networks
);

CREATE TABLE IF NOT EXISTS items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id   INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    slot        TEXT    NOT NULL CHECK(slot IN (
                    'ring','amulet','charm','weapon','helm',
                    'tunic','pair of gloves','shield',
                    'set of leggings','pair of boots'
                )),
    level       INTEGER NOT NULL DEFAULT 0,
    name        TEXT,
    is_unique   INTEGER NOT NULL DEFAULT 0,
    UNIQUE(player_id, slot)
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT    NOT NULL,
    message     TEXT    NOT NULL,
    player1_id  INTEGER REFERENCES players(id),
    player2_id  INTEGER REFERENCES players(id),
    created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE INDEX IF NOT EXISTS idx_players_online  ON players(is_online);
CREATE INDEX IF NOT EXISTS idx_players_network ON players(network);
CREATE INDEX IF NOT EXISTS idx_players_pos     ON players(pos_x, pos_y);
CREATE INDEX IF NOT EXISTS idx_events_time     ON events(created_at DESC);
