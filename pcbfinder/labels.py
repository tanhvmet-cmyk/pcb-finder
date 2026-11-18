"""
Find every designator label printed on the PCB layout pages, whatever way the PDF stores it:

  1. real text in the PDF (some CAD tools export labels as text)       -> taken as is
  2. hidden CAD text (Altium "CO<name>" objects) over drawn letters    -> name from the text,
                                                                          box from the drawn letters
  3. letters drawn as lines (most approval sheets)                     -> redrawn upright and read
                                                                          by OCR, checked against the BOM
"""
import re
import collections
import numpy as np

from . import seg, ocr, recog

DES = re.compile(r"^[A-Za-z]{1,5}\d{1,4}(?:[_\-][A-Za-z0-9]{1,4})?$")
# designators accepted without a BOM: a known prefix and a number
PREFIX = r"(?:TVS|FUSE|LED|USB|NTC|CN|TH|TP|SW|FB|BT|RV|RJ|IC|JP|R|C|D|Q|U|L|E|Z|F|J|K|T|X|Y|M|P|B|H|S|W|G|V)"
PLAIN = re.compile(r"^" + PREFIX + r"\d{1,4}$", re.I)
SPLIT = re.compile(PREFIX + r"\d{1,4}", re.I)
LOOK = {"S": "5", "O": "0", "D": "0", "Q": "0", "B": "8", "I": "1", "L": "1", "Z": "2", "G": "6", "T": "7", "A": "4"}
ROT = {(1, 0): 0, (0, -1): 90, (-1, 0): 180, (0, 1): 270}


def _u(a, b):
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _inter(a, b):
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))


def _area(b):
    return max(1e-6, (b[2] - b[0]) * (b[3] - b[1]))


class Namer:
    """Turns a reading into a designator, using the BOM when there is one."""

    def __init__(self, bom):
        self.bom = set(bom or [])
        self.by_len = collections.defaultdict(list)
        for w in self.bom:
            self.by_len[len(w)].append(w)
        self.upper = {w.upper(): w for w in self.bom}
        self.prefixes = {re.match(r"[A-Za-z]+", w).group().upper() for w in self.bom if re.match(r"[A-Za-z]+", w)}

    def fuzzy(self, t, strict=False):
        """the one BOM name this reading can be (OCR look-alikes allowed in the number part)"""
        if not t:
            return None
        if t in self.bom:
            return t
        if t.upper() in self.upper:
            return self.upper[t.upper()]
        if strict:
            return None
        hit = []
        for w in self.by_len.get(len(t), []):
            if all(a == b or (a.isdigit() and LOOK.get(b) == a) for a, b in zip(w.upper(), t.upper())):
                hit.append(w)
        return hit[0] if len(hit) == 1 else None

    def name(self, t, strict=False):
        """(designator, in_bom) for a whole reading, or (None, False)"""
        w = self.fuzzy(t, strict)
        if w:
            return w, True
        if t and PLAIN.match(t) and self.prefix_ok(t):
            return t.upper(), False
        return None, False

    def prefix_ok(self, t):
        """with a BOM, an unlisted label must still look like one of its kinds (or a test point)"""
        if not self.bom:
            return True
        p = re.match(r"[A-Za-z]+", t).group().upper()
        return p in self.prefixes or p in ("TP", "TH", "CN", "J", "MH")

    def split(self, t, n):
        """a reading of n letters that is several labels printed touching: [(start, end, name, in_bom)]"""
        if not t or len(t) != n:
            return None
        if self.bom:
            dp = {0: []}
            for i in range(n):
                if i not in dp:
                    continue
                for j in range(i + 2, n + 1):
                    w = self.fuzzy(t[i:j])
                    if w and j not in dp:
                        dp[j] = dp[i] + [(i, j, w, True)]
            if n in dp and len(dp[n]) > 1:
                return dp[n]
        ms = list(SPLIT.finditer(t))
        if len(ms) > 1 and "".join(m.group() for m in ms) == t and all(self.prefix_ok(m.group()) for m in ms):
            return [(m.start(), m.end(), m.group().upper(), m.group() in self.bom) for m in ms]
        return None


