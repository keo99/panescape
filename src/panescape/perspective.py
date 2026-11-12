"""Angled and tilted screens, seen from one seat (opt-in: ``viewer:`` in a layout).

The flat layout is folded into 3D: every monitor hangs off the one it's placed
next to, hinged on the shared edge, and is turned (``angle``) and leaned back
(``tilt``) as given. The picture lies in the first monitor's plane. Each point
of a screen shows the part of the picture that sits behind it on the line from
your eye, so from the seat the desk looks like one flat window onto the picture.

For a screen in the first monitor's plane that line ends where it started, so a
flat desk renders exactly as without a viewer. Each screen's mapping is a plane
to plane projection: a 3x3 homography from its lit area (mm, from its top-left)
to the picture plane (layout mm).
"""

from __future__ import annotations

import math

Vec3 = tuple[float, float, float]
Mat3 = tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]
Homography = tuple[float, float, float, float, float, float, float, float, float]

IDENTITY: Mat3 = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def rotation(angle_deg: float, tilt_deg: float) -> Mat3:
    """Turn about the vertical axis (+: the screen's right side comes towards you),
    after leaning back about the horizontal axis (+: the top goes away from you).
    Axes: x right, y down, z towards the viewer."""
    a, t = math.radians(angle_deg), math.radians(tilt_deg)
    ca, sa, ct, st = math.cos(a), math.sin(a), math.cos(t), math.sin(t)
    # tilt: y' = ct*y - st*z, z' = st*y + ct*z ; then yaw: x' = ca*x - sa*z, z' = sa*x + ca*z
    return ((ca, -sa * st, -sa * ct),
            (0.0, ct, -st),
            (sa, ca * st, ca * ct))


def apply(m: Mat3, v: Vec3) -> Vec3:
    return (m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
            m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
            m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2])


class Pose:
    """Where one monitor's flat layout coordinates end up in 3D:
    X(x, y) = origin + R . (x - pivot_x, y - pivot_y, 0)."""

    def __init__(self, pivot: tuple[float, float], origin: Vec3, rot: Mat3):
        self.pivot, self.origin, self.rot = pivot, origin, rot

    def point(self, x: float, y: float) -> Vec3:
        d = apply(self.rot, (x - self.pivot[0], y - self.pivot[1], 0.0))
        return (self.origin[0] + d[0], self.origin[1] + d[1], self.origin[2] + d[2])


def project(eye: Vec3, p: Vec3) -> tuple[float, float]:
    """Where the line from the eye through p meets the picture plane (z = 0)."""
    ex, ey, ez = eye
    if p[2] >= ez - 1.0:
        raise ValueError("behind")
    t = ez / (ez - p[2])
    return (ex + t * (p[0] - ex), ey + t * (p[1] - ey))


def solve_homography(src: list[tuple[float, float]], dst: list[tuple[float, float]]) -> Homography:
    """The projective map taking 4 points onto 4 others (h33 = 1)."""
    rows, rhs = [], []
    for (x, y), (u, v) in zip(src, dst, strict=True):
        rows.append([x, y, 1.0, 0.0, 0.0, 0.0, -u * x, -u * y])
        rhs.append(u)
        rows.append([0.0, 0.0, 0.0, x, y, 1.0, -v * x, -v * y])
        rhs.append(v)
    h = _solve(rows, rhs)
    return (h[0], h[1], h[2], h[3], h[4], h[5], h[6], h[7], 1.0)


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting (8x8, no numpy needed)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[piv][c]) < 1e-12:
            raise ValueError("degenerate quadrilateral")
        m[c], m[piv] = m[piv], m[c]
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                if f:
                    m[r] = [vr - f * vc for vr, vc in zip(m[r], m[c], strict=True)]
    return [m[i][n] / m[i][i] for i in range(n)]


def map_point(h: Homography, x: float, y: float) -> tuple[float, float]:
    w = h[6] * x + h[7] * y + h[8]
    return ((h[0] * x + h[1] * y + h[2]) / w, (h[3] * x + h[4] * y + h[5]) / w)


def invert(h: Homography) -> Homography:
    a, b, c, d, e, f, g, i, j = h
    det = a * (e * j - f * i) - b * (d * j - f * g) + c * (d * i - e * g)
    inv = ((e * j - f * i), -(b * j - c * i), (b * f - c * e),
           -(d * j - f * g), (a * j - c * g), -(a * f - c * d),
           (d * i - e * g), -(a * i - b * g), (a * e - b * d))
    k = inv[8] if abs(inv[8]) > 1e-15 else det
    return tuple(v / k for v in inv)  # type: ignore[return-value]


def compose(h2: Homography, h1: Homography) -> Homography:
    """h2 after h1."""
    a = [h2[0:3], h2[3:6], h2[6:9]]
    b = [h1[0:3], h1[3:6], h1[6:9]]
    m = [sum(a[r][k] * b[k][c] for k in range(3)) for r in range(3) for c in range(3)]
    return tuple(v / m[8] for v in m)  # type: ignore[return-value]


def scale_at(h: Homography, x: float, y: float) -> float:
    """How much the map stretches lengths near (x, y) (the larger of the two directions)."""
    eps = 1.0
    p = map_point(h, x, y)
    px = map_point(h, x + eps, y)
    py = map_point(h, x, y + eps)
    return max(math.hypot(px[0] - p[0], px[1] - p[1]), math.hypot(py[0] - p[0], py[1] - p[1])) / eps
