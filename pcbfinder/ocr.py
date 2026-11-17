"""Ask Tesseract to read each label (redrawn upright and clean from its vector strokes).
Used only as a hint about which letter each shape is; the BOM check decides the final names."""
import os, subprocess, tempfile, shutil
import numpy as np, cv2

CAP = 44          # cap height in pixels
PEN = 5

def _up(d):
    dx, dy = d
    if (dx, dy) == (1, 0): return lambda p: (p[0], p[1])
    if (dx, dy) == (0, -1): return lambda p: (-p[1], p[0])
    if (dx, dy) == (-1, 0): return lambda p: (-p[0], -p[1])
    return lambda p: (p[1], -p[0])

def render(s, mirror=False):
    up0 = _up(s["dir"])
    up = (lambda p: (-up0(p)[0], up0(p)[1])) if mirror else up0
    gl = []
    for g in (s["g"][::-1] if mirror else s["g"]):
        pts = [(up(a), up(b)) for st in g["st"] for a, b in st["segs"]]
        gl.append(pts)
    allv = [q[1] for pts in gl for ab in pts for q in ab]
    v0, v1 = min(allv), max(allv)
    sc = CAP / max(v1 - v0, 0.7 * s.get("H", 1.0), 1e-3)
    widths = []
    for pts in gl:
        us = [q[0] for ab in pts for q in ab]
        widths.append((min(us), max(us)))
    gap = int(0.45 * CAP)
    W = min(4000, int(sum((b - a) * sc for a, b in widths)) + gap * (len(gl) + 1) + 20)
    img = np.full((CAP + 30, max(W, 40)), 255, np.uint8)
    x = gap
    for pts, (a, b) in zip(gl, widths):
        for p, q in pts:
            cv2.line(img, (int(x + (p[0] - a) * sc), int(15 + (p[1] - v0) * sc)),
                     (int(x + (q[0] - a) * sc), int(15 + (q[1] - v0) * sc)), 0, PEN, cv2.LINE_AA)
        x += int((b - a) * sc) + gap
        if x > W - 10:
            break
    return img

_EXE = None


def tesseract():
    """the Tesseract program: on PATH, in the usual Windows install folders, or a copy next to this tool"""
    global _EXE
    if _EXE is not None:
        return _EXE or None
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cands = [os.environ.get("TESSERACT", ""), shutil.which("tesseract") or "",
             os.path.join(here, "tesseract", "tesseract.exe"), os.path.join(here, "Tesseract-OCR", "tesseract.exe"),
             os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Tesseract-OCR", "tesseract.exe"),
             os.path.join(os.environ.get("LOCALAPPDATA", ""), "Tesseract-OCR", "tesseract.exe"),
             r"C:\Program Files\Tesseract-OCR\tesseract.exe", r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"]
    for c in cands:
        if c and os.path.isfile(c):
            _EXE = c
            return c
    _EXE = ""
    return None


def read_all(strings, chars="ABCDEFGHJKLMNPQRSTUVWXYZ0123456789", mirror=False):
    """Returns one OCR string per input string ('' when unreadable). One Tesseract call per 120 strings."""
    exe = tesseract()
    if not exe:
        return None
    out = [""] * len(strings)
    tmp = tempfile.mkdtemp()
    try:
        for start in range(0, len(strings), 120):
            chunk = strings[start:start + 120]
            ims = [render(s, mirror) for s in chunk]
            W = max(i.shape[1] for i in ims) + 40
            rowh = CAP + 60
            page = np.full((rowh * len(ims) + 40, W), 255, np.uint8)
            for k, im in enumerate(ims):
                page[20 + k * rowh: 20 + k * rowh + im.shape[0], 20:20 + im.shape[1]] = im
            f = os.path.join(tmp, "p.png"); cv2.imwrite(f, page)
            r = subprocess.run([exe, f, "-", "--psm", "6", "-c", "tessedit_char_whitelist=" + chars, "tsv"], encoding="utf-8", errors="replace",
                               creationflags=(0x08000000 if os.name == "nt" else 0),
                               capture_output=True, text=True)
            rows = {}
            for line in r.stdout.splitlines()[1:]:
                p = line.split("\t")
                if len(p) < 12 or not p[11].strip():
                    continue
                top, h = int(p[7]), int(p[9])
                k = int((top + h / 2 - 20) // rowh)
                if 0 <= k < len(ims):
                    rows.setdefault(k, []).append((int(p[6]), p[11].strip()))
            for k, ws in rows.items():
                out[start + k] = "".join(w for _, w in sorted(ws))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out

