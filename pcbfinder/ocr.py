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


TESSDATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tessdata")
_API = {}


def _engine():
    """'lib' when the pip package tesserocr is installed (no separate program needed), else 'exe', else None"""
    try:
        import tesserocr  # noqa
        if os.path.isfile(os.path.join(TESSDATA, "eng.traineddata")):
            return "lib"
    except Exception:
        pass
    return "exe" if tesseract() else None


def available():
    return _engine() is not None


def describe():
    e = _engine()
    if e == "lib":
        import tesserocr
        return "tesserocr " + tesserocr.tesseract_version().split()[1] + " (built in)"
    if e == "exe":
        return tesseract()
    return None


def _tsv_lib(img, chars):
    import tesserocr
    from PIL import Image
    api = _API.get(chars)
    if api is None:
        api = tesserocr.PyTessBaseAPI(path=TESSDATA + os.sep, lang="eng", psm=tesserocr.PSM.SINGLE_BLOCK)
        api.SetVariable("tessedit_char_whitelist", chars)
        _API[chars] = api
    api.SetImage(Image.fromarray(img))
    api.Recognize()
    return "level\tpage_num\n" + api.GetTSVText(0)


def read_all(strings, chars="ABCDEFGHJKLMNPQRSTUVWXYZ0123456789", mirror=False):
    """Returns one OCR string per input string ('' when unreadable). One OCR call per 120 strings."""
    eng = _engine()
    if not eng:
        return None
    exe = tesseract()
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
            if eng == "lib":
                tsv = _tsv_lib(page, chars)
            else:
                f = os.path.join(tmp, "p.png"); cv2.imwrite(f, page)
                tsv = subprocess.run([exe, f, "-", "--psm", "6", "-c", "tessedit_char_whitelist=" + chars, "tsv"], encoding="utf-8", errors="replace",
                                     creationflags=(0x08000000 if os.name == "nt" else 0),
                                     capture_output=True, text=True).stdout
            rows = {}
            for line in tsv.splitlines()[1:]:
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



def image_words(img, chars="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+-_/.:"):
    """every word OCR finds anywhere in a picture: [(text, conf, x, y, w, h)] (sparse-text mode)"""
    eng = _engine()
    if not eng:
        return []
    if eng == "lib":
        import tesserocr
        from PIL import Image
        api = _API.get(("sparse", chars))
        if api is None:
            api = tesserocr.PyTessBaseAPI(path=TESSDATA + os.sep, lang="eng", psm=tesserocr.PSM.SPARSE_TEXT)
            api.SetVariable("tessedit_char_whitelist", chars)
            _API[("sparse", chars)] = api
        api.SetImage(Image.fromarray(img))
        api.Recognize()
        tsv = api.GetTSVText(0)
    else:
        tmp = tempfile.mkdtemp()
        try:
            f = os.path.join(tmp, "w.png"); cv2.imwrite(f, img)
            tsv = subprocess.run([tesseract(), f, "-", "--psm", "11", "-c", "tessedit_char_whitelist=" + chars, "tsv"],
                                 encoding="utf-8", errors="replace", capture_output=True, text=True,
                                 creationflags=(0x08000000 if os.name == "nt" else 0)).stdout
            tsv = "\n".join(tsv.splitlines()[1:])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    out = []
    for line in tsv.splitlines():
        p = line.split("\t")
        if len(p) < 12 or not p[11].strip():
            continue
        try:
            out.append((p[11].strip(), float(p[10]), int(p[6]), int(p[7]), int(p[8]), int(p[9])))
        except ValueError:
            pass
    return out
