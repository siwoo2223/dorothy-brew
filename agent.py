"""사무실 PC 발송 도우미 (발송도우미.bat 으로 실행)

kflogistics 관리자 페이지 > 카톡 발송 에서 만든 발송을 가져와 이 PC 의 카카오톡으로 보낸다.
켜 두기만 하면 된다. 끄려면 이 창을 닫는다. 보내는 중에 ESC 를 1초 누르고 있으면 멈춘다.
"""
from __future__ import annotations

import datetime as dt
import os
import platform
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from receivables import control, envfile  # noqa: E402
from receivables.site_agent import Agent, SiteAPI, SiteError, windows_extract_names, windows_sender  # noqa: E402

VERSION = "2026-10-08"
POLL_SECONDS = 20
LOG_FILE = ROOT / "logs" / "agent.log"
STARTUP_NAME = "KF카톡발송도우미.bat"


def log(text: str) -> None:
    line = f"[{dt.datetime.now():%m-%d %H:%M:%S}] {text}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def setup() -> tuple[str, str]:
    url = os.getenv("KF_SITE_URL", "").strip()
    token = os.getenv("KF_AGENT_TOKEN", "").strip()
    if url and token:
        return url, token
    print("=" * 60)
    print(" 처음 설정: 관리자 페이지 > 카톡 발송 > 설정 에 있는 값을 넣어 주세요.")
    print("=" * 60)
    url = ask("사이트 주소 (예: https://kflogistics-group.com) : ") or "https://kflogistics-group.com"
    token = ask("연결 키 : ")
    envfile.set_values(ROOT / ".env", {"KF_SITE_URL": url, "KF_AGENT_TOKEN": token})
    if sys.platform == "win32" and ask("PC를 켤 때 발송 도우미를 자동으로 실행할까요? (Y/N) : ").lower().startswith("y"):
        install_startup()
    return url, token


def install_startup() -> None:
    folder = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / STARTUP_NAME).write_text(
        f'@echo off\r\ncd /d "{ROOT}"\r\nstart "KF 카톡 발송 도우미" /min cmd /c "{ROOT / "발송도우미.bat"}"\r\n',
        encoding="cp949", errors="replace")
    print(f"자동 실행을 켰습니다: {folder / STARTUP_NAME}")


def main(argv: list[str]) -> int:
    load_dotenv(ROOT / ".env")
    if "--startup" in argv:
        install_startup()
        return 0
    url, token = setup()
    try:
        api = SiteAPI(url, token)
        api.pull(f"{platform.node()} · 도우미 {VERSION} · 시작")
    except (ValueError, SiteError) as exc:
        log(f"연결 실패: {exc}")
        if "연결 키" in str(exc):
            envfile.set_values(ROOT / ".env", {"KF_AGENT_TOKEN": ""})
            print("다음에 실행하면 연결 키를 다시 물어봅니다.")
        ask("Enter 를 누르면 닫힙니다.")
        return 1

    if sys.platform != "win32":
        log("이 도우미는 카카오톡이 설치된 Windows PC 에서만 보낼 수 있습니다.")
        return 1

    agent = Agent(api, windows_sender, log=log, local_stop=control.escape_held, extract_names=windows_extract_names)
    log(f"사이트 {url} 에 연결됐습니다. {POLL_SECONDS}초마다 보낼 메시지를 확인합니다. (끄려면 이 창을 닫으세요)")
    log("보내는 동안 이 PC 의 마우스·키보드를 쓰지 마세요. 멈추려면 ESC 를 1초 누르고 있거나 관리자 페이지의 '발송 중지'.")
    errors = 0
    while True:
        try:
            control.clear_stop()
            n = agent.tick(f"{platform.node()} · 도우미 {VERSION}")
            errors = 0
            if n:
                log("이번 묶음을 끝냈습니다.")
                continue  # 남은 것이 있을 수 있으니 바로 다시 확인
        except SiteError as exc:
            errors += 1
            if errors in (1, 5) or errors % 30 == 0:
                log(f"사이트 연결 문제(계속 다시 시도합니다): {exc}")
        except KeyboardInterrupt:
            return 0
        except Exception as exc:  # 도우미가 꺼지지 않게: 기록만 하고 계속
            log(f"오류: {type(exc).__name__}: {exc}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
