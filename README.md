# PCB component finder

Turns a PCBA approval-sheet PDF into one HTML page where you
type a designator (R43, C5, TP33), a value (100K) or a part number and see where the part
sits on the board drawing, on the real photo and on the schematic.

Nothing is uploaded anywhere. The result is a single HTML file you can open offline or send to a colleague.

## Use it

1. On GitHub click **Code > Download ZIP**, then unzip the folder anywhere on the Windows laptop (Python must be installed).
2. Drag the PDF onto **run.bat**.
   The first run installs five Python packages with pip; later runs skip that.
3. After about 15 seconds the page opens in your browser. It is saved next to the PDF as
   `<PDF name> - component finder.html`.

Without the .bat file: `python build.py "C:\path\to\file.pdf"`

## Check the positions (10-15 minutes per new board)

The tool reads every printed label exactly, but it has to guess which outline belongs to
each label. On a crowded board many guesses are wrong, so each part gets a coloured dot:

| Dot | Meaning |
|---|---|
| dark green | you checked it |
| light green | outline next to its own label, safe in almost all cases |
| orange | outline guessed, may be a neighbour: check it |
| red | no outline matched, only the printed label is marked |

To make the page fully correct:

1. Click **Check positions**. The tool walks through the orange and red parts.
2. If the red box is on the right part, press **Enter**.
   If not, click the right outline (blue boxes). If there is no blue box, hold **Shift** and drag one.
3. Click **Save corrections...**. A file `<PDF name>.fixes.json` is downloaded.
4. Move that file into the same folder as the PDF and drag the PDF onto run.bat again.
   Every checked part now shows a dark green dot. You can repeat this any time; earlier checks are kept.

## What to expect on a new PDF

Measured on one two-sided test board (about 160 parts on top, 85 test points on the bottom):

| Step | Result |
|---|---|
| Labels found on the board | 243, including all 137 BOM designators |
| Bottom side (test points) | 82 of 85 outlines right without any checking |
| Top side (components) | 85 of 157 outlines right, 33 wrong, 39 label only, before checking |
| Top side, light-green parts only | 40 of 44 right |
| Photo alignment | within about 0.7 mm |

So on the component side expect roughly half of the outlines to need a click. The printed
label is marked correctly either way, which is already enough to find a part by eye.

## When it will not work

- The PCB layout pages must be vector drawings (the PDF shows them sharp at any zoom).
  Scanned or photographed layouts have no labels to read.
- The label letters must be the same CAD line font as in the board this tool was built on. A different CAD
  tool may draw letters differently; then few labels are found and the tool says so.
  Letters I, J, O and Y were not present on that board and may be misread.
- The BOM is read by column position from a table with the headers `Item` and `Designator`.
  If the BOM is missing or laid out differently, the page still works but shows no values.
- The photo is used only when the tool finds a green board whose shape matches the drawing.
  It assumes the photo is not rotated relative to the drawing.
- If the first photo on the picture page is the bottom side, the two photos are swapped.

## Files

- `build.py` - the command you run
- `pcbfinder/core.py` - reading the PDF (BOM, labels, outlines, photos, schematic)
- `pcbfinder/glyphs.npz` - shapes of the label letters
- `pcbfinder/template.html` - the page
