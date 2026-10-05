@echo off
rem Starter aarshjulet. Lukk dette vinduet for aa stoppe det.
cd /d "%~dp0"
set PY=py -3
where py >nul 2>nul || set PY=python
if not exist ".venv\Scripts\python.exe" (
  echo Forste oppstart: lager Python-miljo ...
  %PY% -m venv .venv || goto :feil
)
rem installer pakker paa nytt hvis requirements.txt er endret siden sist
fc /b requirements.txt ".venv\requirements.installed" >nul 2>nul
if errorlevel 1 (
  echo Installerer pakker ...
  ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt || goto :feil
  copy /y requirements.txt ".venv\requirements.installed" >nul
)
title Aarshjul for innkjop
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" app.py
if errorlevel 1 pause
goto :eof
:feil
echo.
echo Noe gikk galt under installasjonen. Sjekk at Python 3 er installert.
pause
