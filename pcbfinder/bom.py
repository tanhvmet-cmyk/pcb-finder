"""Read the BOM table of a PCBA approval sheet."""
import re


def _join_des(lines):
    """designator cell text, one string per printed line -> list of names"""
    txt = ""
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if txt and txt[-1].isdigit() and ln[0].isalpha():
            txt += ","
        txt += ln
    txt = re.sub(r"(?i)designators?|references?|ref\.?des", ",", txt)   # header words stuck to names
    return [x.strip() for x in re.split(r"[,/;\s]+", txt) if re.fullmatch(r"[A-Za-z]+\d+", x.strip())]


def parse_bom_tables(doc):
    """BOM from the table's ruling lines (works whatever the column widths)."""
    COLS = {"item": ("item",), "comp": ("component",), "pkg": ("type", "device package", "package", "footprint"),
            "desc": ("description", "value", "spec"), "mfr": ("manufacturer",), "pn": ("manufacturer p/n", "p/n", "part number", "mpn"),
            "qty": ("qty", "quantity"), "des": ("designator", "reference", "location", "ref")}
    info, pages, colmap, ncols, colx = {}, [], None, None, None
    last_item = None
    for pn in range(len(doc)):
        pg = doc[pn]
        words = pg.get_text("words")
        low = {w[4].lower() for w in words}
        follows = bool(pages) and pn == pages[-1]          # a page right after a BOM page may continue it
        if not follows and not ("designator" in low or "reference" in low or "location" in low):
            continue                                         # table search is slow: only on BOM pages
        try:
            tabs = pg.find_tables().tables
        except Exception:
            continue
        if not tabs and colx and pages and pn == pages[-1]:
            # a BOM page whose table lines were not found: cut its words into the previous page's columns
            def col_of(w):
                cx = (w[0] + w[2]) / 2
                for k, (x0, x1) in colx.items():
                    if x0 - 1 <= cx <= x1 + 1:
                        return k
                return None
            ic = colmap.get("item")
            items = sorted((w[1], int(w[4])) for w in words if col_of(w) == ic and re.fullmatch(r"\d+", w[4]))
            if items and (last_item is None or items[0][1] in (last_item["item"] + 1, last_item["item"])):
                bounds = [y - 9 for y, _ in items] + [1e9]
                segs = [(-1e9, bounds[0], last_item)] + [(bounds[k], bounds[k + 1], None) for k in range(len(items))]
                for k, (y0, y1, cont) in enumerate(segs):
                    blk = [w for w in words if y0 <= w[1] < y1]
                    alt = [w[1] for w in blk if col_of(w) == colmap.get("comp") and w[4].upper().startswith("ALT")]
                    cut = min(alt) - 1 if alt else 1e9
                    allblk = blk
                    blk = [w for w in blk if w[1] < cut or col_of(w) == colmap.get("des")]
                    cells = {}
                    for w in blk:
                        c = col_of(w)
                        if c is not None:
                            cells.setdefault(c, {}).setdefault(round(w[1] / 2), []).append(w)
                    txt = lambda c: [" ".join(x[4] for x in sorted(v, key=lambda w: w[0])) for _, v in sorted(cells.get(c, {}).items())]
                    get = lambda key: " ".join(txt(colmap[key])) if key in colmap else ""
                    if cont is None:
                        cur = dict(item=items[k - 1][1], comp=get("comp"), pkg=get("pkg"), desc=get("desc"),
                                   mfr=get("mfr"), pn=get("pn"), page=pn + 1)
                        last_item = cur
                    else:
                        cur = cont
                    if cur is None or "des" not in colmap:
                        continue
                    for d in _join_des(txt(colmap["des"])):
                        info.setdefault(d, {kk: cur[kk] for kk in ("item", "comp", "pkg", "desc", "mfr", "pn", "page")})
                pages.append(pn + 1)
            continue
        for t in tabs:
            rows = t.rows
            texts = []
            for r in rows:
                cells = []
                for c in r.cells:
                    if c is None:
                        cells.append([]); continue
                    ws = [w for w in words if c[0] - 0.5 <= (w[0] + w[2]) / 2 <= c[2] + 0.5 and c[1] - 0.5 <= (w[1] + w[3]) / 2 <= c[3] + 0.5]
                    lines = {}
                    for w in ws:
                        lines.setdefault(round(w[1] / 2), []).append(w)
                    cells.append([" ".join(x[4] for x in sorted(v, key=lambda w: w[0])) for _, v in sorted(lines.items())])
                texts.append(cells)
            start = 0
            for ri, cells in enumerate(texts):
                low = [" ".join(c).lower().strip() for c in cells]
                if "item" in low and any(x in low for x in ("designator", "reference", "location")):
                    cm = {}
                    for k, keys in COLS.items():
                        for ci, h in enumerate(low):
                            if ci in cm.values():
                                continue
                            if any(h == key or (key in h and k not in ("item",)) for key in keys):
                                if k == "mfr" and "p/n" in h:
                                    continue
                                cm[k] = ci; break
                    colmap, ncols, start = cm, len(cells), ri + 1
                    hdr = t.rows[ri].cells
                    colx = {ci: (c[0], c[2]) for ci, c in enumerate(hdr) if c is not None}
                    break
            if colmap is None or len(texts[0]) != ncols:
                continue
            used = False
            cur = None
            for cells in texts[start:]:
                get = lambda k: " ".join(cells[colmap[k]]) if k in colmap and colmap[k] < len(cells) else ""
                it = get("item").strip()
                if re.fullmatch(r"\d+", it):
                    cur = dict(item=int(it), comp=get("comp"), pkg=get("pkg"), desc=get("desc"),
                               mfr=get("mfr"), pn=get("pn"), page=pn + 1)
                    last_item = cur
                elif not it:
                    comp = get("comp").strip()
                    if comp and not comp.upper().startswith("ALT") and (get("desc").strip() or get("pn").strip()):
                        # a part of its own whose item number sits in a cell above (merged cell / page break)
                        cur = dict(item=(last_item["item"] if last_item else 0), comp=comp, pkg=get("pkg"), desc=get("desc"),
                                   mfr=get("mfr"), pn=get("pn"), page=pn + 1)
                    elif cur is None and last_item is not None:
                        cur = last_item          # row continued from the previous page
                if cur is None:
                    continue
                des = _join_des(cells[colmap["des"]]) if "des" in colmap and colmap["des"] < len(cells) else []
                for d in des:
                    if d not in info:
                        info[d] = {k: cur[k] for k in ("item", "comp", "pkg", "desc", "mfr", "pn", "page")}
                        used = True
            if used:
                pages.append(pn + 1)
            if colmap is not None and texts and len(texts[0]) == ncols and colx:
                # keep column edges from this table's own last row (headers can span several columns)
                lr = t.rows[-1].cells
                colx = {ci: (c[0], c[2]) for ci, c in enumerate(lr) if c is not None} or colx
    return info, sorted(set(pages))


def parse_bom(doc):
    info, pages = parse_bom_tables(doc)
    if len(info) >= 5:
        return info, pages
    return parse_bom_words(doc)


def parse_bom_words(doc):
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
        for x in _join_des([b["des"]]):
            info.setdefault(x, {k: b[k] for k in ("item", "comp", "pkg", "desc", "mfr", "pn", "page")})
    return info, sorted({b["page"] for b in items})


# ----------------------------------------------------------------- labels
