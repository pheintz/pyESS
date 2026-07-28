@echo off
REM Build a release. Produces dist\pyESS\ plus pyESS-<version>-win64.zip
setlocal
cd /d "%~dp0.."

echo === checks ===
python src\pyESS_app.py --selftest || (echo SELFTEST FAILED - not building & exit /b 1)
for /f "tokens=2" %%v in ('python src\pyESS_app.py --version') do set VER=%%v
echo building pyESS %VER%

echo === deps ===
python -m pip install --quiet --upgrade pyinstaller || exit /b 1
python -m pip install --quiet -r requirements.txt || exit /b 1

echo === build ===
rmdir /s /q build dist 2>nul
python -m PyInstaller --noconfirm --clean packaging\pyESS.spec || exit /b 1

echo === stage top-level files ===
REM These must sit BESIDE the exe, not inside _internal: the licence has to be visible,
REM and pyESS_zones.json must land where _base_dir() looks when frozen.
copy /y LICENSE      dist\pyESS\ >nul || exit /b 1
copy /y README.md    dist\pyESS\ >nul || exit /b 1
copy /y pyESS_zones.json dist\pyESS\ >nul || exit /b 1
copy /y docs\pyess.ico dist\pyESS\ >nul || exit /b 1

echo === smoke test the built exe ===
dist\pyESS\pyESS.exe --version || (echo BUILT EXE FAILED TO RUN & exit /b 1)
dist\pyESS\pyESS.exe --selftest || (echo BUILT EXE SELFTEST FAILED & exit /b 1)

echo === zip ===
powershell -NoProfile -Command ^
  "Compress-Archive -Path 'dist\pyESS\*' -DestinationPath 'pyESS-%VER%-win64.zip' -Force"

echo.
echo Done: pyESS-%VER%-win64.zip
echo Remember: users still need ViGEmBus installed separately.
endlocal
