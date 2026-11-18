"""Current and signal paths: one HTML page that lights up a net (all copper joined to a pin) on
both board layers and on the schematic, plus the step-by-step paths written in a notes file.

    python paths.py "board.pdf"                      # step 1: netlist -> "board - nets.json"
    python paths.py "board.pdf" --notes notes.json   # step 2: page with the explained paths

Needs a CAD print whose copper-layer pages carry the hidden pad texts "PA<designator><pin>"
(Altium). The schematic page is used when it carries the hidden pin texts "PI<designator><pin>".
"""
import base64, collections, json, os, re
import numpy as np
import cv2
import pymupdf

from . import nets as N

HERE = os.path.dirname(os.path.abspath(__file__))


def norm_pin(pin):
    return pin.lstrip("0") or "0"


def ref(des, pin):
    return f"{des}.{norm_pin(pin)}"


# ---------------------------------------------------------------- page choice
def copper_pages(doc, log=print):
    """(top, bottom) copper-layer page numbers (0-based): by the page titles "Top Layer" /
    "Bottom Layer", else the two pad pages with the most coloured copper in different colours"""
    pad_pages = [i for i, p in enumerate(doc) if len(N.hidden_tokens(p, "PA")) >= 4]
    top = bot = None
    for i in pad_pages:
        t = doc[i].get_text().lower()
        if re.search(r"\btop\s*layer\b", t) and top is None:
            top = i
        if re.search(r"\bbottom\s*layer\b", t) and bot is None:
            bot = i
    if top is not None and bot is not None:
        return top, bot
    scored = []
    for i in pad_pages:
        p = doc[i]
        pads = N.pads_of(p)
        xs, ys = [q["x"] for q in pads], [q["y"] for q in pads]
        clip = pymupdf.Rect(min(xs) - 10, min(ys) - 10, max(xs) + 10, max(ys) + 10)
        img = N._render(p, clip, 1.5)
        cm = N._colour_masks(img)
        if not cm:
            continue
        c, m = max(cm.items(), key=lambda kv: kv[1].sum())
        scored.append((m.mean(), i, c))
    scored.sort(reverse=True)
    for a in range(len(scored)):
        for b in range(a + 1, len(scored)):
            if scored[a][2] != scored[b][2]:
                i, j = scored[a][1], scored[b][1]
                return (min(i, j), max(i, j))
    raise SystemExit("STOP: two copper-layer pages with pad names were not found in this PDF.")


def schematic_page(doc):
    best = max(range(len(doc)), key=lambda i: len(N.hidden_tokens(doc[i], "PI")))
    return best if len(N.hidden_tokens(doc[best], "PI")) >= 10 else None


# ---------------------------------------------------------------- schematic overlay
def _onseg(px, py, s, tol):
    x1, y1, x2, y2 = s
    if min(x1, x2) - tol <= px <= max(x1, x2) + tol and min(y1, y2) - tol <= py <= max(y1, y2) + tol:
        dx, dy = x2 - x1, y2 - y1
        L = (dx * dx + dy * dy) ** .5
        if L < 1e-6:
            return abs(px - x1) < tol and abs(py - y1) < tol
        return abs((px - x1) * dy - (py - y1) * dx) / L < tol
    return False


