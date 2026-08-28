"""실행 전 점검 — 뭐가 빠졌는지 먼저 알려준다.

설명서는 읽는 사람이 자기 환경과 대조해야 하지만, 이건 그냥 답을 준다.
실전 직전에 "왜 안 되지"로 시간을 쓰지 않게 하는 것이 목적이다.
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""
    fatal: bool = True      # False면 없어도 일부 기능만 못 쓴다


def _python() -> Check:
    major, minor = sys.version_info[:2]
    ok = (major, minor) >= (3, 10)
    return Check(
        "파이썬 3.10+", ok, f"{major}.{minor}.{sys.version_info[2]}",
        "python.org에서 3.10 이상을 설치하세요.",
    )


def _module(name: str, purpose: str, install: str, *, fatal: bool = True) -> Check:
    try:
        mod = importlib.import_module(name)
        version = getattr(mod, "__version__", "설치됨")
        return Check(f"{name} ({purpose})", True, str(version), fatal=fatal)
    except ImportError:
        return Check(f"{name} ({purpose})", False, "없음", f"pip install {install}",
                     fatal=fatal)


def _config(path: str | None) -> Check:
    if not path:
        return Check("설정 파일", False, "지정 안 함",
                     "--config config/config.yaml 로 지정하세요.")
    p = Path(path)
    if not p.exists():
        return Check("설정 파일", False, f"{path} 없음",
                     "cp config/donchian12.example.yaml config/config.yaml")
    try:
        from .config import load_config

        cfg = load_config(path)
        return Check(
            "설정 파일", True,
            f"{cfg.mode} · {cfg.exchange.symbol} · {cfg.exchange.timeframe}"
            f" · {cfg.strategy.name}",
        )
    except Exception as exc:      # noqa: BLE001
        return Check("설정 파일", False, f"{type(exc).__name__}: {exc}",
                     "YAML 문법과 키 이름을 확인하세요.")


def _secrets(mode: str) -> Check:
    from .config import Config

    cfg = Config()
    has_key = bool(cfg.api_key and cfg.api_secret and cfg.api_passphrase)
    if mode != "live":
        return Check("API 키", True,
                     "불필요 (백테스트·페이퍼는 공개 시세만 씁니다)", fatal=False)
    if has_key:
        return Check("API 키", True, "환경변수에서 읽음")
    return Check(
        "API 키", False, "없음",
        ".env에 BITGET_API_KEY / BITGET_API_SECRET / BITGET_API_PASSPHRASE를 넣으세요. "
        "출금 권한은 반드시 끄고, IP 화이트리스트를 거세요.",
    )


def _exchange_reachable(symbol: str, timeframe: str) -> Check:
    """실제로 시세가 오는지. 여기까지 되면 페이퍼는 돌아간다."""
    try:
        from .exchange.bitget import BitgetExchange
    except ImportError as exc:
        return Check("거래소 연결", False, str(exc), "pip install ccxt")

    try:
        ex = BitgetExchange("", "", "")
        candles = ex.fetch_candles(symbol, timeframe, limit=5)
        if not candles:
            return Check("거래소 연결", False, "캔들이 비어 있음",
                         "심볼 표기를 확인하세요 (예: BTC/USDT:USDT).")
        last = candles[-1]
        return Check("거래소 연결", True,
                     f"{len(candles)}봉 수신, 최근 종가 {last.close:,.2f}")
    except Exception as exc:      # noqa: BLE001
        return Check(
            "거래소 연결", False, f"{type(exc).__name__}",
            "인터넷 연결과 방화벽을 확인하세요. 회사망·VPN에서 막히는 경우가 많습니다.",
        )


def run(config_path: str | None = None, *, check_network: bool = True) -> list[Check]:
    checks = [
        _python(),
        _module("yaml", "설정 파일 읽기", "pyyaml"),
        _module("ccxt", "거래소 연결", "ccxt"),
        _module("websockets", "수집기", "websockets", fatal=False),
    ]
    config = _config(config_path)
    checks.append(config)

    mode, symbol, timeframe = "paper", "BTC/USDT:USDT", "12h"
    if config.ok and config_path:
        from .config import load_config

        cfg = load_config(config_path)
        mode, symbol, timeframe = cfg.mode, cfg.exchange.symbol, cfg.exchange.timeframe

    checks.append(_secrets(mode))
    if check_network:
        checks.append(_exchange_reachable(symbol, timeframe))
    return checks


def report(checks: list[Check]) -> str:
    lines = ["═" * 70, "  실행 전 점검", "═" * 70]
    for c in checks:
        mark = "✓" if c.ok else ("✗" if c.fatal else "○")
        lines.append(f"  {mark} {c.name:<28}{c.detail}")
        if not c.ok and c.fix:
            lines.append(f"      → {c.fix}")
    lines.append("═" * 70)

    blocking = [c for c in checks if not c.ok and c.fatal]
    optional = [c for c in checks if not c.ok and not c.fatal]
    if blocking:
        lines.append(f"  ✗ 해결해야 할 항목 {len(blocking)}개 — 위 화살표를 따라가세요.")
    else:
        lines.append("  ✓ 실행 준비가 됐습니다.")
    if optional:
        names = ", ".join(c.name.split(" (")[0] for c in optional)
        lines.append(f"  ○ 선택 항목이 빠져 있습니다: {names} (없어도 매매는 됩니다)")
    return "\n".join(lines)
