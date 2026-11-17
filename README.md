# PCB component finder

Turns a PCBA PDF (approval sheet or CAD layout print) into one HTML page. Type a designator
(R43, C5, TP33), a value (100nF) or a part number, and the page zooms to **the name printed on
the PCB** and marks it, on the board drawing, on the real-board photo (if the PDF has one) and on
the schematic (if the PDF has one).

Nothing is uploaded anywhere. The result is a single HTML file you can open offline or send to a colleague.

## Install (once)

1. **Python 3.9 or newer** (already on the company laptop).
2. **Tesseract OCR**, used to read labels that the PDF draws as lines:
   - download the Windows installer from <https://github.com/UB-Mannheim/tesseract/wiki>
   - run it; "Install just for me" works without admin rights
   - the tool finds it in the usual install folders, no PATH change needed.
     A portable copy in a folder named `Tesseract-OCR` next to `build.py` also works.
3. The Python packages are installed by `run.bat` on first use (`pip install --user -r requirements.txt`).

## Use

Drag the PDF onto **run.bat**. After 1-2 minutes the page opens in your browser. It is saved next
to the PDF as `<PDF name> - component finder.html`.

Without the .bat file: `python build.py "C:\path\to\file.pdf"`

The window shows what it found, for example:

    2/5 reading the labels on the layout pages ...
      page 17: 118 labels (111 in BOM)
      page 18: 67 labels (64 in BOM)
    DONE
      178 of 180 BOM designators located
      BOM designators whose label was not found: L2, TVS5

Parts listed as "not found" are still searchable on the page: it shows their BOM values and their
place on the schematic, and says that the label was not found on the board drawing.

## What the page shows

- **Red box**: the designator as printed on the PCB. This position is read from the PDF itself, so it is exact.
- **Blue dashed box** (sometimes): the part next to the label, shown only where that is clear.
- Board drawing / Real photo / Schematic buttons switch the view; Top side / Bottom side switch the side.
- Click any label on the board to see what that part is.

## How it reads the labels

The tool handles three kinds of PDF and picks the right one per page by itself:

| The PDF stores the labels as | Example | How they are read |
|---|---|---|
| Hidden CAD text over drawn letters | Altium print (battery pack PCBA) | name from the CAD text, box from the drawn letters under it |
| Lines only (no text) | GCE / CSMCN approval sheets (3B, 4A) | redrawn upright, read by Tesseract, checked against the BOM |
| Real text | some CAD exports | taken as is |

Then it picks the layout page of each board side (the plain silkscreen page, not combined-layer views)
and reads the BOM table from the table lines in the PDF.

## Results on the example files

| PDF | Labels located | BOM parts located |
|---|---|---|
| 3B main PCBA (2 sides + photos) | 237 | 134 of 137 |
| 4A main PCBA (2 sides) | 182 | 178 of 180 |
| Battery pack PCBA (Altium, no BOM) | 35 | (no BOM in the PDF) |

On the 3B board all 158 top-side labels were checked against a hand-made list: 153 right, 1 wrong
(an unlisted part read as L64 instead of R97) and 4 missed (C35 and C37 printed touching, lower-case
"Fuse1", and the unlisted C27). All 83 bottom-side test points were right.

## Limits

- Layout pages must be vector drawings (sharp at any zoom). Scanned pages cannot be read.
- Labels printed on top of each other, or touching other text, may be missed or merged.
- Without a BOM in the PDF, only names with a usual designator form (letters then a number) are kept.
- The photo is used only when a green board of the same shape as the drawing is found; the mark on the
  photo can be off by about 1 mm.

## Files

- `build.py` - the command you run
- `pcbfinder/labels.py` - finding and reading the labels, choosing the layout pages
- `pcbfinder/seg.py` - cutting drawn lines into letters and words
- `pcbfinder/ocr.py` - Tesseract calls
- `pcbfinder/bom.py` - reading the BOM table
- `pcbfinder/board.py` - board outline, part outlines, photos, schematic
- `pcbfinder/template.html` - the page
