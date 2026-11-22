@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   Sportklub: zapusk sayta s bazoy Access
echo ============================================

rem --- 1. Podbor Python: snachala 32-bit (dlya 32-bit Access), potom lyuboy
set PY=
py -3-32 -c "import sys" >nul 2>&1 && set PY=py -3-32
if not defined PY py -3 -c "import sys" >nul 2>&1 && set PY=py -3
if not defined PY (
  echo.
  echo [!] Python ne najden. Ustanovite Python s python.org/downloads
  echo     (esli u vas 32-bitnyj Access - ustanovite 32-bit versiyu Python).
  pause
  exit /b 1
)
echo Python: %PY%

rem --- 2. Biblioteki
%PY% -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo [!] Ne udalos ustanovit biblioteki. Prover'te internet i povtorite.
  pause
  exit /b 1
)

rem --- 3. Proverka bazy (ona dolzhna lezhat v etoj papke ili v papke data)
echo.
echo Proverka bazy Access...
%PY% check_access.py
if errorlevel 1 (
  echo.
  echo [!] Sm. soobshchenie vyshe. Isprav'te i zapustite fajl snova.
  pause
  exit /b 1
)

rem --- 4. Sozdat nedostayushchie tablicy i demo-dannye
%PY% init_access.py
if errorlevel 1 (
  echo.
  echo [!] Oshibka pri sozdanii tablic. Skopiruj'te tekst oshibki vyshe i otpravte razrabotchiku.
  pause
  exit /b 1
)

rem --- 5. Zapusk sajta
echo.
echo Sajt zapuskaetsya: http://127.0.0.1:5000   (zakrojte eto okno, chtoby ostanovit)
start "" "http://127.0.0.1:5000"
%PY% app.py
pause
