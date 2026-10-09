"""발송 도우미 자동 업데이트.

2026-10-09 요청 - "지금 외부에 있어서 너가 업데이트 파일 실행하고 도우미켜줄수 있을까?" / "자동 업데이트 기능 넣어줘":
도우미가 스스로 GitHub 의 최신 버전을 확인해, 새 버전이 있으면 보내는 일이 없을 때 받아서 덮어쓰고 다시 켜진다.
관리자 페이지 > 카톡 발송 > 설정 의 「지금 업데이트」로 바로 시킬 수도 있다.

- 버전은 GitHub 브랜치의 마지막 커밋(sha)으로 구분하고, 이 폴더의 .version 에 적어 둔다.
- 덮어쓰지 않는 것: .venv(설치된 프로그램), logs, attachments, schedules, .env(연결 키),
  그리고 지금 돌고 있는 발송도우미.bat(실행 중인 배치 파일을 바꾸면 창이 꼬인다 - 업데이트.bat 으로만 바뀜).
- requirements.txt 가 바뀌었으면 필요한 프로그램(pip)도 설치한다.
"""
from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Callable

import requests

REPO = "siwoo2223/dorothy-brew"
BRANCH = "ccr-7ffe51b4-9hzurs"
ZIP_URL = f"https://codeload.github.com/{REPO}/zip/refs/heads/{BRANCH}"
SHA_URL = f"https://api.github.com/repos/{REPO}/commits/{BRANCH}"

SKIP_DIRS = {".venv", "logs", "attachments", "schedules", ".git", "__pycache__", ".pytest_cache"}
SKIP_FILES = {".env", ".version", "발송도우미.bat"}
RESTART_CODE = 3  # 발송도우미.bat 이 이 값으로 끝나면 기다리지 않고 바로 다시 켠다


def current_version(root: Path) -> str:
    try:
        return (root / ".version").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def latest_version(timeout: float = 20) -> str:
    r = requests.get(SHA_URL, headers={"Accept": "application/vnd.github.sha"}, timeout=timeout)
    r.raise_for_status()
    sha = r.text.strip()
    if len(sha) < 7 or not all(c in "0123456789abcdef" for c in sha.lower()):
        raise RuntimeError(f"버전 정보를 읽지 못했습니다: {sha[:40]}")
    return sha


def _skip(rel: Path) -> bool:
    return any(p in SKIP_DIRS for p in rel.parts[:-1]) or rel.parts[0] in SKIP_DIRS or rel.name in SKIP_FILES


def apply_zip(data: bytes, root: Path) -> list[str]:
    """받은 zip 을 root 에 덮어쓴다(내용이 다른 파일만). 반환: 바뀐 파일 목록."""
    changed: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf, tempfile.TemporaryDirectory() as tmp:
        zf.extractall(tmp)
        tops = [p for p in Path(tmp).iterdir() if p.is_dir()]
        if len(tops) != 1 or not (tops[0] / "agent.py").is_file():
            raise RuntimeError("받은 파일이 올바르지 않습니다")
        src = tops[0]
        for f in sorted(src.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(src)
            if _skip(rel):
                continue
            dst = root / rel
            new = f.read_bytes()
            try:
                if dst.read_bytes() == new:
                    continue
            except OSError:
                pass
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp_dst = dst.with_name(dst.name + ".updating")
            tmp_dst.write_bytes(new)
            tmp_dst.replace(dst)
            changed.append(rel.as_posix())
    return changed


def install_requirements(root: Path, log: Callable[[str], None]) -> bool:
    req = root / "requirements.txt"
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(req)],
                       cwd=root, capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        log("필요한 프로그램 설치 실패: " + (r.stderr or r.stdout)[-300:])
        return False
    marker = root / ".venv" / "installed.txt"  # 발송도우미.bat 이 다시 설치하지 않게
    if marker.parent.is_dir():
        shutil.copyfile(req, marker)
    return True


def update(root: Path, log: Callable[[str], None], latest: str | None = None) -> bool:
    """새 버전이 있으면 받아서 적용한다. 반환: 다시 켜야 하면 True."""
    latest = latest or latest_version()
    if latest == current_version(root):
        return False
    log(f"새 버전({latest[:7]})을 받는 중입니다...")
    r = requests.get(ZIP_URL, timeout=120)
    r.raise_for_status()
    old_req = (root / "requirements.txt").read_bytes() if (root / "requirements.txt").exists() else b""
    changed = apply_zip(r.content, root)
    if "requirements.txt" in changed and (root / "requirements.txt").read_bytes() != old_req:
        log("필요한 프로그램을 설치합니다(몇 분 걸릴 수 있음)...")
        install_requirements(root, log)
    (root / ".version").write_text(latest, encoding="utf-8")
    if changed:
        log(f"업데이트 완료: 파일 {len(changed)}개 바뀜 ({', '.join(changed[:5])}{' …' if len(changed) > 5 else ''}). 다시 시작합니다.")
        return True
    log("이미 최신 파일입니다.")
    return False
