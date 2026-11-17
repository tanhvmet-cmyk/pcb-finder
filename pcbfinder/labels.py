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
    if nglyph == len(name.replace("_", "").replace("-", "")) and _lev(name.upper(), t.upper()) <= 1:
        return True
    a = name.upper().replace("_", "").replace("-", "")
    b = "".join(LOOK.get(c, c) if i and a[i:i + 1].isdigit() else c for i, c in enumerate(t.upper()))
    return a == t.upper() or a == b or (len(a) >= 3 and _lev(a, b) <= 1)


def text_labels(page, strs, namer):
    """returns (labels, used string indexes, whether the page carries CAD component names)"""
    words = text_words(page)
    hidden_co = sum(1 for w in words if w["hidden"] and w["text"].startswith("CO"))
    labels, used, pend = [], set(), []
    vis = [w for w in words if not w["hidden"]]
    vis_ok = bool(vis) and sum(1 for w in vis if DES.match(w["text"])) >= 0.5 * len(vis)
    for w in words:
        t = w["text"]
        if w["hidden"]:
            if hidden_co < 3 or not t.startswith("CO"):
                continue
            t = t[2:]                                   # Altium component object: "CO" + designator
            if not re.match(r"^[A-Za-z][A-Za-z0-9_+\-]{1,9}$", t):
                continue
        elif not vis_ok or not DES.match(t):
            continue
        name = namer.fuzzy(t) or t
        if w["hidden"]:
            # the hidden text sits over the printed label: the label is the drawn letters inside it
            inside = [k for k, s in enumerate(strs) if _inter(s["bb"], w["bb"]) > 0.5 * _area(s["bb"])]
            if inside:
                pend.append((name, inside))
        else:
            labels.append(dict(name=name, bb=tuple(w["bb"]), rot=0, bom=name in namer.bom, how="text"))
    if pend:
        ks = sorted({k for _, ii in pend for k in ii})
        reads = {}
        if ks:
            got = ocr.read_all([strs[k] for k in ks]) or [""] * len(ks)
            reads = dict(zip(ks, got))
            # vertical text may read either way
            vk = [k for k in ks if strs[k]["dir"][0] == 0]
            if vk:
                flips = []
                for k in vk:
                    f = dict(strs[k]); f["dir"] = (0, -strs[k]["dir"][1]); f["g"] = strs[k]["g"][::-1]
                    flips.append(f)
                for k, t in zip(vk, ocr.read_all(flips) or [""] * len(vk)):
                    reads[k] = (reads[k], t)
        for name, ii in pend:
            hit = [k for k in ii if any(_same(name, t, len(strs[k]["g"])) for t in (reads.get(k) if isinstance(reads.get(k), tuple) else (reads.get(k),)))]
            if not hit:
                continue                                 # this name is not printed on this page
            bb = strs[hit[0]]["bb"]
            used.update(hit)
            labels.append(dict(name=name, bb=tuple(bb), rot=ROT.get(strs[hit[0]]["dir"], 0), bom=name in namer.bom, how="text"))
    return labels, used, hidden_co >= 3


# ----------------------------------------------------------------- 3: OCR of drawn letters
_WARNED = []


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
            print("  WARNING: Tesseract OCR was not found, so labels drawn as lines cannot be read. "
                  "Install it (see README) and run again.", flush=True)
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
        vis = [w for w in text_words(pg) if not w["hidden"]]
        if len(vis) > 150 and sum(1 for w in vis if DES.match(w["text"])) < 0.5 * len(vis):
            continue                                     # schematic or text page
        r = page_labels(pg, namer)
        names = {l["name"] for l in r["labels"]}
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
    options = [[c] for c in cands] + [[a, b] for i, a in enumerate(cands) for b in cands[i + 1:]]
    top = max(val(o) for o in options)
    near = [o for o in options if val(o) >= 0.97 * top]
    chosen = list(min(near, key=lambda o: sum(c["nd"] for c in o)))
    chosen.sort(key=lambda c: c["pn"])
    # final, careful reading of the chosen pages (letter-shape voting on)
    out = []
    for c in chosen:
        r = page_labels(doc[c["pn"]], namer, vote=True)
        r["pn"] = c["pn"]
        out.append(r)
        log(f"  side page {c['pn']+1}: {len(r['labels'])} labels" + (" (drawn mirrored)" if r["mirror"] else ""))
    return out
