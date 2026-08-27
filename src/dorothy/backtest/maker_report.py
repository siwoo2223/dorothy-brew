"""메이커 진입이 정말 이득인지 재는 보고서.

수수료만 보면 지정가가 무조건 낫다(왕복 0.22% → 0.13%). 그런데 지정가는
**가격이 되돌아와야** 체결된다. 돌파 전략에서 되돌아오지 않은 캔들은 대개
그대로 크게 간 캔들이다. 그래서 물어야 할 질문은 하나다:

    **놓친 신호가 잡은 신호보다 좋았는가?**

좋았다면 비용 절감분이 그 손실을 못 메운다. 이 보고서는 잡은 것과 놓친 것을
나란히 놓고, 절감분과 놓친 수익을 직접 비교한다.
"""

from __future__ import annotations

import math
import statistics
import unicodedata
from dataclasses import dataclass, field

from ..config import Config
from ..data.indicators import atr
from ..execution.maker import limit_price, round_trip_cost, simulate_limit_fill
from ..ml.labeling import Sample, triple_barrier
from ..models import Candle, Side


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return " " * max(0, width - _width(text)) + text


def _ljust(text: str, width: int) -> str:
    return text + " " * max(0, width - _width(text))


@dataclass
class Leg:
    """한 무리의 매매를 요약한 것."""

    label: str
    returns: list[float] = field(default_factory=list)
    cost: float | None = None      # None이면 체결되지 않아 비용 자체가 없다
    #: 손절 거리로 나눈 수익(R배수). 리스크 기준 사이징에서는 이게 실제 계좌 영향이다.
    #: 지정가로 싸게 잡으면 가격 수익률은 좋아지지만 손절이 멀어져 수량이 줄어든다.
    #: 가격 수익률만 보면 그 대가가 안 보인다.
    r_multiples: list[float] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.returns)

    @property
    def gross(self) -> float:
        return statistics.fmean(self.returns) * 100 if self.returns else 0.0

    @property
    def net(self) -> float | None:
        if self.cost is None:
            return None
        return self.gross - self.cost * 100

    @property
    def t_stat(self) -> float:
        if self.cost is None or len(self.returns) < 3:
            return 0.0
        spread = statistics.stdev(self.returns)
        if spread <= 0:
            return 0.0
        mean_net = statistics.fmean(self.returns) - self.cost
        return mean_net / (spread / math.sqrt(len(self.returns)))

    @property
    def total(self) -> float:
        """단리 합계(%). 복리가 아니라 '이 우위를 몇 번 반복했나'를 보는 값이다.

        1회 평균만 비교하면 안 된다. 지정가는 매매 수 자체를 줄이기 때문에,
        평균이 올라도 총액은 내려갈 수 있다. 판단은 이 값으로 한다.
        """
        return (self.net or 0.0) * self.count

    @property
    def gross_total(self) -> float:
        return self.gross * self.count

    @property
    def mean_r(self) -> float:
        """1회 평균 R배수(수수료 전). 리스크 1단위당 몇 배를 벌었나."""
        return statistics.fmean(self.r_multiples) if self.r_multiples else 0.0

    def net_r(self, risk_pct: float) -> float | None:
        """수수료를 R 단위로 환산해 뺀 값.

        risk_pct는 손절 거리를 진입가 대비 %로 본 평균이다. 왕복 비용 0.13%를
        손절 거리 3%짜리 매매에 물리면 0.043R이 날아간다.
        """
        if self.cost is None or not self.r_multiples or risk_pct <= 0:
            return None
        return self.mean_r - (self.cost * 100) / risk_pct


