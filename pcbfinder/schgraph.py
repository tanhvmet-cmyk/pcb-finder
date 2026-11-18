"""The wires of a schematic (one or more PDF pages) as a graph, to draw how current flows.

Pins
  * Altium prints carry a hidden text "PI<designator><pin>" at every pin: used when present.
  * Otherwise pins are found from the drawing: a pin is a short straight line in the pin colour
    with one end on a part body (the body colour) and the other end free for wires; the part is
    the designator text (R12, Q3, U1…) nearest to the body. Those pins are numbered 1, 2, … in
    drawing order (top-left first), so flows for such PDFs name parts, not pin numbers.
Wires
  * lines of the wire colour; a wire end lying on another wire, a pin tip or a junction dot joins
    them; wires that only cross do not join
Labels
  * net labels, ports and power symbols (names written next to a wire end) join wires drawn apart;
    the same name on another page joins too

Several pages are stacked top to bottom into one drawing (page offsets in `self.pages`).
route(a, b) gives the polyline along the wires from pin a to pin b, or through the part body when
both pins belong to one part, or two wire pieces and a "jump" when the net continues elsewhere.
"""
import collections, heapq, math, re
import numpy as np

from . import nets as N

# designators: the usual prefixes followed by a number (R12, C3, Q101, U2, CN1, FUSE1, SCR302…)
DES_RE = re.compile(r"^(?:R|C|L|D|Q|U|IC|J|CN|CON|P|F|FU|FUSE|T|TR|K|RY|RL|Y|X|SW|S|LED|ZD|Z|TVS|TP|VR|RV|RT|NTC|"
                    r"TH|BT|BAT|M|MOT|FB|SCR|TRIAC|DB|BR|EC|CE|CY|CX|RX|LF|JP|H|MOV|ZNR|PTC|VD|VT|BZ|BUZ|MIC|SP|"
                    r"OP|PS|PT|CT|TC|RS|USB|HDR|IR|OPT|PC|ISO|DS|VDR|NR|GDT|E)\d{1,4}[A-Z]?$")
VALUE_RE = re.compile(r"^\d+(\.\d+)?[pnuµ]F$|^\d+(\.\d+)?[kKM]$|^\d+[kKMR]\d+$|^\d+(\.\d+)?V$|^\d+(\.\d+)?R$|^\d+(\.\d+)?Ω$|^\d+[PNU]$|^\d+[PNU]\d$", re.I)
GAP = 30.0          # space between stacked pages (pt)


def _norm(pin):
    return pin.lstrip("0") or "0"


def _dist_seg(px, py, s):
    x1, y1, x2, y2 = s
    dx, dy = x2 - x1, y2 - y1
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / L2))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy)), t


def _on_seg(px, py, s, tol):
    return _dist_seg(px, py, s)[0] < tol


def schematic_pages(doc):
    """pages that look like schematics: hidden pin texts, or many designators and wire-like lines"""
    out = [i for i, p in enumerate(doc) if len(N.hidden_tokens(p, "PI")) >= 10]
    if out:
        return out
    for i, p in enumerate(doc):
        words = [w[4] for w in p.get_text("words")]
        des = sum(1 for w in words if DES_RE.match(w))
        vals = sum(1 for w in words if VALUE_RE.match(w))
        if des < 25 or vals < 15:          # a schematic prints part values (10K, 100nF…); a layout does not
            continue
        lines = sum(1 for d in p.get_drawings() for it in d["items"] if it[0] == "l")
        if lines >= 400:
            out.append(i)
    return out


