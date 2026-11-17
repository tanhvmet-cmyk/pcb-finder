#!/usr/bin/env python3
"""
PCB component finder: turn a PCBA approval-sheet PDF into one searchable HTML page.

    python build.py  "C:\\path\\to\\Approval sheet.pdf"

Output: "<pdf name> - component finder.html" next to the PDF (it opens in the browser when done).
If a file "<pdf name>.fixes.json" sits next to the PDF, the corrected positions in it are used.
"""
import sys, os, re, io, json, base64, argparse, webbrowser
import numpy as np
import cv2
import pymupdf
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from pcbfinder import core
from pcbfinder.core import log

ZD = 10      # pixels per PDF point in the board drawing image
ZS = 2.4     # pixels per PDF point in the schematic images


def b64(data, mime):
    return f"data:{mime};base64," + base64.b64encode(data).decode()


def render(page, clip, z, colors):
    pm = page.get_pixmap(matrix=pymupdf.Matrix(z, z), clip=pymupdf.Rect(*clip) if clip else None, alpha=False)
    im = Image.frombytes("RGB", (pm.width, pm.height), pm.samples).quantize(colors, dither=Image.Dither.NONE)
    bio = io.BytesIO()
    im.save(bio, "PNG", optimize=True)
    return dict(src=b64(bio.getvalue(), "image/png"), w=pm.width, h=pm.height)


def natural(name):
    m = re.match(r"([A-Za-z]+)(\d+)", name)
    return (m.group(1).upper(), int(m.group(2))) if m else (name, 0)


