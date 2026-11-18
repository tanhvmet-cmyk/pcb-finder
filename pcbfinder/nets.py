"""Which pin is joined to which: the netlist of a board, traced from the copper-layer pages of a
CAD print (Altium style: every pad carries a hidden text "PA<designator><pin>").

How it works
  * the copper of each layer page is drawn in one colour (red / blue ...); grey = drilled holes;
    a colour drawn identically on both pages (e.g. a mechanical layer of thick tracks) is kept as a
    third layer that touches the others only through pads and holes
  * the copper of each layer is cut into connected areas
  * plated holes (vias, through-hole pads) join the areas of all layers at that place
  * every pad joins the areas under it on its own side (both sides for through-hole pads)
  * areas joined together = one net

The two layer pages may be mirrored or shifted against each other: the pads that appear on both
pages give the transform.
"""
import re
import numpy as np
import cv2
import pymupdf


def hidden_tokens(page, prefix):
    """hidden texts starting with prefix, one entry per token, with the centre of its characters"""
    out = []
    for s in page.get_texttrace():
        if s.get("type") != 3:
            continue
        chars = s["chars"]
        tok, boxes = "", []
        for ch in list(chars) + [(32, 0, (0, 0), (0, 0, 0, 0))]:
            c = chr(ch[0])
            if c.isspace():
                if tok.startswith(prefix) and boxes:
                    b = np.array(boxes)
                    out.append((tok, float((b[:, 0].min() + b[:, 2].max()) / 2), float((b[:, 1].min() + b[:, 3].max()) / 2)))
                tok, boxes = "", []
            else:
                tok += c
                boxes.append(ch[3])
    return out


def designators(page):
    return {t[2:] for t, _, _ in hidden_tokens(page, "CO")}


def split_pad(name, des):
    """'PAC10101' -> ('C101', '01') using the known designators (longest match)"""
    s = name[2:]
    # pins are written with 2 digits (01, 02 ... 10): prefer the split that leaves 2, then 3 characters
    cand = [d for d in des if s.startswith(d) and len(s) > len(d)]
    if cand:
        best = min(cand, key=lambda d: ({2: 0, 3: 1}.get(len(s) - len(d), 2), -len(d)))
        return best, s[len(best):]
    m = re.match(r"(.*?\d+)(\d\d)$", s)
    return (m.group(1), m.group(2)) if m else (s, "")


def pads_of(page):
    des = designators(page)
    seen, out = set(), []
    for tok, x, y in hidden_tokens(page, "PA"):
        key = (tok, round(x), round(y))
        if key in seen:
            continue
        seen.add(key)
        d, pin = split_pad(tok, des)
        out.append(dict(tok=tok, des=d, pin=pin, x=x, y=y))
    return out


def page_transform(pa, pb):
    """affine transform taking page-a coordinates to page-b coordinates, from pads found once on each"""
    ca, cb = {}, {}
    for p in pa:
        ca.setdefault(p["tok"], []).append((p["x"], p["y"]))
    for p in pb:
        cb.setdefault(p["tok"], []).append((p["x"], p["y"]))
    A, B = [], []
    for t, v in ca.items():
        if len(v) == 1 and len(cb.get(t, [])) == 1:
            A.append(v[0])
            B.append(cb[t][0])
    A, B = np.float32(A), np.float32(B)
    M, inl = cv2.estimateAffine2D(A, B, ransacReprojThreshold=1.0)
    return M


def _render(page, clip, z):
    pix = page.get_pixmap(matrix=pymupdf.Matrix(z, z), clip=clip, alpha=False)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, 3)


def _colour_masks(img):
    """masks of the strong colours in an image: {(r,g,b) rounded: mask}"""
    q = (img.astype(int) + 32) // 64 * 64
    q = np.clip(q, 0, 255)
    key = q[:, :, 0] * 65536 + q[:, :, 1] * 256 + q[:, :, 2]
    vals, cnt = np.unique(key, return_counts=True)
    out = {}
    for v, c in zip(vals, cnt):
        r, g, b = v // 65536, (v // 256) % 256, v % 256
        sat = max(r, g, b) - min(r, g, b)
        if c < 0.002 * key.size or sat < 100:
            continue
        out[(r, g, b)] = key == v
    return out


def _grey(img):
    a = img.astype(int)
    return (np.abs(a - 128).max(2) < 40) & (np.abs(a[:, :, 0] - a[:, :, 1]) < 12) & (np.abs(a[:, :, 1] - a[:, :, 2]) < 12)


