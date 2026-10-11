-- Run once against multirpg.db AFTER backing it up (see README) and with the bot stopped:
--   sqlite3 multirpg.db ".backup multirpg.db.bak"
--   sqlite3 multirpg.db < rename_locations.sql
-- Renames old places in event history / the active quest and moves the active
-- quest's destinations to the new map positions (engine/locations.py).
BEGIN;

UPDATE events SET message = replace(replace(message, 'The Frozen South', 'Bonecrown Isle'), 'Antarctica''s Edge', 'Wreckers'' Shoals')
 WHERE message LIKE '%'||'The Frozen South'||'%' OR message LIKE '%'||'Antarctica''s Edge'||'%';

UPDATE quest SET
  p1name = replace(replace(p1name, 'The Frozen South', 'Bonecrown Isle'), 'Antarctica''s Edge', 'Wreckers'' Shoals'),
  p2name = replace(replace(p2name, 'The Frozen South', 'Bonecrown Isle'), 'Antarctica''s Edge', 'Wreckers'' Shoals'),
  text   = replace(replace(text, 'The Frozen South', 'Bonecrown Isle'), 'Antarctica''s Edge', 'Wreckers'' Shoals');

UPDATE quest SET
  p1x = COALESCE(CASE p1name WHEN 'Deadman''s Cove' THEN 68 WHEN 'Mermaid''s Lagoon' THEN 195 WHEN 'Smuggler''s Run' THEN 308 WHEN 'Whaler''s Notch' THEN 415 WHEN 'Kraken Deep' THEN 347 WHEN 'Gallows Reef' THEN 249 WHEN 'The Devil''s Passage' THEN 430 WHEN 'Serpent''s Current' THEN 210 WHEN 'Cutthroat Cove' THEN 59 WHEN 'The Roaring Swell' THEN 129 WHEN 'The Howling Gale' THEN 322 WHEN 'Marauder''s Bay' THEN 54 WHEN 'Freeport' THEN 195 WHEN 'Blackbeard''s Cove' THEN 449 WHEN 'The Abyssal Plain' THEN 342 WHEN 'Bonecrown Isle' THEN 73 WHEN 'Wreckers'' Shoals' THEN 288 WHEN 'The Endless Deep' THEN 229 WHEN 'Tempest Archipelago' THEN 435 END, p1x),
  p1y = COALESCE(CASE p1name WHEN 'Deadman''s Cove' THEN 59 WHEN 'Mermaid''s Lagoon' THEN 54 WHEN 'Smuggler''s Run' THEN 46 WHEN 'Whaler''s Notch' THEN 54 WHEN 'Kraken Deep' THEN 137 WHEN 'Gallows Reef' THEN 137 WHEN 'The Devil''s Passage' THEN 190 WHEN 'Serpent''s Current' THEN 166 WHEN 'Cutthroat Cove' THEN 205 WHEN 'The Roaring Swell' THEN 261 WHEN 'The Howling Gale' THEN 256 WHEN 'Marauder''s Bay' THEN 303 WHEN 'Freeport' THEN 317 WHEN 'Blackbeard''s Cove' THEN 308 WHEN 'The Abyssal Plain' THEN 352 WHEN 'Bonecrown Isle' THEN 439 WHEN 'Wreckers'' Shoals' THEN 430 WHEN 'The Endless Deep' THEN 464 WHEN 'Tempest Archipelago' THEN 435 END, p1y),
  p2x = COALESCE(CASE p2name WHEN 'Deadman''s Cove' THEN 68 WHEN 'Mermaid''s Lagoon' THEN 195 WHEN 'Smuggler''s Run' THEN 308 WHEN 'Whaler''s Notch' THEN 415 WHEN 'Kraken Deep' THEN 347 WHEN 'Gallows Reef' THEN 249 WHEN 'The Devil''s Passage' THEN 430 WHEN 'Serpent''s Current' THEN 210 WHEN 'Cutthroat Cove' THEN 59 WHEN 'The Roaring Swell' THEN 129 WHEN 'The Howling Gale' THEN 322 WHEN 'Marauder''s Bay' THEN 54 WHEN 'Freeport' THEN 195 WHEN 'Blackbeard''s Cove' THEN 449 WHEN 'The Abyssal Plain' THEN 342 WHEN 'Bonecrown Isle' THEN 73 WHEN 'Wreckers'' Shoals' THEN 288 WHEN 'The Endless Deep' THEN 229 WHEN 'Tempest Archipelago' THEN 435 END, p2x),
  p2y = COALESCE(CASE p2name WHEN 'Deadman''s Cove' THEN 59 WHEN 'Mermaid''s Lagoon' THEN 54 WHEN 'Smuggler''s Run' THEN 46 WHEN 'Whaler''s Notch' THEN 54 WHEN 'Kraken Deep' THEN 137 WHEN 'Gallows Reef' THEN 137 WHEN 'The Devil''s Passage' THEN 190 WHEN 'Serpent''s Current' THEN 166 WHEN 'Cutthroat Cove' THEN 205 WHEN 'The Roaring Swell' THEN 261 WHEN 'The Howling Gale' THEN 256 WHEN 'Marauder''s Bay' THEN 303 WHEN 'Freeport' THEN 317 WHEN 'Blackbeard''s Cove' THEN 308 WHEN 'The Abyssal Plain' THEN 352 WHEN 'Bonecrown Isle' THEN 439 WHEN 'Wreckers'' Shoals' THEN 430 WHEN 'The Endless Deep' THEN 464 WHEN 'Tempest Archipelago' THEN 435 END, p2y)
 WHERE active = 1;

COMMIT;
