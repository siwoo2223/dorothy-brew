"""로깅 설정. 콘솔 + 회전 파일 로그.

매매 봇에서 로그는 사후 원인 규명의 유일한 증거다. 파일로 반드시 남긴다.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path


def setup(level: str = "INFO", log_file: str | Path = "logs/dorothy.log") -> None:
    p = Path(log_file)
    p.parent.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)-28s %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    file_handler = logging.handlers.RotatingFileHandler(
        p, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("ccxt").setLevel(logging.WARNING)
