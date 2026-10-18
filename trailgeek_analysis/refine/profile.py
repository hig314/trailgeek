"""The design grade line: the taut string through a tube in (distance,
elevation). It is the shortest path between the pinned ends that stays
between `lo` and `hi`, which is also the profile with the least maximum
grade and the least total grade change inside the tube (docs/REFINE_TOOL.md
§2). Funnel algorithm: from the current anchor, keep the steepest slope the
lower bound allows and the shallowest the upper bound allows; when they
cross, the string bends at the vertex that set the violated bound.
"""
import numpy as np


def taut_string(d, lo, hi):
    """d increasing; lo <= hi; the ends are pinned at the midpoint of their
    bounds. Returns the string's elevation at every d."""
    n = len(d)
    if n == 0:
        return np.zeros(0)
    d = np.asarray(d, float).tolist()
    lo = np.asarray(lo, float).tolist()
    hi = np.asarray(hi, float).tolist()
    lo[0] = hi[0] = (lo[0] + hi[0]) / 2
    lo[-1] = hi[-1] = (lo[-1] + hi[-1]) / 2
    z = [0.0] * n
    ia, za = 0, lo[0]
    z[0] = za
    while ia < n - 1:
        smax, smin = float("inf"), float("-inf")
        imax = imin = ia + 1
        nxt = None
        j = ia + 1
        da = d[ia]
        while j < n:
            ds = d[j] - da
            if ds <= 0:
                ds = 1e-9
            su = (hi[j] - za) / ds
            sl = (lo[j] - za) / ds
            if sl > smax:
                nxt = (imax, hi[imax])
                break
            if su < smin:
                nxt = (imin, lo[imin])
                break
            if su < smax:
                smax, imax = su, j
            if sl > smin:
                smin, imin = sl, j
            j += 1
        if nxt is None:
            nxt = (n - 1, lo[n - 1])
        ib, zb = nxt
        db = d[ib] - da
        for k in range(ia + 1, ib + 1):
            z[k] = za + (zb - za) * (d[k] - da) / db if db > 0 else zb
        ia, za = ib, zb
    return np.asarray(z)