@dataclass
class MakerComparison:
    taker: Leg              # 전체 신호를 시장가로
    maker: Leg              # 지정가로 체결된 것만, 체결가 기준으로 다시 태운 결과
    missed: Leg             # 지정가로 못 잡은 신호를 시장가로 잡았다면
    offset_atr: float
    timeout_bars: int
    taker_cost: float
    maker_cost: float
    taker_on_filled: Leg = field(default_factory=lambda: Leg("", cost=None))
    taker_risk_pct: float = 0.0            # 진입가 대비 평균 손절 거리 (%)
    maker_risk_pct: float = 0.0
    taker_filled_risk_pct: float = 0.0

    @property
    def fill_rate(self) -> float:
        total = self.maker.count + self.missed.count
        return self.maker.count / total * 100 if total else 0.0

    @property
    def selection_penalty(self) -> float:
        """놓친 신호가 잡은 신호보다 얼마나 좋았나 (%p). 양수면 좋은 걸 놓쳤다는 뜻."""
        return self.missed.gross - self.maker.gross

    @property
    def cost_saving(self) -> float:
        return (self.taker_cost - self.maker_cost) * 100

    def report(self) -> str:
        lines = [
            "═" * 74,
            "  메이커 진입 검증 — 지정가로 넣으면 정말 이득인가",
            "═" * 74,
            f"  지정가 = 신호봉 종가에서 {self.offset_atr:.2f} ATR 물러난 값,"
            f" 대기 {self.timeout_bars}봉 후 취소",
            f"  왕복 비용   시장가 {self.taker_cost * 100:.2f}%"
            f"  →  지정가 {self.maker_cost * 100:.2f}%"
            f"   (절감 {self.cost_saving:.2f}%p)",
            "─" * 74,
            f"  전체 신호 {self.maker.count + self.missed.count:>6}건",
            f"  체결됨    {self.maker.count:>6}건  ({self.fill_rate:.1f}%)",
            f"  미체결    {self.missed.count:>6}건  ({100 - self.fill_rate:.1f}%)",
            "═" * 74,
            "  " + _ljust("구분", 22) + _pad("건수", 7) + _pad("수수료 전", 12)
            + _pad("비용", 8) + _pad("수수료 후", 12) + _pad("t", 8) + _pad("합계", 10),
            "─" * 74,
        ]
        legs = [self.taker]
        if self.taker_on_filled.count:
            legs.append(self.taker_on_filled)
        legs += [self.maker, self.missed]
        for leg in legs:
            net = leg.net
            if net is None:
                lines.append(
                    "  " + _ljust(leg.label, 22) + f"{leg.count:>7}"
                    f"{leg.gross:>+11.3f}%" + _pad("—", 8) + _pad("—", 12) + _pad("—", 8)
                    + _pad("—", 10)
                )
                continue
            mark = "✓" if net > 0 and leg.t_stat >= 2 else ("?" if net > 0 else "✗")
            lines.append(
                "  " + _ljust(leg.label, 22) + f"{leg.count:>7}"
                f"{leg.gross:>+11.3f}%{leg.cost * 100:>7.2f}%{net:>+11.3f}%"
                f"{leg.t_stat:>8.2f}{leg.total:>+9.1f}% {mark}"
            )
        lines += ["═" * 74]
        if self.maker.r_multiples and self.taker.r_multiples:
            lines += self._risk_table()
        lines += self._verdict()
        lines.append("  ※ 청산은 손절·익절 모두 시장가로 계산했습니다."
                     " 손절을 지정가로 걸 수는 없습니다.")
        return "\n".join(lines)

    def _risk_table(self) -> list[str]:
        """리스크 1단위당 성적. 사이징까지 감안하면 그림이 달라질 수 있다."""
        taker_r = self.taker.net_r(self.taker_risk_pct)
        maker_r = self.maker.net_r(self.maker_risk_pct)
        lines = [
            "  리스크 기준 (R배수) — 손절 거리로 나눈 값. 수량은 여기에 반비례합니다",
            "  " + "─" * 70,
            f"  {'시장가 — 전량 진입':<20}  손절거리 {self.taker_risk_pct:>5.2f}%"
            f"   1회 {taker_r if taker_r is not None else 0:>+7.4f}R"
            f"   합계 {(taker_r or 0) * self.taker.count:>+7.1f}R",
            f"  {'지정가 — 체결분':<21}  손절거리 {self.maker_risk_pct:>5.2f}%"
            f"   1회 {maker_r if maker_r is not None else 0:>+7.4f}R"
            f"   합계 {(maker_r or 0) * self.maker.count:>+7.1f}R",
            "═" * 74,
        ]
        if self.maker_risk_pct > self.taker_filled_risk_pct > 0:
            widened = (self.maker_risk_pct / self.taker_filled_risk_pct - 1) * 100
            lines.insert(2, f"  ※ 지정가로 싸게 잡은 대가로 손절 거리가 {widened:.1f}% 멀어졌고,"
                            " 그만큼 수량이 줄어듭니다.")
        return lines

    def _verdict(self) -> list[str]:
        out = []
        penalty = self.selection_penalty

        if self.maker.count == 0:
            return ["  ✗ 체결된 신호가 없습니다. 지정가를 너무 멀리 뒀거나 대기가 짧습니다."]

        if self.offset_atr < 0.05 and self.fill_rate > 95:
            out += [
                f"  ⚠ 체결률 {self.fill_rate:.1f}%는 이 시뮬레이션이 낙관적이라는 신호입니다.",
                "     지정가를 종가에 붙이면 '저가가 종가를 스쳤다'는 이유로 거의 다 체결됩니다.",
                "     실제로는 호가 대기열 순서가 있습니다. 가격이 스치기만 하고 내 주문까지"
                " 안 내려오거나, 급하게 쓸려나가면 체결되지 않습니다.",
                "     이 조건의 이득은 사실상 수수료 절감분"
                f" ({self.cost_saving:.2f}%p/회)뿐이라고 보는 편이 안전합니다.",
                "",
            ]

        if penalty > 0:
            out.append(f"  ⚠ 놓친 신호가 잡은 신호보다 좋았습니다"
                       f" (수수료 전 {self.missed.gross:+.3f}% vs {self.maker.gross:+.3f}%,"
                       f" 차이 {penalty:.3f}%p).")
            if penalty > self.cost_saving:
                out.append(f"  놓친 손실 {penalty:.3f}%p > 비용 절감 {self.cost_saving:.2f}%p"
                           " — **지정가가 손해입니다.**")
                out.append("  가격이 안 돌아온 캔들이 그대로 크게 간 캔들이었다는 뜻입니다."
                           " 돌파 전략의 전형적인 함정입니다.")
            else:
                out.append(f"  다만 비용 절감 {self.cost_saving:.2f}%p가"
                           f" 놓친 손실 {penalty:.3f}%p보다 큽니다. 지정가가 그래도 낫습니다.")
        else:
            out.append(f"  ✓ 놓친 신호가 오히려 나빴습니다"
                       f" (수수료 전 {self.missed.gross:+.3f}% vs {self.maker.gross:+.3f}%).")
            out.append("  지정가가 비용도 깎고 나쁜 신호도 걸러냈습니다."
                       " 되돌림을 요구하는 것 자체가 필터로 작동한 겁니다.")

        maker_net, taker_net = self.maker.net, self.taker.net
        if maker_net is None or taker_net is None:
            return out

        # 1회 평균으로 비교하면 안 된다. 지정가는 매매 수를 줄이므로
        # 평균이 올라도 총액은 내려갈 수 있다. 실제로 여기가 그렇다.
        delta_avg = maker_net - taker_net
        delta_total = self.maker.total - self.taker.total
        out.append(f"  1회 평균으로는 {taker_net:+.3f}% → {maker_net:+.3f}%"
                   f" ({delta_avg:+.3f}%p)로 지정가가 좋아 보입니다.")
        out.append(f"  하지만 매매 수가 {self.taker.count}건 → {self.maker.count}건으로 줄었습니다.")
        verb = "낫습니다" if delta_total > 0 else "못합니다"
        out.append(f"  결론: 5년 합계 {self.taker.total:+.1f}% → {self.maker.total:+.1f}%"
                   f" ({delta_total:+.1f}%p) — 지정가가 {verb}.")

        taker_r = self.taker.net_r(self.taker_risk_pct)
        maker_r = self.maker.net_r(self.maker_risk_pct)
        if taker_r is not None and maker_r is not None:
            taker_total_r = taker_r * self.taker.count
            maker_total_r = maker_r * self.maker.count
            agrees = (maker_total_r > taker_total_r) == (delta_total > 0)
            if agrees:
                out.append(f"  리스크 기준으로도 같은 방향입니다"
                           f" ({taker_total_r:+.1f}R → {maker_total_r:+.1f}R).")
            else:
                out.append(f"  ⚠ 그런데 리스크 기준으로는 반대입니다"
                           f" ({taker_total_r:+.1f}R → {maker_total_r:+.1f}R).")
                out.append("     가격 수익률이 좋아진 건 싸게 잡아서인데, 그만큼 손절이 멀어져"
                           " 수량이 줄어듭니다. 계좌에 미치는 영향은 그 둘의 곱입니다.")

        if maker_net > 0 and self.maker.t_stat < 2:
            out.append(f"  ※ 어느 쪽이든 t={self.maker.t_stat:.2f}로 우연과 구별되지 않습니다."
                       " 이걸로 실전에 넣지 마세요.")
        return out


