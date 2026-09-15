@echo off
rem AIROOT CLI launcher (P1 development launcher).
rem
rem This launcher depends on an interpreter that AIROOT does not manage. That
rem bootstrap weakness is exactly why the CLI moves to a native single-file
rem binary in P2 - see docs/AIROOT-v0.3-??????.md, ADR-0001.
rem
rem It sets PYTHONPATH for this process only and must never write PATH, ACLs or
rem the registry. Use AIROOT_PYTHON to select a specific interpreter.

setlocal
set "AIROOT_APP=%~dp0..\app"
if defined AIROOT_PYTHON (set "AIROOT_INTERPRETER=%AIROOT_PYTHON%") else (set "AIROOT_INTERPRETER=python")
set "PYTHONPATH=%AIROOT_APP%;%PYTHONPATH%"
"%AIROOT_INTERPRETER%" -m airoot %*
exit /b %ERRORLEVEL%
