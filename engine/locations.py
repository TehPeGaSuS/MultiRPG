"""engine/locations.py — Named places on the 500x500 world map.

Single source of truth: quests pick destinations from LOCATIONS and the web
map names the nearest one when you hover. Positions follow the islands on
web/static/map.png. To rename a place, edit it here, then run
tools/rename_locations.py against the database so quest/event history agrees.
"""

LOCATIONS = [
    ("Deadman's Cove",       68,  59),
    ("Mermaid's Lagoon",    195,  54),
    ("Smuggler's Run",      308,  46),
    ("Whaler's Notch",      415,  54),
    ("Kraken Deep",         347, 137),
    ("Gallows Reef",        249, 137),
    ("The Devil's Passage", 430, 190),
    ("Serpent's Current",   210, 166),
    ("Cutthroat Cove",       59, 205),
    ("The Roaring Swell",   129, 261),
    ("The Howling Gale",    322, 256),
    ("Marauder's Bay",       54, 303),
    ("Freeport",            195, 317),
    ("Blackbeard's Cove",   449, 308),
    ("The Abyssal Plain",   342, 352),
    ("Bonecrown Isle",       73, 439),
    ("Wreckers' Shoals",    288, 430),
    ("The Endless Deep",    229, 464),
    ("Tempest Archipelago", 435, 435),
]


def nearest_location(x, y):
    """Name of the closest location to (x, y)."""
    return min(LOCATIONS, key=lambda l: (l[1] - x) ** 2 + (l[2] - y) ** 2)[0]
