# PCB component finder

Turns a PCBA PDF (approval sheet or CAD layout print) into one HTML page. Type a designator
(R43, C5, TP33), a value (100nF) or a part number, and the page zooms to **the name printed on
the PCB** and marks it, on the board drawing, on the real-board photo (if the PDF has one) and on
the schematic (if the PDF has one).

Nothing is uploaded anywhere. The result is a single HTML file you can open offline or send to a colleague.

## Install (once)

Only **Python 3.10 or newer** is needed. Everything else comes from pip: `run.bat` installs it on
first use (`pip install --user -r requirements.txt`), with no admin rights and no separate program.
The OCR engine is the pip package `tesserocr`, and its English data file is included in
`pcbfinder/tessdata`.

If you already have the Tesseract program installed, the tool can also use that instead.

## Use

Drag the PDF onto **run.bat**. After about 1 minute the page opens in your browser as a preview, and the
window asks whether to keep it:

    [Y] save next to the PDF:  C:\...\<PDF name> - component finder.html
    [S] choose where to save        (opens the normal Save As window)
    [N] do not save                 (the preview copy is removed automatically after a day)

Pressing Enter means Y. To save without being asked: `python build.py "file.pdf" --save`.

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

## Using it on a new PDF

It works when the new PDF is of the same kinds as the examples:

- the layout pages are vector drawings (sharp at any zoom);
- the designators are drawn as lines or carried as text in the PDF (the tool decides per page);
- ideally there is a BOM table with "Item" and "Designator" columns (names are then checked against it).

Check the last lines of the window: "x of y BOM designators located" and the list of names not found.
If that number is unusually low, or the tool stops with "no layout page", the PDF is of a new kind and the
tool needs adjusting for it.

### Hướng dẫn nhanh (tiếng Việt)

1. Kéo file PDF vào `run.bat`. Lần đầu tool tự cài thư viện; các lần sau chạy luôn (1-2 phút/file).
2. Trang kết quả mở trong trình duyệt để xem trước. Cửa sổ hỏi có lưu không: **Y** lưu cạnh file PDF,
   **S** chọn nơi lưu (mở hộp thoại Save As), **N** không lưu. Nhấn Enter = Y.
3. Gõ tên linh kiện (R43, C5, TP33...), khung đỏ là chỗ in tên đó trên PCB.
4. Xem cuối cửa sổ: "x of y BOM designators located". Nếu số này thấp bất thường hoặc tool báo
   "STOP: no layout page", PDF đó là kiểu mới và cần chỉnh tool.

Chạy tốt: bản vẽ dạng vector, có bảng BOM. Không đọc được: bản vẽ là ảnh scan. Có thể sót: tên in chồng
lên nhau hoặc dính vào chữ khác.

## Dùng như một agent trong VS Code (GitHub Copilot)

1. Mở thư mục này trong VS Code (File → Open Folder).
2. Mở Copilot Chat, ở ô chọn agent chọn **pcb-finder**.
3. Gõ, ví dụ: `Chạy file D:\PCBA\Main board.pdf`
   Agent tự chạy, đọc kết quả, rồi báo: dùng trang nào cho mặt trên/dưới, tìm được bao nhiêu linh
   kiện trong BOM, thiếu cái nào, có cảnh báo gì. File kết quả nằm cạnh file PDF.
4. Nếu file đọc kém, gõ: `Sửa để file này đọc tốt hơn`. Agent thêm file vào bộ kiểm tra, sửa code,
   rồi chạy lại tất cả file cũ (`python check.py`) để chắc không làm hỏng file nào.

Không dùng agent: Terminal → Run Task → chọn **PCB Finder: build page from a PDF**.

Bộ kiểm tra (thư mục `test-cases/`) chỉ nằm trên máy, không bao giờ đưa lên GitHub.
Thêm một file đã chạy đúng vào bộ kiểm tra: `python check.py add "đường dẫn file.pdf"`.

## Kéo PDF vào trang web để xem dòng điện (không cần Python)

Mở file **`flow-viewer.html`** bằng Chrome hoặc Edge, rồi kéo file PDF schematic vào (hoặc bấm để chọn file).
Mọi thứ chạy ngay trong trình duyệt, không cần cài gì, không cần mạng, file không bị gửi đi đâu.

- **Bấm vào một dây**: cả net của dây đó sáng lên. Bảng bên phải cho biết tên net và các linh kiện nối vào.
- **Vẽ đường dòng điện**: gõ chuỗi nhãn và linh kiện theo thứ tự dòng chạy, ví dụ `ACL F1 D1 R1 D4 NTC1 ACN`,
  rồi bấm **Vẽ**. Dòng điện chạy theo đúng dây thật; đoạn đi xuyên qua linh kiện là nét chấm. Có thể bấm vào
  linh kiện trên sơ đồ để thêm nó vào chuỗi. Vẽ được nhiều đường, mỗi đường một màu; trang nhớ các đường cho lần sau.
