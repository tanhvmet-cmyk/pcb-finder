"""Current flowing on the schematic: one HTML page with the schematic page(s) (vector, sharp at any
zoom) and the paths of current drawn along the wires, with arrows and moving dots in the
direction of the current. Without a flows file the page still lets you click any wire and see its
whole net.

A flow is an ordered list of tokens, written by a person or by the agent:
    "F101.1", "F101.2"   two pins of one part: the current goes through the part
    "F101.2", "LF101.2"  pins of two parts: the current follows the wires (or a label) between them
    "Q1"                 a part without a pin: the pins are chosen automatically (in and out)
    "<F101.1"            start at the net label / port wired to F101.1 (where current enters)
    "LF101.1>"           end at the net label / port wired to LF101.1 (where current leaves)
    "~Suction motor~"    between two pins: the current leaves the board (to a motor, a lamp…)
"""
import base64, json, math, os, re

from . import schgraph as SG

HERE = os.path.dirname(os.path.abspath(__file__))
BIG = 1e6

DEFAULT_KINDS = {
    "ac": {"label": "AC mains (L → load → N)", "color": "#e8202a"},
    "hv": {"label": "High-voltage DC / power conversion", "color": "#f07800"},
    "batt": {"label": "Battery / main DC current", "color": "#d81b60"},
    "dc": {"label": "Low-voltage DC supply", "color": "#11a34a"},
    "ctl": {"label": "Commands from the controller", "color": "#8a3ffc"},
    "sense": {"label": "Measurements, buttons, sensors", "color": "#0a8fd1"},
}


def _parse(path):
    """tokens -> list of dict(k=pin|part|off, v=value, start, end)"""
    out = []
    for t in path:
        t = str(t).strip()
        if not t:
            continue
        if t.startswith("~") and t.endswith("~") and len(t) > 1:
            out.append(dict(k="off", v=t.strip("~").strip()))
            continue
        if t.startswith("@"):
            out.append(dict(k="label", v=t[1:].strip()))
            continue
        start, end = t.startswith("<"), t.endswith(">")
        v = t.strip("<>").strip()
        out.append(dict(k="pin" if "." in v else "part", v=v, start=start, end=end))
    return out


CONNECTOR = re.compile(r"^(P|J|CN|CON|X|XS|XP|JP|PL|SK)\d", re.I)


def _label_ref(name, node):
    return f"@{name}|{node[0]},{node[1]}"


def _label_node(ref):
    name, xy = ref[1:].rsplit("|", 1)
    x, y = xy.split(",")
    return name, (float(x), float(y))