def _round_lands(cu, z):
    """centres and radii of round copper lands (via pads): a local peak of the distance to the
    copper edge, of via size, with little copper just outside the disc"""
    dt = cv2.distanceTransform(cu.astype(np.uint8), cv2.DIST_L2, 5)
    rmin, rmax = 0.5 * z, 1.8 * z
    mx = cv2.dilate(dt, np.ones((5, 5), np.uint8))
    ys, xs = np.where((dt >= rmin) & (dt <= rmax) & (dt >= mx - 1e-3))
    out, taken = [], np.zeros(cu.shape, bool)
    H, W = cu.shape
    ang = np.linspace(0, 2 * np.pi, 48, endpoint=False)
    for y, x in sorted(zip(ys, xs), key=lambda t: -dt[t[0], t[1]]):
        if taken[y, x]:
            continue
        r = dt[y, x]
        rr = 1.45 * r + 1
        px = (x + rr * np.cos(ang)).astype(int)
        py = (y + rr * np.sin(ang)).astype(int)
        ok = (px >= 0) & (px < W) & (py >= 0) & (py < H)
        if ok.sum() < 40:
            continue
        fill = cu[py[ok], px[ok]].mean()
        if fill < 0.4:
            out.append((x, y, r))
            taken[max(0, int(y - r)):int(y + r) + 1, max(0, int(x - r)):int(x + r) + 1] = True
    return out


def _tented_vias(cuT, cuB, labT, labB, PX, z, join):
    """vias whose hole is not printed: a round land on both layers at the same place"""
    lt = _round_lands(cuT, z)
    lb = _round_lands(cuB, z)
    if not lt or not lb:
        return 0
    B = np.array([(x, y, r) for x, y, r in lb])
    n = 0
    for x, y, r in lt:
        bx, by = PX @ [x, y, 1]
        d = np.hypot(B[:, 0] - bx, B[:, 1] - by)
        i = int(d.argmin())
        if d[i] < 0.45 * z and abs(B[i, 2] - r) < 0.4 * max(r, B[i, 2]):
            a, b = labT[int(y), int(x)], labB[int(B[i, 1]), int(B[i, 0])]
            if a and b:
                join([("T", int(a)), ("B", int(b))], ("via", float(x), float(y)))
                n += 1
    return n


