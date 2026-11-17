@echo off
rem  PCB component finder - drag a PCBA PDF onto this file.
cd /d "%~dp0"
set "PDF=%~1"
if "%PDF%"=="" set /p "PDF=Drag the PDF into this window and press Enter: "
set PDF=%PDF:"=%
python -c "import pymupdf, cv2, scipy, PIL, numpy" 2>nul
if errorlevel 1 (
  echo First run: installing the Python packages this tool needs...
  python -m pip install --user -r requirements.txt
)
python build.py "%PDF%"
echo.
pause
