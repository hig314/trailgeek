"""Basemaps shared with landslidescience.

basemaps.js (synced from landslidescience, tools/shared.json) gives both
sites the same built-in basemaps and tile handling. The QuickMapServices
layers an editor promotes in landslidescience's /inventory/ live in its
database, so they are read here from its public list (owner, 2026-10-19:
curate them in one place). Only its public layers come: trailgeek is a
public site, and the editors-only ones stay there.

The list is cached for an hour; when landslidescience cannot be reached the
last list read is used (kept a week), and failing that none, so the map
never waits on, or breaks with, the other site.
"""
import json
import logging
import urllib.request

from django.conf import settings
from django.core.cache import cache

log = logging.getLogger(__name__)

CACHE_KEY = "ls_qms_shared"
LAST_KEY = "ls_qms_shared_last"
TTL_S = 60 * 60
RETRY_S = 5 * 60            # after a failure, try again this soon
LAST_TTL_S = 7 * 24 * 60 * 60
TIMEOUT_S = 3
KEYS = ("id", "label", "category", "tiles", "scheme", "minzoom", "maxzoom", "reproject", "attr")


def _clean(layer):
    """A promoted layer as a basemaps.js descriptor, or None if it is not a
    public, https, landslidescience-shaped one."""
    if not isinstance(layer, dict) or layer.get("public") is not True:
        return None
    out = {k: layer.get(k) for k in KEYS}
    if not (isinstance(out["id"], str) and out["id"].startswith("qmsshared-")
            and isinstance(out["label"], str) and out["label"]
            and isinstance(out["tiles"], str) and out["tiles"].startswith("https://")):
        return None
    if out["scheme"] not in ("xyz", "tms"):
        out["scheme"] = "xyz"
    if out["reproject"] not in (None, "epsg3395"):
        return None
    for k, default in (("minzoom", 0), ("maxzoom", 19)):
        v = out[k]
        out[k] = int(v) if isinstance(v, (int, float)) and 0 <= v <= 24 else default
    out["attr"] = str(out["attr"] or "")[:300]
    out["category"] = "Shared"
    return out


def _fetch():
    req = urllib.request.Request(settings.LS_QMS_PROMOTED_URL, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        data = json.loads(r.read(1_000_000))
    layers = [_clean(x) for x in (data.get("layers") or [])]
    return [x for x in layers if x]


def shared_qms_layers():
    """landslidescience's public promoted QMS layers, as basemap descriptors."""
    if not settings.LS_QMS_PROMOTED_URL:
        return []
    got = cache.get(CACHE_KEY)
    if got is not None:
        return got
    try:
        got = _fetch()
        cache.set(LAST_KEY, got, LAST_TTL_S)
        cache.set(CACHE_KEY, got, TTL_S)
    except Exception as exc:                   # unreachable, slow, or not JSON: the map goes on without
        log.warning("shared QMS basemaps: %s", exc)
        got = cache.get(LAST_KEY) or []
        cache.set(CACHE_KEY, got, RETRY_S)
    return got
