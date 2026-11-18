"""Board outline, component outlines, real-board photos and schematic hits."""
import re, math
import numpy as np
import cv2
import pymupdf


def drawing_extent(page, seed):
    """the drawn content that touches the labels' area, grown until nothing more joins it"""
    pr = page.rect
    z = 2.0
    pm = page.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False)
    im = np.frombuffer(pm.samples, np.uint8).reshape(pm.height, pm.width, 3)
    ink = (im.min(axis=2) < 225).astype(np.uint8)
    gap = max(3, int(0.012 * max(pr.width, pr.height) * z))       # pieces closer than this belong together
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * gap + 1, 2 * gap + 1))
    n, lbl, st, _ = cv2.connectedComponentsWithStats(cv2.dilate(ink, k), connectivity=8)
    comps = []
    for i in range(1, n):
        x, y, w, h, a = st[i]
        bx = (pr.x0 + (x + gap) / z, pr.y0 + (y + gap) / z, pr.x0 + (x + w - gap) / z, pr.y0 + (y + h - gap) / z)
        if (bx[2] - bx[0]) > 0.9 * pr.width and (bx[3] - bx[1]) > 0.9 * pr.height:
            continue                                                  # the page frame (spans the whole page)
        comps.append(bx)
    box = tuple(seed)
    for _ in range(6):
        grow = box
        for c in comps:
            if c[0] <= box[2] and c[2] >= box[0] and c[1] <= box[3] and c[3] >= box[1]:
                grow = (min(grow[0], c[0]), min(grow[1], c[1]), max(grow[2], c[2]), max(grow[3], c[3]))
        if grow == box:
            break
        box = grow
    # never swallow most of the page (title block, notes): then stay with the labels' area
    if (box[2] - box[0]) * (box[3] - box[1]) > 0.8 * pr.width * pr.height:
        m = 0.04 * max(seed[2] - seed[0], seed[3] - seed[1])
        box = (seed[0] - m, seed[1] - m, seed[2] + m, seed[3] + m)
    return box


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
        # no closed board outline: take everything drawn around the labels (parts, pads, logos, the
        # partial outline), not just the box around the names, so the whole side of the board shows
        best = drawing_extent(page, (xs0, ys0, xs1, ys1))
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
    """where each designator is written in dark, readable text on the schematic pages"""
    hits, per_page = {}, {}
    up = {n.upper(): n for n in names}
    for pn in range(len(doc)):
        if pn + 1 in skip_pages:
            continue
        got = []
        try:
            d = doc[pn].get_text("dict")
        except Exception:
            continue
        for blk in d.get("blocks", []):
            for ln in blk.get("lines", []):
                for sp in ln.get("spans", []):
                    c = sp.get("color", 0)
                    r, g, b = (c >> 16) & 255, (c >> 8) & 255, c & 255
                    if 0.299 * r + 0.587 * g + 0.114 * b > 190 or sp.get("size", 0) < 2.5:
                        continue                        # pale or tiny text (layer prints, notes): not readable
                    for w in re.finditer(r"[^\s,;:()]+", sp["text"]):
                        t = w.group().upper()
                        if t in up:
                            x0, y0, x1, y1 = sp["bbox"]
                            n = len(sp["text"]) or 1
                            cw = (x1 - x0) / n
                            got.append((up[t], (x0 + cw * w.start(), y0, x0 + cw * w.end(), y1)))
        if len({g[0] for g in got}) >= 10:
            per_page[pn] = got
    for pn, got in per_page.items():
        for n, b in got:
            hits.setdefault(n, []).append((pn, b))
    return hits, sorted(per_page)
