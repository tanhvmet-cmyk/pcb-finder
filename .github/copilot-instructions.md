# Repository notes for Copilot

This repository is **PCB Finder**: `build.py` turns a PCBA PDF into one offline HTML page where a
component's designator can be searched and its printed name is shown on the board drawing.

- For any request about running a PDF or improving how PDFs are read, follow
  `.github/agents/pcb-finder.agent.md` (also selectable as the **pcb-finder** agent).
- Questions about how power or signals flow on a board: section 5 of the agent file (`paths.py`).
- After changing any file in `build.py`, `paths.py` or `pcbfinder/`, run `python check.py`. Every test case
  must report OK before the change is kept.
- Fixes must be general (work for other PDFs of the same style), never tied to one file, page
  number or designator.
- PDFs and `test-cases/` are company documents: never commit or upload them.
- The user is not a programmer: explain results in short plain sentences, in their language.