# ----------------------------------------------------------------- 1 + 2: text in the PDF
def text_words(page):
    """words of the page's text with their box and whether they are drawn invisibly"""
    out = []
    try:
        traces = page.get_texttrace()
    except Exception:
        return out
    for t in traces:
        cur, bb = "", None
        for ch in list(t["chars"]) + [(32, 0, (0, 0), (0, 0, 0, 0))]:
            c = chr(ch[0])
            if c.isspace():
                if cur:
                    out.append(dict(text=cur, bb=bb, hidden=(t["type"] == 3 or t.get("opacity", 1) == 0)))
                cur, bb = "", None
                continue
            b = tuple(ch[3])
            cur += c
            bb = b if bb is None else _u(bb, b)
    return out


def _lev(a, b):
    d = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        p, d[0] = d[0], i
        for j, cb in enumerate(b, 1):
            p, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, p + (ca != cb))
    return d[-1]


def _same(name, t, nglyph=0):
    """does an OCR reading of drawn letters say this name? (allow one slip, and look-alikes)"""
    if not t:
        return False
    if len(name) >= 4 and nglyph == len(name.replace("_", "").replace("-", "")) and _lev(name.upper(), t.upper()) <= 1:
        return True                                      # one OCR slip allowed only in longer names (R7 is not R1)
    a = name.upper().replace("_", "").replace("-", "")
    b = "".join(LOOK.get(c, c) if i and a[i:i + 1].isdigit() else c for i, c in enumerate(t.upper()))
    return a == t.upper() or a == b or (len(a) >= 3 and _lev(a, b) <= 1)


_SMALL = {}
_INK_SCORE = {}


def _small_drawings(page):
    """boxes of small drawn items (letter strokes, glyph outlines) on the page, cached"""
    key = (id(page.parent), page.number)
    if key not in _SMALL:
        out = []
        try:
            for d in page.get_drawings():
                r = d["rect"]
                if max(r.width, r.height) < 30:
                    out.append((r.x0, r.y0, r.x1, r.y1, 0))
        except Exception:
            pass
        try:                                             # names printed as real (visible) text
            for t in page.get_texttrace():
                if t["type"] == 3 or t.get("opacity", 1) == 0:
                    continue
                for ch in t["chars"]:
                    if not chr(ch[0]).isspace():
                        out.append(tuple(ch[3]) + (1,))
        except Exception:
            pass
        _SMALL[key] = np.array(out) if out else np.zeros((0, 5))
    return _SMALL[key]


def _ink_in(page, bb, nchar):
    """box of the strokes drawn inside a CAD name's box, or None when nothing (enough) is drawn there"""
    D = _small_drawings(page)
    if not len(D):
        return None
    w, h = bb[2] - bb[0], bb[3] - bb[1]
    if w <= 0 or h <= 0:
        return None
    m = 0.15 * min(w, h)
    x0, y0, x1, y1 = bb[0] - m, bb[1] - m, bb[2] + m, bb[3] + m
    cx, cy = (D[:, 0] + D[:, 2]) / 2, (D[:, 1] + D[:, 3]) / 2
    # a letter is about the text height: a drawn stroke no longer than it, a real-text character box
    # (which includes the font's ascent and descent) somewhat taller. Part outlines are longer.
    size = np.maximum(D[:, 2] - D[:, 0], D[:, 3] - D[:, 1])
    small = np.where(D[:, 4] == 1, size <= 1.9 * min(w, h), size <= 1.15 * min(w, h))
    inside = D[(cx >= x0) & (cx <= x1) & (cy >= y0) & (cy <= y1) & small]
    if len(inside) == 0:
        return None
    # the strokes must cover the box: real text spans most of it, a stray pad or track end does not
    ux0, uy0 = max(inside[:, 0].min(), bb[0] - m), max(inside[:, 1].min(), bb[1] - m)
    ux1, uy1 = min(inside[:, 2].max(), bb[2] + m), min(inside[:, 3].max(), bb[3] + m)
    cover = ((ux1 - ux0) * (uy1 - uy0)) / (w * h)
    if len(inside) < max(2, 0.7 * nchar) or cover < 0.45:
        return None
    # letters of one name are about the same size: enough items must be close to the biggest one
    sz = np.maximum(inside[:, 2] - inside[:, 0], inside[:, 3] - inside[:, 1])
    if (sz >= 0.55 * sz.max()).sum() < max(2, 0.7 * nchar):
        return None
    # letters follow one another along the reading direction: their spans must fill most of it,
    # while a few stray bits at the box corners leave it mostly empty
    a, b_ = (0, 2) if w >= h else (1, 3)
    lo, hi = bb[a] - m, bb[b_] + m
    iv = sorted((max(lo, s), min(hi, e)) for s, e in zip(inside[:, a], inside[:, b_]))
    tot, cs, ce = 0.0, None, None
    for s, e in iv:
        if cs is None or s > ce:
            tot += 0 if cs is None else ce - cs
            cs, ce = s, e
        else:
            ce = max(ce, e)
    tot += ce - cs
    if tot < 0.5 * (bb[b_] - bb[a]):
        return None
    _INK_SCORE[(round(bb[0], 1), round(bb[1], 1))] = cover * min(1.0, len(inside) / max(1, nchar))
    return (float(ux0), float(uy0), float(ux1), float(uy1))


