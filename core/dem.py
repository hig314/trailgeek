"""Sample elevations and terrain gradients along a line, from a stack of
DEMs: the finest lidar archive that covers each point, then the regional
Terrarium tiles where no lidar does.

Runs in the Huey worker only (never in a web request). Everything is read
through GeoDjango's GDAL bindings, i.e. the same system libgdal GeoDjango
already needs, so there is no second GDAL copy in the image:

  - lidar: the archive COGs on landslidescience's R2 bucket, opened with
    /vsicurl/ and read in windows (GDAL fetches only the byte ranges it
    needs). A `cog_url` that is a local path or file:// URL is read from
    disk, for dev with archives on an external drive.
  - context: the baked USGS 3DEP context (`ctx_3dep`, Mapbox terrain-RGB
    PNG tiles, z13, through landslidescience's tile Worker) where the
    catalogue has it, then AWS Terrain Tiles (Terrarium PNG, z14); decoded
    with GDAL's PNG driver, cached in the worker process.

Gradient: central differences between four probe points h metres either
side of the sample, along the axes of the *evaluation* CRS (the route's
UTM zone). Probing in the evaluation CRS means the gradient and the trail
heading share axes, so TSA needs no grid-convergence correction even when
a lidar archive is in a neighbouring UTM zone. h follows the DEM's
resolution (at least 1 m), so the gradient baseline is 2h: comparable to a
Horn 3x3 slope on that grid.
"""
import logging
import math
import os
import struct
import tempfile
import threading
import urllib.request
from collections import OrderedDict

import numpy as np
from django.contrib.gis.gdal import GDALRaster, OGRGeometry, SpatialReference

# vsicurl behaviour for COGs on object storage. Set before the first open;
# GDAL reads these from the environment when it needs them.
for _k, _v in {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",      # do not list the bucket
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.tiff",
    "GDAL_HTTP_MAX_RETRY": "3",
    "GDAL_HTTP_RETRY_DELAY": "1",
    "VSI_CACHE": "TRUE",
    "VSI_CACHE_SIZE": "100000000",
    "GDAL_CACHEMAX": "256",
}.items():
    os.environ.setdefault(_k, _v)

log = logging.getLogger(__name__)

BLOCK = 256                  # pixels per read window side
TERRARIUM_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
TERRARIUM_Z = 14
_WEB_MERC_M = 40075016.68557849


# ---------------------------------------------------------------------------
# Coordinate transforms for many points at once (one OGR call, via WKB)
# ---------------------------------------------------------------------------

def transform_xy(x, y, src, dst):
    """Transform coordinate arrays between two SRIDs / SpatialReferences.
    Traditional GIS axis order (x = easting / longitude) throughout."""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    n = len(x)
    if n == 0:
        return x, y
    one = n == 1
    if one:
        x, y = np.r_[x, x], np.r_[y, y]
        n = 2
    xy = np.empty(2 * n, "<f8")
    xy[0::2], xy[1::2] = x, y
    wkb = struct.pack("<BII", 1, 2, n) + xy.tobytes()
    src_srs = src if isinstance(src, SpatialReference) else SpatialReference(src)
    g = OGRGeometry(memoryview(wkb), srs=src_srs)
    g.transform(dst if isinstance(dst, SpatialReference) else SpatialReference(dst))
    raw = bytes(g.wkb)
    order = "<" if raw[0] == 1 else ">"
    out = np.frombuffer(raw[9:9 + 16 * n], dtype=f"{order}f8")
    ox, oy = out[0::2].copy(), out[1::2].copy()
    if one:
        return ox[:1], oy[:1]
    return ox, oy


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