def resolve(g, path, net_of=None):
    """explicit pins for every token. Returns (resolved tokens, problems).
    A part token becomes two positions (pin in, pin out) — except the very first and last tokens
    when they carry no "<" / ">", which are a source or a sink (one pin). The pins are chosen so
    that the chain follows real connections (wires first, then labels, then the board copper) and
    does not come back to a net it has already left."""
    toks = _parse(path)
    probs = []
    pos = []
    idx_pin = [i for i, t in enumerate(toks) if t["k"] != "off"]
    for i, t in enumerate(toks):
        if t["k"] == "off":
            pos.append(dict(off=True, text=t["v"], tok=i))
            continue
        if t["k"] == "label":
            nodes = [k for gi, labs in g.labels.items() for (nm, k) in labs if nm.lower() == t["v"].lower()]
            if not nodes:
                probs.append(f"no label @{t['v']} on the schematic")
                continue
            pos.append(dict(cands=[_label_ref(t["v"], k) for k in dict.fromkeys(nodes)], tok=i, part="@" + t["v"]))
            continue
        if t["k"] == "pin":
            if t["v"] not in g.tip:
                probs.append(f"{t['v']} is not drawn on the schematic")
                continue
            pos.append(dict(cands=[t["v"]], tok=i, part=t["v"].rsplit(".", 1)[0]))
            continue
        pins = g.pins_of(t["v"])
        if not pins:
            probs.append(f"{t['v']} is not drawn on the schematic")
            continue
        edge = i in (idx_pin[0], idx_pin[-1]) and not (t.get("start") or t.get("end"))
        beside_off = (i > 0 and toks[i - 1]["k"] == "off") or (i + 1 < len(toks) and toks[i + 1]["k"] == "off")
        if edge or len(pins) == 1 or (beside_off and CONNECTOR.match(t["v"]) and not (t.get("start") or t.get("end"))):
            pos.append(dict(cands=pins, tok=i, part=t["v"]))
        elif beside_off and CONNECTOR.match(t["v"]):
            pos.append(dict(cands=pins, tok=i, part=t["v"]))
        else:
            pos.append(dict(cands=pins, tok=i, part=t["v"], role="in"))
            pos.append(dict(cands=pins, tok=i, part=t["v"], role="out"))
    seq = [p for p in pos if "cands" in p]
    if not seq:
        return [], probs
    off_before = set()
    k = 0
    for p in pos:
        if p.get("off"):
            off_before.add(k)
        else:
            k += 1
    g.nets()

    def grp(r):
        return g.group.get(_label_node(r)[1]) if r.startswith("@") else g.group_of(r)

    def netid(r):
        return g.net_of_group.get(grp(r))

    def has_end(r):
        return r.startswith("@") or bool(g.label_nodes(r)) or g.nearest_free_end(r) is not None

    def link(a, b):
        if a.startswith("@") or b.startswith("@"):
            ga, gb = grp(a), grp(b)
            if ga is not None and ga == gb:
                na = _label_node(a)[1] if a.startswith("@") else g.tip_node(a)
                nb = _label_node(b)[1] if b.startswith("@") else g.tip_node(b)
                L = g.path_length(na, nb)
                return 1.0 + (L or 0) / 400.0
            if netid(a) is not None and netid(a) == netid(b):
                return 25.0
            return None
        return g.link_cost(a, b, net_of)
    pen = {}

    def run_dp():
        def first_cost(j, c):
            t = toks[seq[j]["tok"]]
            v = pen.get((j, c), 0.0)
            if t.get("start") and seq[j].get("role") in (None, "in") and not has_end(c):
                v += 8.0
            if t.get("end") and seq[j].get("role") in (None, "out") and not has_end(c):
                v += 8.0
            return v
        cost = [{c: first_cost(0, c) for c in seq[0]["cands"]}]
        back = [{}]
        for j in range(1, len(seq)):
            a, b = seq[j - 1], seq[j]
            cur, bk = {}, {}
            for cb in b["cands"]:
                best = None
                for ca, c0 in cost[-1].items():
                    if a["part"] == b["part"] and a["tok"] == b["tok"]:
                        if ca == cb:
                            continue
                        step = 0.0
                    elif a["part"] == b["part"]:
                        step = 0.0 if ca != cb else 0.5
                    elif j in off_before:
                        step = 2.0 + (0 if has_end(ca) else 3) + (0 if has_end(cb) else 3)
                    else:
                        lc = link(ca, cb)
                        step = BIG if lc is None else lc
                    v = c0 + step
                    if best is None or v < best[0]:
                        best = (v, ca)
                if best:
                    cur[cb] = best[0] + first_cost(j, cb)
                    bk[cb] = best[1]
            if not cur:          # a one-pin part asked to be passed through
                cur = {cb: min(cost[-1].values()) + BIG for cb in b["cands"]}
                bk = {cb: min(cost[-1], key=cost[-1].get) for cb in b["cands"]}
            cost.append(cur)
            back.append(bk)
        end = min(cost[-1], key=cost[-1].get)
        chosen = [end]
        for j in range(len(seq) - 1, 0, -1):
            chosen.append(back[j][chosen[-1]])
        chosen.reverse()
        return chosen, cost[-1][end]

    for _ in range(8):
        chosen, total = run_dp()
        # the current never comes back to a net it has left (except to close a loop)
        nets_seen, last, bad = {}, None, None
        for j, r in enumerate(chosen):
            n = netid(r)
            if n != last:
                if n in nets_seen and not (j == len(chosen) - 1 and nets_seen[n] == 0):
                    bad = (nets_seen[n], j)
                    break
                nets_seen.setdefault(n, j)
            last = n
        if bad is None:
            break
        for jj in bad:      # either the first visit or the return is the wrong pin
            pen[(jj, chosen[jj])] = pen.get((jj, chosen[jj]), 0) + 20.0
    if total >= BIG:
        probs.append("some steps are not joined by a wire, a label or the board copper (check the order)")
    out = []
    j = 0
    for p in pos:
        if p.get("off"):
            out.append("~" + p["text"] + "~")
            continue
        t = toks[p["tok"]]
        ref = chosen[j]
        j += 1
        if t.get("start") and p.get("role") in (None, "in"):
            out.append("<" + ref)
        out.append(ref)
        if t.get("end") and p.get("role") in (None, "out"):
            out.append(ref + ">")
    clean = []
    for t in out:
        if not clean or clean[-1] != t:
            clean.append(t)
    return clean, probs


