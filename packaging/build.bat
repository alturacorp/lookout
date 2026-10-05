@echo off
rem Local Windows build.   packaging\build.bat 1.0.0 [https://.../latest.json]
rem Optional: set FIREBASE_API_KEY and FIREBASE_PROJECT first to build in the shared watchlist.
rem Needs: Python 3.10-3.12, `pip install -r requirements.txt -r requirements-build.txt`, and Inno Setup 6 for the installer.
setlocal
if "%~1"=="" (
  echo usage: packaging\build.bat VERSION [FEED_URL]
  exit /b 1
)
cd /d "%~dp0.."

python packaging\fetch_icons.py
python packaging\write_build_info.py --version %1 --feed "%~2" --cloud-api-key "%FIREBASE_API_KEY%" --cloud-project "%FIREBASE_PROJECT%" || exit /b 1
pyinstaller packaging\roblox_tracker.spec --noconfirm || exit /b 1

echo Self-testing the built exe...
start /wait "" dist\Lookout\Lookout.exe --selftest
if errorlevel 1 (
  echo SELFTEST FAILED. Log follows:
  type "%APPDATA%\Lookout\logs\tracker.log"
  exit /b 1
)

set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
  echo Inno Setup 6 not found - the app is built in dist\Lookout but there is no installer.
  exit /b 0
)
"%ISCC%" /DAppVersion=%1 packaging\installer.iss || exit /b 1
echo Done: dist\Lookout-Setup-%1.exe
