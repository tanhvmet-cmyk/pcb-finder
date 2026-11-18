#!/usr/bin/env python3
"""
PCB component finder: turn a PCBA layout PDF into one searchable HTML page.

    python build.py "C:\\path\\to\\board.pdf"

Output: "<pdf name> - component finder.html" next to the PDF (opens in the browser when done).
"""
import sys, os, re, io, json, base64, argparse, webbrowser, time, shutil, tempfile, pathlib
import numpy as np
import cv2
import pymupdf
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from pcbfinder import bom as bomlib, labels as lab, board as brd

ZD = 10      # pixels per PDF point for the board drawing (scaled down for big pages)
ZS = 2.4     # pixels per PDF point for schematic pages


def log(*a):
    print(*a, flush=True)


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
    return (m.group(1).upper(), int(m.group(2)), name) if m else (name.upper(), 0, name)


def board_mm(doc, pages):
    for pn in pages:
        m = re.search(r"(\d{1,3}(?:\.\d+)?)\s*[*xX×]\s*(\d{1,3}(?:\.\d+)?)\s*[*xX×]\s*\d(?:\.\d+)?\s*mm", doc[pn - 1].get_text())
        if m:
            return sorted([float(m.group(1)), float(m.group(2))], reverse=True)
    return None


def preview_dir():
    """a private folder for pages not saved yet; pages older than a day are cleared on each run"""
    d = os.path.join(tempfile.gettempdir(), "pcb-finder-preview")
    os.makedirs(d, exist_ok=True)
    for f in os.listdir(d):
        p = os.path.join(d, f)
        try:
            if time.time() - os.path.getmtime(p) > 86400:
                os.remove(p)
        except OSError:
            pass
    return d


def open_in_browser(path):
    try:
        if os.name == "nt":
            os.startfile(os.path.abspath(path))          # the browser Windows uses for .html files
        else:
            webbrowser.open(pathlib.Path(os.path.abspath(path)).as_uri())
    except Exception as e:
        log(f"  (could not open the browser: {e}) - open this file yourself: {path}")


def ask_save(tmp, default_path):
    """after the preview: the person decides whether, and where, the page is kept"""
    log("")
    log("The page is open in your browser. Keep it?")
    log("  [Y] save next to the PDF:  " + default_path)
    log("  [S] choose where to save")
    log("  [N] do not save")
    try:
        ans = input("Your choice (Y/S/N) [Y]: ").strip().lower()
    except EOFError:
        ans = "y"                                        # no one to ask (script use): keep it
    if ans.startswith("n"):
        log("Not saved. (The preview copy is removed automatically after a day.)")
        return None
    dest = default_path
    if ans.startswith("s"):
        try:
            import tkinter
            from tkinter import filedialog
            root = tkinter.Tk(); root.withdraw(); root.attributes("-topmost", True)
            dest = filedialog.asksaveasfilename(parent=root, title="Save the component finder page",
                                                initialdir=os.path.dirname(os.path.abspath(default_path)),
                                                initialfile=os.path.basename(default_path),
                                                defaultextension=".html", filetypes=[("Web page", "*.html")])
            root.destroy()
        except Exception as e:
            log(f"  (save window not available: {e})")
            dest = input("Type the full path to save to (empty = next to the PDF): ").strip().strip('"') or default_path
        if not dest:
            log("Not saved.")
            return None
    shutil.copyfile(tmp, dest)
    log("Saved:  " + dest)
    return dest


