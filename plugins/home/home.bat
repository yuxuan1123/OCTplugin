@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

title Auto Setup: Open work.html x2 + Shutdown + Feishu Docs

:: ========== 检查管理员权限 ==========
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERROR] Please run as Administrator.
    pause
    exit /b 1
)

echo ============================================
echo  Auto Setup (No input required)
echo ============================================

:: -------- 1. 场景A：11:59 打开网页 ----------
call :installScenarioA

:: -------- 2. 场景B：17:55 打开网页 ----------
call :installScenarioB

:: -------- 3. 场景C：17:58 无条件关机 ----------
call :installShutdown

:: -------- 4. 立即打开飞书文档 ----------
call :openFeishuDocs

echo ============================================
echo  All steps completed.
echo  - Scenario A: open work.html at 11:59
echo  - Scenario B: open work.html at 17:55
echo  - Scenario C: shutdown at 17:58 (unconditional)
echo  - Three Feishu docs opened in browser
echo ============================================
pause
goto :eof

:: =================================================
:: 场景A：11:59 用 Chrome 打开 work.html（一次性任务）
:: =================================================
:installScenarioA
set "TASK_NAME=OpenWorkAtNoon"
set "OPEN_SCRIPT=%~dp0open_work_noon_temp.bat"
set "HTML_PATH=D:\work\work.html"
set "CHROME_PATH=C:\Program Files\Google\Chrome\Application\chrome.exe"

echo [Step 1/4] Scenario A: open work.html at 11:59...

for /f %%a in ('powershell -NoProfile -Command Get-Date -Format yyyy-MM-dd') do set "TODAY=%%a"

(
    echo @echo off
    echo start "" "%CHROME_PATH%" "%HTML_PATH%"
    echo schtasks /delete /tn "%TASK_NAME%" /f ^>nul 2^>^&1
    echo del "%%~f0" ^>nul 2^>^&1
) > "%OPEN_SCRIPT%"

if not exist "%OPEN_SCRIPT%" (
    echo   [ERROR] Failed to create script.
    exit /b 1
)

schtasks /create /tn "%TASK_NAME%" /tr "\"%OPEN_SCRIPT%\"" /sc once /sd %TODAY% /st 11:59 /f >nul 2>&1

if %errorLevel% equ 0 (
    echo   SUCCESS.
) else (
    echo   FAILED ^(current time may be past 11:59^).
)
exit /b

:: =================================================
:: 场景B：17:55 用 Chrome 打开 work.html（一次性任务）
:: =================================================
:installScenarioB
set "TASK_NAME=OpenWorkAtEvening"
set "OPEN_SCRIPT=%~dp0open_work_evening_temp.bat"
set "HTML_PATH=D:\work\work.html"
set "CHROME_PATH=C:\Program Files\Google\Chrome\Application\chrome.exe"

echo [Step 2/4] Scenario B: open work.html at 17:55...

for /f %%a in ('powershell -NoProfile -Command Get-Date -Format yyyy-MM-dd') do set "TODAY=%%a"

(
    echo @echo off
    echo start "" "%CHROME_PATH%" "%HTML_PATH%"
    echo schtasks /delete /tn "%TASK_NAME%" /f ^>nul 2^>^&1
    echo del "%%~f0" ^>nul 2^>^&1
) > "%OPEN_SCRIPT%"

if not exist "%OPEN_SCRIPT%" (
    echo   [ERROR] Failed to create script.
    exit /b 1
)

schtasks /create /tn "%TASK_NAME%" /tr "\"%OPEN_SCRIPT%\"" /sc once /sd %TODAY% /st 17:55 /f >nul 2>&1

if %errorLevel% equ 0 (
    echo   SUCCESS.
) else (
    echo   FAILED ^(current time may be past 17:55^).
)
exit /b

:: =================================================
:: 场景C：17:58 无条件关机（无弹框、无倒计时）
:: =================================================
:installShutdown
echo [Step 3/4] Scenario C: shutdown at 17:58 (unconditional)...

for /f %%a in ('powershell -NoProfile -Command Get-Date -Format yyyy-MM-dd') do set "TODAY=%%a"

schtasks /create /tn "OneTimeShutdown" /tr "shutdown /s /f /t 120" /sc once /sd %TODAY% /st 17:58 /f >nul 2>&1

if %errorLevel% equ 0 (
    echo   SUCCESS.
) else (
    echo   FAILED ^(current time may be past 17:58^).
)
exit /b

:: =================================================
:: 子程序：无条件打开三个飞书文档
:: =================================================
:openFeishuDocs
echo [Step 4/4] Opening Feishu docs...

set "CHROME="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not defined CHROME if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not defined CHROME if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "CHROME=%LocalAppData%\Google\Chrome\Application\chrome.exe"

if not defined CHROME (
  for /f "tokens=2,*" %%A in ('reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe" /ve 2^>nul ^| find "REG_"') do set "CHROME=%%A"
)
if not defined CHROME (
  for /f "tokens=2,*" %%A in ('reg query "HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe" /ve 2^>nul ^| find "REG_"') do set "CHROME=%%A"
)

set "DOC1=https://dej4esdop1.feishu.cn/wiki/WEpJwuaZditGJAkDJG6cGB4knQQ"
set "DOC2=https://dej4esdop1.feishu.cn/sheets/shtcnj4YBiK5miobFJmXzf6wNcf?sheet=VE8X5A"
set "DOC3=https://dej4esdop1.feishu.cn/wiki/RXTcw0iDXiYKDyk6yZZcVIN1nid?table=tbl8UVBA5oiQDvIX&view=vewH6RqXnd"

if not defined CHROME (
  echo   Chrome not found, using default browser.
  start "" "%DOC1%"
  start "" "%DOC2%"
  start "" "%DOC3%"
  exit /b
)

start "" "%CHROME%" --new-window "%DOC1%" "%DOC2%" "%DOC3%"
echo   Three Feishu docs opened in Chrome.
exit /b
