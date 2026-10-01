"""updatecheck.py — build the VERSION reply and look up the newest version in Git.

The newest version is read from version.py on the main branch of the project
repository. The result is cached, the request has a short timeout, and any
failure just means "unknown": VERSION still answers, without that sentence.
"""
import re, sys, time
from typing import Optional
import aiohttp

RAW_URL    = "https://raw.githubusercontent.com/TehPeGaSuS/MultiRPG/main/version.py"
CACHE_OK   = 6 * 3600     # seconds to keep a successful lookup
CACHE_FAIL = 10 * 60      # seconds before retrying after a failure
TIMEOUT    = 5            # seconds

_cache = {"at": 0.0, "value": None, "ttl": 0}


def parse_version(text: str) -> Optional[str]:
    m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.M)
    return m.group(1) if m else None


def version_key(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v))


async def latest_version() -> Optional[str]:
    """Newest version in Git, or None if it can't be determined. Cached."""
    now = time.time()
    if _cache["ttl"] and now - _cache["at"] < _cache["ttl"]:
        return _cache["value"]
    value = None
    try:
        timeout = aiohttp.ClientTimeout(total=TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get(RAW_URL) as r:
                if r.status == 200:
                    value = parse_version(await r.text())
    except Exception:
        value = None
    _cache.update(at=now, value=value, ttl=CACHE_OK if value else CACHE_FAIL)
    return value


def describe(running: str, latest: Optional[str]) -> str:
    """The VERSION reply, in the style of Limnoria's."""
    py  = sys.version.replace("\n", " ")
    msg = f"The current (running) version of MultiRPG is {running}, running on Python {py}."
    if latest:
        try:
            a, b = version_key(running), version_key(latest)
        except Exception:
            a = b = ()
        if b > a:
            msg += f" The newest version available in Git is {latest}."
        elif b == a:
            msg += " This is the newest version available in Git."
        else:
            msg += f" This is newer than the version in Git ({latest})."
    return msg
