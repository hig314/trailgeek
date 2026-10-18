"""The offset field: a clamped uniform cubic B-spline over the input's arc
length. The value at each end is its end control point, so pinning those
two pins the ends of the refined line."""
import numpy as np

DEGREE = 3


def knot_vector(n_ctrl):
    p = DEGREE
    return np.concatenate([np.zeros(p), np.linspace(0, 1, n_ctrl - p + 1), np.ones(p)])


def basis(u, n_ctrl):
    """Basis matrix (len(u), n_ctrl) at parameters u in [0, 1] (Cox-de Boor)."""
    if n_ctrl < DEGREE + 1:
        raise ValueError("a cubic B-spline needs at least 4 control points")
    t = knot_vector(n_ctrl)
    u = np.clip(np.asarray(u, float), 0, 1 - 1e-12)
    N = np.zeros((len(u), len(t) - 1))
    for i in range(len(t) - 1):
        N[:, i] = (t[i] <= u) & (u < t[i + 1])
    for k in range(1, DEGREE + 1):
        M = np.zeros((len(u), len(t) - 1 - k))
        for i in range(len(t) - 1 - k):
            d1, d2 = t[i + k] - t[i], t[i + k + 1] - t[i + 1]
            a = (u - t[i]) / d1 * N[:, i] if d1 > 0 else 0.0
            b = (t[i + k + 1] - u) / d2 * N[:, i + 1] if d2 > 0 else 0.0
            M[:, i] = a + b
        N = M
    return N


def greville(n_ctrl):
    """Where along [0, 1] each control point has most influence."""
    t = knot_vector(n_ctrl)
    return np.array([t[i + 1:i + DEGREE + 1].mean() for i in range(n_ctrl)])