- **Tìm**: gõ R12, C5, VCC… để nhảy tới chỗ đó.
- **Lưu HTML**: lưu một file HTML gồm sơ đồ và các đường đã vẽ, gửi cho người khác mở là xem được.

Đọc tốt: PDF vector có chữ thật (bôi đen chữ được), ví dụ PDF in từ Altium. Hai nhãn cùng tên (VCC, GND…) được
coi là nối với nhau. Không đọc được: ảnh scan, hoặc PDF mà chữ được vẽ bằng nét (khi đó vẫn bấm dây để xem net
được, nhưng không tìm được tên linh kiện). Chân linh kiện được đoán theo đầu dây gần tên linh kiện nhất, nên mạch
dày có thể sai vài chỗ: nếu đường vẽ sai, thêm linh kiện trung gian vào chuỗi.

Sửa trang: sửa `flow-viewer/app.html` rồi chạy `python flow-viewer/build_page.py flow-viewer/app.html pdf.min.js pdf.worker.min.js flow-viewer.html`
(pdf.js 3.11.174, lấy từ gói npm `pdfjs-dist@3.11.174`, thư mục `build/`).

## Dòng điện chạy trên schematic (current flow)

    python paths.py "đường dẫn file.pdf"
    python paths.py "đường dẫn file.pdf" --flows "tên file - flows.json"

Lần chạy đầu đọc trang schematic (tự tìm, nhiều trang cũng được) và tạo
`<tên PDF> - current flow.html`: bấm vào dây bất kỳ là sáng cả net. Tệp
`<tên PDF> - schematic nets.json` ghi mọi net, linh kiện, nhãn để agent đọc.
Agent **pcb-finder** viết `<tên PDF> - flows.json` (tiếng Anh): mỗi đường điện là một dãy
nhãn / linh kiện / chân, ví dụ `["@ACL", "F101", "LF101.2", "LF101.3", "P112",
"~Suction motor (off board)~", "P113", "RY301.2", "RY301.1", "@ACN1"]`. Lần chạy thứ hai vẽ các
đường đó dọc theo dây thật, có mũi tên và chấm chạy theo chiều dòng điện, kèm giải thích.
Trong Copilot chỉ cần gõ: `Vẽ dòng điện chạy trên schematic của file D:\...\abc.pdf`.

PDF in từ Altium (có tên chân ẩn) được đọc chính xác; schematic khác được đọc từ nét vẽ
(có thể sót vài linh kiện — chương trình báo ra).

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
| Lines only (no text) | GCE / CSMCN approval sheets (3B, 4A) | redrawn upright, read by OCR, checked against the BOM |
| Real text | some CAD exports | taken as is |

Then it picks the layout page of each board side (the plain silkscreen page, not combined-layer views)
and reads the BOM table from the table lines in the PDF.

## Results on the example files

| PDF | Labels located | BOM parts located |
|---|---|---|
| 3B main PCBA (2 sides + photos) | 238 | 134 of 137 |
| 4A main PCBA (2 sides) | 182 | 178 of 180 |
| Battery pack PCBA (Altium, no BOM) | 35 | (no BOM in the PDF) |

On the 3B board all 158 top-side labels were checked against a hand-made list: 154 right, 1 wrong
(an unlisted part read as L64 instead of R97) and 3 missed (C35 and C37 printed touching, and
lower-case "Fuse1"). All 83 bottom-side test points were right.

## Limits

- Layout pages must be vector drawings (sharp at any zoom). Scanned pages cannot be read.
- Labels printed on top of each other, or touching other text, may be missed or merged.
- Without a BOM in the PDF, only names with a usual designator form (letters then a number) are kept.
- The photo is used only when a green board of the same shape as the drawing is found; the mark on the
  photo can be off by about 1 mm.

## Files

- `build.py` - the command you run (`--report r.json` writes the numbers and warnings)
- `check.py` - re-runs saved test PDFs and reports anything that got worse (labels and netlist)
- `flow-viewer.html` - drag a PDF onto this page in the browser: nets, typed current paths, search (source in `flow-viewer/`)
- `paths.py` - current flow on the schematic; `pcbfinder/schgraph.py` reads wires, pins and labels, `pcbfinder/flowview.py` + `pcbfinder/flow.html` make the page; `pcbfinder/nets.py` traces the board copper (checks the flows)
- `.github/agents/pcb-finder.agent.md` - the Copilot agent
- `pcbfinder/labels.py` - finding and reading the labels, choosing the layout pages
- `pcbfinder/seg.py` - cutting drawn lines into letters and words
- `pcbfinder/ocr.py` - OCR calls (built-in tesserocr, or the Tesseract program)
- `pcbfinder/tessdata/` - English OCR data
- `pcbfinder/bom.py` - reading the BOM table
- `pcbfinder/board.py` - board outline, part outlines, photos, schematic
- `pcbfinder/template.html` - the page
