"""안내·공지 문구(템플릿) 파일 저장소. templates/<종류>/<이름>.txt"""
from __future__ import annotations

import re
from pathlib import Path


def safe_name(name: str) -> str:
    """파일 이름으로 쓸 수 없는 글자를 빼고 앞뒤 공백·점을 정리한다."""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "", name or "").strip().strip(".")
    return name[:60]


def list_templates(folder: Path) -> list[str]:
    return sorted(p.stem for p in folder.glob("*.txt"))


def load_template(folder: Path, name: str) -> str:
    path = folder / f"{name}.txt"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def save_template(folder: Path, name: str, text: str, overwrite: bool = False) -> str:
    """저장한 이름을 돌려준다. 같은 이름이 있는데 overwrite=False 면 오류."""
    clean = safe_name(name)
    if not clean:
        raise ValueError("문구 이름을 입력해 주세요.")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{clean}.txt"
    if path.exists() and not overwrite:
        raise ValueError(f"'{clean}' 이름의 문구가 이미 있습니다. 다른 이름을 쓰거나 덮어쓰기를 해 주세요.")
    path.write_text(text, encoding="utf-8")
    return clean


def delete_template(folder: Path, name: str) -> None:
    (folder / f"{name}.txt").unlink(missing_ok=True)
