"""GPX reader for Track uploads, and a GPX writer for field exports. Standard library only: a track file is
`<trk><trkseg><trkpt lat lon><ele/><time/></trkpt>…`, which is not worth a
dependency. GPX 1.0 and 1.1 namespaces are both accepted, as are files with
no namespace at all (some phone apps).

Returns plain Python: a name and a list of (lon, lat, ele, time) tuples, so
the caller decides how to store them. Multiple segments and multiple tracks
are concatenated in file order into one line; a Track is one outing.
"""
import datetime
import xml.etree.ElementTree as ET

MAX_POINTS = 200_000


class GpxError(ValueError):
    pass


def _local(tag):
    """Strip any namespace: '{http://www.topografix.com/GPX/1/1}trkpt' -> 'trkpt'."""
    return tag.rsplit("}", 1)[-1]


def _parse_time(text):
    if not text:
        return None
    t = text.strip()
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def parse_gpx(data):
    """Parse GPX bytes or text. Returns {"name", "points", "device"}.

    points: list of (lon, lat, ele_or_None, datetime_or_None). Track points
    are preferred; a file with only a route (`<rte>`) is accepted too, so a
    planned route exported from a mapping app can be loaded as a Track.
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise GpxError(f"not well-formed XML: {e}") from e
    if _local(root.tag) != "gpx":
        raise GpxError("root element is not <gpx>")

    name = None
    device = root.get("creator") or ""
    points = []

    def walk(container_tag, point_tag):
        nonlocal name
        for trk in root.iter():
            if _local(trk.tag) != container_tag:
                continue
            for child in trk:
                if _local(child.tag) == "name" and name is None and child.text:
                    name = child.text.strip()
            for pt in trk.iter():
                if _local(pt.tag) != point_tag:
                    continue
                try:
                    lat = float(pt.get("lat"))
                    lon = float(pt.get("lon"))
                except (TypeError, ValueError):
                    continue
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    continue
                ele = time = None
                for c in pt:
                    tag = _local(c.tag)
                    if tag == "ele" and c.text:
                        try:
                            ele = float(c.text)
                        except ValueError:
                            ele = None
                    elif tag == "time":
                        time = _parse_time(c.text)
                points.append((lon, lat, ele, time))
                if len(points) > MAX_POINTS:
                    raise GpxError(f"more than {MAX_POINTS} points")

    walk("trk", "trkpt")
    if not points:
        walk("rte", "rtept")
    if name is None:
        for child in root:
            if _local(child.tag) == "metadata":
                for c in child:
                    if _local(c.tag) == "name" and c.text:
                        name = c.text.strip()
    if len(points) < 2:
        raise GpxError("no track with at least two points")
    return {"name": name or "", "points": points, "device": device}


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

GPX_NS = "http://www.topografix.com/GPX/1/1"
# Garmin's track extension carries a display colour that GaiaGPS and Garmin
# devices honour on import; other readers ignore it.
GPXX_NS = "http://www.garmin.com/xmlschemas/GpxExtensions/v3"
GPXX_COLORS = {"Black", "DarkRed", "DarkGreen", "DarkYellow", "DarkBlue", "DarkMagenta", "DarkCyan",
               "LightGray", "DarkGray", "Red", "Green", "Yellow", "Blue", "Magenta", "Cyan", "White"}


def _text(parent, tag, text):
    if text is None or text == "":
        return None
    e = ET.SubElement(parent, tag)
    e.text = str(text)
    return e


def write_gpx(name, tracks, waypoints=(), creator="trailgeek.org", desc=None, when=None):
    """Build a GPX 1.1 document and return it as bytes (UTF-8, declaration
    included). Standard library only, like the reader.

    tracks: iterable of dicts {name, coords, desc?, type?, color?}; coords is
      a sequence of (lon, lat) or (lon, lat, ele) tuples. Each becomes one
      <trk> with one <trkseg>. Named tracks are what GaiaGPS lists, so every
      track should have a distinct, sortable name.
    waypoints: iterable of dicts {name, lon, lat, desc?, ele?, sym?}.
    color: one of GPXX_COLORS, written as a Garmin DisplayColor extension.
    """
    ET.register_namespace("", GPX_NS)
    ET.register_namespace("gpxx", GPXX_NS)
    root = ET.Element(f"{{{GPX_NS}}}gpx", {"version": "1.1", "creator": creator})
    meta = ET.SubElement(root, f"{{{GPX_NS}}}metadata")
    _text(meta, f"{{{GPX_NS}}}name", name)
    _text(meta, f"{{{GPX_NS}}}desc", desc)
    when = when or datetime.datetime.now(datetime.timezone.utc)
    _text(meta, f"{{{GPX_NS}}}time", when.replace(microsecond=0).isoformat().replace("+00:00", "Z"))

    for w in waypoints:
        wpt = ET.SubElement(root, f"{{{GPX_NS}}}wpt", {"lat": f"{w['lat']:.7f}", "lon": f"{w['lon']:.7f}"})
        if w.get("ele") is not None:
            _text(wpt, f"{{{GPX_NS}}}ele", f"{w['ele']:.1f}")
        _text(wpt, f"{{{GPX_NS}}}name", w.get("name"))
        _text(wpt, f"{{{GPX_NS}}}desc", w.get("desc"))
        _text(wpt, f"{{{GPX_NS}}}sym", w.get("sym"))

    for t in tracks:
        trk = ET.SubElement(root, f"{{{GPX_NS}}}trk")
        _text(trk, f"{{{GPX_NS}}}name", t.get("name"))
        _text(trk, f"{{{GPX_NS}}}desc", t.get("desc"))
        _text(trk, f"{{{GPX_NS}}}type", t.get("type"))
        color = t.get("color")
        if color in GPXX_COLORS:
            ext = ET.SubElement(trk, f"{{{GPX_NS}}}extensions")
            tx = ET.SubElement(ext, f"{{{GPXX_NS}}}TrackExtension")
            _text(tx, f"{{{GPXX_NS}}}DisplayColor", color)
        seg = ET.SubElement(trk, f"{{{GPX_NS}}}trkseg")
        for c in t["coords"]:
            pt = ET.SubElement(seg, f"{{{GPX_NS}}}trkpt", {"lat": f"{c[1]:.7f}", "lon": f"{c[0]:.7f}"})
            if len(c) > 2 and c[2] is not None:
                _text(pt, f"{{{GPX_NS}}}ele", f"{c[2]:.1f}")

    ET.indent(root)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8")
