"""Refine route: modest tweaks to an alignment so it fits the terrain better.

Pure numpy, no Django (docs/REFINE_TOOL.md is the design). The caller hands
over the input line in a metric CRS (one array of x, y per leg) and a
`Grid` of ground elevations covering the corridor; `refine()` returns the
input scored the same way as the candidates, and up to three candidates.

    settings.py   factors, their default curves and weights, limits, validation
    terrain.py    Grid: elevations on a regular grid, bilinear sampling
    spline.py     the offset field: a clamped cubic B-spline along the input
    profile.py    the design grade line: a taut string through ground ± tube
    score.py      Problem: one candidate's geometry, profile and factor costs
    search.py     the annealer and refine()
"""
from .settings import DEFAULT_SETTINGS, FACTOR_META, PARAM_META, clean_settings, merge_settings
from .terrain import Grid
from .search import refine

__all__ = ["DEFAULT_SETTINGS", "FACTOR_META", "PARAM_META", "Grid", "clean_settings", "merge_settings",
           "refine"]
