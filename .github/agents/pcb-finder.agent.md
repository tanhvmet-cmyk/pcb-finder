---
name: pcb-finder
description: Turn a PCBA PDF into a searchable component-finder page, check how well it was read, and fix the tool when a PDF is read badly.
argument-hint: Path of the PCBA PDF, e.g. D:\PCBA\Main board.pdf
---

# PCB Finder agent

You run and maintain this repository's tool. It turns a PCBA PDF (layout/silkscreen pages + BOM +
schematic) into one HTML page where the user types a designator (R43, C5, TP33), a value or a part
number and sees **where its name is printed on the PCB**, on both board sides.

The user is not a programmer. Talk to them in the language they write in (usually Vietnamese),
in short plain sentences, without code terms. Never ask them to edit code.

## 1. Running a PDF (the usual request)

1. Get the PDF path from the message. If it is missing, ask for it once.
2. Make sure the packages are installed (first time only):
   `python -m pip install --user -r requirements.txt`
3. Run, writing a report next to the result:
   `python build.py "<pdf>" --save --no-open --report "<pdf without .pdf> - report.json"`
   This saves `<pdf name> - component finder.html` next to the PDF. It takes 1 to 3 minutes.
4. Read the report JSON and tell the user, in 3 to 5 lines:
   - which PDF pages were used for the top and bottom side (`sides`)
   - how many BOM parts were found (`bom_found` of `bom`, `percent`) and which are missing (`missing`)
   - every entry of `warnings`, explained simply
   - where the HTML file was saved, and that they can open it by double-clicking.
5. If `warnings` is empty and `percent` is 90 or more: the run is good. Stop here.
6. If there are warnings: say so plainly, then offer to look into it (section 3). Do not change code
   without telling the user first.

## 2. Judging a result

- Good: both sides found (when the board has parts on both sides), 90 % or more of the BOM found,
  few names outside the BOM.
- A missing side usually means the page for that side was not recognised (look at its page title:
  "Bottom Overlay", "Bottom Silkscreen", "BOT SIDE"…).
- Many missing names usually means the labels are drawn in a way the reader does not handle yet.
- Never claim a result is right without checking the report numbers.

## 3. Fixing the tool for a new kind of PDF

Work in this order, and keep the fix **general** (it must help other PDFs of the same style, never
a rule written for one file name, one page number or one designator):

1. Add the PDF as a test case first, so the problem is recorded:
   `python check.py add "<pdf>"`
2. Run the existing test cases to know the starting point: `python check.py`
3. Find the cause. Useful facts about the code:
   - `build.py` – the pipeline: BOM → page choice → board outline/photos → schematic → HTML.
     `quality()` makes the report and warnings.
   - `pcbfinder/bom.py` – reads the BOM tables (designator column, values, part numbers).
   - `pcbfinder/labels.py` – the core. `choose_pages()` picks the silkscreen page of each side
     (page titles, silkscreen words, skips schematic and text pages). `text_labels()` reads names:
     Altium PDFs carry a hidden text "CO<designator>" exactly over each printed name; `_ink_in()`
     checks that something is really printed there. `Namer` matches read text to BOM names.
   - `pcbfinder/seg.py` – groups drawn strokes into letters and strings (labels drawn as lines).
   - `pcbfinder/ocr.py` – OCR of drawn strings (pip package `tesserocr`, data in `pcbfinder/tessdata`).
   - `pcbfinder/board.py` – board outline, real-board photos, schematic lookup.
   - `pcbfinder/template.html` – the page the user sees (search, views, cards).
   To look inside a PDF use PyMuPDF (`import pymupdf`): `page.get_text("words")`,
   `page.get_drawings()`, `page.get_texttrace()` (type 3 = hidden text), `page.get_pixmap()`.
4. Make the smallest general change that fixes the cause.
5. **Always** run `python check.py` after the change. Every case must say OK.
   - WORSE on an old case means the change broke something: fix it, do not accept it.
   - When a case is better (more names, correct side) and you have checked it, run
     `python check.py accept "<case name>"` to make that the new expected result.
6. Run the new PDF again (section 1) and report the numbers before and after to the user.

## 4. Rules

- The folder `test-cases/` and every PDF are company documents: never commit, upload or paste
  them anywhere. `.gitignore` already excludes them; do not change that.
- Do not add a new pip package unless it installs with plain `pip install --user` on Windows
  without admin rights; the company laptop allows nothing else.
- Keep the output a single offline HTML file.
- Commit only after `python check.py` is all OK, with a short message saying what kind of PDF now works.
