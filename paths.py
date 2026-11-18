"""How current flows on a PCBA, drawn on its schematic (from the PDF).

    python paths.py "board.pdf"                                  # step 1
    python paths.py "board.pdf" --flows "board - flows.json"     # step 2

Step 1 reads the schematic page(s) of the PDF and writes, next to the PDF:
    <pdf name> - schematic nets.json   every net (wires + labels): which pins are joined, the
                                      parts and their pins, BOM text — for the agent to read
    <pdf name> - current flow.html     the schematic; click any wire to light up its net
Step 2 adds the current paths written in the flows file (by the Copilot agent or a person):
arrows and moving dots along the real wires, in the direction of the current, with
explanations. Format of the flows file: see README / .github/agents/pcb-finder.agent.md.

Works best on Altium prints (hidden pin texts); other vector schematics are read from the
drawing (pins found from the symbols — some parts may be missed, the log says which).
"""
import sys, os, json, argparse, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pymupdf
from pcbfinder import flowview as FV, schgraph as SG, bom as bomlib


def log(s):
    print(s, flush=True)


def main():
    ap = argparse.ArgumentParser(description="Current flow on the schematic of a PCBA PDF")
    ap.add_argument("pdf")
    ap.add_argument("--flows", help="JSON file with the current paths to draw")
    ap.add_argument("--pages", help="schematic page numbers, e.g. 5 or 10,11 (default: found automatically)")
    ap.add_argument("-o", "--out", help="output HTML (default: next to the PDF)")
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()
    if not os.path.exists(a.pdf):
        sys.exit("File not found: " + a.pdf)
    t0 = time.time()
    stem = os.path.splitext(a.pdf)[0]
    doc = pymupdf.open(a.pdf)
    log(f"Reading {os.path.basename(a.pdf)} ({len(doc)} pages)")
    pages = [int(x) - 1 for x in a.pages.split(",")] if a.pages else SG.schematic_pages(doc)
    if not pages:
        sys.exit("STOP: no schematic page was found in this PDF (a vector schematic with part designators "
                 "and values). Give the page numbers with --pages if the tool missed them.")
    log(f"1/3 schematic: PDF page(s) {', '.join(str(p + 1) for p in pages)}")
    bom, _ = bomlib.parse_bom(doc)
    flows = {}
    if a.flows:
        flows = json.load(open(a.flows, encoding="utf-8"))
    # the board copper, when the PDF has copper-layer pages: used to check the flows
    net_of = None
    try:
        from pcbfinder import pathview as PV
        import io, contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            res = PV.netlist(doc, log=lambda s: None)
        net_of = {PV.ref(p["des"], p["pin"]): k for k, n in enumerate(res["nets"]) for p in n}
        log("    copper layers found: the flows are also checked against the board copper")
    except SystemExit:
        pass
    except Exception:
        pass
    log("2/3 page ...")
    out = a.out or stem + " - current flow.html"
    out, fl, g = FV.build_page(doc, pages, flows, out, net_of=net_of, bom=bom, log=log,
                               title=os.path.basename(stem))
    log(f"    pins read {'from hidden pin texts' if g.pin_source == 'hidden' else 'from the drawing (check them)'}: "
        f"{len(g.tip)} pins on {len(g.parts())} parts")
    log("3/3 netlist file ...")
    nl = FV.netlist(g)
    nl["schematic_pages"] = [p + 1 for p in pages]
    nl["bom"] = {d: " · ".join(x for x in (b.get("desc"), b.get("pn")) if x) for d, b in bom.items()}
    nl["labels"] = sorted({t for labs in g.labels.values() for t, _ in labs})
    json.dump(nl, open(stem + " - schematic nets.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    nprob = sum(len(f["probs"]) for f in fl)
    log("")
    log(f"DONE ({time.time() - t0:.0f} s)")
    log(f"  page:    {out}")
    log(f"  netlist: {stem} - schematic nets.json  ({len(nl['nets'])} nets, {len(nl['labels'])} label names)")
    if a.flows:
        log(f"  {len(fl)} current paths drawn" + (f", {nprob} problems (lines starting with 'note:' above)" if nprob else ", no problems"))
    if not a.no_open:
        try:
            os.startfile(out)
        except Exception:
            pass


if __name__ == "__main__":
    main()
