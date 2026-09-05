"""박스 매매 — 하단에서 사고 상단에서 판다.

**왜 이 전략인가**
이 저장소에서 지표 조합으로 방향을 맞히려는 시도는 202개 중 0개가 통과했다.
박스 매매는 방향을 맞히는 것이 아니라 **경계를 맞히는 것**이다.
목표가 박스 높이(1시간봉 30봉 기준 중앙값 4.2%)라 수수료 0.22%가
목표의 5%밖에 안 된다 — ATR 손절/익절 구조에서 수수료가 목표의 상당 부분을
차지하던 것과 다르다.

**손절이 논리적으로 필요한 자리**
박스 매매의 전제는 "경계가 유지된다"이다. 그 전제가 깨지는 순간이
박스 이탈이고, 그때가 손절이다. 손절폭을 ATR로 잡지 않고
**박스 하단 바로 아래**로 잡는 이유가 그것이다 — 손절 근거가
전략의 전제와 같아야 한다.

**한계 (미리 적어둔다)**
- 박스는 언젠가 깨진다. 깨질 때 크게 잃고 그 전까지 조금씩 번다.
  승률은 높고 손익비는 나쁜 전형적인 구조라, 승률만 보면 속는다.
- 이 전략이 통과하려면 겹침을 뺀 t가 2를 넘어야 한다. 아직 안 쟀다.
"""

from __future__ import annotations

from ..analysis.box import detect
from ..models import Action, Candle, Position, Side, Signal
from .base import Strategy, register
from .common import bounded


@register
class BoxRangeStrategy(Strategy):
    """박스 하단 매수 / 상단 매도."""

    name = "box"

    def __init__(
        self,
        lookback: int = 30,
        entry_zone: float = 0.20,
        exit_zone: float = 0.80,
        stop_buffer: float = 0.25,
        min_touches: int = 2,
        touch_zone: float = 0.15,
        min_height_pct: float = 0.012,
        max_variance_ratio: float = 1.05,
        allow_short: bool = False,
        analysis_window: int = 400,
    ) -> None:
        super().__init__(
            lookback=lookback, entry_zone=entry_zone, exit_zone=exit_zone,
            stop_buffer=stop_buffer, min_touches=min_touches,
            touch_zone=touch_zone, min_height_pct=min_height_pct,
            max_variance_ratio=max_variance_ratio, allow_short=allow_short,
            analysis_window=analysis_window,
        )
        if not 0.0 < entry_zone < exit_zone < 1.0:
            raise ValueError(
                f"0 < entry_zone({entry_zone}) < exit_zone({exit_zone}) < 1 이어야 합니다."
            )
        if stop_buffer <= 0:
            raise ValueError("stop_buffer는 0보다 커야 합니다 (손절이 경계와 같으면 즉시 체결).")
        if lookback < 4:
            raise ValueError("lookback은 4 이상이어야 합니다.")
        self.lookback = lookback
        self.entry_zone = entry_zone
        self.exit_zone = exit_zone
        self.stop_buffer = stop_buffer
        self.min_touches = min_touches
        self.touch_zone = touch_zone
        self.min_height_pct = min_height_pct
        self.max_variance_ratio = max_variance_ratio
        self.allow_short = allow_short
        self.analysis_window = analysis_window

    @property
    def warmup(self) -> int:
        return self.lookback + 2

    def _box(self, candles: list[Candle]):
        return detect(
            bounded(candles, self.analysis_window),
            lookback=self.lookback,
            min_touches=self.min_touches,
            touch_zone=self.touch_zone,
            min_height_pct=self.min_height_pct,
            max_variance_ratio=self.max_variance_ratio,
        )

    def generate(self, candles: list[Candle], position: Position | None) -> Signal:
        if len(candles) < self.warmup:
            return Signal(Action.HOLD, "워밍업 부족")

        box = self._box(candles)
        price = candles[-1].close

        if position is not None:
            if box is None:
                # 박스가 사라졌다 = 전제가 깨졌다. 목표를 기다릴 이유가 없다.
                return Signal(Action.EXIT, "박스 소멸")
            where = box.position_of(price)
            if position.side is Side.LONG and where >= self.exit_zone:
                return Signal(Action.EXIT, f"박스 상단 도달 ({where:.0%})")
            if position.side is Side.SHORT and where <= 1.0 - self.exit_zone:
                return Signal(Action.EXIT, f"박스 하단 도달 ({where:.0%})")
            return Signal(Action.HOLD, "박스 안 보유")

        if box is None:
            return Signal(Action.HOLD, "박스 없음")

        where = box.position_of(price)
        # 박스 밖이면 들어가지 않는다 — 이탈했는데 되돌림을 노리는 것은
        # 박스 매매가 아니라 다른 전략이다.
        if not 0.0 <= where <= 1.0:
            return Signal(Action.HOLD, f"박스 이탈 ({where:.0%})")

        buffer = box.height * self.stop_buffer
        if where <= self.entry_zone:
            return Signal(
                Action.ENTER_LONG,
                f"박스 하단 ({where:.0%}, 폭 {box.height_pct:.2%}, VR {box.variance_ratio:.2f})",
                stop_loss=box.lower - buffer,
                take_profit=box.upper,
                meta={"box_lower": box.lower, "box_upper": box.upper,
                      "box_height_pct": box.height_pct},
            )
        if self.allow_short and where >= 1.0 - self.entry_zone:
            return Signal(
                Action.ENTER_SHORT,
                f"박스 상단 ({where:.0%}, 폭 {box.height_pct:.2%}, VR {box.variance_ratio:.2f})",
                stop_loss=box.upper + buffer,
                take_profit=box.lower,
                meta={"box_lower": box.lower, "box_upper": box.upper,
                      "box_height_pct": box.height_pct},
            )
        return Signal(Action.HOLD, f"박스 중간 ({where:.0%})")