def build(pdf, out=None, open_after=True, ask=True):
    """ask=True: preview first, then the person decides whether to save. ask=False: save straight to `out`."""
    t0 = time.time()
    stem = os.path.splitext(pdf)[0]
    final = os.path.abspath(out or stem + " - component finder.html")
    out = os.path.join(preview_dir(), os.path.basename(final)) if ask else final
    doc = pymupdf.open(pdf)
    log(f"Reading {os.path.basename(pdf)} ({len(doc)} pages)")

    from pcbfinder import ocr
    if ocr.available():
        log("    OCR: " + ocr.describe())
    else:
        log("    NOTE: no OCR engine found (run: python -m pip install --user tesserocr). Labels drawn as lines")
        log("          cannot be read; only PDFs that carry the names as text (e.g. Altium prints) will work.")
    log("1/5 BOM table ...")
    bom, bom_pages = bomlib.parse_bom(doc)
    log(f"    {len(bom)} designators in the BOM (pages {bom_pages})" if bom else "    no BOM table found - continuing without part values")

    log("2/5 reading the labels on the layout pages ...")
    pages = lab.choose_pages(doc, bom, log=log, skip=set(bom_pages))
    if not pages:
        from pcbfinder import ocr
        sys.exit("STOP: no layout page with component labels was found in this PDF." +
                 ("" if ocr.available() else "\nThe OCR engine is missing: run  python -m pip install --user tesserocr  and try again."))
    sides = ["top", "bottom"][:len(pages)]
    if len(pages) == 1 and pages[0].get("side_hint") == "bottom":
        sides = ["bottom"]

    log("3/5 board outline, photos ...")
    photos = brd.find_photos(doc)
    imgs, comps, meta = {}, [], {"sides": {}, "title": os.path.basename(stem)}
    for si, (side, p) in enumerate(zip(sides, pages)):
        page = doc[p["pn"]]
        clip, board, _ = brd.board_clip(page, p["labels"])
        labels = [l for l in p["labels"] if clip[0] <= l["bb"][0] and l["bb"][2] <= clip[2]
                  and clip[1] <= l["bb"][1] and l["bb"][3] <= clip[3]]
        z = min(ZD, 4200 / max(clip[2] - clip[0], clip[3] - clip[1]))
        imgs["draw_" + side] = render(page, clip, z, 8)
        # the part next to its label, only where that is clear (shown as a hint, never instead of the label)
        outline = {}
        try:
            uniq = [l for l in labels if not l.get("dup")]
            bodies, _ = brd.find_bodies(page, clip, uniq, [s["bb"] for s in p["strs"]], bom)
            outline = {n: b for n, (b, conf) in bodies.items() if conf == 1}
        except Exception as e:
            log("    (outline hints skipped: %s)" % e)
        Hm = None
        if si < len(photos):
            a, quad = photos[si]
            Hm = brd.photo_homography(board, quad, a.shape)
            if Hm is not None:
                ok, enc = cv2.imencode(".jpg", a, [cv2.IMWRITE_JPEG_QUALITY, 90])
                imgs["photo_" + side] = dict(src=b64(enc.tobytes(), "image/jpeg"), w=a.shape[1], h=a.shape[0])
        bw, bh = board[2] - board[0], board[3] - board[1]
        meta["sides"][side] = dict(page=p["pn"] + 1, mirror=bool(p.get("mirror", False)), tall=bool(bh > bw))
        px = lambda bb: [round((bb[0] - clip[0]) * z, 1), round((bb[1] - clip[1]) * z, 1),
                         round((bb[2] - clip[0]) * z, 1), round((bb[3] - clip[1]) * z, 1)]
        names_here = {l["name"].upper() for l in labels}
        marks = [m for m in p.get("marks", []) if clip[0] <= m["bb"][0] and m["bb"][2] <= clip[2]
                 and clip[1] <= m["bb"][1] and m["bb"][3] <= clip[3] and m["name"].upper() not in names_here]
        for l in labels + marks:
            b = l["bb"]
            cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            c = dict(n=l["name"], s=side, r=px(b), f=[round((cx - board[0]) / bw, 4), round((cy - board[1]) / bh, 4)])
            if l["name"] in outline and not l.get("dup"):
                c["o"] = px(outline[l["name"]])
            if l.get("dup"):
                c["dup"] = 1
            if l.get("how") == "mark":
                c["t"] = 1
            if Hm is not None:
                q = cv2.perspectiveTransform(np.float32([[[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]]]), Hm)[0]
                c["q"] = [[round(float(x), 1), round(float(y), 1)] for x, y in q]
            if l["name"] in bom:
                c["b"] = bom[l["name"]]
            comps.append(c)
        log(f"    {side} side = PDF page {p['pn']+1}: {len(labels)} labels, {len(marks)} other printed texts" + (", photo matched" if Hm is not None else ""))
    mm = board_mm(doc, bom_pages)
    if mm:
        for sd in meta["sides"].values():               # size from the BOM, turned to match the drawing
            sd["mm"] = [mm[1], mm[0]] if sd["tall"] else mm

    log("4/5 schematic pages ...")
    names = {c["n"] for c in comps} | set(bom)
    hits, sch_pages = brd.find_schematic(doc, names, set(bom_pages) | {p["pn"] + 1 for p in pages})
    for pn in sch_pages:
        imgs[f"sch_{pn+1}"] = render(doc[pn], None, ZS, 16)
    meta["sch"] = [p + 1 for p in sch_pages]
    for c in comps:
        if c["n"] in hits:
            c["sch"] = [[pn + 1] + [round(v * ZS, 1) for v in b] for pn, b in hits[c["n"]]]
    found = {c["n"] for c in comps}
    # BOM parts whose label was not read: still searchable (shows values and schematic, says label not found)
    for n in sorted(set(bom) - found, key=natural):
        c = dict(n=n, s=None, b=bom[n], miss=1)
        if n in hits:
            c["sch"] = [[pn + 1] + [round(v * ZS, 1) for v in b] for pn, b in hits[n]]
        comps.append(c)
    comps.sort(key=lambda c: natural(c["n"]))

    log("5/5 writing the page ...")
    tpl = open(os.path.join(HERE, "pcbfinder", "template.html"), encoding="utf-8").read()
    data = ("const COMPS=" + json.dumps(comps, separators=(",", ":"), ensure_ascii=False) +
            ";\nconst IMGS=" + json.dumps(imgs, separators=(",", ":")) +
            ";\nconst META=" + json.dumps(meta, separators=(",", ":"), ensure_ascii=False) + ";")
    html = tpl.replace("/*__DATA__*/", data).replace("__TITLE__", meta["title"].replace("<", ""))
    open(out, "w", encoding="utf-8").write(html)

    on_board = [c for c in comps if not c.get("miss") and not c.get("t")]
    log("")
    log("DONE")
    log(f"  {len(on_board)} labels located on the board drawing ({time.time()-t0:.0f} s)")
    if bom:
        nb = len(found & set(bom))
        log(f"  {nb} of {len(bom)} BOM designators located")
        miss = sorted(set(bom) - found, key=natural)
        if miss:
            log("  BOM designators whose label was not found: " + ", ".join(miss))
    if open_after:
        open_in_browser(out)
    if not ask:
        log("Saved:  " + out)
        return out
    return ask_save(out, final)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build a searchable component finder page from a PCBA PDF")
    ap.add_argument("pdf", help="the PCBA PDF")
    ap.add_argument("-o", "--out", help="output HTML file (default: next to the PDF)")
    ap.add_argument("--no-open", action="store_true", help="do not open the browser when finished")
    ap.add_argument("--save", action="store_true", help="save without asking (next to the PDF, or to --out)")
    a = ap.parse_args()
    if not os.path.exists(a.pdf):
        sys.exit("File not found: " + a.pdf)
    build(a.pdf, a.out, not a.no_open, ask=not a.save)
