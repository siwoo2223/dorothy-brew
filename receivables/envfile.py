""".env 파일의 값 몇 개를 고쳐 쓴다 (나머지 줄과 주석은 그대로 둔다)."""
from __future__ import annotations

import os
from pathlib import Path


def set_values(path: Path, values: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    left = dict(values)
    for i, line in enumerate(lines):
        name = line.split("=", 1)[0].strip()
        if "=" in line and not line.lstrip().startswith("#") and name in left:
            lines[i] = f"{name}={left.pop(name)}"
    lines += [f"{k}={v}" for k, v in left.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ.update(values)  # 껐다 켜지 않아도 바로 쓰도록
