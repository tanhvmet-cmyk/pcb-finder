"""Font-independent label segmentation: strokes -> glyphs -> strings, using drawing order."""
import math, collections
import numpy as np, cv2
GW, GH = 20, 28
MAXS = 4.5

def strokes(page, maxs=None):
    maxs = maxs or MAXS
    out = []
    for i, x in enumerate(page.get_drawings()):
        if x["type"] != "s" or any(it[0] != "l" for it in x["items"]):
            continue
        segs = [((it[1].x, it[1].y), (it[2].x, it[2].y)) for it in x["items"]]
        xs = [p[0] for s in segs for p in s]; ys = [p[1] for s in segs for p in s]
        bb = (min(xs), min(ys), max(xs), max(ys))
        if max(bb[2] - bb[0], bb[3] - bb[1]) > maxs:
            continue
        out.append(dict(i=i, w=round(x["width"] or 0, 3), segs=segs, bb=bb, col=tuple(round(c, 2) for c in (x.get("color") or ()))))
    return out

def _u(a, b): return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
def _gap(a, b):
    return max(a[0] - b[2], b[0] - a[2], 0), max(a[1] - b[3], b[1] - a[3], 0)

def _blocks(st):
    """strokes drawn one after another with the same pen = one piece of text (or one drawing)"""
    B, cur = [], None
    for s in st:
        if cur and s["w"] == cur[-1]["w"] and s["col"] == cur[-1]["col"] and s["i"] - cur[-1]["i"] <= 2:
            cur.append(s)
        else:
            cur = [s]; B.append(cur)
    return B


def _group(block, eps, H, win=80):
    """join touching strokes into characters, never letting one character grow past a letter cell"""
    n = len(block)
    par = list(range(n)); bb = [s["bb"] for s in block]
    def f(i):
        while par[i] != i:
            par[i] = par[par[i]]; i = par[i]
        return i
    pairs = []
    for i in range(n):
        for j in range(i + 1, min(n, i + win)):
            dx, dy = _gap(bb[i], bb[j])
            g = max(dx, dy)
            if g <= eps:
                pairs.append((g, i, j))
    pairs.sort()
    box = {i: bb[i] for i in range(n)}
    for g, i, j in pairs:
        a, b = f(i), f(j)
        if a == b:
            continue
        nb = _u(box[a], box[b])
        if max(nb[2] - nb[0], nb[3] - nb[1]) <= 1.3 * H and min(nb[2] - nb[0], nb[3] - nb[1]) <= 1.12 * H:
            par[b] = a; box[a] = nb
    comp = {}
    for i in range(n):
        comp.setdefault(f(i), []).append(i)
    out = []
    for r, ii in sorted(comp.items(), key=lambda kv: min(kv[1])):
        out.append(dict(w=block[0]["w"], col=block[0]["col"], st=[block[i] for i in ii], bb=box[r], last=block[ii[-1]]["i"]))
    return out


def glyphs(st, maxs=None):
    maxs = maxs or MAXS
    """characters: groups of touching strokes inside one piece of text"""
    B = _blocks(st)
    # letter height per pen width, from a first rough grouping
    by = collections.defaultdict(list)
    for blk in B:
        for g in _group(blk, 0.03 * maxs, maxs):
            L = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for s in g["st"] for a, b in s["segs"])
            d = max(g["bb"][2] - g["bb"][0], g["bb"][3] - g["bb"][1])
            if L > 1.5 * d and d > 0.5:
                by[g["w"]].append(d)
    Hw = {}
    for w, ds in by.items():
        step = maxs / 45
        h = np.histogram(ds, bins=np.arange(0.4, maxs + 2 * step, step))
        k = int(np.argmax(h[0])); Hw[w] = float(h[1][k] + step)
    G = []
    for blk in B:
        H = Hw.get(blk[0]["w"], 0.45 * maxs)
        for g in _group(blk, 0.07 * H, H):
            g["H"] = H
            G.append(g)
    return G, Hw