def extract(doc, top_pn, bot_pn, sides, z=None, log=print):
    """sides: {designator: 'top'|'bottom'} for SMD parts (unknown parts: decided by the copper).
    Returns dict(nets=[[pad,...],...], pads=[...], maps=...) where every pad is
    dict(des, pin, x, y) in top-page coordinates."""
    pt, pb = doc[top_pn], doc[bot_pn]
    PT, PB = pads_of(pt), pads_of(pb)
    M = page_transform(PT, PB)                                  # top page -> bottom page
    xs = [p["x"] for p in PT]
    ys = [p["y"] for p in PT]
    m = 12
    clipT = pymupdf.Rect(min(xs) - m, min(ys) - m, max(xs) + m, max(ys) + m)
    corners = np.float32([[clipT.x0, clipT.y0], [clipT.x1, clipT.y0], [clipT.x0, clipT.y1], [clipT.x1, clipT.y1]])
    cb = corners @ M[:, :2].T + M[:, 2]
    clipB = pymupdf.Rect(cb[:, 0].min(), cb[:, 1].min(), cb[:, 0].max(), cb[:, 1].max())
    if z is None:
        z = min(10.0, 4200 / max(clipT.width, clipT.height))
    it, ib = _render(pt, clipT, z), _render(pb, clipB, z)
    ct, cbm = _colour_masks(it), _colour_masks(ib)
    # a colour drawn the same on both pages is a shared (mechanical) layer
    shared = None
    for c in set(ct) & set(cbm):
        a, b = ct[c].sum(), cbm[c].sum()
        if 0.8 < a / max(b, 1) < 1.25:
            shared = c
    own_t = max((c for c in ct if c != shared), key=lambda c: ct[c].sum())
    own_b = max((c for c in cbm if c != shared), key=lambda c: cbm[c].sum())
    cuT, cuB = ct[own_t], cbm[own_b]
    cuS = ct[shared] if shared else np.zeros_like(cuT)
    grT = _grey(it)
    log("    copper colours: top %s, bottom %s%s" % (tuple(map(int, own_t)), tuple(map(int, own_b)), (", shared layer %s" % (tuple(map(int, shared)),)) if shared else ""))
    nT, labT = cv2.connectedComponents(cuT.astype(np.uint8), connectivity=8)
    nB, labB = cv2.connectedComponents(cuB.astype(np.uint8), connectivity=8)
    nS, labS = cv2.connectedComponents(cuS.astype(np.uint8), connectivity=8)
    H, W = cuT.shape
    # pixel transform top raster -> bottom raster
    S1 = np.array([[1 / z, 0, clipT.x0], [0, 1 / z, clipT.y0], [0, 0, 1]])
    M3 = np.vstack([M, [0, 0, 1]])
    S2 = np.array([[z, 0, -clipB.x0 * z], [0, z, -clipB.y0 * z], [0, 0, 1]])
    PX = (S2 @ M3 @ S1)[:2]
    par = {}

    def f(x):
        par.setdefault(x, x)
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x

    edges = []

    def join(items, why="pad"):
        items = list(items)
        edges.append((why, items))
        for a in items[1:]:
            par[f(a)] = f(items[0])

    k5 = np.ones((5, 5), np.uint8)
    ng, lg, st, cen = cv2.connectedComponentsWithStats(grT.astype(np.uint8))
    grB = _grey(ib)
    ngb, lgb, stb, cenb = cv2.connectedComponentsWithStats(grB.astype(np.uint8))
    bh = [i for i in range(1, ngb) if stb[i][4] >= 6 * z]
    CB = np.array([cenb[i] for i in bh]) if bh else np.zeros((0, 2))

    def hb(m, i=None):
        """the same hole on the bottom raster: the bottom page's own grey disc when it is there
        (exact), otherwise the top disc moved by the page transform"""
        if i is not None and len(CB):
            bx, by = PX @ [cen[i][0], cen[i][1], 1]
            dd = np.hypot(CB[:, 0] - bx, CB[:, 1] - by)
            j = int(dd.argmin())
            if dd[j] < 1.2 * z:
                k = bh[j]
                x, y, w, h = stb[k][:4]
                out = np.zeros(lgb.shape, np.uint8)
                sl = (slice(max(0, y - 6), y + h + 6), slice(max(0, x - 6), x + w + 6))
                out[sl] = lgb[sl] == k
                return cv2.dilate(out, k5) > 0
        return cv2.warpAffine(m.astype(np.uint8), PX, (labB.shape[1], labB.shape[0]), flags=cv2.INTER_NEAREST) > 0
    holes = 0
    for i in range(1, ng):
        if st[i][4] < 6 * z:
            continue
        x, y, w, h = st[i][:4]
        sl = (slice(max(0, y - 6), y + h + 6), slice(max(0, x - 6), x + w + 6))
        blob = np.zeros((H, W), np.uint8)
        blob[sl] = (lg[sl] == i)
        ring = cv2.dilate(blob, k5) > 0
        items = {("T", c) for c in np.unique(labT[ring & cuT]) if c}
        items |= {("S", c) for c in np.unique(labS[ring & cuS]) if c}
        rb = hb(ring, i)
        items |= {("B", c) for c in np.unique(labB[rb & cuB]) if c}
        if len(items) > 1:
            join(items, ("hole", float(cen[i][0] / z + clipT.x0), float(cen[i][1] / z + clipT.y0)))
            holes += 1
    log(f"    {holes} plated holes join the layers")
    if holes < 10:   # holes not printed: fall back to round lands at the same place on both layers
        vias = _tented_vias(cuT, cuB, labT, labB, PX, z, join)
        log(f"    {vias} covered vias (round lands at the same place on both layers)")

    def near(lab, mask, px, py, r):
        x, y, r = int(px), int(py), int(r)
        win = lab[max(0, y - r):y + r + 1, max(0, x - r):x + r + 1]
        return {int(c) for c in np.unique(win) if c}

    PB_by = {}
    for p in PB:
        PB_by.setdefault(p["tok"], []).append(p)
    # first pass: copper under each pad on each layer
    info = []
    for p in PT:
        tx, ty = (p["x"] - clipT.x0) * z, (p["y"] - clipT.y0) * z
        bx, by = PX @ [tx, ty, 1]
        cand = PB_by.get(p["tok"], [])
        if cand:      # the bottom page's own position of this pad
            q = min(cand, key=lambda q: (q["x"] - (bx / z + clipB.x0)) ** 2 + (q["y"] - (by / z + clipB.y0)) ** 2)
            bx, by = (q["x"] - clipB.x0) * z, (q["y"] - clipB.y0) * z
        inside = 0 <= int(ty) < H and 0 <= int(tx) < W
        hole = bool(inside and grT[int(ty), int(tx)])
        r = max(2, 0.6 * z)
        t = near(labT, cuT, tx, ty, r) if inside else set()
        b = near(labB, cuB, bx, by, r) if 0 <= int(by) < labB.shape[0] and 0 <= int(bx) < labB.shape[1] else set()
        sh = near(labS, cuS, tx, ty, r) if inside else set()
        t0 = near(labT, cuT, tx, ty, 1) if inside else set()
        b0 = near(labB, cuB, bx, by, 1) if 0 <= int(by) < labB.shape[0] and 0 <= int(bx) < labB.shape[1] else set()
        s0 = near(labS, cuS, tx, ty, 1) if inside else set()
        p = dict(p, bx=float(bx / z + clipB.x0), by=float(by / z + clipB.y0))
        info.append((p, tx, ty, hole, t, b, sh, t0, b0, s0))
    # the side of each part: a pad is copper, so on its own side every pad has copper under it
    # When both sides have copper under every pad (a pour on the far side), the pads of a part
    # on its own side sit on different copper (else the part would be shorted out).
    per = {}
    for p, tx, ty, hole, t, b, sh, t0, b0, s0 in info:
        if not hole:
            c = per.setdefault(p["des"], [0, 0, 0, set(), set()])
            c[0] += 1
            c[1] += bool(t0 or s0)
            c[2] += bool(b0 or s0)
            c[3].add(min(t0) if t0 else None)
            c[4].add(min(b0) if b0 else None)
    areaT, areaB = np.bincount(labT.ravel()), np.bincount(labB.ravel())
    side_of = {}
    for d_, (n, ct_, cb_, dt_, db_) in per.items():
        if ct_ == n and cb_ < n:
            side_of[d_] = "top"
        elif cb_ == n and ct_ < n:
            side_of[d_] = "bottom"
        elif n > 1 and len(dt_ - {None}) != len(db_ - {None}):
            side_of[d_] = "top" if len(dt_ - {None}) > len(db_ - {None}) else "bottom"
        elif dt_ - {None} and db_ - {None}:
            # pads sit on small copper (pads, tracks); the far side under them is a big pour
            at = np.median([areaT[c] for c in dt_ - {None}])
            ab = np.median([areaB[c] for c in db_ - {None}])
            side_of[d_] = "top" if at < ab / 3 else "bottom" if ab < at / 3 else (sides.get(d_) or "bottom")
        else:
            side_of[d_] = sides.get(d_) or ("top" if ct_ > cb_ else "bottom")
    pads = []
    for p, tx, ty, hole, t, b, s, t0, b0, s0 in info:
        side = side_of.get(p["des"])
        items = set()
        if hole:
            bi = lg[int(ty), int(tx)]
            sl = slice(max(0, int(ty) - 200), int(ty) + 200), slice(max(0, int(tx) - 200), int(tx) + 200)
            ring = np.zeros((H, W), np.uint8)
            ring[sl] = lg[sl] == bi
            ring = cv2.dilate(ring, k5) > 0
            items |= {("T", c) for c in np.unique(labT[ring & cuT]) if c}
            items |= {("S", c) for c in np.unique(labS[ring & cuS]) if c}
            items |= {("B", c) for c in np.unique(labB[hb(ring, bi) & cuB]) if c}
        elif side == "top":
            items |= {("T", c) for c in t}
        else:
            items |= {("B", c) for c in b}
        items |= {("S", c) for c in s}
        p = dict(p, layer="both" if hole else side)
        p["items"] = items
        pads.append(p)
        if items:
            join(items, ("pad", p["tok"]))
    groups = {}
    for p in pads:
        if p["items"]:
            groups.setdefault(f(next(iter(p["items"]))), []).append(p)
    nets = sorted(groups.values(), key=len, reverse=True)
    root_net = {r: i for i, r in enumerate(sorted(groups, key=lambda r: -len(groups[r])))}
    # label maps: net index per copper pixel (for drawing a net on the board)
    def netmap(lab, layer, n):
        lut = np.full(n, -1, np.int32)
        for c in range(1, n):
            r = f((layer, c)) if (layer, c) in par else None
            lut[c] = root_net.get(r, -1) if r is not None else -1
        return lut[lab]
    maps = dict(z=z, clipT=tuple(clipT), clipB=tuple(clipB), M=M.tolist(),
                top=netmap(labT, "T", nT), bottom=netmap(labB, "B", nB),
                shared=netmap(labS, "S", nS) if shared else None,
                colours=dict(top=own_t, bottom=own_b, shared=shared))
    unconnected = [p for p in pads if not p["items"]]
    for p in pads:
        p["cu"] = sorted(p.pop("items"))
    return dict(nets=nets, pads=pads, maps=maps, unconnected=unconnected, edges=edges, side_of=side_of, per={k: v[:3] for k, v in per.items()})
