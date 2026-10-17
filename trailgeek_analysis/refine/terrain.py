"""Ground elevations on a regular grid, sampled bilinearly. The caller fills
it (from the DEM stack, or a function in tests); cells it did not fill are
nan, and a sample touching one is nan."""
import numpy as np


class Grid:
    """z[row, col] is the ground at x = x0 + col * res, y = y0 + row * res."""

    def __init__(self, z, x0, y0, res):
        self.z = np.asarray(z, float)
        self.x0, self.y0, self.res = float(x0), float(y0), float(res)
        self.h, self.w = self.z.shape

    @classmethod
    def from_function(cls, f, xmin, ymin, xmax, ymax, res=1.0):
        xs = np.arange(xmin, xmax + res / 2, res)
        ys = np.arange(ymin, ymax + res / 2, res)
        X, Y = np.meshgrid(xs, ys)
        return cls(f(X, Y), xmin, ymin, res)

    def sample(self, x, y):
        x = np.asarray(x, float)
        y = np.asarray(y, float)
        col = (x - self.x0) / self.res
        row = (y - self.y0) / self.res
        c0 = np.floor(col).astype(np.int64)
        r0 = np.floor(row).astype(np.int64)
        out = np.full(x.shape, np.nan)
        ok = (c0 >= 0) & (r0 >= 0) & (c0 + 1 < self.w) & (r0 + 1 < self.h)
        if not ok.any():
            return out
        c, r = c0[ok], r0[ok]
        tx, ty = col[ok] - c, row[ok] - r
        z = self.z
        out[ok] = ((z[r, c] * (1 - tx) + z[r, c + 1] * tx) * (1 - ty)
                   + (z[r + 1, c] * (1 - tx) + z[r + 1, c + 1] * tx) * ty)
        return out
