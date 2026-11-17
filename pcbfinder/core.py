"""
PCB component finder - core.

Reads a PCBA approval-sheet PDF and works out, without OCR:
  1. the BOM table (designator -> value, package, part number)
  2. the silkscreen pages (top / bottom) and every designator label drawn on them
  3. the component outline that belongs to each label
  4. where the board sits in the "real picture" photos
  5. where each designator is written on the schematic pages
"""
import os, re, json, math
import numpy as np
import cv2
import pymupdf

HERE = os.path.dirname(os.path.abspath(__file__))
_G = np.load(os.path.join(HERE, "glyphs.npz"))
G_IMGS = [(g > 0) for g in _G["imgs"]]
G_CHARS = [str(c) for c in _G["chars"]]
GW, GH = 20, 28

# designator prefixes accepted even when the BOM does not list the part
PREFIXES = ("TVS", "Fuse", "FUSE", "LED", "CN", "TH", "TP", "SW", "FB", "BT", "IC", "RV", "NTC",
            "R", "C", "D", "Q", "U", "L", "E", "Z", "F", "J", "K", "T", "X", "Y", "P", "M")
PAT = re.compile(r"(?:%s)(?:[0-9]|\|(?![A-Za-z?]))+" % "|".join(PREFIXES))
MAX_GLYPH_PT = 3.6      # label letters are smaller than this (PDF points)


def log(*a):
    print(*a, flush=True)


# ----------------------------------------------------------------- BOM
def parse_bom(doc):
    """Find pages with a BOM table and read it by column position."""
    items, last_head = [], None
    for pn in range(len(doc)):
        ws = doc[pn].get_text("words")
        head = {}
        for w in ws:
            t = w[4].strip().lower()
            if t in ("item", "designator", "description", "qty", "component", "package", "unit") and t not in head:
                head[t] = w
            if t == "manufacturer":
                head.setdefault("mfr", w)
            if t == "p/n":
                head.setdefault("pn", w)
        if "item" not in head or "designator" not in head:
            nxt = items[-1]["item"] + 1 if items else None
            if last_head is None or not any(abs(w[0] - last_head["item"][0]) < 14 and w[4] == str(nxt) for w in ws):
                continue
            head = dict(last_head)
            head["item"] = (head["item"][0], -10, 0, 0, "Item")      # no header row on this page
        last_head = head
        hy = head["item"][1]
        hx = lambda k, d: head[k][0] if k in head else d
        x_item = head["item"][0]
        x_comp = hx("component", x_item + 25)
        x_pkg = hx("package", x_comp + 55) - 16          # header reads "Device Package"
        x_desc = hx("description", x_pkg + 55)
        x_unit = hx("unit", x_desc + 50)
        x_mfr = hx("mfr", x_unit + 45)
        x_pn = hx("pn", x_mfr + 85) - 28                 # header reads "Manufacturer P/N"
        x_qty = hx("qty", head["designator"][0] - 30)
        x_des = (x_qty + head["designator"][0]) / 2
        b_pkg = (x_comp + 22 + x_pkg) / 2
        b_desc = x_desc - 22
        b_unit = x_unit - 3
        b_mfr = x_mfr - 28
        b_pn = x_pn - 6
        b_end = x_pn + 62
        rows = sorted((w[1], int(w[4])) for w in ws
                      if abs(w[0] - x_item) < 14 and re.fullmatch(r"\d+", w[4]) and w[1] > hy + 2)
        for k, (y, n) in enumerate(rows):
            y0 = y - 9
            y1 = rows[k + 1][0] - 9 if k + 1 < len(rows) else 1e9
            blk = [w for w in ws if y0 <= w[1] < y1]

            def col(a, b):
                sel = sorted([w for w in blk if a <= w[0] < b and abs(w[1] - y) < 7],
                             key=lambda w: (round(w[1] / 3), w[0]))
                return " ".join(w[4] for w in sel)
            des = "".join(w[4] for w in sorted([w for w in blk if w[0] >= x_des],
                                                key=lambda w: (round(w[1]), w[0])))
            items.append(dict(item=n, comp=col(x_item + 12, b_pkg), pkg=col(b_pkg, b_desc),
                              desc=re.sub(r"\s*-\s*", "-", col(b_desc, b_unit)),
                              mfr=col(b_mfr, b_pn), pn=re.sub(r"\s+-", "-", col(b_pn, b_end)),
                              des=des, page=pn + 1))
    info = {}
    for b in items:
        for x in re.split(r"[,/;]", b["des"]):
            x = x.strip()
            if re.fullmatch(r"[A-Za-z]+\d+", x):
                info[x] = {k: b[k] for k in ("item", "comp", "pkg", "desc", "mfr", "pn", "page")}
    return info, sorted({b["page"] for b in items})


