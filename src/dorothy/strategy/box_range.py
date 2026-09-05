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

**측정 결과 — 통하지 않는다 (1시간봉 8.6년)**

    익절(박스 상단)  26.1%   평균 +4.531%
    손절(박스 이탈)  61.4%   평균 -2.288%
    시간 만료        12.4%   평균 +1.752%
    ────────────────────────────────────
    수수료 전 총평균           -0.0031%

**박스는 유지되기보다 깨지는 쪽이 훨씬 많다**(61%). 다만 손익비(약 2.0)가
그것을 정확히 상쇄해서 총합이 0이다. 손절폭을 0.10에서 1.00까지 바꿔봐도
승률과 손익비가 자리를 바꿀 뿐 기대값은 0 근처에 붙어 있다:

    손절버퍼 0.10 → 승률 18.8% 평균 -0.008%   (t=-0.07)
             0.25 →      26.1%      -0.003%   (t=-0.02)
             0.50 →      34.0%      +0.228%   (t= 1.27)
             1.00 →      37.6%      +0.053%   (t= 0.26)

**이건 경계에 예측 정보가 없다는 뜻이다.** 기하를 바꾸면 승률과 손익비는
움직이지만 기대값은 안 움직인다 — 마팅게일이 정확히 그렇게 행동한다.
수수료 전이 0이므로 메이커(0.09%)든 테이커(0.22%)든 순수익은 음수다.

작성 당시 "박스 매매는 승률이 높고 손익비가 나쁜 구조"라고 적었는데
**정반대였다**(승률 26%, 손익비 2.0). 예상으로 적은 성질은 재기 전까지
믿지 말아야 한다는 사례로 남긴다.

지우지 않고 남기는 이유: 박스 탐지기(analysis/box.py)는 국면 판정에
그대로 쓸 수 있고, 이 측정이 "박스 경계"라는 흔한 아이디어를 다시
파지 않게 해준다.
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
    retired = (
        "1시간봉 8.6년 788거래에서 수수료 전 기대값이 -0.0031%로 0이다. "
        "손절폭을 0.10~1.00으로 바꿔도 승률(18.8%~37.6%)과 손익비만 맞바뀌고 "
        "기대값은 안 움직인다 — 경계에 예측 정보가 없다. "
        "겹침 제거 757~1,369거래에서 메이커 0.09% 기준 1회 -0.056%~-0.149%"
    )

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