def build(pdf, out=None, open_after=True):
    stem = os.path.splitext(pdf)[0]
    out = out or stem + " - component finder.html"
    fixes_path = stem + ".fixes.json"
    doc = pymupdf.open(pdf)
    log(f"Reading {os.path.basename(pdf)} ({len(doc)} pages)")

    log("1/6 BOM table ...")
    bom, bom_pages = core.parse_bom(doc)
    log(f"    {len(bom)} designators in the BOM (pages {bom_pages})" if bom else "    no BOM table found - continuing without values")
    board_mm = None
    for pn in bom_pages:
        m = re.search(r"(\d{2,3}(?:\.\d+)?)\s*[*xX×]\s*(\d{2,3}(?:\.\d+)?)\s*[*xX×]\s*\d(?:\.\d+)?\s*mm", doc[pn - 1].get_text())
        if m:
            board_mm = sorted([float(m.group(1)), float(m.group(2))], reverse=True)
            break

    log("2/6 looking for the silkscreen drawings (pages without text) ...")
    silk = core.find_silk_pages(doc, bom)
    if not silk:
        sys.exit("STOP: no page with drawn component labels was found. This tool needs the PCB layout "
                 "pages to be vector drawings (not scanned pictures).")
    sides = ["top", "bottom"][:len(silk)]
    fixes = {}
    if os.path.exists(fixes_path):
        fixes = json.load(open(fixes_path, encoding="utf-8"))
        log(f"    using corrections from {os.path.basename(fixes_path)}")

    imgs, comps, meta = {}, [], {"sides": {}, "title": os.path.basename(stem), "fixfile": os.path.basename(fixes_path)}
    photos = core.find_photos(doc)
    stats = {}
    for si, (side, s) in enumerate(zip(sides, silk)):
        page = doc[s["pn"]]
        clip, board, has_outline = core.board_clip(page, s["labels"])
        labels = [l for l in s["labels"] if clip[0] <= l["bb"][0] and l["bb"][2] <= clip[2]
                  and clip[1] <= l["bb"][1] and l["bb"][3] <= clip[3]]
        seen_n, uniq = set(), []
        for l in labels:                       # one label per name
            if l["name"] not in seen_n:
                seen_n.add(l["name"])
                uniq.append(l)
        labels = uniq
        log(f"3/6 {side} side = PDF page {s['pn']+1}: {len(labels)} labels")
        log("4/6 matching labels to component outlines ...")
        bodies, cands = core.find_bodies(page, clip, labels, s["allbb"], bom)
        fx = fixes.get(side, {})
        imgs["draw_" + side] = render(page, clip, ZD, 8)
        Hm = None
        if si < len(photos):
            a, quad = photos[si]
            Hm = core.photo_homography(board, quad, a.shape)
            if Hm is not None:
                ok, enc = cv2.imencode(".jpg", a, [cv2.IMWRITE_JPEG_QUALITY, 90])
                imgs["photo_" + side] = dict(src=b64(enc.tobytes(), "image/jpeg"), w=a.shape[1], h=a.shape[0])
            else:
                log(f"    photo for the {side} side does not match the board shape - left out")
        bw, bh = board[2] - board[0], board[3] - board[1]
        meta["sides"][side] = dict(page=s["pn"] + 1, clip=[round(v, 2) for v in clip], z=ZD,
                                   board=[round(v, 2) for v in board],
                                   cands=[[round(v, 1) for v in c] for c in cands])
        n = dict(c0=0, c1=0, c2=0, c3=0)
        for l in labels:
            name = l["name"]
            b, conf = bodies[name]
            if name in fx:
                b, conf = fx[name], 0
            n["c%d" % conf] += 1
            px = lambda bb: [round((bb[0] - clip[0]) * ZD, 1), round((bb[1] - clip[1]) * ZD, 1),
                             round((bb[2] - clip[0]) * ZD, 1), round((bb[3] - clip[1]) * ZD, 1)]
            cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            c = dict(n=name, s=side, r=px(b), l=px(l["bb"]), c=conf,
                     f=[round((cx - board[0]) / bw, 4), round((cy - board[1]) / bh, 4)])
            if Hm is not None:
                q = cv2.perspectiveTransform(np.float32([[[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]]]), Hm)[0]
                c["q"] = [[round(float(x), 1), round(float(y), 1)] for x, y in q]
            if name in bom:
                c["b"] = bom[name]
            comps.append(c)
        stats[side] = n
        if Hm is not None:
            meta["sides"][side]["H"] = [[float(v) for v in row] for row in Hm]
    if board_mm:
        meta["mm"] = board_mm

    log("5/6 schematic pages ...")
    names = {c["n"] for c in comps}
    hits, sch_pages = core.find_schematic(doc, names, set(bom_pages))
    for pn in sch_pages:
        imgs[f"sch_{pn+1}"] = render(doc[pn], None, ZS, 16)
    meta["sch"] = [p + 1 for p in sch_pages]
    for c in comps:
        if c["n"] in hits:
            c["sch"] = [[pn + 1] + [round(v * ZS, 1) for v in b] for pn, b in hits[c["n"]]]
    # the same name on both sides: keep the side where it has an outline
    seen = {}
    for c in sorted(comps, key=lambda c: c["c"]):
        seen.setdefault(c["n"], c)
    comps = sorted(seen.values(), key=lambda c: natural(c["n"]))

    log("6/6 writing the page ...")
    tpl = open(os.path.join(HERE, "pcbfinder", "template.html"), encoding="utf-8").read()
    data = ("const COMPS=" + json.dumps(comps, separators=(",", ":"), ensure_ascii=False) +
            ";\nconst IMGS=" + json.dumps(imgs, separators=(",", ":")) +
            ";\nconst META=" + json.dumps(meta, separators=(",", ":"), ensure_ascii=False) + ";")
    html = tpl.replace("/*__DATA__*/", data).replace("__TITLE__", meta["title"].replace("<", ""))
    open(out, "w", encoding="utf-8").write(html)

    log("")
    log("DONE  ->  " + out)
    in_bom = sum(1 for c in comps if "b" in c)
    missing = sorted(set(bom) - names, key=natural)
    log(f"  {len(comps)} labels found on the board; {in_bom} of {len(bom)} BOM designators located")
    if missing:
        log("  BOM designators with NO label found on the board: " + ", ".join(missing))
    for side, n in stats.items():
        log(f"  {side} side: {n['c0']} checked by you, {n['c1']} outline sure, {n['c2']} outline to check, {n['c3']} label only")
    log("  Open the page and use 'Check positions' to confirm or correct the ones to check.")
    if open_after:
        try:
            webbrowser.open("file://" + os.path.abspath(out))
        except Exception:
            pass
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build a searchable component finder page from a PCBA PDF")
    ap.add_argument("pdf", help="the PCBA approval sheet PDF")
    ap.add_argument("-o", "--out", help="output HTML file (default: next to the PDF)")
    ap.add_argument("--no-open", action="store_true", help="do not open the browser when finished")
    a = ap.parse_args()
    if not os.path.exists(a.pdf):
        sys.exit("File not found: " + a.pdf)
    build(a.pdf, a.out, not a.no_open)