def chains(G):
    """consecutive glyphs advancing along one axis = one text string"""
    S, cur = [], None
    def c(g): return ((g["bb"][0] + g["bb"][2]) / 2, (g["bb"][1] + g["bb"][3]) / 2)
    for g in G:
        H = g["H"]
        d = max(g["bb"][2] - g["bb"][0], g["bb"][3] - g["bb"][1])
        if d < 0.45 * H or d > 1.3 * H:           # dots, dashes, junk: do not start or extend names
            cur = None; continue
        if cur is not None and g["w"] == cur["w"] and g["col"] == cur["col"]:
            p, q = c(cur["g"][-1]), c(g)
            vx, vy = q[0] - p[0], q[1] - p[1]
            ok = False
            if cur["dir"] is None:
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    along = vx * dx + vy * dy; perp = abs(-vx * dy + vy * dx)
                    gx, gy = _gap(cur["g"][-1]["bb"], g["bb"]); gapd = gx if dx else gy
                    if 0.25 * H < along < 1.45 * H and perp < 0.3 * H and gapd < 0.6 * H:
                        cur["dir"] = (dx, dy); ok = True; break
            else:
                dx, dy = cur["dir"]; along = vx * dx + vy * dy; perp = abs(-vx * dy + vy * dx)
                gx, gy = _gap(cur["g"][-1]["bb"], g["bb"]); gapd = gx if dx else gy
                ok = 0.25 * H < along < 1.45 * H and perp < 0.3 * H and gapd < 0.6 * H
            if ok:
                cur["g"].append(g); continue
        cur = dict(w=g["w"], col=g["col"], g=[g], dir=None, H=H); S.append(cur)
    return [s for s in S if len(s["g"]) >= 2]

def glyph_img(g, direction, v0, Hs, mirror=False):
    dx, dy = direction
    # upright frame: u along reading direction, v down
    if (dx, dy) == (1, 0): up = lambda p: (p[0], p[1])
    elif (dx, dy) == (0, -1): up = lambda p: (-p[1], p[0])
    elif (dx, dy) == (-1, 0): up = lambda p: (-p[0], -p[1])
    else: up = lambda p: (p[1], -p[0])
    pts = [(up(a), up(b)) for s in g["st"] for a, b in s["segs"]]
    u0 = min(min(a[0], b[0]) for a, b in pts)
    sc = (GH - 5) / Hs
    img = np.zeros((GH, GW), np.uint8)
    for a, b in pts:
        cv2.line(img, (int(round((a[0] - u0) * sc)) + 2, int(round((a[1] - v0) * sc)) + 2),
                 (int(round((b[0] - u0) * sc)) + 2, int(round((b[1] - v0) * sc)) + 2), 255, 2)
    return img

def string_imgs(s):
    dx, dy = s["dir"]
    if (dx, dy) == (1, 0): vv = lambda b: (b[1], b[3])
    elif (dx, dy) == (0, -1): vv = lambda b: (b[0], b[2])
    elif (dx, dy) == (-1, 0): vv = lambda b: (-b[3], -b[1])
    else: vv = lambda b: (-b[2], -b[0])
    v0 = min(vv(g["bb"])[0] for g in s["g"]); v1 = max(vv(g["bb"])[1] for g in s["g"])
    Hs = max(v1 - v0, 0.5)
    return [glyph_img(g, s["dir"], v0, Hs) for g in s["g"]]

def orient(s):
    """The order the CAD tool drew the letters in says nothing about which way the text reads.
    Try both reading directions along the string's axis and keep the one whose letters look upright."""
    horiz = s["dir"][0] != 0
    key = (lambda g: (g["bb"][0] + g["bb"][2]) / 2) if horiz else (lambda g: (g["bb"][1] + g["bb"][3]) / 2)
    gs = sorted(s["g"], key=key)
    opts = [((1, 0), gs), ((-1, 0), gs[::-1])] if horiz else [((0, -1), gs[::-1]), ((0, 1), gs)]
    # upright for horizontal text; vertical text is first assumed to read upwards and OCR checks the other way
    d, order = opts[0]
    s["dir"], s["g"] = d, order
    best = (0, d, order, string_imgs(s))
    s["dir"], s["g"], s["imgs"] = best[1], best[2], best[3]

def page_strings(page):
    """drawn text strings; tries the usual letter size first, then large letters (small boards drawn big)"""
    best = None
    for maxs in (MAXS, 26.0):
        r = _page_strings(page, maxs)
        if best is None or len(r[0]) > 1.5 * len(best[0]):
            best = r
        if len(r[0]) >= 25:
            break
    return best


def _page_strings(page, maxs):
    st = strokes(page, maxs)
    G, Hw = glyphs(st, maxs)
    S = chains(G)
    for s in S:
        orient(s)
        bbs = [g["bb"] for g in s["g"]]
        s["cbb"] = bbs
        s["bb"] = (min(b[0] for b in bbs), min(b[1] for b in bbs), max(b[2] for b in bbs), max(b[3] for b in bbs))
    return S, Hw