class SchGraph:
    def __init__(self, pages, force_drawing=False):
        """pages: list of PyMuPDF pages, stacked top to bottom"""
        if not isinstance(pages, (list, tuple)):
            pages = [pages]
        self.pages = []
        y = 0.0
        for p in pages:
            self.pages.append((p, 0.0, y))
            y += p.rect.height + GAP
        self.width = max(p.rect.width for p in pages)
        self.height = y - GAP
        self.pins = {}
        self.pin_source = "hidden"
        lines = collections.defaultdict(list)
        rect_lines = collections.defaultdict(list)       # edges of rectangles (part bodies)
        dots, texts, bodies = [], [], []
        self.des_text = {}
        for p, ox, oy in self.pages:
            des = N.designators(p)
            hidden = {t for pre in ("CO", "PI", "PA", "NL") for t, _, _ in N.hidden_tokens(p, pre)}
            if not force_drawing:
                for tok, x, yy in N.hidden_tokens(p, "PI"):
                    d, pin = N.split_pad("PA" + tok[2:], des)
                    self.pins.setdefault(f"{d}.{_norm(pin)}", (x + ox, yy + oy))
            for dr in p.get_drawings():
                col = tuple(round(v, 2) for v in dr["color"]) if dr.get("color") else None
                fcol = tuple(round(v, 2) for v in dr["fill"]) if dr.get("fill") else None
                for it in dr["items"]:
                    if it[0] == "l" and col:
                        lines[col].append((it[1].x + ox, it[1].y + oy, it[2].x + ox, it[2].y + oy))
                    elif it[0] in ("re", "qu") and col:
                        if it[0] == "re":
                            q = it[1]
                            pts = [(q.x0, q.y0), (q.x1, q.y0), (q.x1, q.y1), (q.x0, q.y1)]
                        else:
                            q = it[1]
                            pts = [(q.ul.x, q.ul.y), (q.ur.x, q.ur.y), (q.lr.x, q.lr.y), (q.ll.x, q.ll.y)]
                        for (ax, ay), (bx, by) in zip(pts, pts[1:] + pts[:1]):
                            rect_lines[col].append((ax + ox, ay + oy, bx + ox, by + oy))
                r = dr["rect"]
                if fcol and r.width < 3.5 and r.height < 3.5 and abs(r.width - r.height) < 0.3 and \
                        any(it[0] == "c" for it in dr["items"]):
                    dots.append((fcol, (r.x0 + r.x1) / 2 + ox, (r.y0 + r.y1) / 2 + oy))
                bodies.append((col, fcol, (r.x0 + ox, r.y0 + oy, r.x1 + ox, r.y1 + oy), [it[0] for it in dr["items"]]))
            for t, x, yy in N.hidden_tokens(p, "NL"):
                texts.append((re.sub(r"(?<=[A-Za-z])0(?=[A-Za-z])", "_", t[2:]), x + ox, yy + oy, "nl", None))
            for b in p.get_text("dict")["blocks"]:
                for l in b.get("lines", []):
                    for s in l["spans"]:
                        t = s["text"].strip()
                        x0, y0, x1, y1 = s["bbox"]
                        cx, cy = (x0 + x1) / 2 + ox, (y0 + y1) / 2 + oy
                        if t and len(t) < 30 and s["color"] == 0x800000 and re.match(r"^[A-Za-z0-9_+\-&./]+$", t):
                            texts.append((t, cx, cy, "sym", (x0 + ox, y0 + oy, x1 + ox, y1 + oy)))
                        if DES_RE.match(t) and t not in hidden:
                            self.des_text.setdefault(t, []).append((cx, cy))
        self.lines = lines
        self.rect_lines = rect_lines
        self._shapes = bodies
        self.inner_by_ref = {}
        if len(self.pins) < 10:
            self.pin_source = "drawing"
            self._pins_from_drawing(lines, bodies)
        self._build(lines, dots, texts)

    # ------------------------------------------------------------------ pins without hidden texts
    def _pins_from_drawing(self, lines, bodies):
        """pins = short straight lines in the pin colour (black) with one end on a part body and
        the other end on a wire; the part = the nearest designator text to that body"""
        pin_col = (0.0, 0.0, 0.0)
        S = [s for s in lines.get(pin_col, [])
             if 1.5 < math.hypot(s[2] - s[0], s[3] - s[1]) < 25 and (abs(s[0] - s[2]) < 0.05 or abs(s[1] - s[3]) < 0.05)]
        if not S:
            return
        # which colour do pin ends touch? the wire colour (long lines) and the body colour
        cols = [c for c in sorted(lines, key=lambda c: -len(lines[c])) if c != pin_col][:6]
        touch = collections.Counter()
        seglen = {}
        for c in cols:
            W = np.array(lines[c])
            seglen[c] = float(np.median(np.hypot(W[:, 2] - W[:, 0], W[:, 3] - W[:, 1])))
            for st in S[:600]:
                for (px, py) in ((st[0], st[1]), (st[2], st[3])):
                    sel = np.where((np.minimum(W[:, 0], W[:, 2]) - .3 <= px) & (np.maximum(W[:, 0], W[:, 2]) + .3 >= px) &
                                   (np.minimum(W[:, 1], W[:, 3]) - .3 <= py) & (np.maximum(W[:, 1], W[:, 3]) + .3 >= py))[0]
                    if any(_on_seg(px, py, tuple(W[i]), 0.3) for i in sel):
                        touch[c] += 1
                        break
        top = [c for c, n in touch.most_common(2)]
        if len(top) < 2:
            return
        wire_col = top[0] if seglen[top[0]] >= seglen[top[1]] else top[1]
        # part bodies may use several colours (Altium: blue symbols, dark-red IC boxes)
        body_cols = [c for c, n_ in touch.most_common() if c != wire_col and n_ >= 0.03 * min(len(S), 600)]
        W = np.array(lines[wire_col])

        def on(px, py, arr):
            if not len(arr):
                return False
            sel = np.where((np.minimum(arr[:, 0], arr[:, 2]) - .3 <= px) & (np.maximum(arr[:, 0], arr[:, 2]) + .3 >= px) &
                           (np.minimum(arr[:, 1], arr[:, 3]) - .3 <= py) & (np.maximum(arr[:, 1], arr[:, 3]) + .3 >= py))[0]
            return any(_on_seg(px, py, tuple(arr[i]), 0.3) for i in sel)
        shapes = [r for c, f, r, its in bodies if c in body_cols and (r[2] - r[0]) < 150 and (r[3] - r[1]) < 150]
        B = np.array([l for c in body_cols for l in lines.get(c, []) + self.rect_lines.get(c, [])])
        n = len(shapes)
        par = list(range(n))

        def f(i):
            while par[i] != i:
                par[i] = par[par[i]]
                i = par[i]
            return i
        R = np.array(shapes) if shapes else np.zeros((0, 4))
        for i in range(n):
            x0, y0, x1, y1 = R[i]
            near = np.where((R[:, 0] <= x1 + .6) & (R[:, 2] >= x0 - .6) & (R[:, 1] <= y1 + .6) & (R[:, 3] >= y0 - .6))[0]
            for j in near:
                if j > i:
                    par[f(i)] = f(int(j))
        clusters = collections.defaultdict(list)
        for i in range(n):
            clusters[f(i)].append(i)
        cbox = {}
        for k, idx in clusters.items():
            bx = R[idx]
            cbox[k] = (bx[:, 0].min(), bx[:, 1].min(), bx[:, 2].max(), bx[:, 3].max())
        CB = list(cbox.items())
        stub_of = collections.defaultdict(list)
        for st in S:
            ends = [(st[0], st[1]), (st[2], st[3])]
            on_w = [on(x, y, W) for x, y in ends]
            on_b = [on(x, y, B) for x, y in ends]
            # tip: the end on a wire (or the end not on the body); inner: the end on the body
            if on_b[0] and not on_b[1]:
                inner, tip = ends[0], ends[1]
            elif on_b[1] and not on_b[0]:
                inner, tip = ends[1], ends[0]
            else:
                continue
            ks = [k for k, (x0, y0, x1, y1) in CB if x0 - .6 <= inner[0] <= x1 + .6 and y0 - .6 <= inner[1] <= y1 + .6]
            if not ks:
                continue
            k = min(ks, key=lambda k: (cbox[k][2] - cbox[k][0]) * (cbox[k][3] - cbox[k][1]))
            stub_of[k].append((tip, inner))
        cand = []
        for k, stubs in stub_of.items():
            x0, y0, x1, y1 = cbox[k]
            for d, pts in self.des_text.items():
                for (tx, ty) in pts:
                    dist = math.hypot(max(x0 - tx, 0, tx - x1), max(y0 - ty, 0, ty - y1))
                    if dist < 18:
                        cand.append((dist, k, d))
        cand.sort()
        used_k, used_d = set(), set()
        for dist, k, d in cand:
            if k in used_k or d in used_d:
                continue
            used_k.add(k)
            used_d.add(d)
            stubs = sorted(stub_of[k], key=lambda t: (round(t[0][1], 1), round(t[0][0], 1)))
            for i, (tip, inner) in enumerate(stubs, 1):
                ref = f"{d}.{i}"
                self.pins[ref] = ((tip[0] + inner[0]) / 2, (tip[1] + inner[1]) / 2)
                self.inner_by_ref[ref] = (tip, inner)

    # ------------------------------------------------------------------ graph
    def _build(self, lines, dots, texts):
        cand = {}
        stub_col = collections.Counter()
        if self.pin_source == "drawing":
            for ref, (tip, inner) in self.inner_by_ref.items():
                cand[ref] = (0.0, (tip[0], tip[1], inner[0], inner[1]), (0.0, 0.0, 0.0))
                stub_col[(0.0, 0.0, 0.0)] += 1
        else:
            near_lines = {}
            for ref, (x, y) in self.pins.items():
                cs = []
                for col, segs in lines.items():
                    for s in segs:
                        L = math.hypot(s[2] - s[0], s[3] - s[1])
                        if not (0.8 < L < 30) or not (abs(s[0] - s[2]) < 0.05 or abs(s[1] - s[3]) < 0.05):
                            continue
                        d, _ = _dist_seg(x, y, s)
                        if d < 1.6:
                            cs.append((d, s, col))
                if cs:
                    cs.sort(key=lambda c: c[0])
                    near_lines[ref] = cs
                    cand[ref] = cs[0]
                    stub_col[cs[0][2]] += 1
        self.stub_colour = stub_col.most_common(1)[0][0] if stub_col else (0.0, 0.0, 0.0)

        def touches(col):
            n = 0
            segs = lines[col]
            for ref, (d, st, c) in list(cand.items())[:300]:
                for s in segs:
                    if s == st:
                        continue
                    if _on_seg(s[0], s[1], st, 0.3) or _on_seg(s[2], s[3], st, 0.3):
                        n += 1
                        break
            return n
        cols = [c for c in sorted(lines, key=lambda c: -len(lines[c]))[:8] if c != self.stub_colour]
        self.wire_colour = max(cols, key=touches) if cols else None
        wires = list(lines.get(self.wire_colour, []))
        if self.pin_source != "drawing" and wires:
            # second look at each pin: pin-direction marks (small triangles) are drawn in the pin
            # colour too; the real pin line is the one whose end touches a wire, and is longer
            W = np.array(wires)

            def on_wire(px, py):
                sel = np.where((np.minimum(W[:, 0], W[:, 2]) - .3 <= px) & (np.maximum(W[:, 0], W[:, 2]) + .3 >= px) &
                               (np.minimum(W[:, 1], W[:, 3]) - .3 <= py) & (np.maximum(W[:, 1], W[:, 3]) + .3 >= py))[0]
                return any(_on_seg(px, py, tuple(W[i]), 0.3) for i in sel)
            for ref, cs in near_lines.items():
                def score(c):
                    d, st, col = c
                    L = math.hypot(st[2] - st[0], st[3] - st[1])
                    touch = on_wire(st[0], st[1]) or on_wire(st[2], st[3])
                    return d + (0 if touch else 2.0) + (0 if L >= 2 else 1.0) + (0 if col == self.stub_colour else 0.5)
                cand[ref] = min(cs, key=score)
        dot_pts = [(x, y) for c, x, y in dots if c == self.wire_colour or c == self.stub_colour]
        by_part = collections.defaultdict(list)
        for ref, (x, y) in self.pins.items():
            by_part[ref.rsplit(".", 1)[0]].append((x, y))
        centre = {p: (np.mean([q[0] for q in v]), np.mean([q[1] for q in v])) for p, v in by_part.items()}
        self.tip, self.inner = {}, {}
        stubs = {ref: st for ref, (d, st, c) in cand.items()}
        for ref, st in stubs.items():
            ends = [(st[0], st[1]), (st[2], st[3])]
            if self.pin_source == "drawing":
                self.tip[ref], self.inner[ref] = ends[0], ends[1]
                continue
            score = []
            for ex, ey in ends:
                s = 0
                for w in wires:
                    if _on_seg(ex, ey, w, 0.3):
                        s += 2
                        break
                for r2, st2 in stubs.items():
                    if r2 != ref and (math.hypot(ex - st2[0], ey - st2[1]) < 0.3 or math.hypot(ex - st2[2], ey - st2[3]) < 0.3):
                        s += 1
                        break
                score.append(s)
            cx, cy = centre[ref.rsplit(".", 1)[0]]
            if score[0] != score[1]:
                i = 0 if score[0] > score[1] else 1
            else:
                i = 0 if math.hypot(ends[0][0] - cx, ends[0][1] - cy) > math.hypot(ends[1][0] - cx, ends[1][1] - cy) else 1
            self.tip[ref] = ends[i]
            self.inner[ref] = ends[1 - i]
        pts = [(w[0], w[1]) for w in wires] + [(w[2], w[3]) for w in wires] + list(self.tip.values()) + dot_pts
        P = np.array(pts) if pts else np.zeros((0, 2))
        self.nodes = {}
        self.adj = collections.defaultdict(list)

        def node(x, y):
            k = (round(x, 1), round(y, 1))
            self.nodes.setdefault(k, (x, y))
            return k

        def edge(a, b):
            if a == b:
                return
            ax, ay = self.nodes[a]
            bx, by = self.nodes[b]
            w = math.hypot(ax - bx, ay - by)
            self.adj[a].append((b, w))
            self.adj[b].append((a, w))
        self.wires = wires
        self.wire_edges = []
        for w in wires:
            x1, y1, x2, y2 = w
            sel = np.where((np.minimum(x1, x2) - .3 <= P[:, 0]) & (P[:, 0] <= np.maximum(x1, x2) + .3) &
                           (np.minimum(y1, y2) - .3 <= P[:, 1]) & (P[:, 1] <= np.maximum(y1, y2) + .3))[0]
            on = []
            for i in sel:
                d, t = _dist_seg(P[i, 0], P[i, 1], w)
                if d < 0.3:
                    on.append((t, P[i, 0], P[i, 1]))
            on.sort()
            ks = [node(x1, y1)] + [node(x, y) for t, x, y in on] + [node(x2, y2)]
            for a, b in zip(ks, ks[1:]):
                if a != b:
                    edge(a, b)
                    self.wire_edges.append((a, b))
        tips = list(self.tip.items())
        for i, (r1, t1) in enumerate(tips):
            for r2, t2 in tips[i + 1:]:
                if math.hypot(t1[0] - t2[0], t1[1] - t2[1]) < 0.3:
                    edge(node(*t1), node(*t2))
        for ref, t in self.tip.items():
            node(*t)
        self.group = {}
        g = 0
        for k in self.nodes:
            if k in self.group:
                continue
            st = [k]
            self.group[k] = g
            while st:
                u = st.pop()
                for v, _ in self.adj[u]:
                    if v not in self.group:
                        self.group[v] = g
                        st.append(v)
            g += 1
        deg = collections.Counter({u: len(vs) for u, vs in self.adj.items()})
        tipk = {(round(t[0], 1), round(t[1], 1)) for t in self.tip.values()}
        self.free_ends = collections.defaultdict(list)
        for k in self.nodes:
            if deg[k] == 1 and k not in tipk:
                self.free_ends[self.group[k]].append(k)
        # names of wires:
        #  * hidden net-label texts ("NL…", Altium) sit right on the wire: nearest wire point
        #  * ports and power symbols are small shapes in the label colour with the name inside or
        #    beside them; the wire end touching the shape gets the name
        self.labels = collections.defaultdict(list)
        free = {k for v in self.free_ends.values() for k in v}
        keys = list(self.nodes)
        E = np.array([self.nodes[k] for k in keys]) if keys else np.zeros((0, 2))
        for t, x, y, kind, bb in texts:
            if kind != "nl" or not len(E):
                continue
            d = np.hypot(E[:, 0] - x, E[:, 1] - y)
            i = int(d.argmin())
            if d[i] < 8:
                self.labels[self.group[keys[i]]].append((t, keys[i]))
        sym = [r for c, f, r, its in self._shapes if c == (0.5, 0.0, 0.0) and (r[2] - r[0]) < 60 and (r[3] - r[1]) < 60]
        n = len(sym)
        par = list(range(n))

        def ff(i):
            while par[i] != i:
                par[i] = par[par[i]]
                i = par[i]
            return i
        S = np.array(sym) if sym else np.zeros((0, 4))
        for i in range(n):
            x0, y0, x1, y1 = S[i]
            # the bars of a ground symbol are drawn as separate lines about 1 pt apart
            near = np.where((S[:, 0] <= x1 + 1.3) & (S[:, 2] >= x0 - 1.3) & (S[:, 1] <= y1 + 1.3) & (S[:, 3] >= y0 - 1.3))[0]
            for j in near:
                if j > i:
                    par[ff(i)] = ff(int(j))
        cl = collections.defaultdict(list)
        for i in range(n):
            cl[ff(i)].append(i)
        boxes = []
        for k, idx in cl.items():
            bx = S[idx]
            boxes.append((bx[:, 0].min(), bx[:, 1].min(), bx[:, 2].max(), bx[:, 3].max()))
        # text -> symbol (nearest, one to one)
        pairs = []
        stexts = [(t, x, y, bb) for t, x, y, kind, bb in texts if kind == "sym"]
        for ti, (t, x, y, bb) in enumerate(stexts):
            for bi, (x0, y0, x1, y1) in enumerate(boxes):
                dist = math.hypot(max(x0 - x, 0, x - x1), max(y0 - y, 0, y - y1))
                if dist < 7:
                    pairs.append((dist, ti, bi))
        pairs.sort()
        used_t, used_b = set(), set()
        for dist, ti, bi in pairs:
            if ti in used_t or bi in used_b:
                continue
            x0, y0, x1, y1 = boxes[bi]
            inside = np.where((E[:, 0] >= x0 - .8) & (E[:, 0] <= x1 + .8) & (E[:, 1] >= y0 - .8) & (E[:, 1] <= y1 + .8))[0] if len(E) else []
            ks = [keys[i] for i in inside]
            if not ks:
                continue
            ks.sort(key=lambda k: (k not in free, 0))
            used_t.add(ti)
            used_b.add(bi)
            self.labels[self.group[ks[0]]].append((stexts[ti][0], ks[0]))
        # texts with no symbol (net labels drawn as plain text): when the text touches a wire end
        # (or sits right on a wire point)
        taken = {k for g_ in self.labels.values() for _, k in g_}
        for ti, (t, x, y, bb) in enumerate(stexts):
            if ti in used_t or not len(E):
                continue
            x0, y0, x1, y1 = bb
            dx = np.maximum(np.maximum(x0 - E[:, 0], 0), E[:, 0] - x1)
            dy = np.maximum(np.maximum(y0 - E[:, 1], 0), E[:, 1] - y1)
            d = np.hypot(dx, dy) + np.array([0 if k in free else 1.5 for k in keys])
            i = int(d.argmin())
            if d[i] < 3 and keys[i] not in taken:
                taken.add(keys[i])
                self.labels[self.group[keys[i]]].append((t, keys[i]))

    # ------------------------------------------------------------------ nets (wires + labels)
    def nets(self):
        """wire groups joined by equal label names: {net id: dict(groups, names, pins)}"""
        par = {}

        def f(x):
            par.setdefault(x, x)
            while par[x] != x:
                par[x] = par[par[x]]
                x = par[x]
            return x
        by_name = {}
        for g, labs in self.labels.items():
            f(g)
            for t, _ in labs:
                key = t.lower()
                if key in by_name:
                    par[f(g)] = f(by_name[key])
                else:
                    by_name[key] = g
        groups = set(self.group.values())
        for g in groups:
            f(g)
        out = collections.defaultdict(lambda: dict(groups=set(), names=collections.Counter(), pins=[]))
        for g in groups:
            n = out[f(g)]
            n["groups"].add(g)
            for t, _ in self.labels.get(g, []):
                n["names"][t] += 1
        for ref in self.tip:
            out[f(self.group_of(ref))]["pins"].append(ref)
        self.net_of_group = {g: f(g) for g in groups}
        return dict(out)

    # ------------------------------------------------------------------ queries
    def tip_node(self, ref):
        t = self.tip.get(ref)
        return (round(t[0], 1), round(t[1], 1)) if t else None

    def group_of(self, ref):
        k = self.tip_node(ref)
        return self.group.get(k) if k else None

    def parts(self):
        return sorted({r.rsplit(".", 1)[0] for r in self.tip})

    def pins_of(self, part):
        return sorted((r for r in self.tip if r.rsplit(".", 1)[0] == part),
                      key=lambda r: [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", r)])

    def path_nodes(self, a, b):
        if a == b:
            return [self.nodes[a]]
        dist, prev, h = {a: 0.0}, {}, [(0.0, a)]
        while h:
            d, u = heapq.heappop(h)
            if u == b:
                break
            if d > dist.get(u, 1e18):
                continue
            for v, w in self.adj[u]:
                nd = d + w
                if nd < dist.get(v, 1e18):
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(h, (nd, v))
        if b not in dist:
            return None
        out = [b]
        while out[-1] != a:
            out.append(prev[out[-1]])
        return [self.nodes[k] for k in reversed(out)]

    def path_length(self, a, b):
        p = self.path_nodes(a, b)
        if not p:
            return None
        return sum(math.hypot(p[i][0] - p[i - 1][0], p[i][1] - p[i - 1][1]) for i in range(1, len(p)))

    def nearest_free_end(self, ref):
        g = self.group_of(ref)
        if g is None:
            return None
        cands = self.free_ends.get(g, [])
        if not cands:
            return None
        tx, ty = self.tip[ref]
        return min(cands, key=lambda k: math.hypot(self.nodes[k][0] - tx, self.nodes[k][1] - ty))

    def label_nodes(self, ref, name=None):
        g = self.group_of(ref)
        return [(t, k) for t, k in self.labels.get(g, []) if name is None or t.lower() == name.lower()]

    def jump_ends(self, a, b):
        la, lb = self.label_nodes(a), self.label_nodes(b)
        ta, tb = self.tip[a], self.tip[b]
        near = lambda cands, t: min(cands, key=lambda c: math.hypot(self.nodes[c[1]][0] - t[0], self.nodes[c[1]][1] - t[1]))
        common = {x.lower() for x, _ in la} & {x.lower() for x, _ in lb}
        if common:
            ca = near([c for c in la if c[0].lower() in common], ta)
            cb = near([c for c in lb if c[0].lower() == ca[0].lower()], tb)
            return ca[1], cb[1], ca[0]
        ea = near(la, ta)[1] if la else self.nearest_free_end(a)
        eb = near(lb, tb)[1] if lb else self.nearest_free_end(b)
        return ea, eb, (near(la, ta)[0] if la else None)

    def start_end(self, ref, prefer=None):
        labs = self.label_nodes(ref, prefer) or self.label_nodes(ref)
        t = self.tip[ref]
        if labs:
            c = min(labs, key=lambda c: math.hypot(self.nodes[c[1]][0] - t[0], self.nodes[c[1]][1] - t[1]))
            return c[1], c[0]
        return self.nearest_free_end(ref), None

    def relation(self, a, b, net_of=None):
        """how two pins are joined: 'part', 'wire', 'label', 'board' (only the PCB copper) or 'none'"""
        if a.rsplit(".", 1)[0] == b.rsplit(".", 1)[0]:
            return "part"
        ga, gb = self.group_of(a), self.group_of(b)
        if ga is not None and ga == gb:
            return "wire"
        la = {t.lower() for t, _ in self.labels.get(ga, [])}
        lb = {t.lower() for t, _ in self.labels.get(gb, [])}
        if la & lb:
            return "label"
        if net_of is not None and net_of.get(a) is not None and net_of.get(a) == net_of.get(b):
            return "board"
        return "none"

    def link_cost(self, a, b, net_of=None):
        """cost of going from pin a to pin b of another part (for choosing pins automatically)"""
        rel = self.relation(a, b, net_of)
        if rel == "wire":
            L = self.path_length(self.tip_node(a), self.tip_node(b))
            return 1.0 + (L or 0) / 400.0
        if rel == "label":
            return 25.0
        if rel == "board":
            return 40.0
        return None

    def route(self, a, b):
        if a not in self.tip or b not in self.tip:
            return None
        pa, pb = a.rsplit(".", 1)[0], b.rsplit(".", 1)[0]
        ta, tb = self.tip[a], self.tip[b]
        if pa == pb:
            return [dict(kind="body", pts=[ta, self.inner[a], self.inner[b], tb])]
        A, B = self.tip_node(a), self.tip_node(b)
        if self.group.get(A) == self.group.get(B):
            return [dict(kind="wire", pts=self.path_nodes(A, B))]
        legs = []
        ea, eb, name = self.jump_ends(a, b)
        if ea is not None:
            legs.append(dict(kind="wire", pts=self.path_nodes(A, ea)))
            ja = self.nodes[ea]
        else:
            ja = ta
        jb = self.nodes[eb] if eb is not None else tb
        legs.append(dict(kind="jump", pts=[ja, jb], text=name or ""))
        if eb is not None:
            legs.append(dict(kind="wire", pts=self.path_nodes(eb, B)))
        return legs