# ----------------------------------------------------------------- labels (stroke-font text)
def text_paths(page):
    out = []
    for i, x in enumerate(page.get_drawings()):
        if x["type"] != "s" or any(it[0] != "l" for it in x["items"]):
            continue
        segs = [((it[1].x, it[1].y), (it[2].x, it[2].y)) for it in x["items"]]
        xs = [p[0] for s in segs for p in s]
        ys = [p[1] for s in segs for p in s]
        bb = (min(xs), min(ys), max(xs), max(ys))
        if max(bb[2] - bb[0], bb[3] - bb[1]) > MAX_GLYPH_PT:
            continue
        out.append(dict(i=i, w=round(x["width"] or 0, 3), segs=segs, bb=bb))
    return out


def strings(paths):
    S, cur = [], None
    for p in paths:
        h = max(9 * p["w"], 1.6)
        if cur and cur["w"] == p["w"] and p["i"] - cur["last"] <= 3:
            b = cur["bb"]
            dx = max(b[0] - p["bb"][2], p["bb"][0] - b[2], 0)
            dy = max(b[1] - p["bb"][3], p["bb"][1] - b[3], 0)
            if max(dx, dy) < 0.75 * h:
                cur["paths"].append(p)
                cur["last"] = p["i"]
                cur["bb"] = (min(b[0], p["bb"][0]), min(b[1], p["bb"][1]), max(b[2], p["bb"][2]), max(b[3], p["bb"][3]))
                continue
        cur = dict(w=p["w"], paths=[p], bb=p["bb"], last=p["i"])
        S.append(cur)
    return S


def _lines(paths, rot):
    ax = (1, 3) if rot == 0 else (0, 2)
    L, cur, hi = [], None, None
    for p in sorted(paths, key=lambda p: p["bb"][ax[0]]):
        if cur is not None and p["bb"][ax[0]] < hi - 0.15:
            cur.append(p)
            hi = max(hi, p["bb"][ax[1]])
        else:
            cur = [p]
            L.append(cur)
            hi = p["bb"][ax[1]]
    return L


def _classify(img):
    a = img > 0
    best, bs = "?", 0.0
    for g, c in zip(G_IMGS, G_CHARS):
        v = (a & g).sum() / max(1, (a | g).sum())
        if v > bs:
            bs, best = v, c
    return best if bs >= 0.6 else "?"


def _decode(paths, rot):
    def up(p):
        return (p[0], p[1]) if rot == 0 else (-p[1], p[0])
    items = []
    for p in paths:
        sg = [(up(a), up(c)) for a, c in p["segs"]]
        us = [q[0] for g in sg for q in g]
        items.append([min(us), max(us), sg, p["bb"]])
    items.sort(key=lambda t: t[0])
    allv = [q[1] for it in items for sg in it[2] for q in sg]
    v0 = min(allv)
    Hs = max(allv) - v0
    if Hs < 0.8:
        return None
    groups = []
    for it in items:
        if groups and it[0] < groups[-1][1] + 0.06 * Hs:
            g = groups[-1]
            g[1] = max(g[1], it[1])
            g[2] += it[2]
            g[3].append(it[3])
        else:
            groups.append([it[0], it[1], list(it[2]), [it[3]]])
    text, cbb = "", []
    sc = (GH - 5) / Hs
    for g in groups:
        img = np.zeros((GH, GW), np.uint8)
        for a, c in g[2]:
            cv2.line(img, (int(round((a[0] - g[0]) * sc)) + 2, int(round((a[1] - v0) * sc)) + 2),
                     (int(round((c[0] - g[0]) * sc)) + 2, int(round((c[1] - v0) * sc)) + 2), 255, 2)
        text += "." if (img > 0).sum() < 40 else _classify(img)
        b = g[3]
        cbb.append((min(x[0] for x in b), min(x[1] for x in b), max(x[2] for x in b), max(x[3] for x in b)))
    return text, cbb


