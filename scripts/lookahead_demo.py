#!/usr/bin/env python3
"""리페인팅(미래참조) 지표가 백테스트를 어떻게 속이는지 실측한다.

    PYTHONPATH=src python3 scripts/lookahead_demo.py

MT4/MT5에서 널리 쓰이는 'TMA 밴드'는 **중심이동(centered) 평균**을 쓴다.
중심이동은 i 시점 값을 계산할 때 i 이후 캔들을 쓴다. 차트에서는 가격을
기가 막히게 감싸는 것처럼 보이고, 백테스트는 훌륭하게 나온다.
실시간에는 그 값을 알 수 없다 — 새 캔들이 오면 과거 선이 다시 그려진다.

같은 규칙, 같은 데이터, 지표 계산 방식만 바꿔서 그 차이를 숫자로 보여준다.
**승률 100%가 나오면 그건 실력이 아니라 미래를 본 것이다.**

어떤 지표·EA를 검토하든 이 검사를 먼저 하라:
지표가 확정된 과거 봉의 값을 나중에 바꾸는가? 바꾼다면 그 백테스트는 무의미하다.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dorothy.data.indicators import atr, tma, tma_centered  # noqa: E402
from dorothy.data.loader import synthetic  # noqa: E402

ROUND_TRIP_FEE = 0.0006 * 2


def band_backtest(closes, atr_line, line, *, band_mult=1.5, equity=100.0):
    """TMA 밴드의 실제 용법: 하단 밴드 이탈에서 진입, 중심선 복귀에서 청산."""
    position_price = None
    trades = wins = 0
    for i in range(1, len(closes)):
        mid, a = line[i], atr_line[i]
        if mid is None or a is None:
            continue
        if position_price is None:
            if closes[i] < mid - band_mult * a:
                position_price = closes[i]
        elif closes[i] >= mid:
            ret = (closes[i] - position_price) / position_price - ROUND_TRIP_FEE
            equity *= 1 + ret
            trades += 1
            wins += ret > 0
            position_price = None
    return equity, trades, (wins / trades * 100 if trades else 0.0)


def main() -> int:
    candles = synthetic(6000, seed=5, timeframe="15m", start=65000.0)
    closes = [c.close for c in candles]
    atr_line = atr([c.high for c in candles], [c.low for c in candles], closes, 14)

    print("=" * 74)
    print("  리페인팅 지표가 백테스트를 속이는 방식")
    print("=" * 74)
    print("  같은 규칙(하단밴드 진입 → 중심선 청산), 같은 데이터, $100 시작")
    print("  차이는 지표를 인과적으로 계산했는가 뿐이다.")
    print("-" * 74)

    for label, line in (
        ("인과적 TMA (정상)", tma(closes, 20)),
        ("중심이동 TMA (미래 참조)", tma_centered(closes, 20)),
    ):
        final, trades, win_rate = band_backtest(closes, atr_line, line)
        print(
            f"  {label:<26} 거래 {trades:>4}  승률 {win_rate:>5.1f}%  "
            f"최종 ${final:>9,.2f}  ({final - 100:+.2f}%)"
        )

    print("-" * 74)
    print("  ⚠ 승률 100%는 전략이 훌륭하다는 뜻이 아니라 미래를 봤다는 뜻이다.")
    print("     중심이동 평균은 확정된 과거 봉의 값을 나중에 바꾼다(리페인팅).")
    print()
    print("  지표·EA를 검토할 때 던질 질문:")
    print("    1. 확정된 과거 봉의 지표값이 나중에 바뀌는가?")
    print("    2. 판매자 백테스트의 승률이 비정상적으로 높지 않은가?")
    print("    3. 아웃오브샘플(학습에 안 쓴 구간) 성과를 제시하는가?")
    print()
    print("  이 저장소에서 직접 확인하는 법:")
    print("    python -m dorothy walkforward --csv <데이터> --strategy <전략>")
    print("    → 효율(검증÷학습)이 0.5 아래면 과최적화, 0 이하면 버릴 것")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
