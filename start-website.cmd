@echo off
rem Double-click this file to start the nlsql website. Keep the black window open while you use the site.
rem Close the window (or press Ctrl+C in it) to stop the site.
cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
  echo Could not find .venv in %CD%. Set up the project first ^(SETUP-NEW-DEVICE.md^).
  pause
  exit /b 1
)
call ".venv\Scripts\activate.bat"

rem Your database password lives in local-settings.cmd, which is never uploaded to GitHub.
rem The first time, this script asks for it and creates that file. Delete the file to change it.
if exist "local-settings.cmd" call "local-settings.cmd"
if "%NLSQL_DB%"=="" (
  set /p "NLSQL_RO_PW=Password for the MySQL user nlsql_ro (letters and digits only): "
  call :save_settings
  call "local-settings.cmd"
)
if "%NLSQL_LLM%"=="" set "NLSQL_LLM=ollama"

echo Starting the nlsql website. Leave this window open.
nlsql-web
echo.
echo The website stopped. If you see an error above, send a screenshot of this window.
pause
exit /b 0

:save_settings
> "local-settings.cmd" echo @set "NLSQL_DB=mysql+pymysql://nlsql_ro:%NLSQL_RO_PW%@localhost/nlsql_demo"
exit /b 0