def schematic_overlay(page, net_of):
    """pins and wires of each net on the schematic page.
    net_of: {"R309.1": net index}. Returns ({net: [[x,y],...]}, {net: [[x1,y1,x2,y2],...]}, names)"""
    des = N.designators(page)
    pins = []
    for tok, x, y in N.hidden_tokens(page, "PI"):
        d, pin = N.split_pad("PA" + tok[2:], des)
        pins.append((ref(d, pin), x, y))
    # the wire colour: the stroke colour of thin lines that touch most pin stubs is found below;
    # take the commonest line colour that is not black and not the part-body colour
    lines = collections.defaultdict(list)
    for dr in page.get_drawings():
        if dr.get("fill") or not dr.get("color"):
            continue
        col = tuple(round(v, 2) for v in dr["color"])
        for it in dr["items"]:
            if it[0] == "l":
                lines[col].append((it[1].x, it[1].y, it[2].x, it[2].y))
    allsegs = [s for v in lines.values() for s in v]
    stub = {}
    for r_, x, y in pins:
        best = None
        for t in allsegs:
            L = abs(t[2] - t[0]) + abs(t[3] - t[1])
            if 1 < L < 40 and (t[0] == t[2] or t[1] == t[3]) and _onseg(x, y, t, 0.4) and (best is None or L < best[0]):
                best = (L, t)
        if best:
            stub[r_] = best[1]
    # wires: the colour whose segment ends touch the most pin stubs
    def touches(col):
        """pin stubs that a segment of this colour ends on (the stub itself not counted)"""
        n = 0
        segs_c = lines[col][:4000]
        for t in list(stub.values())[:400]:
            for s_ in segs_c:
                if s_ == t:
                    continue
                if _onseg(s_[0], s_[1], t, 0.4) or _onseg(s_[2], s_[3], t, 0.4):
                    n += 1
                    break
        return n
    cols = sorted(lines, key=lambda c: -len(lines[c]))[:6]
    wcol = max(cols, key=touches) if cols else None
    segs = list(lines.get(wcol, []))
    nw = len(segs)
    stub_i = {}
    for r_, t in stub.items():
        stub_i[r_] = len(segs)
        segs.append(t)
    par = list(range(len(segs)))

    def f(x):
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x
    S = np.array(segs) if segs else np.zeros((0, 4))
    for i, s in enumerate(segs):
        for px, py in ((s[0], s[1]), (s[2], s[3])):
            near = np.where((np.minimum(S[:, 0], S[:, 2]) - .4 <= px) & (np.maximum(S[:, 0], S[:, 2]) + .4 >= px) &
                            (np.minimum(S[:, 1], S[:, 3]) - .4 <= py) & (np.maximum(S[:, 1], S[:, 3]) + .4 >= py))[0]
            for j in near:
                if j != i and _onseg(px, py, segs[j], 0.35):
                    par[f(i)] = f(j)
    grp_nets = collections.defaultdict(set)
    for r_, i in stub_i.items():
        if r_ in net_of:
            grp_nets[f(i)].add(net_of[r_])
    pin_xy = collections.defaultdict(list)
    for r_, x, y in pins:
        if r_ in net_of:
            pin_xy[net_of[r_]].append([round(x, 1), round(y, 1)])
    wires = collections.defaultdict(list)
    for i in range(nw):
        ns = grp_nets.get(f(i), set())
        if len(ns) == 1:
            wires[next(iter(ns))].append([round(v, 1) for v in segs[i]])
    for r_, i in stub_i.items():
        if r_ in net_of:
            wires[net_of[r_]].append([round(v, 1) for v in segs[i]])
    # net names written on the schematic: net labels (hidden "NL") next to a wire of one net
    names = collections.defaultdict(collections.Counter)
    marks = [(tok[2:], x, y) for tok, x, y in N.hidden_tokens(page, "NL")]
    for t, x, y in marks:
        best = None
        for i in range(nw):
            s = segs[i]
            dx, dy = s[2] - s[0], s[3] - s[1]
            L2 = dx * dx + dy * dy
            u = 0 if L2 == 0 else max(0, min(1, ((x - s[0]) * dx + (y - s[1]) * dy) / L2))
            d = ((x - s[0] - u * dx) ** 2 + (y - s[1] - u * dy) ** 2) ** .5
            if best is None or d < best[0]:
                best = (d, i)
        if best and best[0] < 6:
            ns = grp_nets.get(f(best[1]), set())
            if len(ns) == 1:
                names[next(iter(ns))][t] += 1
    # Altium writes some symbols of hidden texts as "0" (Hall_Ctrl -> Hall0Ctrl)
    names = {k: re.sub(r"(?<=[A-Za-z])0(?=[A-Za-z])", "_", v.most_common(1)[0][0]) for k, v in names.items()}
    return dict(pin_xy), dict(wires), names


