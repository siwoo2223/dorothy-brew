@echo off
chcp 65001 > nul
cd /d "%~dp0"
if not exist .venv (
  echo 처음 실행: 필요한 프로그램을 설치합니다. 몇 분 걸릴 수 있습니다...
  python -m venv .venv
  .venv\Scripts\pip install -r requirements.txt
)
.venv\Scripts\streamlit run app.py
pause