def analyse(
    candles: list[Candle],
    samples: list[Sample],
    cfg: Config,
    *,
    offset_atr: float = 0.25,
    timeout_bars: int = 3,
    max_bars: int = 42,
    atr_period: int = 14,
    keep: set[int] | None = None,
) -> MakerComparison:
    """같은 신호 집합을 시장가/지정가 두 방식으로 각각 체결시켜 비교한다.

    keep을 주면 그 표본 index만 쓴다 (메타 필터를 통과한 신호만 보고 싶을 때).
    """
    taker_cost = round_trip_cost(
        maker_fee=cfg.exchange.maker_fee, taker_fee=cfg.exchange.taker_fee,
        slippage=cfg.exchange.slippage, maker_entry=False,
    )
    maker_cost = round_trip_cost(
        maker_fee=cfg.exchange.maker_fee, taker_fee=cfg.exchange.taker_fee,
        slippage=cfg.exchange.slippage, maker_entry=True,
    )

    taker = Leg("시장가 — 전량 진입", cost=taker_cost)
    taker_on_filled = Leg("시장가 — 체결분만", cost=taker_cost)
    maker = Leg("지정가 — 체결분", cost=maker_cost)
    missed = Leg("놓친 신호 (시장가 기준)", cost=None)

    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    closes = [c.close for c in candles]
    risk_pcts: list[float] = []
    maker_risk_pcts: list[float] = []
    taker_filled_risk_pcts: list[float] = []

    for position, sample in enumerate(samples):
        if keep is not None and position not in keep:
            continue

        i = sample.index
        signal_bar = candles[i]
        entry = signal_bar.close
        exit_price = candles[sample.exit_index].close
        taker.returns.append((exit_price - entry) / entry * sample.side.sign)

        risk = abs(entry - sample.stop)
        if risk > 0:
            taker.r_multiples.append((exit_price - entry) * sample.side.sign / risk)
            risk_pcts.append(risk / entry * 100)

        window = slice(max(0, i - atr_period * 20), i + 1)
        atr_line = atr(highs[window], lows[window], closes[window], atr_period)
        atr_now = atr_line[-1] if atr_line else None
        if not atr_now or atr_now <= 0:
            continue

        wanted = limit_price(signal_bar, sample.side, atr_now, offset_atr)
        fill = simulate_limit_fill(candles, i, sample.side, wanted, timeout_bars=timeout_bars)

        if not fill.filled:
            # 놓친 신호의 성과는 시장가로 잡았을 때의 값으로 본다.
            # "안 잡아서 다행이었나, 아까웠나"를 보는 것이므로 비용은 붙이지 않는다.
            missed.returns.append((exit_price - entry) / entry * sample.side.sign)
            continue

        # 체결가가 달라졌으니 배리어를 다시 태운다. 손절·익절 절대가는 그대로다.
        outcome = triple_barrier(
            candles, fill.index, sample.side, sample.stop, sample.target,
            max_bars=max_bars, include_entry_bar=True,
        )
        if outcome is None:
            continue
        filled_exit = candles[outcome.exit_index].close
        maker.returns.append((filled_exit - fill.price) / fill.price * sample.side.sign)
        # 같은 신호를 시장가로 잡았다면 얼마였나 — 체결가 차이만 떼어내 보기 위해서다
        taker_on_filled.returns.append((exit_price - entry) / entry * sample.side.sign)

        # R배수는 각자의 체결가에서 손절까지의 거리로 나눈다.
        # 지정가로 싸게 잡으면 이 거리가 멀어지고, 그만큼 수량이 줄어든다.
        maker_risk = abs(fill.price - sample.stop)
        if maker_risk > 0:
            maker.r_multiples.append(
                (filled_exit - fill.price) * sample.side.sign / maker_risk
            )
            maker_risk_pcts.append(maker_risk / fill.price * 100)
        if risk > 0:
            taker_on_filled.r_multiples.append((exit_price - entry) * sample.side.sign / risk)
            taker_filled_risk_pcts.append(risk / entry * 100)

    mean = statistics.fmean
    return MakerComparison(
        taker, maker, missed, offset_atr, timeout_bars, taker_cost, maker_cost,
        taker_on_filled=taker_on_filled,
        taker_risk_pct=mean(risk_pcts) if risk_pcts else 0.0,
        maker_risk_pct=mean(maker_risk_pcts) if maker_risk_pcts else 0.0,
        taker_filled_risk_pct=mean(taker_filled_risk_pcts) if taker_filled_risk_pcts else 0.0,
    )