class CogSource:
    """A single-band elevation raster (a lidar archive COG, or any GDAL
    raster). Bilinear between pixel centres; a point whose four neighbours
    are not all valid is no data."""

    kind = "lidar"

    def __init__(self, key, url, title="", res=None, bounds=None):
        self.key = key
        self.title = title or key
        self.url = url
        self.bounds = bounds             # [w, s, e, n] lon/lat, to skip far points cheaply
        self._ds = None
        self._lock = threading.Lock()
        self._res_hint = res

    def _path(self):
        u = self.url
        if u.startswith("file://"):
            return u[len("file://"):]
        if u.startswith("http://") or u.startswith("https://"):
            return "/vsicurl/" + u
        return u

    @property
    def ds(self):
        if self._ds is None:
            self._ds = GDALRaster(self._path())
            gt = self._ds.geotransform
            if gt[2] or gt[4]:
                raise ValueError(f"{self.key}: rotated rasters are not supported")
            b = self._ds.bands[0]
            self._nodata = b.nodata_value
            self._srs = self._ds.srs
        return self._ds

    @property
    def res(self):
        if self._res_hint:
            return float(self._res_hint)
        gt = self.ds.geotransform
        return abs(gt[1])

    def sample(self, x, y, eval_srs):
        """Elevations at points given in `eval_srs`. nan where no data."""
        z = np.full(len(x), np.nan)
        if not len(x):
            return z
        with self._lock:
            ds = self.ds
            X, Y = transform_xy(x, y, eval_srs, self._srs)
            gt = ds.geotransform
            col = (X - gt[0]) / gt[1] - 0.5
            row = (Y - gt[3]) / gt[5] - 0.5
            W, H = ds.width, ds.height
            c0 = np.floor(col).astype(np.int64)
            r0 = np.floor(row).astype(np.int64)
            inside = (c0 >= 0) & (r0 >= 0) & (c0 + 1 < W) & (r0 + 1 < H)
            idx = np.flatnonzero(inside)
            if not len(idx):
                return z
            band = ds.bands[0]
            keys = (r0[idx] // BLOCK) * (W // BLOCK + 1) + (c0[idx] // BLOCK)
            for key in np.unique(keys):
                sel = idx[keys == key]
                br, bc = int(r0[sel[0]] // BLOCK), int(c0[sel[0]] // BLOCK)
                x0, y0 = bc * BLOCK, br * BLOCK
                w = min(BLOCK + 1, W - x0)
                h = min(BLOCK + 1, H - y0)
                arr = np.asarray(band.data(offset=(x0, y0), size=(w, h)), dtype=float).reshape(h, w)
                if self._nodata is not None:
                    arr[arr == self._nodata] = np.nan
                cc, rr = c0[sel] - x0, r0[sel] - y0
                tx, ty = col[sel] - c0[sel], row[sel] - r0[sel]
                a, b = arr[rr, cc], arr[rr, cc + 1]
                c, d = arr[rr + 1, cc], arr[rr + 1, cc + 1]
                z[sel] = (a * (1 - tx) + b * tx) * (1 - ty) + (c * (1 - tx) + d * tx) * ty
        return z


def _png_bands(png_bytes):
    """The bands of a PNG tile as float arrays. GeoDjango opens an in-memory
    raster in write mode, which the PNG driver refuses, so the tile goes
    through a temporary file opened read-only."""
    with tempfile.NamedTemporaryFile(suffix=".png") as fh:
        fh.write(png_bytes)
        fh.flush()
        rast = GDALRaster(fh.name)
        bands = [np.asarray(rast.bands[i].data(), dtype=float).reshape(rast.height, rast.width)
                 for i in range(len(rast.bands))]
        del rast
    return bands


def decode_terrarium(png_bytes):
    """Heights from a Terrarium PNG: h = R*256 + G + B/256 - 32768 (the same
    decode as landslidescience's dem_fill.js)."""
    rgb = _png_bands(png_bytes)
    return (rgb[0] * 256.0 + rgb[1] + rgb[2] / 256.0 - 32768.0).astype(np.float32)


def decode_mapbox(png_bytes):
    """Heights from a Mapbox terrain-RGB PNG: h = -10000 + (R*65536 + G*256
    + B) * 0.1. Transparent pixels, and the all-zero -10000 m code, are no
    data (NaN), as demshade reads them."""
    b = _png_bands(png_bytes)
    h = -10000.0 + (b[0] * 65536.0 + b[1] * 256.0 + b[2]) * 0.1
    nodata = h <= -9999.0
    if len(b) > 3:
        nodata |= b[3] == 0
    h[nodata] = np.nan
    return h.astype(np.float32)


class TileSource:
    """A DEM served as XYZ PNG tiles, read at one zoom with bilinear
    interpolation. Two kinds are in use, both regional context under the
    lidar:

      - the baked USGS 3DEP 1/3 arc-second context (`ctx_3dep`, Mapbox
        terrain-RGB, z13, ~10 m; through landslidescience's tile Worker),
        what /lidar/ composites every survey over;
      - AWS Terrain Tiles (Terrarium, z14), which in Alaska is the USGS
        2 arc-second NED (~60 m): fine for a long route's climb, coarse for
        grade and TSA at trail scale.

    Results say which samples came from which source."""

    kind = "context"
    _cache = OrderedDict()           # (key, z, x, y) -> heights, shared per process
    _cache_lock = threading.Lock()
    CACHE_MAX = 300
    decode = staticmethod(decode_terrarium)

    def __init__(self, key, title, url, z, data_res=None, encoding="terrarium"):
        self.key, self.title, self.url, self.z = key, title, url, z
        self.bounds = None
        self.data_res = data_res
        if encoding == "mapbox":
            self.decode = decode_mapbox

    @property
    def res(self):
        # The probe spacing for the gradient: the data's own resolution when
        # it is known, else one z pixel at 60 degrees N.
        if self.data_res:
            return self.data_res
        return 2 * _WEB_MERC_M / (256 * 2 ** self.z) * 0.5

    def fetch_tile(self, z, x, y):
        """Decoded heights for one tile, or None if it does not exist.
        Tests replace this."""
        url = self.url.format(z=z, x=x, y=y)
        req = urllib.request.Request(url, headers={"User-Agent": "trailgeek.org evaluator"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                if r.status == 204:          # the tile Worker's "no tile here"
                    return None
                data = r.read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                return None
            raise
        return self.decode(data) if data else None

    def _tile(self, z, x, y):
        key = (self.key, z, x, y)
        with self._cache_lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        t = self.fetch_tile(z, x, y)
        with self._cache_lock:
            self._cache[key] = t
            while len(self._cache) > self.CACHE_MAX:
                self._cache.popitem(last=False)
        return t

    def sample(self, x, y, eval_srs):
        z = np.full(len(x), np.nan)
        if not len(x):
            return z
        lon, lat = transform_xy(x, y, eval_srs, 4326)
        n = 2 ** self.z
        px = (lon + 180.0) / 360.0 * n * 256
        lr = np.radians(lat)
        py = (1 - np.log(np.tan(lr) + 1 / np.cos(lr)) / math.pi) / 2 * n * 256
        fx, fy = px - 0.5, py - 0.5
        c0, r0 = np.floor(fx).astype(np.int64), np.floor(fy).astype(np.int64)
        tx, ty = fx - c0, fy - r0
        out = np.zeros((4, len(x)))
        for k, (dc, dr) in enumerate(((0, 0), (1, 0), (0, 1), (1, 1))):
            gc, gr = c0 + dc, r0 + dr
            tcol, trow = gc // 256, gr // 256
            keys = trow * n + tcol
            vals = np.full(len(x), np.nan)
            for key in np.unique(keys):
                sel = keys == key
                t = self._tile(self.z, int(key % n), int(key // n))
                if t is not None:
                    vals[sel] = t[gr[sel] - (key // n) * 256, gc[sel] - (key % n) * 256]
            out[k] = vals
        z = (out[0] * (1 - tx) + out[1] * tx) * (1 - ty) + (out[2] * (1 - tx) + out[3] * tx) * ty
        return z


class TerrariumSource(TileSource):
    """AWS Terrain Tiles, the context of last resort: global, no key."""

    def __init__(self, key="terrarium", title="AWS Terrain Tiles", url=TERRARIUM_URL, z=TERRARIUM_Z):
        super().__init__(key, title, url, z)



# ---------------------------------------------------------------------------
# The stack
# ---------------------------------------------------------------------------

def sample_stack(x, y, eval_srid, sources):
    """For points in the evaluation CRS, the elevation and gradient from the
    first source (in the given order) that has data at the point and at all
    four probes. Returns z, gx, gy, src (index into `sources`, -1 = none),
    warnings. A source that cannot be read (network, permissions, a moved
    archive) is skipped with a warning, so its points fall through to the
    next source instead of failing the whole evaluation."""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    n = len(x)
    z, gx, gy = (np.full(n, np.nan) for _ in range(3))
    src = np.full(n, -1)
    warnings = []
    eval_srs = SpatialReference(eval_srid)
    lonlat = None
    for k, s in enumerate(sources):
        todo = np.flatnonzero(src < 0)
        if not len(todo):
            break
        if s.bounds:
            if lonlat is None:
                lonlat = transform_xy(x, y, eval_srs, 4326)
            w_, s_, e_, n_ = s.bounds
            lo, la = lonlat[0][todo], lonlat[1][todo]
            todo = todo[(lo >= w_) & (lo <= e_) & (la >= s_) & (la <= n_)]
            if not len(todo):
                continue
        try:
            h = max(1.0, s.res)
            px = np.concatenate([x[todo], x[todo] + h, x[todo] - h, x[todo], x[todo]])
            py = np.concatenate([y[todo], y[todo], y[todo], y[todo] + h, y[todo] - h])
            v = s.sample(px, py, eval_srs).reshape(5, len(todo))
        except Exception:                # noqa: BLE001 - any read failure means "skip this DEM"
            log.warning("DEM %s (%s) could not be read", s.key, getattr(s, "url", ""), exc_info=True)
            warnings.append(f"{s.title} could not be read just now, so the next DEM was used there.")
            continue
        ok = np.all(np.isfinite(v), axis=0)
        sel = todo[ok]
        z[sel] = v[0, ok]
        gx[sel] = (v[1, ok] - v[2, ok]) / (2 * h)
        gy[sel] = (v[3, ok] - v[4, ok]) / (2 * h)
        src[sel] = k
    return z, gx, gy, src, warnings
