@echo off
chcp 65001 > nul
cd /d "%~dp0"

rem ---- 파이썬 찾기 (py 런처 우선) ----
set PY=
py -3 --version > nul 2>&1 && set PY=py -3
if not defined PY (
  python --version > nul 2>&1 && set PY=python
)
if not defined PY (
  echo [오류] 파이썬이 설치되어 있지 않습니다.
  echo https://www.python.org/downloads/ 에서 설치한 뒤 다시 실행해 주세요.
  echo 설치할 때 "Add python.exe to PATH" 를 꼭 체크하세요.
  pause
  exit /b 1
)

rem ---- 처음 한 번만 설치 (중간에 실패하면 다음에 다시 설치) ----
if not exist ".venv\installed.txt" (
  echo 처음 실행: 필요한 프로그램을 설치합니다. 몇 분 걸릴 수 있습니다...
  if not exist .venv %PY% -m venv .venv
  .venv\Scripts\python -m pip install --upgrade pip
  .venv\Scripts\python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [오류] 설치에 실패했습니다. 인터넷 연결을 확인하고 다시 실행해 주세요.
    pause
    exit /b 1
  )
  echo ok> ".venv\installed.txt"
)

echo 프로그램을 시작합니다. 잠시 후 브라우저가 열립니다.
echo 사용하는 동안 이 검은 창을 닫지 마세요. (닫으면 프로그램이 종료됩니다)
start "" cmd /c "timeout /t 4 > nul & start http://localhost:8501"
.venv\Scripts\python -m streamlit run app.py --server.port 8501
pause
