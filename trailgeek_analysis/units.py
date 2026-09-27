"""Unit strings as the old settings wrote them: "5 ft", "30 m/day",
"1 mi/day", "10 %". The UI keeps the strings; these turn them into metres,
metres per day, or a plain number."""
import re

_LEN = {"m": 1.0, "meter": 1.0, "metre": 1.0, "meters": 1.0, "metres": 1.0,
        "ft": 0.3048, "foot": 0.3048, "feet": 0.3048,
        "km": 1000.0, "mi": 1609.344, "mile": 1609.344, "miles": 1609.344, "yd": 0.9144}
_NUM = r"\s*([-+]?\d+(?:\.\d+)?)\s*"


def length_m(s):
    """'5 ft' -> 1.524. A bare number is metres."""
    if isinstance(s, (int, float)):
        return float(s)
    m = re.fullmatch(_NUM + r"([A-Za-z]*)\s*", str(s))
    if not m:
        raise ValueError(f"not a length: {s!r}")
    unit = m.group(2).lower() or "m"
    if unit not in _LEN:
        raise ValueError(f"unknown length unit in {s!r}")
    return float(m.group(1)) * _LEN[unit]


def rate_m_per_day(s):
    """'30 m/day' -> 30.0, '1 mi/day' -> 1609.344."""
    if isinstance(s, (int, float)):
        return float(s)
    m = re.fullmatch(_NUM + r"([A-Za-z]+)\s*/\s*day\s*", str(s))
    if not m:
        raise ValueError(f"not a rate per day: {s!r}")
    return length_m(f"{m.group(1)} {m.group(2)}")


def percent(s):
    """'10 %' -> 10.0."""
    if isinstance(s, (int, float)):
        return float(s)
    m = re.fullmatch(_NUM + r"%?\s*", str(s))
    if not m:
        raise ValueError(f"not a percentage: {s!r}")
    return float(m.group(1))