def route_flow(g, path, net_of=None):
    """legs of one flow: [dict(kind, pts, text?)], problems, resolved tokens.
    kind: 'wire', 'body', 'jump' (same net drawn apart), 'off' (leaves the board), 'in', 'out'"""
    toks, probs = resolve(g, path, net_of)
    legs = []

    def node_of(pt):
        return pt[2] if pt[0] == "lab" else g.tip_node(pt[1])

    def group(pt):
        return g.group.get(node_of(pt))

    def end_of(pt, name=None):
        """where the current leaves / enters this point's wires: its label, else a free wire end"""
        if pt[0] == "lab":
            return pt[2], pt[1]
        if name:
            labs = g.label_nodes(pt[1], name)
            if labs:
                t = g.tip[pt[1]]
                k = min(labs, key=lambda c: math.hypot(g.nodes[c[1]][0] - t[0], g.nodes[c[1]][1] - t[1]))[1]
                return k, name
        return g.start_end(pt[1])

    def wire(a_node, b_node):
        p = g.path_nodes(a_node, b_node)
        if p and len(p) > 1:
            legs.append(dict(kind="wire", pts=p))

    def connect(a, b):
        if a[0] == "pin" and b[0] == "pin":
            if g.relation(a[1], b[1], net_of) == "none":
                probs.append(f"{a[1]} → {b[1]}: not the same part, not wired together, no common net label")
            r = g.route(a[1], b[1])
            if r:
                legs.extend(x for x in r if x["pts"])
            return
        if group(a) is not None and group(a) == group(b):
            wire(node_of(a), node_of(b))
            return
        name = a[1] if a[0] == "lab" else (b[1] if b[0] == "lab" else None)
        ea, _ = end_of(a, name)
        eb, _ = end_of(b, name)
        if ea is not None:
            wire(node_of(a), ea)
        ja = g.nodes[ea] if ea is not None else g.nodes[node_of(a)]
        jb = g.nodes[eb] if eb is not None else g.nodes[node_of(b)]
        legs.append(dict(kind="jump", pts=[ja, jb], text=name or ""))
        if eb is not None:
            wire(eb, node_of(b))

    prev, off = None, None
    n = len(toks)
    for i, t in enumerate(toks):
        if t.startswith("~"):
            off = t.strip("~")
            continue
        if t.startswith("@"):
            name, xy = _label_node(t)
            k = (round(xy[0], 1), round(xy[1], 1))
            k = min(g.nodes, key=lambda q: abs(q[0] - k[0]) + abs(q[1] - k[1])) if k not in g.nodes else k
            pt = ("lab", name, k)
            if prev is None:
                legs.append(dict(kind="in", pts=[g.nodes[k]], text=name))
            elif off is not None:
                ea, _ = end_of(prev)
                if ea is not None:
                    wire(node_of(prev), ea)
                legs.append(dict(kind="off", pts=[g.nodes[ea] if ea is not None else g.nodes[node_of(prev)], g.nodes[k]], text=off))
            else:
                connect(prev, pt)
            if i == n - 1:
                legs.append(dict(kind="out", pts=[g.nodes[k]], text=name))
            prev, off = pt, None
            continue
        start, end = t.startswith("<"), t.endswith(">")
        ref = t.strip("<>")
        pt = ("pin", ref)
        if start:
            e, name = g.start_end(ref)
            if e is not None:
                legs.append(dict(kind="in", pts=[g.nodes[e]], text=name or ""))
                wire(e, g.tip_node(ref))
            prev = pt
            continue
        if end:
            e, name = g.start_end(ref)
            if e is not None:
                wire(g.tip_node(ref), e)
                legs.append(dict(kind="out", pts=[g.nodes[e]], text=name or ""))
            prev = None
            continue
        if prev is not None and not (prev[0] == "pin" and prev[1] == ref):
            if off is not None:
                ea, _ = end_of(prev)
                eb, _ = end_of(pt)
                if ea is not None:
                    wire(node_of(prev), ea)
                ja = g.nodes[ea] if ea is not None else g.nodes[node_of(prev)]
                jb = g.nodes[eb] if eb is not None else g.nodes[node_of(pt)]
                legs.append(dict(kind="off", pts=[ja, jb], text=off))
                if eb is not None:
                    wire(eb, node_of(pt))
            else:
                connect(prev, pt)
        off = None
        prev = pt
    return legs, probs, toks


