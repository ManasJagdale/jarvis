@echo off
REM run_jarvis.bat
REM Double-click this (or a shortcut to it) to open Jarvis as a window --
REM no terminal typing required. It always runs from this .bat file's own
REM folder (the Jarvis project root), regardless of where you launch it
REM from, and prefers the local venv if one exists here.
REM
REM gui.py itself lives one folder down, in "UI stuff ig\gui.py" -- that
REM subfolder holds the UI-only files (gui.py, orb.py, and any future
REM UI assets), separate from the backend files (jarvis_core.py,
REM config.py, etc.) that stay at this project root. gui.py adds this
REM root back onto sys.path itself, so it can still import the backend
REM modules -- see the _PROJECT_ROOT lines near the top of gui.py.
REM
REM Uses pythonw.exe (not python.exe) so no console window opens alongside
REM the Jarvis window.
REM
REM IMPORTANT: GEMINI_API_KEY must be a PERMANENT environment variable for
REM double-click launches to see it (a PowerShell $env:... only lasts for
REM that one terminal session). Set it once, permanently, with:
REM     setx GEMINI_API_KEY "your-key-here"
REM Then close and reopen any terminal (and this launcher) once for it to
REM take effect.

cd /d "%~dp0"

if exist "venv\Scripts\pythonw.exe" (
    set PYEXE=venv\Scripts\pythonw.exe
) else (
    set PYEXE=pythonw
)

start "" "%PYEXE%" "UI stuff ig\gui.py"
