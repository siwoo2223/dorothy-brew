"""사무실 PC 발송 도우미 (발송도우미.bat 으로 실행)

kflogistics 관리자 페이지 > 카톡 발송 에서 만든 발송을 가져와 이 PC 의 카카오톡으로 보낸다.
켜 두기만 하면 된다. 끄려면 이 창을 닫는다. 보내는 중에 ESC 를 1초 누르고 있으면 멈춘다.
"""
from __future__ import annotations

import datetime as dt
import os
import platform
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from receivables import control, envfile, lalamove, self_update, user_activity  # noqa: E402
from receivables.site_agent import Agent, SiteAPI, SiteError, windows_extract_names, windows_sender  # noqa: E402

VERSION = "2026-10-10b"
UPDATE_CHECK_SECONDS = 3600  # 새 버전 확인 간격
LALAMOVE_CHECK_SECONDS = 900  # 라라무브 배송 완료 확인 간격(15분)
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


DEFAULT_SITE = "https://kflogistics-group.com"
TOKEN_RE = re.compile(r"^[0-9a-f]{32,64}$", re.I)


def looks_like_site(url: str) -> bool:
    """사이트 주소처럼 생겼는지(점이 있는 도메인). 연결 키를 주소 칸에 넣는 실수를 잡는다."""
    host = re.sub(r"^https?://", "", url.strip(), flags=re.I).split("/")[0]
    return "." in host and " " not in host and not TOKEN_RE.match(host)


def setup() -> tuple[str, str]:
    url = os.getenv("KF_SITE_URL", "").strip()
    token = os.getenv("KF_AGENT_TOKEN", "").strip()
    # 2026-10-08 - 사이트 주소 칸에 연결 키를 넣어 '4ca0c5…' 라는 주소로 접속하려다 실패한 일이 있었다.
    # 주소와 키가 서로 바뀌어 저장돼 있으면 바로잡고, 주소가 이상하면 다시 묻는다.
    if TOKEN_RE.match(re.sub(r"^https?://", "", url, flags=re.I)) and not TOKEN_RE.match(token):
        token = token if token else re.sub(r"^https?://", "", url, flags=re.I)
        url = DEFAULT_SITE
        envfile.set_values(ROOT / ".env", {"KF_SITE_URL": url, "KF_AGENT_TOKEN": token})
        print("사이트 주소 칸에 연결 키가 들어가 있어 바로잡았습니다.")
    if url and token and looks_like_site(url):
        return url, token
    print("=" * 60)
    print(" 처음 설정: 관리자 페이지 > 카톡 발송 > 설정 에 있는 값을 넣어 주세요.")
    print("=" * 60)
    while True:
        url = ask(f"① 사이트 주소 (그냥 Enter 를 누르면 {DEFAULT_SITE}) : ") or DEFAULT_SITE
        if TOKEN_RE.match(url):
            print("   ↳ 그건 연결 키예요. 연결 키는 다음 칸에 넣고, 여기는 그냥 Enter 를 누르세요.")
            token = url
            continue
        if looks_like_site(url):
            break
        print("   ↳ 사이트 주소가 아닌 것 같아요. 예: https://kflogistics-group.com")
    while True:
        typed = ask("② 연결 키 (관리자 페이지 > 카톡 발송 > 설정 > 키 보기) : ") or token
        if TOKEN_RE.match(typed.strip()):
            token = typed.strip()
            break
        print("   ↳ 연결 키는 영어·숫자로 된 긴 값입니다. 화면의 키를 그대로 복사해 붙여 넣으세요.")
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
        elif "getaddrinfo" in str(exc) or "NameResolution" in str(exc) or "찾지 못했습니다" in str(exc):
            envfile.set_values(ROOT / ".env", {"KF_SITE_URL": ""})
            print("사이트 주소가 잘못된 것 같습니다. 다음에 실행하면 사이트 주소를 다시 물어봅니다.")
        ask("Enter 를 누르면 닫힙니다.")
        return 1

    if sys.platform != "win32":
        log("이 도우미는 카카오톡이 설치된 Windows PC 에서만 보낼 수 있습니다.")
        return 1

    agent = Agent(api, windows_sender, log=log, local_stop=control.escape_held, extract_names=windows_extract_names,
                  idle_seconds=user_activity.idle_seconds, input_tick=user_activity.last_input_tick)
    log(f"사이트 {url} 에 연결됐습니다. {POLL_SECONDS}초마다 보낼 메시지를 확인합니다. (끄려면 이 창을 닫으세요)")
    log("PC 를 쓰는 중에는 기다렸다가, 마우스·키보드를 잠시(기본 60초) 안 쓰면 보냅니다. 보내는 중에 손을 대면 바로 멈춥니다.")
    errors = 0
    next_update_check = time.time() + 120  # 켠 직후 2분 뒤 한 번, 그 뒤로 1시간마다
    next_lalamove_check = time.time() + 60
    lalamove_warned = False
    while True:
        try:
            control.clear_stop()
            ver = self_update.current_version(ROOT)[:7] or "?"
            n = agent.tick(f"{platform.node()} · 도우미 {VERSION} ({ver})")
            errors = 0
            if n:
                log("이번 묶음을 끝냈습니다.")
                continue  # 남은 것이 있을 수 있으니 바로 다시 확인
            # 라라무브 배송 완료 확인(보내는 일이 없을 때, 15분마다)
            if agent.lalamove_check and time.time() >= next_lalamove_check:
                next_lalamove_check = time.time() + LALAMOVE_CHECK_SECONDS
                try:
                    agent.check_lalamove(lambda links: lalamove.read_pages(links, log))
                except ImportError:
                    if not lalamove_warned:
                        log("라라무브 확인에 필요한 프로그램(playwright)이 아직 없습니다. 다음 업데이트 때 설치됩니다.")
                        lalamove_warned = True
                except Exception as exc:
                    log(f"라라무브 확인 실패(다음에 다시 시도): {exc}")
            # 보내는 일이 없을 때만 업데이트한다
            if agent.update_requested or (agent.auto_update and time.time() >= next_update_check):
                requested, agent.update_requested = agent.update_requested, False
                next_update_check = time.time() + UPDATE_CHECK_SECONDS
                try:
                    if requested:
                        log("관리자 페이지에서 업데이트를 요청했습니다. 새 버전을 확인합니다...")
                    if self_update.update(ROOT, log):
                        return self_update.RESTART_CODE  # 발송도우미.bat 이 바로 다시 켠다
                    if requested:
                        log("이미 최신 버전입니다.")
                except Exception as exc:  # 업데이트 실패해도 발송은 계속
                    log(f"업데이트 확인 실패(다음에 다시 시도): {exc}")
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
