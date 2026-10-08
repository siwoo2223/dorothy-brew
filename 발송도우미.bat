@echo off
chcp 65001 > nul
cd /d "%~dp0"
title KF 카톡 발송 도우미

rem ---- 처음이면 실행.bat 과 같은 방법으로 필요한 프로그램을 설치 ----
set PY=
py -3 --version > nul 2>&1 && set PY=py -3
if not defined PY (
  python --version > nul 2>&1 && set PY=python
)
set NEED_INSTALL=1
if exist ".venv\installed.txt" fc /b requirements.txt ".venv\installed.txt" > nul && set NEED_INSTALL=
if defined NEED_INSTALL (
  if not defined PY (
    echo [오류] 파이썬이 설치되어 있지 않습니다. https://www.python.org/downloads/ 에서 설치해 주세요.
    pause
    exit /b 1
  )
  echo 필요한 프로그램을 설치합니다. 몇 분 걸릴 수 있습니다...
  if not exist .venv %PY% -m venv .venv
  .venv\Scripts\python -m pip install --upgrade pip
  .venv\Scripts\python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [오류] 설치에 실패했습니다. 인터넷 연결을 확인하고 다시 실행해 주세요.
    pause
    exit /b 1
  )
  copy /y requirements.txt ".venv\installed.txt" > nul
)

echo ============================================================
echo  KF 카톡 발송 도우미 - 관리자 페이지에서 만든 발송을 이 PC 카톡으로 보냅니다.
echo  이 창을 닫으면 발송이 멈춥니다. (최소화해 두세요)
echo ============================================================
:loop
.venv\Scripts\python agent.py
rem 도우미가 예기치 않게 꺼지면 30초 뒤 다시 켠다 (연결 키 오류로 끝난 경우는 창을 닫으면 됨)
echo 30초 뒤 다시 시작합니다... (끄려면 이 창을 닫으세요)
timeout /t 30 > nul
goto loop