def _whole_matches(t):
    """Designators only when the whole word is made of them: 'C35C37' yes, 'ABC7' or 'XY200RF' no."""
    out = []
    for w in re.finditer(r"[A-Za-z0-9|?]+", t):
        pos, ms, word = 0, [], w.group()
        while pos < len(word):
            m = PAT.match(word, pos)
            if not m:
                ms = []
                break
            ms.append(m)
            pos = m.end()
        for m in ms:
            class M:                       # shift match positions back into the full string
                def __init__(s_, m): s_._m = m
                def start(s_): return s_._m.start() + w.start()
                def end(s_): return s_._m.end() + w.start()
                def group(s_): return s_._m.group()
            out.append(M(m))
    return out


def _score(t):
    return sum(len(m.group()) for m in _whole_matches(t)) - 0.5 * t.count("?")


def page_labels(page):
    """All designator labels on a vector page: [{name, bb, rot}], plus bboxes of every text string."""
    labels, allbb = [], []
    for s in strings(text_paths(page)):
        if len(s["paths"]) < 2:
            continue
        best = None
        for rot in (0, 90):
            cand = []
            for ln in _lines(s["paths"], rot):
                if len(ln) < 2:
                    continue
                d = _decode(ln, rot)
                if d:
                    cand.append(d + (rot,))
            sc = sum(_score(c[0]) for c in cand)
            if best is None or sc > best[0]:
                best = (sc, cand)
        for t, cbb, rot in best[1]:
            if sum(ch.isalnum() for ch in t) >= 3 and "?" not in t and "." not in t:
                allbb.append((min(c[0] for c in cbb), min(c[1] for c in cbb), max(c[2] for c in cbb), max(c[3] for c in cbb)))
            for m in _whole_matches(t):
                cb = cbb[m.start():m.end()]
                allbb.append((min(c[0] for c in cb), min(c[1] for c in cb), max(c[2] for c in cb), max(c[3] for c in cb)))
                labels.append(dict(name=m.group().replace("|", "1"),
                                   bb=(min(c[0] for c in cb), min(c[1] for c in cb), max(c[2] for c in cb), max(c[3] for c in cb)),
                                   rot=rot))
    return labels, allbb


# ----------------------------------------------------------------- find the silkscreen pages
def find_silk_pages(doc, bom):
    """Vector-only pages that carry designator labels. Returns [(page_index, labels, textbbs)] top first."""
    cands = []
    for pn in range(len(doc)):
        pg = doc[pn]
        if len(pg.get_text("words")) > 40:
            continue
        try:
            nd = len(pg.get_drawings())
        except Exception:
            continue
        if nd < 300:
            continue
        labs, allbb = page_labels(pg)
        # keep one label per name (the first), drop obvious noise
        seen, uniq = set(), []
        for l in labs:
            if l["name"] not in seen:
                seen.add(l["name"])
                uniq.append(l)
        if len(uniq) >= 8:
            cands.append(dict(pn=pn, labels=labs, allbb=allbb, nd=nd, names=seen))
            log(f"  page {pn+1}: {len(uniq)} labels, {nd} drawing objects")
    groups = []
    for c in sorted(cands, key=lambda c: c["nd"]):          # fewest drawings first = pure silkscreen
        for g in groups:
            inter = len(c["names"] & g["names"]) / max(1, min(len(c["names"]), len(g["names"])))
            if inter > 0.6:
                break
        else:
            groups.append(c)
    groups.sort(key=lambda g: -sum(1 for n in g["names"] if n in bom) if bom else -len(g["names"]))
    return groups[:2]


