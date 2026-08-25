"""ICT 유동성 + 피보나치 OTE + 각도(변위) + 엘리엇 소프트 필터.

시나리오(롱 기준)는 이렇게 읽는다:

    1. 저점 아래 유동성을 쓸어간다 (스윕)      ← 손절 털기
    2. 곧바로 종가로 직전 스윙을 돌파한다 (CHoCH/BOS)  ← 방향 전환 확인
    3. 그 돌파 구간의 각도가 충분히 가파르다 (변위)     ← 진짜 힘인지 검증
    4. 되돌림이 OTE(0.62~0.79) 구간이나 FVG로 들어온다  ← 진입
    5. 손절은 스윕 극단 바깥 + ATR 버퍼               ← 같은 자리 재털림 방지
    6. 목표는 반대편 유동성 풀                        ← 가격이 가려는 곳

설계 원칙 세 가지:

**상태를 들지 않는다.** 매 호출마다 캔들에서 전체 상황을 다시 계산한다.
상태를 들면 재시작·재진입 시 동작이 달라지고 백테스트와 실전이 어긋난다.

**합류(confluence) 점수제.** 각 요소가 점수를 내고 합계가 기준을 넘어야 진입한다.
요소를 켜고 끄며 기여도를 측정할 수 있어야 하기 때문이다 (`dorothy ablate`).
필터를 무작정 쌓으면 거래가 사라지고 과최적화만 남는다.

**엘리엇은 감점 전용.** 리페인팅 측정 결과(카운트 변경률 ~20%)를 근거로,
'몇 파다'로 진입하지 않고 '5파 말미로 보이면 감점'으로만 쓴다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..analysis import elliott
from ..analysis.fibonacci import Leg, Zone, ote_zone
from ..analysis.liquidity import (
    Bias,
    PoolKind,
    StructureEvent,
    detect_sweep,
    find_fvgs,
    find_pools,
    market_structure,
    session_pools,
)
from ..analysis.slope import leg_angle
from ..analysis.swings import find_swings
from ..data.indicators import atr as atr_indicator
from ..models import Action, Candle, Position, Side, Signal
from .base import Strategy, register


@dataclass
class ScoreCard:
    """왜 진입했는지(혹은 안 했는지)를 남기는 진단 카드."""

    points: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def add(self, name: str, value: float, note: str = "") -> None:
        if value:
            self.points[name] = self.points.get(name, 0.0) + value
        if note:
            self.notes.append(note)

    @property
    def total(self) -> float:
        return sum(self.points.values())

    def summary(self) -> str:
        parts = [f"{k} {v:+.1f}" for k, v in self.points.items()]
        return f"[{self.total:+.1f}] " + " · ".join(parts)


@register
class IctConfluenceStrategy(Strategy):
    name = "ict_confluence"

    def __init__(
        self,
        # --- 스윙 검출 ---
        swing_left: int = 2,
        swing_right: int = 2,
        swing_min_atr: float = 0.5,
        atr_period: int = 14,
        # --- 유동성 ---
        pool_tolerance: float = 0.15,      # 등가 레벨로 묶는 허용오차 (ATR 배수)
        sweep_lookback: int = 12,          # 스윕을 몇 봉 전까지 유효로 볼지
        sweep_min_penetration: float = 0.15,
        pool_max_age: int = 150,           # 이보다 오래된 풀은 무시 (묵은 레벨은 노이즈)
        pool_max_distance: float = 6.0,    # 현재가에서 이보다 먼 풀은 무시 (ATR 배수)
        min_displacement_bars: int = 2,    # 스윕 후 변위가 만들어질 최소 봉 수
        session_lookback: int = 0,         # >0이면 직전 구간 고저를 풀에 추가
        # --- 각도(변위) ---
        min_displacement_angle: float = 30.0,   # ATR 정규화 각도. 45도 = 1봉당 1 ATR
        min_r_squared: float = 0.0,
        # --- 진입 구간 ---
        entry_tolerance: float = 0.25,     # 고점·저점/레벨 허용오차 (ATR 배수)
        use_fvg: bool = True,
        # --- 손절/목표 ---
        stop_buffer_atr: float = 0.5,      # 스윕 극단 바깥으로 얼마나 더 뺄지
        min_rr: float = 1.5,               # 최소 손익비. 못 넘기면 진입하지 않는다
        target_extension: float = 1.618,
        # --- 합류 점수 ---
        min_score: float = 3.0,
        analysis_window: int = 400,        # 분석에 쓸 최근 봉 수 (아래 주석 참고)
        elliott_enabled: bool = True,
        elliott_penalty: float = 1.0,
        # --- 청산 ---
        exit_on_opposite_choch: bool = True,
        allow_short: bool = True,
    ) -> None:
        super().__init__(
            swing_left=swing_left, swing_right=swing_right, swing_min_atr=swing_min_atr,
            atr_period=atr_period, pool_tolerance=pool_tolerance,
            sweep_lookback=sweep_lookback, sweep_min_penetration=sweep_min_penetration,
            pool_max_age=pool_max_age, pool_max_distance=pool_max_distance,
            min_displacement_bars=min_displacement_bars, session_lookback=session_lookback, min_displacement_angle=min_displacement_angle,
            min_r_squared=min_r_squared, entry_tolerance=entry_tolerance, use_fvg=use_fvg,
            stop_buffer_atr=stop_buffer_atr, min_rr=min_rr, target_extension=target_extension,
            min_score=min_score, analysis_window=analysis_window,
            elliott_enabled=elliott_enabled,
            elliott_penalty=elliott_penalty, exit_on_opposite_choch=exit_on_opposite_choch,
            allow_short=allow_short,
        )
        self.swing_left = swing_left
        self.swing_right = swing_right
        self.swing_min_atr = swing_min_atr
        self.atr_period = atr_period
        self.pool_tolerance = pool_tolerance
        self.sweep_lookback = sweep_lookback
        self.sweep_min_penetration = sweep_min_penetration
        self.pool_max_age = pool_max_age
        self.pool_max_distance = pool_max_distance
        self.min_displacement_bars = min_displacement_bars
        self.session_lookback = session_lookback
        self.min_displacement_angle = min_displacement_angle
        self.min_r_squared = min_r_squared
        self.entry_tolerance = entry_tolerance
        self.use_fvg = use_fvg
        self.stop_buffer_atr = stop_buffer_atr
        self.min_rr = min_rr
        self.target_extension = target_extension
        self.min_score = min_score
        self.analysis_window = analysis_window
        self.elliott_enabled = elliott_enabled
        self.elliott_penalty = elliott_penalty
        self.exit_on_opposite_choch = exit_on_opposite_choch
        self.allow_short = allow_short
        self.last_card: ScoreCard | None = None   # 진단용. 판단에는 쓰지 않는다

    @property
    def warmup(self) -> int:
        base = max(self.atr_period * 3, 60, self.session_lookback * 2 + 5)
        return base + self.swing_right + 2

    # ------------------------------------------------------------------
    def generate(self, candles: list[Candle], position: Position | None) -> Signal:
        if len(candles) < self.warmup:
            return Signal(Action.HOLD, "워밍업 부족")

        # 최근 analysis_window 봉만 본다. 이유가 두 가지다:
        #
        # 1. 속도 — 매 봉마다 전체 히스토리에 스윙·FVG·구조를 재계산하면 O(n²)가 되어
        #    6,000봉 백테스트가 수 분씩 걸린다. 파라미터를 못 만지면 전략 개발이 멈춘다.
        # 2. 정합성 — 실전에서 거래소는 제한된 개수의 캔들만 준다(fetch_ohlcv limit).
        #    백테스트가 무한한 과거를 보면 실전과 결과가 달라진다. 조건을 맞추는 편이 옳다.
        offset = 0
        if len(candles) > self.analysis_window:
            offset = len(candles) - self.analysis_window
            candles = candles[offset:]

        i = len(candles) - 1
        atr_line = atr_indicator(
            [c.high for c in candles], [c.low for c in candles],
            [c.close for c in candles], self.atr_period,
        )
        atr = atr_line[i]
        if atr is None or atr <= 0:
            return Signal(Action.HOLD, "ATR 미계산")

        swings = find_swings(
            candles, left=self.swing_left, right=self.swing_right,
            atr_period=self.atr_period, min_atr_mult=self.swing_min_atr, as_of=i,
        )
        if len(swings) < 4:
            return Signal(Action.HOLD, "스윙 부족")

        if position is not None:
            return self._exit_signal(market_structure(candles, swings, upto=i), position)

        return self._entry_signal(candles, swings, atr=atr, index=i, offset=offset)

    # ------------------------------------------------------------------
    def _exit_signal(self, structure, position: Position) -> Signal:
        """반대 방향 구조 전환이 나오면 나간다.

        손절·익절은 거래소 스탑이 처리하므로 여기서는 '시나리오가 깨졌는가'만 본다.
        """
        if not self.exit_on_opposite_choch:
            return Signal(Action.HOLD, "포지션 유지")

        opposite = Bias.BEARISH if position.side is Side.LONG else Bias.BULLISH
        if structure.event is StructureEvent.CHOCH and structure.bias is opposite:
            return Signal(Action.EXIT, f"반대 CHoCH ({structure.broken_level:.2f} 이탈)")
        return Signal(Action.HOLD, "포지션 유지")

    # ------------------------------------------------------------------
    def _find_structure_event(self, candles, swings, *, start: int, end: int, direction: Bias):
        """스윕 이후 구간에서 방향이 맞는 구조 전환을 찾는다.

        구조 전환은 되돌림 진입보다 **몇 봉 앞서** 일어난다.
        현재 봉에서만 확인하면, 진입 시점(되돌림 중)에는 이미 돌파 상태가 아니라
        영영 신호가 나오지 않는다. 실제로 이 버그로 거래가 0건이었다.
        """
        latest = None
        for k in range(start, end + 1):
            st = market_structure(candles, swings, upto=k)
            if st.event is StructureEvent.NONE or st.bias is not direction:
                continue
            latest = (st, k)
        return latest

    def _entry_signal(
        self, candles: list[Candle], swings, *, atr: float, index: int, offset: int = 0
    ) -> Signal:
        card = ScoreCard()
        self.last_card = card

        # --- 1. 유동성 풀 구성 ---
        pools = find_pools(swings, atr=atr, tolerance_mult=self.pool_tolerance)
        if self.session_lookback > 0:
            pools += session_pools(candles, upto=index, lookback_bars=self.session_lookback)

        # 묵었거나 현재가에서 멀리 떨어진 풀은 걸러낸다.
        # 이걸 안 하면 수백 개 레벨 중 아무거나 걸려 '스윕'이 매 봉 발생한다.
        price_now = candles[index].close
        pools = [
            p for p in pools
            if index - p.last_index <= self.pool_max_age
            and abs(p.price - price_now) <= atr * self.pool_max_distance
        ]
        if not pools:
            return Signal(Action.HOLD, "유효 유동성 풀 없음")

        # --- 2. 최근 스윕 탐색 ---
        sweep = None
        # 스윕 당일에는 변위가 아직 없다. min_displacement_bars 만큼 지난 스윕부터 본다.
        newest = index - self.min_displacement_bars
        for j in range(newest, max(index - self.sweep_lookback, 0) - 1, -1):
            found = detect_sweep(
                candles, pools, index=j, atr=atr,
                min_penetration=self.sweep_min_penetration,
            )
            if found is not None:
                sweep = found
                break
        if sweep is None:
            return Signal(Action.HOLD, "최근 스윕 없음")

        direction = sweep.direction
        if direction is Bias.BEARISH and not self.allow_short:
            return Signal(Action.HOLD, "숏 비활성화")

        card.add("스윕", 1.0, f"{sweep.pool.label} 스윕 ({sweep.penetration:.2f} ATR 침투)")
        if sweep.pool.is_equal_level:
            # 등가 고점/저점은 손절이 더 두텁게 쌓인 자리다
            card.add("등가레벨", 0.5, f"{sweep.pool.label} 터치 {sweep.pool.touches}회")

        # --- 3. 구조 전환 확인 (스윕 이후 ~ 현재 구간에서 탐색) ---
        found = self._find_structure_event(
            candles, swings, start=sweep.index + 1, end=index, direction=direction
        )
        if found is None:
            return Signal(Action.HOLD, "스윕 후 구조 전환 없음")
        structure, structure_index = found

        if structure.event is StructureEvent.CHOCH:
            card.add("CHoCH", 1.5, f"{structure.broken_level:.2f} 종가 돌파(전환)")
        elif structure.event is StructureEvent.BOS:
            card.add("BOS", 1.0, f"{structure.broken_level:.2f} 종가 돌파(지속)")

        # --- 4. 변위(각도) 검증 — 스윕에서 구조 돌파까지의 구간을 잰다 ---
        angle = leg_angle(candles, sweep.index, structure_index, atr_period=self.atr_period)
        if angle is None:
            return Signal(Action.HOLD, "각도 계산 불가")
        signed = angle.degrees if direction is Bias.BULLISH else -angle.degrees
        if signed < self.min_displacement_angle:
            return Signal(
                Action.HOLD,
                f"변위 부족 ({signed:.1f}도 < {self.min_displacement_angle:.1f}도)",
            )
        if angle.r_squared < self.min_r_squared:
            return Signal(Action.HOLD, f"추세가 지저분함 (R²={angle.r_squared:.2f})")

        card.add("변위각", 1.0, f"{signed:.1f}도 (R²={angle.r_squared:.2f})")
        if angle.is_steep:
            card.add("급변위", 0.5, "1봉당 1 ATR 초과")

        # --- 5. 되돌림 진입 구간 (피보나치 OTE + FVG) ---
        leg = self._displacement_leg(candles, sweep, index, direction)
        if leg is None or leg.size < atr * 0.5:
            return Signal(Action.HOLD, "변위 구간이 너무 작음")

        zone = ote_zone(leg, atr=atr, tolerance_mult=self.entry_tolerance)
        current = candles[index]
        in_zone = zone.touched_by(current.low, current.high)
        if in_zone:
            card.add("OTE", 1.0, f"OTE {zone.low:.2f}~{zone.high:.2f} 진입")

        fvg_zone = None
        if self.use_fvg:
            fvg_zone = self._active_fvg(candles, atr=atr, index=index, direction=direction)
            if fvg_zone is not None and fvg_zone.touched_by(current.low, current.high):
                card.add("FVG", 1.0, f"FVG {fvg_zone.low:.2f}~{fvg_zone.high:.2f} 진입")
                in_zone = True

        if not in_zone:
            retrace = leg.retracement_of(current.close)
            return Signal(Action.HOLD, f"되돌림 대기 (현재 {retrace:.0%})")

        # --- 6. 엘리엇 소프트 필터 (감점 전용) ---
        if self.elliott_enabled:
            count = elliott.analyze(candles, swings, upto=index, atr_period=self.atr_period)
            if count.waves:
                if count.direction is not direction:
                    card.add("엘리엇", -self.elliott_penalty, f"카운트 방향 불일치({count.describe()})")
                elif count.is_terminal:
                    card.add("엘리엇", -self.elliott_penalty, f"추세 말미 추정({count.describe()})")
                elif count.is_impulsive_leg:
                    card.add("엘리엇", 0.5, f"3파 추정({count.describe()})")

        # --- 7. 손절 / 목표 ---
        buffer = atr * self.stop_buffer_atr
        if direction is Bias.BULLISH:
            stop = sweep.extreme - buffer      # 스윕 저점 '아래'. 같은 자리에서 또 털리지 않게
            entry = current.close
            target = self._target_price(pools, leg, entry, direction, atr)
            if stop >= entry or target <= entry:
                return Signal(Action.HOLD, "손절/목표 배치 불가")
            rr = (target - entry) / (entry - stop)
            side_action = Action.ENTER_LONG
        else:
            stop = sweep.extreme + buffer
            entry = current.close
            target = self._target_price(pools, leg, entry, direction, atr)
            if stop <= entry or target >= entry:
                return Signal(Action.HOLD, "손절/목표 배치 불가")
            rr = (entry - target) / (stop - entry)
            side_action = Action.ENTER_SHORT

        if rr < self.min_rr:
            return Signal(Action.HOLD, f"손익비 부족 ({rr:.2f} < {self.min_rr})")
        # min_rr이 0이어도 나눗셈이 터지지 않게 한다 (제거 실험에서 실제로 터졌다)
        rr_scale = max(self.min_rr, 1.0)
        card.add("손익비", min(rr / rr_scale, 2.0) * 0.5, f"R:R {rr:.2f}")

        # --- 8. 합류 점수 판정 ---
        if card.total < self.min_score:
            return Signal(Action.HOLD, f"합류 점수 부족 {card.summary()}")

        return Signal(
            side_action,
            f"{sweep.pool.label} 스윕 → {structure.event.value.upper()} → 되돌림 진입 {card.summary()}",
            stop_loss=stop,
            take_profit=target,
            meta={
                "score": card.total,
                "components": dict(card.points),
                "notes": list(card.notes),
                "rr": rr,
                "angle": signed,
                "sweep_index": sweep.index + offset,
                "pool": sweep.pool.label,
            },
        )

    # ------------------------------------------------------------------
    def _displacement_leg(self, candles, sweep, index: int, direction: Bias) -> Leg | None:
        """스윕 극단에서 변위 극단까지의 구간. 되돌림을 재는 기준이 된다."""
        window = candles[sweep.index : index + 1]
        if not window:
            return None
        if direction is Bias.BULLISH:
            return Leg(start=sweep.extreme, end=max(c.high for c in window))
        return Leg(start=sweep.extreme, end=min(c.low for c in window))

    def _active_fvg(self, candles, *, atr: float, index: int, direction: Bias) -> Zone | None:
        """아직 메워지지 않은 최신 FVG 하나."""
        fvgs = find_fvgs(candles, atr=atr, upto=index)
        for fvg in reversed(fvgs):
            if fvg.direction is not direction:
                continue
            if fvg.confirmed_index > index or fvg.confirmed_index < index - self.sweep_lookback * 3:
                continue
            if not fvg.is_filled(candles, index):
                return fvg.zone
        return None

    def _target_price(self, pools, leg: Leg, entry: float, direction: Bias, atr: float) -> float:
        """반대편 유동성 풀을 1순위 목표로 삼는다. 없으면 피보나치 확장.

        '가격은 유동성을 향해 간다'는 ICT의 전제를 목표 설정에 그대로 쓴 것이다.
        """
        want = PoolKind.BUY_SIDE if direction is Bias.BULLISH else PoolKind.SELL_SIDE
        candidates = [
            p.price for p in pools
            if p.kind is want and (p.price > entry if direction is Bias.BULLISH else p.price < entry)
        ]
        if candidates:
            # 가장 가까운 반대편 풀 = 가장 먼저 닿을 자리
            return min(candidates) if direction is Bias.BULLISH else max(candidates)
        return leg.extension(self.target_extension)
