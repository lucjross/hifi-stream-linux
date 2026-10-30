"""Generate icons/wiim-stream-symbolic.svg: a disc with a tilted, tapering W knocked out.

  python3 icons/make_icon.py icons/wiim-stream-symbolic.svg
"""
import math, sys

CX = CY = 8.0
R = 7.6
TOP, BOTTOM = 4.5, 11.9          # W cut flat at these heights
H_TOP, H_BOTTOM = 1.45, 0.2        # half stroke width, tapering from top to bottom
ROTATE = 15                       # degrees clockwise
CORNER_RADIUS = 2.0               # rounding of the W's top outside corners
PTS = [(3.4, TOP), (5.75, BOTTOM), (8.0, 6.6), (10.25, BOTTOM), (12.6, TOP)]


def half_width(y):
    return H_TOP + (H_BOTTOM - H_TOP) * (y - TOP) / (BOTTOM - TOP)


def offset_line(p, q, sign):
    dx, dy = q[0] - p[0], q[1] - p[1]
    n = math.hypot(dx, dy)
    ux, uy = -dy / n * sign, dx / n * sign
    hp, hq = half_width(p[1]), half_width(q[1])
    return (p[0] + ux * hp, p[1] + uy * hp), (q[0] + ux * hq, q[1] + uy * hq)


def intersect(l1, l2):
    (x1, y1), (x2, y2) = l1
    (x3, y3), (x4, y4) = l2
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den
    return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))


def hline(y):
    return ((0, y), (16, y))


def side(d):
    """One side of the stroke outline, flat-cut at TOP/BOTTOM where it would poke out."""
    segs = [offset_line(PTS[i], PTS[i + 1], d) for i in range(len(PTS) - 1)]
    out = [intersect(segs[0], hline(TOP))]
    for i in range(len(segs) - 1):
        j = intersect(segs[i], segs[i + 1])
        if j[1] > BOTTOM or j[1] < TOP:       # cut flat instead of a spike
            cut = hline(BOTTOM if j[1] > BOTTOM else TOP)
            out += [intersect(segs[i], cut), intersect(segs[i + 1], cut)]
        else:
            out.append(j)
    out.append(intersect(segs[-1], hline(TOP)))
    return out


def toward(p, q, d):
    """The point d along the line from p toward q."""
    n = math.hypot(q[0] - p[0], q[1] - p[1])
    return (p[0] + (q[0] - p[0]) * d / n, p[1] + (q[1] - p[1]) * d / n)


def rounded_path(pts, radii):
    """Closed path through pts, with corner i rounded by radii.get(i, 0)."""
    cmds = []
    for i, c in enumerate(pts):
        r = radii.get(i, 0)
        if r:
            a, b = toward(c, pts[i - 1], r), toward(c, pts[(i + 1) % len(pts)], r)
            cmds += [f"L{a[0]:.2f},{a[1]:.2f}", f"Q{c[0]:.2f},{c[1]:.2f} {b[0]:.2f},{b[1]:.2f}"]
        else:
            cmds.append(f"L{c[0]:.2f},{c[1]:.2f}")
    return "M" + cmds[0][1:] + " " + " ".join(cmds[1:]) + " Z"


# left is the outer edge of the W; its first and last points are the top outside corners
left, right = side(1), side(-1)
w_path = rounded_path(left + right[::-1], {0: CORNER_RADIUS, len(left) - 1: CORNER_RADIUS})
disc = (f"M{CX - R},{CY} a{R},{R} 0 1,0 {2 * R},0 a{R},{R} 0 1,0 {-2 * R},0 Z")
svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">
  <path transform="rotate({ROTATE} 8 8)" fill="#2e3436" fill-rule="evenodd" d="{disc} {w_path}"/>
</svg>
"""
open(sys.argv[1], "w").write(svg)
print(svg)