def board_clip(page, labels):
    """Board rectangle on the page: the smallest drawn outline that contains every label."""
    # ignore stray labels far from the crowd (title block text)
    cx = np.array([(l["bb"][0] + l["bb"][2]) / 2 for l in labels]); cy = np.array([(l["bb"][1] + l["bb"][3]) / 2 for l in labels])
    mx, my = np.median(cx), np.median(cy)
    sx = max(20.0, 4.5 * np.median(np.abs(cx - mx))); sy = max(20.0, 4.5 * np.median(np.abs(cy - my)))
    core_l = [l for l, x, y in zip(labels, cx, cy) if abs(x - mx) < sx and abs(y - my) < sy]
    xs0 = min(l["bb"][0] for l in core_l); ys0 = min(l["bb"][1] for l in core_l)
    xs1 = max(l["bb"][2] for l in core_l); ys1 = max(l["bb"][3] for l in core_l)
    pr = page.rect
    m = 0.12 * max(xs1 - xs0, ys1 - ys0)
    win = (max(pr.x0, xs0 - m), max(pr.y0, ys0 - m), min(pr.x1, xs1 + m), min(pr.y1, ys1 + m))
    # the board outline is the big closed shape around the labels: find its inside area on a small render
    z = 4
    pm = page.get_pixmap(matrix=pymupdf.Matrix(z, z), clip=pymupdf.Rect(*win), alpha=False)
    im = np.frombuffer(pm.samples, np.uint8).reshape(pm.height, pm.width, 3)
    ink = cv2.dilate((im.min(axis=2) < 200).astype(np.uint8), np.ones((3, 3), np.uint8))
    n, lbl, st, _ = cv2.connectedComponentsWithStats((ink == 0).astype(np.uint8), connectivity=4)
    border = set(np.unique(np.concatenate([lbl[0], lbl[-1], lbl[:, 0], lbl[:, -1]])))
    best, ba = None, 0
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if i in border:
            continue
        bx = (win[0] + (x - 1) / z, win[1] + (y - 1) / z, win[0] + (x + w + 1) / z, win[1] + (y + h + 1) / z)
        if bx[0] <= xs0 + 2 and bx[1] <= ys0 + 2 and bx[2] >= xs1 - 2 and bx[3] >= ys1 - 2 and a > ba:
            ba, best = a, bx
    outline = best is not None
    if best is None:
        m = 0.04 * max(xs1 - xs0, ys1 - ys0)
        best = (xs0 - m, ys0 - m, xs1 + m, ys1 + m)
    pad = 4
    clip = (max(pr.x0, best[0] - pad), max(pr.y0, best[1] - pad), min(pr.x1, best[2] + pad), min(pr.y1, best[3] + pad))
    return clip, best, outline


