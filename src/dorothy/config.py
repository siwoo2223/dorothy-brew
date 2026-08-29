"""설정 로딩.

원칙: 전략 파라미터는 YAML(git 추적), API 키는 환경변수(git 추적 금지).
키가 설정 파일에 섞이는 순간 실수로 커밋되므로 구조적으로 분리한다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ExchangeConfig:
    name: str = "bitget"
    symbol: str = "BTC/USDT:USDT"   # ccxt 무기한선물 표기
    timeframe: str = "5m"
    leverage: float = 2.0
    margin_mode: str = "isolated"   # isolated 권장: 한 종목 사고가 계좌 전체로 번지지 않는다
    taker_fee: float = 0.0006       # 0.06% — 백테스트에 반드시 반영
    maker_fee: float = 0.0002
    slippage: float = 0.0005        # 시장가 체결 밀림 가정치
    # 거래소 주문 제약. 반영하지 않으면 소액 계좌 백테스트가 거짓말이 된다
    # (실제로는 나가지도 못할 주문을 체결시킨다).
    # live 모드에서는 거래소가 알려주는 실제 값으로 덮어쓴다.
    min_order_size: float = 0.0001  # Bitget BTCUSDT 무기한 기준
    size_step: float = 0.0001       # 수량 단위 (이 배수만 주문 가능)
    # 무기한 선물은 8시간마다 펀딩비가 오간다. 포지션을 하루 이상 들고 가는
    # 전략에서는 왕복 수수료에 맞먹는 비용이 된다. 반영하지 않으면
    # 백테스트가 낙관적으로 나온다.
    # 양수면 롱이 숏에게 지불한다. 추세장에서는 0.05%~0.1%까지 치솟기도 한다.
    funding_rate: float = 0.0001    # 8시간당 0.01% (평상시 근사치)
    funding_interval_hours: int = 8


@dataclass
class RiskConfig:
    risk_per_trade: float = 0.01        # 1회 매매에서 감수할 자본 비율 (1%)
    max_position_pct: float = 0.30      # 명목가 상한 (자본 대비)
    max_daily_loss_pct: float = 0.03    # 일일 누적 손실 한도 → 초과 시 당일 매매 중단
    max_consecutive_losses: int = 4     # 연속 손실 시 자동 정지
    # 고점 대비 낙폭 한도. 0이면 끔.
    # 일일 한도는 하루 단위라 저빈도 전략(며칠에 한 번 매매)에서는
    # 걸릴 기회가 없다. 이건 날짜와 무관하게 걸린다.
    max_drawdown_pct: float = 0.0
    max_open_positions: int = 1
    max_leverage: float = 3.0           # 설정값이 이보다 크면 여기로 깎는다


@dataclass
class StrategyConfig:
    name: str = "ema_cross"
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class NotifyConfig:
    telegram_enabled: bool = False
    # 토큰/챗ID는 환경변수에서만 읽는다


@dataclass
class Config:
    mode: str = "paper"                 # backtest | paper | live
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    initial_equity: float = 1000.0      # 백테스트/페이퍼 시작 자본
    poll_interval_sec: int = 15
    db_path: str = "data/dorothy.db"
    kill_switch_file: str = "KILL"      # 이 파일이 생기면 즉시 신규 진입 중단

    # --- 비밀값: 환경변수 전용 ---
    @property
    def api_key(self) -> str:
        return os.environ.get("BITGET_API_KEY", "")

    @property
    def api_secret(self) -> str:
        return os.environ.get("BITGET_API_SECRET", "")

    @property
    def api_password(self) -> str:
        """Bitget은 API 생성 시 passphrase를 요구한다."""
        return os.environ.get("BITGET_API_PASSWORD", "")

    @property
    def telegram_token(self) -> str:
        return os.environ.get("TELEGRAM_BOT_TOKEN", "")

    @property
    def telegram_chat_id(self) -> str:
        return os.environ.get("TELEGRAM_CHAT_ID", "")

    def validate(self) -> list[str]:
        """치명적 설정 오류를 미리 잡는다. 반환값이 비어 있어야 정상."""
        errors: list[str] = []
        if not 0 < self.risk.risk_per_trade <= 0.05:
            errors.append("risk_per_trade는 0 초과 0.05 이하여야 합니다 (5% 넘으면 파산 위험).")
        if self.exchange.leverage > self.risk.max_leverage:
            errors.append(
                f"leverage({self.exchange.leverage})가 max_leverage({self.risk.max_leverage})를 초과합니다."
            )
        if self.mode == "live" and not (self.api_key and self.api_secret and self.api_password):
            errors.append("live 모드에는 BITGET_API_KEY/SECRET/PASSWORD 환경변수가 모두 필요합니다.")
        if self.mode not in ("backtest", "paper", "live"):
            errors.append(f"알 수 없는 mode: {self.mode}")
        return errors


def _merge(dc: Any, data: dict[str, Any]) -> None:
    """dataclass 인스턴스에 dict 값을 얕게 덮어쓴다."""
    for key, value in data.items():
        if not hasattr(dc, key):
            raise ValueError(f"알 수 없는 설정 키: {key}")
        current = getattr(dc, key)
        if hasattr(current, "__dataclass_fields__") and isinstance(value, dict):
            _merge(current, value)
        else:
            setattr(dc, key, value)


def load_config(path: str | Path | None = None) -> Config:
    """YAML 설정을 읽어 Config를 만든다. 파일이 없으면 기본값을 쓴다."""
    cfg = Config()
    if path is None:
        return cfg
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"설정 파일이 없습니다: {p}")

    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise ImportError("PyYAML이 필요합니다: pip install pyyaml") from exc

    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    _merge(cfg, data)
    return cfg
