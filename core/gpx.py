"""GPX reader for Track uploads. Standard library only: a track file is
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