# ----------------------------------------------------------------- label -> component outline
def find_bodies(page, clip, labels, allbb, bom=None, Z=16):
    """
    Returns {name: (bbox_in_pt, conf)}  conf 1 = outline right next to its own label,
    2 = matched inside a group of labels (check it), 3 = no outline found (label position only).
    """
    pm = page.get_pixmap(matrix=pymupdf.Matrix(Z, Z), clip=pymupdf.Rect(*clip), alpha=False)
    im = np.frombuffer(pm.samples, np.uint8).reshape(pm.height, pm.width, 3)
    ink = (im.min(axis=2) < 200).astype(np.uint8)
    H, W = ink.shape

    def px(b, pad=0.2):
        return [max(0, int((b[0] - clip[0] - pad) * Z)), max(0, int((b[1] - clip[1] - pad) * Z)),
                min(W, int((b[2] - clip[0] + pad) * Z) + 1), min(H, int((b[3] - clip[1] + pad) * Z) + 1)]
    for b in allbb:                                   # rub out all text so only outlines remain
        x0, y0, x1, y1 = px(b)
        ink[y0:y1, x0:x1] = 0
    # close gaps in the outlines (capacitor brackets, diode frames) and take the enclosed white areas.
    # Start with small gaps and add what only appears with bigger ones.
    regs = []

    def overlaps(a, b):
        ix = min(a[2], b[2]) - max(a[0], b[0]); iy = min(a[3], b[3]) - max(a[1], b[1])
        if ix <= 0 or iy <= 0:
            return False
        return ix * iy > 0.25 * min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    for rad in (0.25, 0.6, 0.95, 1.35, 2.0, 2.8):
        r = max(1, int(rad * Z))
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
        fat = cv2.dilate(ink, k)                      # thick lines: gaps up to 2*rad points disappear
        n, lbl, st, _ = cv2.connectedComponentsWithStats((fat == 0).astype(np.uint8), connectivity=4)
        border = set(np.unique(np.concatenate([lbl[0], lbl[-1], lbl[:, 0], lbl[:, -1]])))
        for i in range(1, n):
            x, y, w, h, a = st[i]
            x -= r; y -= r; w += 2 * r; h += 2 * r     # undo the thickening
            if i in border or w < 1.6 * Z or h < 1.6 * Z or w * h < 4 * Z * Z:
                continue
            if w > 0.45 * W or h > 0.6 * H:
                continue
            rg = [clip[0] + x / Z, clip[1] + y / Z, clip[0] + (x + w) / Z, clip[1] + (y + h) / Z]
            if not any(overlaps(rg, o) for o in regs):
                regs.append(rg)
    # drop regions that are just a frame around labels (label lists) and regions nested in tiny ones
    def centre(b): return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
    def inside(p, b): return b[0] <= p[0] <= b[2] and b[1] <= p[1] <= b[3]
    lc = [centre(l["bb"]) for l in labels]
    cand = []
    for rg in regs:
        ncont = sum(1 for p in lc if inside(p, rg))
        area = (rg[2] - rg[0]) * (rg[3] - rg[1])
        if ncont and area < 400:          # small region with a label printed inside = label box, not a part
            continue
        cand.append(rg)

    def gap(a, b):
        dx = max(a[0] - b[2], b[0] - a[2], 0); dy = max(a[1] - b[3], b[1] - a[3], 0)
        return math.hypot(dx, dy)

    def cdist(a, b):
        ca, cb = centre(a), centre(b)
        return math.hypot(ca[0] - cb[0], ca[1] - cb[1])
    LIM = 3.2
    out, usedR, usedL = {}, set(), set()
    # which labels are crowded together (grouped label lists)?
    crowd = []
    for i, l in enumerate(labels):
        nb = sum(1 for j, m in enumerate(labels) if j != i and gap(l["bb"], m["bb"]) < 0.9)
        crowd.append(nb)
    # pass 1: best overall pairing of labels and outlines (not label by label, which cascades mistakes)
    from scipy.optimize import linear_sum_assignment
    areas = [(c[2] - c[0]) * (c[3] - c[1]) for c in cand]
    small_area = float(np.median(areas)) if areas else 30.0          # a typical chip resistor/capacitor box

    def size_pen(name, j):
        rel = areas[j] / small_area
        pkg = (bom.get(name, {}).get("pkg", "") if bom else "")
        pre = re.match(r"[A-Za-z]+", name).group().upper()
        is_small = bool(re.search(r"0201|0402|0603|0805|1206|SOD|SOT-?23", pkg)) or (not pkg and pre in ("R", "C", "TP", "TH", "Z"))
        is_big = pre in ("U", "CN", "L", "E", "TVS", "FUSE", "IC", "J", "T", "SW") and not is_small
        if is_small and rel > 4.5:
            return 2.5
        if is_big and rel < 1.6:
            return 2.5
        return 0.0

    def cost(i, j):
        # a label normally sits centred on its part, above/below or left/right of it
        l, c = labels[i], cand[j]
        ca, cb = centre(l["bb"]), centre(c)
        mis = min(abs(ca[0] - cb[0]), abs(ca[1] - cb[1]))
        return gap(l["bb"], c) + 0.6 * min(mis, 4.0) + size_pen(l["name"], j)
    NO = 7.0                                                          # cost of leaving a label without outline
    if labels and cand:
        M = np.full((len(labels), len(cand) + len(labels)), NO)
        for i, l in enumerate(labels):
            for j, c in enumerate(cand):
                g_ = gap(l["bb"], c)
                big = areas[j] > 2.2 * small_area
                if g_ < (7.5 if big else LIM):
                    M[i, j] = cost(i, j)
        ri, ci = linear_sum_assignment(M)
        for i, j in zip(ri, ci):
            if j >= len(cand) or M[i, j] >= NO:
                continue
            row = np.sort(M[i, :len(cand)]); col = np.sort(M[:, j])
            l2 = row[1] if len(row) > 1 else 99; r2 = col[1] if len(col) > 1 else 99
            g_ = M[i, j]
            first = row[0] == g_ and col[0] == g_
            conf = 1 if (first and crowd[i] == 0 and g_ < 2.0 and l2 - g_ > 0.8 and r2 - g_ > 0.8) else 2
            out[labels[i]["name"]] = (cand[j], conf)
            usedL.add(i); usedR.add(j)
    # pass 2: whatever is left -> label position only
    for i, l in enumerate(labels):
        if i not in usedL:
            b = l["bb"]
            out[l["name"]] = ([b[0] - 0.6, b[1] - 0.6, b[2] + 0.6, b[3] + 0.6], 3)
    return out, cand