def _svg(page):
    svg = page.get_svg_image(text_as_path=True)
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode()


def _natural(r):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", r)]


def netlist(g):
    """the schematic netlist as plain data (for a person or an agent to read)"""
    nets = g.nets()
    out = []
    for i, (k, n) in enumerate(sorted(nets.items(), key=lambda kv: -len(kv[1]["pins"]))):
        out.append(dict(net=i, names=[t for t, _ in n["names"].most_common()], pins=sorted(n["pins"], key=_natural)))
    parts = {}
    for ref in g.tip:
        d, pin = ref.rsplit(".", 1)
        parts.setdefault(d, []).append(pin)
    return dict(pin_source=g.pin_source, nets=out,
                parts={d: sorted(v, key=_natural) for d, v in sorted(parts.items(), key=lambda kv: _natural(kv[0]))})


def build_page(doc, sch_pns, notes, out, net_of=None, bom=None, log=print, title=None):
    pages = [doc[i] for i in sch_pns]
    g = SG.SchGraph(pages)
    flows = []
    for f in notes.get("flows", []):
        legs, probs, toks = route_flow(g, f.get("path", []), net_of)
        for p in probs:
            log(f"    note: flow '{f.get('title', f.get('id'))}': {p}")
        chain = []
        for t in toks:
            if t.startswith("~"):
                item = "~" + t.strip("~")
            elif t.startswith("@"):
                item = "@" + _label_node(t)[0]
            else:
                item = t.strip("<>").rsplit(".", 1)[0]
            if not chain or chain[-1] != item:
                chain.append(item)
        flows.append(dict(id=f.get("id") or f"f{len(flows)}", k=f.get("kind", "dc"), t=f.get("title", ""), d=f.get("desc", ""),
                          chain=chain, pins=toks,
                          legs=[dict(k=l["kind"], p=[[round(x, 2), round(y, 2)] for x, y in l["pts"]], t=l.get("text", "")) for l in legs],
                          notes=f.get("notes", []), probs=probs))
    nets = g.nets()
    order = sorted(nets, key=lambda k: -len(nets[k]["pins"]))
    idx = {k: i for i, k in enumerate(order)}
    segs = []
    for a, b in g.wire_edges:
        n = idx[g.net_of_group[g.group[a]]]
        (x1, y1), (x2, y2) = g.nodes[a], g.nodes[b]
        segs.append([round(x1, 2), round(y1, 2), round(x2, 2), round(y2, 2), n])
    for ref, t in g.tip.items():
        i_ = g.inner[ref]
        segs.append([round(t[0], 2), round(t[1], 2), round(i_[0], 2), round(i_[1], 2), idx[g.net_of_group[g.group_of(ref)]]])
    netinfo = []
    for k in order:
        n = nets[k]
        netinfo.append(dict(n=[t for t, _ in n["names"].most_common()][:3], p=sorted(n["pins"], key=_natural)))
    parts = {}
    for ref in g.tip:
        d = ref.rsplit(".", 1)[0]
        parts.setdefault(d, []).extend([g.tip[ref], g.inner[ref]])
    pbox = {}
    for d, pts in parts.items():
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        pbox[d] = [round(min(xs) - 3, 1), round(min(ys) - 3, 1), round(max(xs) + 3, 1), round(max(ys) + 3, 1)]
    imgs = [dict(src=_svg(p), x=ox, y=oy, w=p.rect.width, h=p.rect.height, pn=sch_pns[i] + 1)
            for i, (p, ox, oy) in enumerate(g.pages)]
    data = dict(title=notes.get("title") or title or "", summary=notes.get("summary", ""), warning=notes.get("warning", ""),
                convention=notes.get("convention", "Arrows show conventional current (from + to −)."),
                kinds=notes.get("kinds") or DEFAULT_KINDS,
                w=g.width, h=g.height, imgs=imgs, flows=flows, parts=pbox, segs=segs, nets=netinfo,
                bom={d: " · ".join(x for x in (b.get("desc"), b.get("pn")) if x) for d, b in (bom or {}).items() if d in pbox},
                pin_source=g.pin_source)
    tpl = open(os.path.join(HERE, "flow.html"), encoding="utf-8").read()
    html = tpl.replace("/*__DATA__*/", "const D=" + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";")
    html = html.replace("__TITLE__", (data["title"] or "Current flow on the schematic").replace("<", ""))
    open(out, "w", encoding="utf-8").write(html)
    return out, flows, g
