"""Regression check: make sure a change to the code does not make old PDFs worse.

    python check.py add  "D:\\PCBA\\Main board.pdf"   # keep this PDF and today's result as a test case
    python check.py                                  # re-run every test case and compare
    python check.py accept "Main board"              # the new result is right: make it the expected one

Test cases live in the folder test-cases\\ next to this file (one sub-folder per PDF).
That folder holds company drawings, so it is never uploaded to GitHub (.gitignore).

Exit code 0 = nothing got worse, 1 = something got worse (details printed).
"""
import sys, os, json, shutil, tempfile, re, io, contextlib

HERE = os.path.dirname(os.path.abspath(__file__))
CASES = os.path.join(HERE, "test-cases")
sys.path.insert(0, HERE)
TOL = 0.02          # a name may move this much (fraction of the board size) and still count as the same place


def run(pdf):
    import build
    tmp = tempfile.mkdtemp()
    rep = os.path.join(tmp, "r.json")
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            build.build(pdf, os.path.join(tmp, "o.html"), open_after=False, ask=False, report=rep)
    except SystemExit as e:
        return {"error": str(e), "log": buf.getvalue()}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "log": buf.getvalue()}
    r = json.load(open(rep, encoding="utf-8"))
    shutil.rmtree(tmp, ignore_errors=True)
    return r


def case_dirs(only=None):
    if not os.path.isdir(CASES):
        return []
    out = []
    for d in sorted(os.listdir(CASES)):
        p = os.path.join(CASES, d)
        if os.path.isfile(os.path.join(p, "expected.json")) and (not only or only.lower() in d.lower()):
            out.append(p)
    return out


def pdf_of(case):
    return next(os.path.join(case, f) for f in os.listdir(case) if f.lower().endswith(".pdf"))


def compare(exp, got):
    """list of problems (things that got worse) and notes (things that got better or changed)"""
    bad, good = [], []
    if "error" in got:
        return [f"the run failed: {got['error']}"], good
    if got["sides"] != exp["sides"]:
        bad.append(f"board sides / pages changed: was {exp['sides']}, now {got['sides']}")
    e = {(r[0], r[1]): r for r in exp["parts"]}
    g = {(r[0], r[1]): r for r in got["parts"]}
    en = {r[0] for r in exp["parts"]}
    gn = {r[0] for r in got["parts"]}
    lost = sorted(en - gn)
    if lost:
        bad.append(f"{len(lost)} names no longer found: " + ", ".join(lost[:30]))
    moved = []
    for k, r in e.items():
        if k in g:
            if abs(g[k][2] - r[2]) > TOL or abs(g[k][3] - r[3]) > TOL:
                moved.append(k[0])
        elif r[0] in gn:
            moved.append(r[0] + " (other side)")
    if moved:
        bad.append(f"{len(moved)} names now point to another place: " + ", ".join(moved[:30]))
    new = sorted(gn - en)
    if new:
        good.append(f"{len(new)} names newly found (check they are right): " + ", ".join(new[:30]))
    if got.get("bom_found", 0) < exp.get("bom_found", 0):
        bad.append(f"BOM parts found dropped from {exp['bom_found']} to {got['bom_found']} of {got['bom']}")
    elif got.get("bom_found", 0) > exp.get("bom_found", 0):
        good.append(f"BOM parts found rose from {exp['bom_found']} to {got['bom_found']} of {got['bom']}")
    return bad, good


def cmd_add(pdf):
    if not os.path.isfile(pdf):
        sys.exit("File not found: " + pdf)
    name = re.sub(r"[^\w\- ]+", "_", os.path.splitext(os.path.basename(pdf))[0]).strip() or "case"
    case = os.path.join(CASES, name)
    os.makedirs(case, exist_ok=True)
    dst = os.path.join(case, os.path.basename(pdf))
    if os.path.abspath(pdf) != os.path.abspath(dst):
        shutil.copyfile(pdf, dst)
    print(f"Reading {os.path.basename(pdf)} ...")
    r = run(dst)
    if "error" in r:
        sys.exit("The run failed, nothing saved as expected: " + r["error"])
    json.dump(r, open(os.path.join(case, "expected.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"Saved test case '{name}': {r['labels']} labels, sides {r['sides']}, "
          f"{r.get('bom_found', 0)} of {r.get('bom', 0)} BOM parts.")
    for w in r["warnings"]:
        print("  ! " + w)
    print("Open the result and check it by eye: this result is now what later runs are compared with.")


def cmd_accept(only):
    cs = case_dirs(only)
    if not cs:
        sys.exit("No test case matches: " + only)
    for c in cs:
        r = run(pdf_of(c))
        if "error" in r:
            print(f"{os.path.basename(c)}: run failed, not accepted: {r['error']}")
            continue
        json.dump(r, open(os.path.join(c, "expected.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"{os.path.basename(c)}: new result accepted ({r['labels']} labels, {r.get('bom_found', 0)}/{r.get('bom', 0)} BOM).")


def cmd_check(only=None):
    cs = case_dirs(only)
    if not cs:
        print("No test cases yet. Add one with:  python check.py add \"path\\to\\file.pdf\"")
        return 0
    worse = 0
    for c in cs:
        exp = json.load(open(os.path.join(c, "expected.json"), encoding="utf-8"))
        got = run(pdf_of(c))
        bad, good = compare(exp, got)
        tag = "WORSE" if bad else "OK"
        extra = "" if "error" in got else f"  ({got.get('bom_found', 0)}/{got.get('bom', 0)} BOM, sides {got['sides']})"
        print(f"[{tag}] {os.path.basename(c)}{extra}")
        for b in bad:
            print("    - " + b)
        for g in good:
            print("    + " + g)
        worse += bool(bad)
    print()
    print(f"{len(cs) - worse} of {len(cs)} test cases OK." + (" Fix the cases marked WORSE before using this change." if worse else ""))
    return 1 if worse else 0


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "add" and len(a) == 2:
        cmd_add(a[1])
    elif a and a[0] == "accept" and len(a) == 2:
        cmd_accept(a[1])
    elif not a or (len(a) == 1 and a[0] not in ("add", "accept", "-h", "--help")):
        sys.exit(cmd_check(a[0] if a else None))
    else:
        print(__doc__)
