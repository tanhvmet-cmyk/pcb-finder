"""Current and signal paths of a PCBA, from its CAD print PDF.

    python paths.py "board.pdf"                          # netlist + page (click any copper to see its net)
    python paths.py "board.pdf" --notes "board - paths.json"  # same page with the explained paths

Writes next to the PDF:
    <pdf name> - nets.json      every net: which pins are joined (for a person or an agent to read)
    <pdf name> - paths.html     the page

The notes file (written by a person or by the Copilot agent, see .github/agents) names nets and
describes the paths step by step; see README for its format.
"""
import sys, os, json, argparse, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pymupdf
from pcbfinder import pathview as PV, bom as bomlib


def log(s):
    print(s, flush=True)


def main():
    ap = argparse.ArgumentParser(description="Current and signal paths of a PCBA from its CAD print PDF")
    ap.add_argument("pdf")
    ap.add_argument("--notes", help="JSON file with net names and explained paths")
    ap.add_argument("-o", "--out", help="output HTML (default: next to the PDF)")
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()
    if not os.path.exists(a.pdf):
        sys.exit("File not found: " + a.pdf)
    t0 = time.time()
    stem = os.path.splitext(a.pdf)[0]
    doc = pymupdf.open(a.pdf)
    log(f"Reading {os.path.basename(a.pdf)} ({len(doc)} pages)")
    bom, _ = bomlib.parse_bom(doc)
    log("1/3 tracing the copper of both layers ...")
    res = PV.netlist(doc, log=log)
    log(f"    {len(res['nets'])} nets, {len(res['pads'])} pins" +
        (f"; pins joined to nothing: {', '.join(PV.ref(p['des'], p['pin']) for p in res['unconnected'])}" if res['unconnected'] else ""))
    notes = {}
    if a.notes:
        notes = json.load(open(a.notes, encoding="utf-8"))
    log("2/3 schematic and page ...")
    out = a.out or stem + " - paths.html"
    out, sch_names = PV.build_page(doc, res, notes, out, bom=bom, title=os.path.basename(stem), log=log)
    log("3/3 netlist file ...")
    nj = PV.nets_json(res, sch_names, bom)
    nj["copper_pages"] = [res["top_pn"] + 1, res["bot_pn"] + 1]
    json.dump(nj, open(stem + " - nets.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    single = sum(1 for n in res["nets"] if len(n) == 1)
    log("")
    log(f"DONE ({time.time()-t0:.0f} s)")
    log(f"  page:    {out}")
    log(f"  netlist: {stem} - nets.json   ({len(res['nets'])} nets, {single} with a single pin)")
    if not a.no_open:
        try:
            os.startfile(out)
        except Exception:
            pass


if __name__ == "__main__":
    main()
