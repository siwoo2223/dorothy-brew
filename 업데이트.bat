@echo off
chcp 65001 > nul
setlocal

rem ===== 업데이트.bat : 최신 버전을 받아 이 폴더에 덮어쓰고 프로그램을 다시 켭니다 =====
rem 실행 중에 이 파일 자신도 새 버전으로 바뀌므로, 임시 폴더에 복사본을 만들어 그것으로 진행한다.
if not "%~1"=="--run" (
  copy /y "%~f0" "%TEMP%\dorothy-update.bat" > nul
  "%TEMP%\dorothy-update.bat" --run "%~dp0"
)

set "APPDIR=%~2"
set "URL=https://github.com/siwoo2223/dorothy-brew/archive/refs/heads/ccr-7ffe51b4-9hzurs.zip"
set "WORK=%TEMP%\dorothy-update"

echo.
echo  [1/4] 실행 중인 프로그램을 끕니다...
taskkill /FI "WINDOWTITLE eq 미수금 발송 프로그램*" /T /F > nul 2>&1
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8501" ^| findstr "LISTENING"') do taskkill /PID %%p /F > nul 2>&1
timeout /t 2 > nul

echo  [2/4] 최신 버전을 내려받는 중입니다...
if exist "%WORK%" rmdir /s /q "%WORK%"
mkdir "%WORK%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest -UseBasicParsing -Uri '%URL%' -OutFile '%WORK%\update.zip'; Expand-Archive -Force '%WORK%\update.zip' '%WORK%'"
if errorlevel 1 (
  echo.
  echo  [오류] 내려받지 못했습니다. 인터넷 연결을 확인하고 다시 실행해 주세요.
  pause
  exit
)
set "SRC="
for /d %%d in ("%WORK%\dorothy-brew-*") do set "SRC=%%d"
if not defined SRC (
  echo  [오류] 받은 파일이 올바르지 않습니다.
  pause
  exit
)

echo  [3/4] 이 폴더에 덮어씁니다. (설치된 프로그램 .venv, 발송 이력 logs, 첨부 attachments 는 그대로 둡니다)
robocopy "%SRC%" "%APPDIR%." /E /XD .venv logs attachments /NFL /NDL /NJH /NJS /NP > nul
if %errorlevel% GEQ 8 (
  echo  [오류] 파일을 덮어쓰지 못했습니다. 이 폴더의 파일이 열려 있으면 닫고 다시 실행해 주세요.
  pause
  exit
)
rmdir /s /q "%WORK%"

echo  [4/4] 업데이트 완료! 프로그램을 다시 켭니다.
echo.
cd /d "%APPDIR%"
call "%APPDIR%실행.bat"
exit
