"""Generate icons/hifi-stream-symbolic.svg: bold "HI" stacked over "FI".

  python3 icons/make_icon.py icons/hifi-stream-symbolic.svg
"""
import sys

# Everything is on whole pixels so it stays crisp at 16px.
LEFT, TOP = 3, 0          # top left of the lettering
ROW_H, ROW_GAP = 7, 1     # height of each row of letters, and the space between rows
S = 2                     # stroke width
H_W, F_W = 6, 6           # letter widths; I is one stroke wide
GAP = 2                   # between letters


def rect(x, y, w, h):
    return f"M{x:g},{y:g} h{w:g} v{h:g} h{-w:g} Z"


def letters():
    """One rectangle per stroke."""
    i_x = LEFT + max(H_W, F_W) + GAP
    top, bottom = TOP, TOP + ROW_H + ROW_GAP
    h_bar = (ROW_H - S + 1) // 2           # H's crossbar, just below the middle
    f_arm = S + max(1, S // 2)             # F's middle arm, a clear gap below the top one
    return [
        # H
        rect(LEFT, top, S, ROW_H),
        rect(LEFT + H_W - S, top, S, ROW_H),
        rect(LEFT + S, top + h_bar, H_W - 2 * S, S),
        # I
        rect(i_x, top, S, ROW_H),
        # F: top arm full width, middle arm a little shorter
        rect(LEFT, bottom, S, ROW_H),
        rect(LEFT + S, bottom, F_W - S, S),
        rect(LEFT + S, bottom + f_arm, F_W - S - 1, S),
        # I
        rect(i_x, bottom, S, ROW_H),
    ]


d = " ".join(letters())
svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">
  <path fill="#2e3436" d="{d}"/>
</svg>
"""
open(sys.argv[1], "w").write(svg)
print(svg)
