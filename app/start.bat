@echo off
echo Starting SEPAQ Camping app...
echo.
cd /d "%~dp0"
pip install -r requirements.txt --quiet
echo Open http://localhost:5000 in your browser
python app.py
pause
