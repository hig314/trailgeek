"""trailgeek_analysis: trail alignment evaluation, pure Python + numpy.

No Django, no GDAL: callers resample a line (`resample_legs`), sample a DEM
however they like, and hand the arrays to `evaluate`. That keeps one copy of
the analysis for every caller (the Huey jobs today; notebooks, other sites
or a CLI later), and makes it testable against synthetic surfaces.

The algorithms are the ones in docs/TRAIL_ANALYSIS.md §1, with its listed
bugs fixed: nulls are skipped rather than summed as zero, longest runs count
every point, descending grades are binned by |grade|, and TSA is computed
from the trail heading and the terrain gradient rather than clipped to 0
where |grade| > slope.
"""
from .evaluate import DEFAULT_SETTINGS, evaluate
from .resample import resample_legs
from .units import length_m, rate_m_per_day

__all__ = ["DEFAULT_SETTINGS", "evaluate", "resample_legs", "length_m", "rate_m_per_day"]