# ---------------------------------------------------------------- images
def _jpeg(img, q=82):
    ok, enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
    return "data:image/jpeg;base64," + base64.b64encode(enc.tobytes()).decode()


def _png(img):
    ok, enc = cv2.imencode(".png", img)
    return "data:image/png;base64," + base64.b64encode(enc.tobytes()).decode()


def _idmap(ids):
    """net index per pixel (-1 none) -> RGB PNG: value+1 in R (low byte) and G (high byte)"""
    v = (ids + 1).astype(np.int32)
    img = np.zeros(ids.shape + (3,), np.uint8)
    img[:, :, 2] = v & 255          # cv2 writes BGR: channel 2 = R
    img[:, :, 1] = (v >> 8) & 255
    return _png(img)


def _faded(img):
    g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    g = (255 - (255 - g.astype(np.float32)) * 0.45).astype(np.uint8)
    return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)


# ---------------------------------------------------------------- main
def netlist(doc, sides=None, log=print):
    top, bot = copper_pages(doc, log)
    log(f"    copper layers: top = PDF page {top+1}, bottom = PDF page {bot+1}")
    res = N.extract(doc, top, bot, sides or {}, log=log)
    res["top_pn"], res["bot_pn"] = top, bot
    return res


def nets_json(res, sch_names=None, bom=None):
    """the netlist as plain data, for a person or an agent to read"""
    out = []
    for k, n in enumerate(res["nets"]):
        out.append(dict(net=k, name=(sch_names or {}).get(k, ""),
                        pins=sorted({ref(p["des"], p["pin"]) for p in n})))
    parts = {}
    for p in res["pads"]:
        parts.setdefault(p["des"], dict(side=p["layer"], pins={}))
    for k, n in enumerate(res["nets"]):
        for p in n:
            parts[p["des"]]["pins"][norm_pin(p["pin"])] = k
    for d, v in parts.items():
        if bom and d in bom:
            b = bom[d]
            v["bom"] = " ".join(x for x in (b.get("desc"), b.get("pn")) if x)
    return dict(nets=out, parts=parts, not_connected=[ref(p["des"], p["pin"]) for p in res["unconnected"]])