def text_labels(page, strs, namer):
    """returns (labels, used string indexes, whether the page carries CAD component names)"""
    words = text_words(page)
    hidden_co = sum(1 for w in words if w["hidden"] and w["text"].startswith("CO"))
    labels, used, pend = [], set(), []
    strs, big_added = list(strs), []
    vis = [w for w in words if not w["hidden"]]
    vis_ok = bool(vis) and sum(1 for w in vis if DES.match(w["text"])) >= 0.5 * len(vis)
    for w in words:
        t = w["text"]
        if w["hidden"]:
            if hidden_co < 3 or not t.startswith("CO"):
                continue
            t = t[2:]                                   # Altium component object: "CO" + designator
            if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_+\-]{1,9}$", t) or not re.search(r"[A-Za-z]", t):
                continue
        elif not vis_ok or not DES.match(t):
            continue
        name = namer.fuzzy(t) or t
        if w["hidden"]:
            # the hidden text sits exactly over the printed name. It is printed on this page when drawn
            # strokes fill that box: this test needs no OCR and works for any CAD font
            nchar = len(re.sub(r"[^A-Za-z0-9]", "", t))
            ink = _ink_in(page, w["bb"], nchar)
            sc = _INK_SCORE.get((round(w["bb"][0], 1), round(w["bb"][1], 1)), 0)
            inside = [k for k, s in enumerate(strs) if _inter(s["bb"], w["bb"]) > 0.5 * _area(s["bb"])]
            if not inside:
                if not big_added:                         # big lettering: strings at the large letter size
                    big_added.append(1)
                    try:
                        for b in seg._page_strings(page, 26.0)[0]:
                            if not any(_inter(b["bb"], a_["bb"]) > 0.3 * min(_area(b["bb"]), _area(a_["bb"])) for a_ in strs):
                                strs.append(b)
                    except Exception:
                        pass
                inside = [k for k, s in enumerate(strs) if _inter(s["bb"], w["bb"]) > 0.5 * _area(s["bb"])]
            if inside and ocr.available():
                # the name is drawn as lines: OCR confirms it (and shows symbols as printed: B0 -> B-)
                pend.append((name, inside, ink if sc >= 0.6 else None))
            elif ink is not None:
                # drawn as filled letters or real text: strokes filling the name's box are proof enough
                labels.append(dict(name=name, bb=ink, rot=0, bom=name in namer.bom, how="text", ink=sc))
        else:
            labels.append(dict(name=name, bb=tuple(w["bb"]), rot=0, bom=name in namer.bom, how="text"))
    if pend:
        ks = sorted({k for _, ii, _ in pend for k in ii})
        reads = {}
        have_ocr = ocr.available()
        if ks and have_ocr:
            # one string at a time (reading many at once drops small symbols such as the + of B+),
            # upright and, for vertical text, turned the other way; capitals first, then with lower case
            low = MARKCH + "abcdefghijklmnopqrstuvwxyz"
            for k in ks:
                vs = [strs[k]]
                if strs[k]["dir"][0] == 0:
                    f = dict(strs[k]); f["dir"] = (0, -strs[k]["dir"][1]); f["g"] = strs[k]["g"][::-1]
                    vs.append(f)
                got = []
                for ch in (MARKCH, low):
                    for v in vs:
                        t = (ocr.read_all([v], chars=ch) or [""])[0]
                        if t and t not in got:
                            got.append(t)
                reads[k] = tuple(got)
        for name, ii, ink in pend:
            orig = name
            if have_ocr:
                hit = [k for k in ii if any(_same(name, t, len(strs[k]["g"])) for t in (reads.get(k) if isinstance(reads.get(k), tuple) else (reads.get(k),)))]
            else:
                # no OCR: the drawn word under the CAD name must have as many letters as the name
                n = len(name.replace("_", "").replace("-", ""))
                hit = [k for k in ii if len(strs[k]["g"]) == n] or [k for k in ii if n > 3 and abs(len(strs[k]["g"]) - n) == 1]
                hit = sorted(hit, key=lambda k: -_inter(strs[k]["bb"], strs[k]["bb"]))[:1]
            if not hit and have_ocr:
                # some PDFs store + - _ / in CAD names as "0" (B- -> B0, P+/C+ -> P00C0): compare without
                # symbols, and take the symbols from what is printed
                key = lambda x: re.sub(r"[^A-Z1-9]", "", (x or "").replace("l", "1").upper())
                rd = lambda k: [x for x in (reads.get(k) if isinstance(reads.get(k), tuple) else (reads.get(k),)) if x]
                for k in ii:
                    for t in rd(k):
                        if key(t) == key(name) and key(t):
                            hit, name = [k], t
                            break
                    if hit:
                        break
                if not hit and len(ii) > 1:                 # name printed in pieces (B1_EX as "B1" + "EX")
                    order = sorted(ii, key=lambda k: (strs[k]["bb"][0], strs[k]["bb"][1]) if strs[k]["dir"][0] else (-strs[k]["bb"][3], strs[k]["bb"][0]))
                    import itertools
                    opts = [rd(k) for k in order]
                    if all(opts):
                        for parts in itertools.product(*opts):
                            if key("".join(parts)) == key(name):
                                hit, name = order, "_".join(parts)
                                break
            if not hit:
                if ink is not None:                       # OCR unsure but the box is clearly full of letters
                    labels.append(dict(name=name, bb=ink, rot=0, bom=name in namer.bom, how="text"))
                continue
            if have_ocr and len(hit) == 1:
                # show the name as printed when it only differs in symbols (B0 -> B-, TAB0 -> TAB+, B10 -> B1_)
                key = lambda x: re.sub(r"[^A-Z1-9]", "", (x or "").replace("l", "1").upper())
                for t in (reads.get(hit[0]) if isinstance(reads.get(hit[0]), tuple) else (reads.get(hit[0]),)):
                    if t and t != name and key(t) == key(name):
                        if re.search(r"[+\-_/.]", t) or t.upper() == name.upper():
                            name = t                      # printed form, symbols and case included
                        elif "0" in name and len(t) < len(name):
                            # a symbol too small to read (the _ of B1_): the PDF's "0" stands for it
                            name = re.sub(r"0", "_", name) if "0" not in t else name
                        break
            if "l" in name and "l" not in orig and "L" not in orig:
                name = name.replace("l", "1")             # OCR's lower-case l for the digit 1
            bb = strs[hit[0]]["bb"]
            for k in hit[1:]:
                bb = _u(bb, strs[k]["bb"])
            used.update(hit)
            labels.append(dict(name=name, bb=tuple(bb), rot=ROT.get(strs[hit[0]]["dir"], 0), bom=name in namer.bom, how="text"))
    return labels, used, hidden_co >= 3


