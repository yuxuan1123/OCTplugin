@echo off
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" "D:\work\work.html"
schtasks /delete /tn "OpenWorkAtNoon" /f >nul 2>&1
del "%~f0" >nul 2>&1
