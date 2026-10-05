-- Give every player the same per-slot item levels as the player with the
-- highest item sum. Named/unique flags are cleared so uniques aren't duplicated.
-- Usage:  sqlite3 multirpg.db < equalize_items.sql
-- Restart the bot first so ensure_item_rows() has backfilled missing rows.
BEGIN;

CREATE TEMP TABLE best AS
  SELECT slot, level FROM items
  WHERE player_id = (SELECT player_id FROM items
                     GROUP BY player_id ORDER BY SUM(level) DESC LIMIT 1);

UPDATE items
   SET level = (SELECT level FROM best WHERE best.slot = items.slot),
       name = NULL, is_unique = 0;

SELECT player_id, SUM(level) FROM items GROUP BY player_id;

COMMIT;