# ----------------------------------------------------------------- 3: OCR of drawn letters
_WARNED = []
MARKCH = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+-_/."


def raster_marks(page, labels, taken):
    """read every visible word on the board area from a picture of it, at 0, 90 and 270 degrees.
    Works whatever way the PDF stores the text (lines, filled letters, real text)."""
    import cv2
    import pymupdf
    if not ocr.available() or not labels:
        return []
    xs0 = min(l["bb"][0] for l in labels); ys0 = min(l["bb"][1] for l in labels)
    xs1 = max(l["bb"][2] for l in labels); ys1 = max(l["bb"][3] for l in labels)
    m = 0.08 * max(xs1 - xs0, ys1 - ys0)
    pr = page.rect
    clip = (max(pr.x0, xs0 - m), max(pr.y0, ys0 - m), min(pr.x1, xs1 + m), min(pr.y1, ys1 + m))
    hts = sorted(min(l["bb"][2] - l["bb"][0], l["bb"][3] - l["bb"][1]) for l in labels)
    h = hts[len(hts) // 2] if hts else 3.0
    z = max(2.0, min(12.0, 26.0 / max(h, 0.5)))           # usual name height -> about 26 pixels
    z = min(z, 5000 / max(clip[2] - clip[0], clip[3] - clip[1]))
    pm = page.get_pixmap(matrix=pymupdf.Matrix(z, z), clip=pymupdf.Rect(*clip), alpha=False)
    im = np.frombuffer(pm.samples, np.uint8).reshape(pm.height, pm.width, 3)
    gray = cv2.cvtColor(im, cv2.COLOR_RGB2GRAY)
    bw = np.where(gray < 235, 0, 255).astype(np.uint8)    # any coloured ink -> black on white
    H, W = bw.shape
    out = []
    for rot in (0, 90, 270):
        img = bw if rot == 0 else np.rot90(bw, k=1 if rot == 90 else 3).copy()
        for t, conf, x, y, w, hh in ocr.image_words(img):
            if conf < 80 or len(t) < 2 or not any(ch.isalnum() for ch in t) or len(set(t)) == 1:
                continue
            if rot == 0:
                bx = (x, y, x + w, y + hh)
            elif rot == 90:                               # image turned left: undo
                bx = (W - (y + hh), x, W - y, x + w)
            else:
                bx = (y, H - (x + w), y + hh, H - x)
            pb = (clip[0] + bx[0] / z, clip[1] + bx[1] / z, clip[0] + bx[2] / z, clip[1] + bx[3] / z)
            if any(_inter(pb, b) > 0.3 * _area(pb) for b in taken):
                continue
            out.append(dict(name=t, bb=pb, rot=rot, bom=False, how="mark"))
            taken.append(pb)
    # the same short "word" read many times is a repeated drawing (part outlines), not text
    cnt = collections.Counter(o["name"] for o in out)
    return [o for o in out if not ((len(o["name"]) <= 2 and cnt[o["name"]] >= 4) or (len(o["name"]) == 3 and cnt[o["name"]] >= 8))]


def marks_of(strs, labels):
    """all other printed text on the page (GND, RX, 3.3V, B+, TAB-, dates ...), so everything visible can be searched"""
    if not ocr.available():
        return []
    taken = [l["bb"] for l in labels]
    rest = [s for s in strs if not any(_inter(s["bb"], b) > 0.3 * _area(s["bb"]) for b in taken)]
    if not rest:
        return []
    t1 = ocr.read_all(rest, chars=MARKCH) or [""] * len(rest)
    vert = [i for i, s in enumerate(rest) if s["dir"][0] == 0]
    t2 = {}
    if vert:
        fl = []
        for i in vert:
            f = dict(rest[i]); f["dir"] = (0, -rest[i]["dir"][1]); f["g"] = rest[i]["g"][::-1]
            fl.append(f)
        t2 = dict(zip(vert, ocr.read_all(fl, chars=MARKCH) or [""] * len(vert)))

    def q(t, n):
        if not t:
            return -9
        al = sum(c.isalnum() for c in t)
        return (3 if len(t) == n else 1 if abs(len(t) - n) == 1 else -5) + min(al, 4) - 2 * (t.count("/") > 1)
    out = []
    for i, s in enumerate(rest):
        n = len(s["g"])
        t = t1[i]
        if i in t2 and q(t2[i], n) > q(t, n):
            t = t2[i]
        if not t or q(t, n) < 3 or sum(c.isalnum() for c in t) < 1 or len(t) < 2 or len(set(t)) == 1:
            continue
        out.append(dict(name=t, bb=s["bb"], rot=ROT.get(s["dir"], 0), bom=False, how="mark"))
    return out


def _better(namer, t):
    n, inb = namer.name(t)
    return 3 if inb else 2 if n else 1 if t else 0


def ocr_labels(strs, namer, mirror=False, vote=True):
    """name drawn strings; returns (labels, number of strings that read as designators)"""
    if not strs:
        return [], 0
    texts = ocr.read_all(strs, mirror=mirror)
    if texts is None:
        if not _WARNED:
            _WARNED.append(1)
            print("  WARNING: no OCR engine, so labels drawn as lines cannot be read.", flush=True)
        return [], 0
    # vertical text reads upwards or downwards: read it the other way too and keep the better reading
    vert = [i for i, s in enumerate(strs) if s["dir"][0] == 0 and _better(namer, texts[i]) < 3]
    if vert:
        flips = []
        for i in vert:
            f = dict(strs[i]); f["dir"] = (0, -strs[i]["dir"][1]); f["g"] = strs[i]["g"][::-1]
            flips.append(f)
        t2 = ocr.read_all(flips, mirror=mirror)
        # most vertical labels on a board read the same way: that decides ties
        up = sum(1 for i, t in zip(vert, t2) if _better(namer, texts[i]) > _better(namer, t))
        down = sum(1 for i, t in zip(vert, t2) if _better(namer, t) > _better(namer, texts[i]))
        for i, f, t in zip(vert, flips, t2):
            a_, b_ = _better(namer, t), _better(namer, texts[i])
            if a_ > b_ or (a_ == b_ and a_ >= 2 and down > up):
                strs[i]["dir"], strs[i]["g"] = f["dir"], f["g"]
                strs[i]["cbb"] = [g["bb"] for g in f["g"]]
                strs[i]["imgs"] = seg.string_imgs(strs[i])
                texts[i] = t
    if any(ch.islower() for w in namer.bom for ch in w):
        lows = {len(w) for w in namer.bom if any(ch.islower() for ch in w)}
        idx = [i for i, st in enumerate(strs) if len(st["g"]) in lows and _better(namer, texts[i]) < 3]
        if idx:
            al = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
            for i, t in zip(idx, ocr.read_all([strs[i] for i in idx], chars=al, mirror=mirror) or []):
                if namer.fuzzy(t):
                    texts[i] = t
            vi = [i for i in idx if strs[i]["dir"][0] == 0 and not namer.fuzzy(texts[i])]
            if vi:
                fl = []
                for i in vi:
                    f = dict(strs[i]); f["dir"] = (0, -strs[i]["dir"][1]); f["g"] = strs[i]["g"][::-1]; fl.append(f)
                for i, f, t in zip(vi, fl, ocr.read_all(fl, chars=al, mirror=mirror) or []):
                    if namer.fuzzy(t):
                        strs[i]["dir"], strs[i]["g"] = f["dir"], f["g"]
                        strs[i]["cbb"] = [g["bb"] for g in f["g"]]
                        strs[i]["imgs"] = seg.string_imgs(strs[i])
                        texts[i] = t
    raws = [None] * len(strs)
    if vote:
        # every copy of a letter shape is the same letter: majority vote repairs single OCR slips
        flat = [im for s in strs for im in s["imgs"]]
        _, ids = recog.cluster(flat, 0.2)
        seqs, k = [], 0
        for s in strs:
            seqs.append(ids[k:k + len(s["imgs"])]); k += len(s["imgs"])
        if mirror:
            seqs = [q[::-1] for q in seqs]
        votes = collections.defaultdict(collections.Counter)
        for t, q in zip(texts, seqs):
            if t and len(t) == len(q):
                for c, ch in zip(q, t):
                    votes[c][ch] += 1
        shape = {c: v.most_common(1)[0][0] for c, v in votes.items()
                 if v.most_common(1)[0][1] >= 3 and v.most_common(1)[0][1] >= 0.7 * sum(v.values())}
        raws = ["".join(shape.get(c, "?") for c in q) for q in seqs]
    labels, hits = [], 0
    for i, s in enumerate(strs):
        cbb = list(reversed(s["cbb"])) if mirror else s["cbb"]
        rot = ROT.get(s["dir"], 0)
        # a reading voted by every copy of each letter shape beats the single OCR reading of this string
        voted = raws[i] if raws[i] and "?" not in raws[i] else None
        cands = ([voted] if voted else []) + ([texts[i]] if texts[i] != voted else [])
        got = None
        for t in cands:                                  # whole string is one label
            # when every copy of the letters agrees with OCR (e.g. "DS"), they are letters: no look-alikes
            n, inb = namer.name(t, strict=(voted is not None and (t == voted or voted == texts[i])))
            if n and len(t) == len(cbb) and (got is None or (inb and not got[1])):
                got = (n, inb, t)
        if got:
            labels.append(dict(name=got[0], bb=s["bb"], rot=rot, bom=got[1], how="ocr", exact=(got[2] == texts[i]),
                               agree=(voted is not None and voted == texts[i])))
            hits += 1
            continue
        for t in cands:                                  # several labels printed touching each other
            sp = namer.split(t, len(cbb))
            if sp:
                for a, b, n, inb in sp:
                    cb = cbb[a:b]
                    labels.append(dict(name=n, rot=rot, bom=inb, how="ocr", exact=True, agree=(voted == texts[i]),
                                       bb=(min(c[0] for c in cb), min(c[1] for c in cb), max(c[2] for c in cb), max(c[3] for c in cb))))
                hits += 1
                break
    return labels, hits


# ----------------------------------------------------------------- pages
def page_labels(page, namer, vote=False):
    """labels of one page; the letter size is not known, so small and large lettering are both tried"""
    best = None
    for maxs in (seg.MAXS, 26.0):
        strs, _ = seg._page_strings(page, maxs)
        if not strs:
            continue
        r = _page_labels(page, namer, strs, vote)
        if best is None or len(r["labels"]) > len(best["labels"]):
            best = r
        if len(r["labels"]) >= 25:
            break
    return best or dict(labels=[], strs=[], mirror=False)


def _page_labels(page, namer, strs, vote):
    strs = [s for s in strs if 1 <= len(s["g"]) <= 14]
    tl, used, cad = text_labels(page, strs, namer)
    if cad:
        # the CAD names are complete: other drawn text (version, net names, notes) is not a designator
        return dict(labels=tl, strs=strs, mirror=False)
    taken = [l["bb"] for l in tl]
    rest = [s for k, s in enumerate(strs) if k not in used and len(s["g"]) >= 2
            and not any(_inter(s["bb"], b) > 0.3 * _area(s["bb"]) for b in taken)]
    ol, hits = ocr_labels(rest, namer, vote=vote)
    mirror = False
    if len(rest) >= 20 and hits < 0.15 * len(rest):          # bottom side drawn mirrored?
        ol2, hits2 = ocr_labels(rest, namer, mirror=True, vote=vote)
        if hits2 > 2 * hits + 5:
            ol, hits, mirror = ol2, hits2, True
    labels = tl + ol
    # one place per name on a page: the most certain reading wins; a real tie keeps both places
    by = collections.defaultdict(list)
    for l in labels:
        by[l["name"]].append(l)
    out = []
    for k, ls in by.items():
        sc = lambda l: (l["how"] == "text", l.get("agree", True), l.get("exact", True), l["bom"])
        top = max(sc(l) for l in ls)
        keep = [l for l in ls if sc(l) == top]
        for l in keep:
            l["dup"] = len(keep) > 1
        out += keep
    labels = out
    return dict(labels=labels, strs=strs, mirror=mirror)


VALUE = re.compile(r"^\d+(\.\d+)?[pnuµ]F$|^\d+(\.\d+)?[kKM]$|^\d+[kKMR]\d+$|^\d+(\.\d+)?V$|^\d+(\.\d+)?R$|^\d+(\.\d+)?Ω$", re.I)


def choose_pages(doc, bom, log=print, skip=()):
    """read the layout pages and pick the silkscreen page of each board side (at most two)"""
    namer = Namer(bom)
    cands = []
    for pn in range(len(doc)):
        if pn + 1 in skip:
            continue
        pg = doc[pn]
        try:
            nd = len(pg.get_drawings())
        except Exception:
            continue
        if nd < 150:
            continue
        tw = text_words(pg)
        vis = [w for w in tw if not w["hidden"]]
        cad = sum(1 for w in tw if w["hidden"] and w["text"].startswith("CO")) >= 3
        if not cad and len(vis) > 150 and sum(1 for w in vis if DES.match(w["text"])) < 0.5 * len(vis):
            continue                                     # schematic or text page
        nco = sum(1 for w in tw if w["hidden"] and w["text"].startswith("CO"))
        if sum(1 for w in vis if VALUE.match(w["text"])) >= max(10, 0.1 * nco):
            continue                                     # part values written out (10K, 100nF): a schematic
        r = page_labels(pg, namer)
        names = {l["name"] for l in r["labels"]}
        title = " ".join(w["text"].lower() for w in text_words(pg) if not w["hidden"])
        r["silk"] = bool(re.search(r"(top|bottom|bot) (overlay|silk ?screen|silk|legend)|silk ?screen|\boverlay\b", title))
        r["bottom_title"] = bool(re.search(r"bottom (overlay|silk|layer)|\bbot(tom)? side", title))
        r["top_title"] = bool(re.search(r"top (overlay|silk|layer)|\btop side", title))
        if len(names) >= 5:
            r.update(pn=pn, nd=nd, names=names, nbom=sum(1 for l in r["labels"] if l["bom"]))
            cands.append(r)
            log(f"  page {pn+1}: {len(names)} labels ({r['nbom']} in BOM)")
    if not cands:
        return []
    # best one or two pages: cover the most names with the least overlap; plain silkscreen pages
    # (fewest drawing objects) win ties against combined-layer and assembly views of the same side
    def val(sel):
        u = set().union(*(c["names"] for c in sel))
        inter = len(sel[0]["names"] & sel[1]["names"]) if len(sel) == 2 else 0
        if namer.bom:
            nb = len(u & namer.bom)
            return 2 * nb + 0.5 * (len(u) - nb) - 1.5 * inter
        return len(u) - 1.5 * inter
    best_n = max(len(c["names"]) for c in cands)
    silk = [c for c in cands if c["silk"] and len(c["names"]) >= 0.2 * best_n]
    if silk:
        cands = silk                                   # the PDF names its silkscreen pages: use only those
    options = [[c] for c in cands] + [[a, b] for i, a in enumerate(cands) for b in cands[i + 1:]]
    top = max(val(o) for o in options)
    # plain silkscreen pages beat busier views of the same side unless those read clearly more
    near = [o for o in options if val(o) >= 0.92 * top]
    chosen = list(min(near, key=lambda o: sum(c["nd"] for c in o)))
    # the PDF titles its top and bottom silkscreen pages: take the best of each
    tops = [c for c in cands if c.get("top_title") and not c.get("bottom_title")]
    bots = [c for c in cands if c.get("bottom_title")]
    if tops and bots:
        best = lambda cs: max(cs, key=lambda c: (len(c["names"]) + c["nbom"], -c["nd"]))
        chosen = [best(tops), best(bots)]
    # top side first: the side carrying more BOM parts (the other is often only test points)
    chosen.sort(key=lambda c: (-c["nbom"], c["pn"]))
    if len(chosen) == 2 and chosen[0].get("bottom_title") and not chosen[1].get("bottom_title"):
        chosen.reverse()                               # the PDF says which page is the bottom side
    for c in chosen:
        c["side_hint"] = "bottom" if c.get("bottom_title") else "top" if c.get("top_title") else None
    # final, careful reading of the chosen pages (letter-shape voting on)
    out = []
    for c in chosen:
        r = page_labels(doc[c["pn"]], namer, vote=True)
        r["side_hint"] = c.get("side_hint")
        out.append(r)
    out_final, out = out, []
    if len(out_final) == 2:
        a_, b_ = out_final
        sa = {l["name"]: l.get("ink", 0) for l in a_["labels"] if "ink" in l}
        sb = {l["name"]: l.get("ink", 0) for l in b_["labels"] if "ink" in l}
        for nm in set(sa) & set(sb):
            loser = b_ if sa[nm] >= sb[nm] else a_
            loser["labels"] = [l for l in loser["labels"] if l["name"] != nm]
    for c, r in zip(chosen, out_final):
        r["pn"] = c["pn"]
        # other printed text can be bigger than the part names: look for strings at both letter sizes
        allstr = list(r["strs"])
        try:
            big, _ = seg._page_strings(doc[c["pn"]], 26.0)
            for b in big:
                if 1 <= len(b["g"]) <= 14 and not any(_inter(b["bb"], a_["bb"]) > 0.3 * min(_area(b["bb"]), _area(a_["bb"])) for a_ in allstr):
                    allstr.append(b)
        except Exception:
            pass
        r["marks"] = marks_of(allstr, r["labels"])
        # plus anything else readable in a picture of the board (filled-letter fonts, logos' text ...)
        r["marks"] += raster_marks(doc[c["pn"]], r["labels"], [l["bb"] for l in r["labels"]] + [m["bb"] for m in r["marks"]])
        # plus text the PDF carries as real, visible text (KEY, NTC, Brush +, 3.3V ...)
        taken = [l["bb"] for l in r["labels"]] + [m["bb"] for m in r["marks"]]
        for w in text_words(doc[c["pn"]]):
            t = w["text"]
            if w["hidden"] or not any(ch.isalnum() for ch in t) or len(t) > 30 or len(t) < 2:
                continue
            if any(_inter(w["bb"], b) > 0.3 * _area(w["bb"]) for b in taken):
                continue
            r["marks"].append(dict(name=t, bb=tuple(w["bb"]), rot=0, bom=False, how="mark"))
        # the same short "word" read many times is a repeated drawing (part outlines, pad marks), not text
        cnt = collections.Counter(m["name"] for m in r["marks"])
        r["marks"] = [m for m in r["marks"] if not ((len(m["name"]) <= 2 and cnt[m["name"]] >= 4)
                                                     or (len(m["name"]) == 3 and cnt[m["name"]] >= 8))]
        # printed text that turns out to be a BOM part name not found yet is a part label after all
        have = {l["name"] for l in r["labels"]}
        keep = []
        for m in r["marks"]:
            w = namer.fuzzy(m["name"]) if namer.bom else None
            if w and w not in have:
                r["labels"].append(dict(name=w, bb=m["bb"], rot=m["rot"], bom=True, how="ocr", exact=False))
                have.add(w)
            else:
                keep.append(m)
        r["marks"] = keep
        out.append(r)
        log(f"  side page {c['pn']+1}: {len(r['labels'])} labels" + (" (drawn mirrored)" if r["mirror"] else ""))
    return out