def build_page(doc, res, notes, out, bom=None, title=None, log=print):
    maps = res["maps"]
    pt, pb = doc[res["top_pn"]], doc[res["bot_pn"]]
    cT, cB = pymupdf.Rect(maps["clipT"]), pymupdf.Rect(maps["clipB"])
    zd = min(4.0, 1700 / max(cT.width, cT.height))
    z = maps["z"]
    imgT = N._render(pt, cT, zd)
    imgB = N._render(pb, cB, zd)
    H, W = imgT.shape[:2]
    Hb, Wb = imgB.shape[:2]
    top = maps["top"].copy()
    if maps["shared"] is not None:
        sh = maps["shared"]
        top = np.where(top >= 0, top, sh)
    bot = maps["bottom"].copy()
    if maps["shared"] is not None:
        # the shared layer sits on the bottom page too, at the bottom page's own place
        M = np.array(maps["M"])
        S1 = np.array([[1 / z, 0, cT.x0], [0, 1 / z, cT.y0], [0, 0, 1]])
        S2 = np.array([[z, 0, -cB.x0 * z], [0, z, -cB.y0 * z], [0, 0, 1]])
        PX = (S2 @ np.vstack([M, [0, 0, 1]]) @ S1)[:2]
        shb = cv2.warpAffine((maps["shared"] + 1).astype(np.float32), PX, (bot.shape[1], bot.shape[0]),
                             flags=cv2.INTER_NEAREST).astype(np.int32) - 1
        bot = np.where(bot >= 0, bot, shb)
    top_s = cv2.resize(top.astype(np.int32).astype(np.float32), (W, H), interpolation=cv2.INTER_NEAREST).astype(np.int32)
    bot_s = cv2.resize(bot.astype(np.int32).astype(np.float32), (Wb, Hb), interpolation=cv2.INTER_NEAREST).astype(np.int32)

    net_of = {}
    for k, n in enumerate(res["nets"]):
        for p in n:
            net_of[ref(p["des"], p["pin"])] = k
    sp = schematic_page(doc)
    sch = None
    sch_names = {}
    if sp is not None:
        log(f"    schematic: PDF page {sp+1}")
        page = doc[sp]
        zs = min(3.0, 2600 / max(page.rect.width, page.rect.height))
        pin_xy, wires, sch_names = schematic_overlay(page, net_of)
        simg = N._render(page, page.rect, zs)
        sch = dict(img=_jpeg(cv2.cvtColor(simg, cv2.COLOR_RGB2BGR), 80), w=simg.shape[1], h=simg.shape[0], z=zs,
                   pins={k: v for k, v in pin_xy.items()}, wires={k: v for k, v in wires.items()})
    names = {}
    for k in range(len(res["nets"])):
        names[k] = sch_names.get(k, "")
    for r_, nm in (notes.get("nets") or {}).items():
        if r_ in net_of:
            names[net_of[r_]] = nm
    nets = []
    for k, n in enumerate(res["nets"]):
        pins = sorted({ref(p["des"], p["pin"]) for p in n}, key=lambda s: [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)])
        nets.append(dict(n=names.get(k) or "", p=pins))
    parts = {}
    for p in res["pads"]:
        d = parts.setdefault(p["des"], dict(s=p["layer"], pads=[]))
        if p["layer"] == "both":
            d["s"] = "both" if d["s"] in ("both",) else d["s"]
        d["pads"].append([norm_pin(p["pin"]), round((p["x"] - cT.x0) * zd, 1), round((p["y"] - cT.y0) * zd, 1),
                          round((p["bx"] - cB.x0) * zd, 1), round((p["by"] - cB.y0) * zd, 1),
                          net_of.get(ref(p["des"], p["pin"]), -1), p["layer"]])
    for d, v in parts.items():
        ls = {q[6] for q in v["pads"]}
        v["s"] = "top" if ls <= {"top", "both"} and "top" in ls else "bottom" if "bottom" in ls else "both"
        if bom and d in bom:
            b = bom[d]
            v["b"] = " · ".join(x for x in (b.get("desc"), b.get("pn")) if x)
    paths = []
    for pth in notes.get("paths", []):
        steps = []
        for s in pth.get("steps", []):
            st = dict(t=s.get("text", ""))
            if s.get("ref") and s["ref"] in net_of:
                st["net"] = net_of[s["ref"]]
            elif s.get("ref"):
                log(f"    note: path '{pth.get('title')}' names pin {s['ref']}, which is not on the board")
            if s.get("comp"):
                st["comp"] = s["comp"]
            steps.append(st)
        paths.append(dict(id=pth.get("id"), k=pth.get("kind", "power"), t=pth.get("title", ""), d=pth.get("desc", ""), s=steps))
    data = dict(title=notes.get("title") or title or "", summary=notes.get("summary", ""), warning=notes.get("warning", ""),
                top=dict(img=_jpeg(_faded(imgT)), ids=_idmap(top_s), w=W, h=H),
                bottom=dict(img=_jpeg(_faded(imgB)), ids=_idmap(bot_s), w=Wb, h=Hb),
                sch=sch, nets=nets, parts=parts, paths=paths)
    tpl = open(os.path.join(HERE, "paths.html"), encoding="utf-8").read()
    html = tpl.replace("/*__DATA__*/", "const D=" + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";")
    html = html.replace("__TITLE__", (data["title"] or "Đường điện").replace("<", ""))
    open(out, "w", encoding="utf-8").write(html)
    return out, sch_names
