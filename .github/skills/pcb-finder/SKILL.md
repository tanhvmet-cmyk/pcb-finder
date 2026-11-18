---
name: pcb-finder
description: Build a searchable component-finder HTML page from a PCBA PDF (layout + BOM + schematic), and a page of current and signal paths, with this repository; check the result and keep old PDFs working after code changes. Use when the user gives a PCBA / PCB / board PDF, asks where a component is, asks how power or a signal flows on the board, or reports that a PDF was read badly.
---

# PCB Finder

Run a PDF:

    python build.py "<pdf>" --save --no-open --report "<pdf without .pdf> - report.json"

Read the report JSON: `sides` (PDF page per board side), `bom_found` / `bom` / `percent`,
`missing`, `warnings`. A good run has no warnings and 90 % or more of the BOM found.
Tell the user these numbers in plain words and where the HTML was saved.

After any code change, run `python check.py`; every test case must say OK.
Add a badly read PDF as a test case before fixing it: `python check.py add "<pdf>"`.
Accept a checked improvement with `python check.py accept "<case name>"`.

Current and signal paths: `python paths.py "<pdf>" --no-open` (netlist traced from the copper
layers → `<pdf name> - nets.json`), then write `<pdf name> - paths.json` and rebuild with
`--notes`. Steps and the notes format: `.github/agents/pcb-finder.agent.md` section 5.

Never commit or upload PDFs or the `test-cases/` folder (company documents).
The full working guide is in `.github/agents/pcb-finder.agent.md`.