# ----------------------------------------------------------------- photos
def find_photos(doc):
    """Photos of the real board: images that are mostly PCB-green. Returns list of (img_bgr, quad) left to right."""
    best = []
    for pn in range(len(doc)):
        pg = doc[pn]
        found = []
        for im in pg.get_images(full=True):
            try:
                pm = pymupdf.Pixmap(doc, im[0])
                if pm.n - pm.alpha != 3:
                    pm = pymupdf.Pixmap(pymupdf.csRGB, pm)
                if pm.width < 300 or pm.height < 200:
                    continue
                a = np.frombuffer(pm.samples, np.uint8).reshape(pm.height, pm.width, pm.n)[:, :, :3][:, :, ::-1].copy()
            except Exception:
                continue
            hsv = cv2.cvtColor(a, cv2.COLOR_BGR2HSV)
            m = ((hsv[:, :, 0] > 35) & (hsv[:, :, 0] < 95) & (hsv[:, :, 1] > 70) & (hsv[:, :, 2] > 40)).astype(np.uint8)
            if m.mean() < 0.3:
                continue
            m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8))
            cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            c = max(cs, key=cv2.contourArea)
            if cv2.contourArea(c) < 0.3 * a.shape[0] * a.shape[1]:
                continue
            hull = cv2.convexHull(c)
            box = None
            mar = cv2.minAreaRect(hull)
            straight = cv2.contourArea(hull) / max(1.0, mar[1][0] * mar[1][1]) > 0.965   # photo taken square-on
            per = cv2.arcLength(hull, True)
            for eps in (0.01, 0.015, 0.02, 0.03, 0.04, 0.06):
                ap = cv2.approxPolyDP(hull, eps * per, True)
                if len(ap) == 4 and not straight:
                    box = ap.reshape(4, 2).astype(np.float32)
                    break
            if box is None:
                box = cv2.boxPoints(mar)
            # order: top-left, top-right, bottom-right, bottom-left
            s = box.sum(1); d = box[:, 0] - box[:, 1]
            quad = np.float32([box[s.argmin()], box[d.argmax()], box[s.argmax()], box[d.argmin()]])
            rects = pg.get_image_rects(im[0])
            found.append((rects[0].x0 if rects else 0, a, quad))
        if len(found) > len(best):
            best = sorted(found, key=lambda f: f[0])
    return [(a, q) for _, a, q in best]


def photo_homography(board, quad, img_shape):
    """Map board rectangle (PDF pt) to the photo quad if the shapes agree."""
    bw, bh = board[2] - board[0], board[3] - board[1]
    qw = (np.linalg.norm(quad[1] - quad[0]) + np.linalg.norm(quad[2] - quad[3])) / 2
    qh = (np.linalg.norm(quad[3] - quad[0]) + np.linalg.norm(quad[2] - quad[1])) / 2
    if abs((bw / bh) / (qw / qh) - 1) > 0.12:
        return None
    src = np.float32([[board[0], board[1]], [board[2], board[1]], [board[2], board[3]], [board[0], board[3]]])
    return cv2.getPerspectiveTransform(src, quad)


# ----------------------------------------------------------------- schematic
def find_schematic(doc, names, skip_pages):
    hits, per_page = {}, {}
    up = {n.upper(): n for n in names}
    for pn in range(len(doc)):
        if pn + 1 in skip_pages:
            continue
        ws = doc[pn].get_text("words")
        got = []
        for w in ws:
            t = w[4].strip(",;:()").upper()
            if t in up:
                got.append((up[t], w[:4]))
        if len({g[0] for g in got}) >= 10:
            per_page[pn] = got
    for pn, got in per_page.items():
        for n, b in got:
            hits.setdefault(n, []).append((pn, b))
    return hits, sorted(per_page)
