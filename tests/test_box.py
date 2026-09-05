"""박스 탐지기.

**이 파일이 막는 사고:**
"최근 N봉의 고저"를 박스라고 부르면 **추세 구간도 전부 박스가 된다.**
한 방향으로 쭉 간 구간도 고저 폭은 있으니까. 경계에 반복해서 닿았는지를
봐야 진짜 박스다. 그걸 안 걸러내면 박스 매매가 추세에 역행하게 된다.
"""

import unittest

from dorothy.analysis.box import Box, detect
from dorothy.models import Candle


def bar(i, o, h, l, c):
    return Candle(i * 3_600_000, o, h, l, c, 1.0)


def ranging(n=40, lo=99.0, hi=101.0):
    """상·하단을 번갈아 찍는 진짜 박스."""
    out = []
    for i in range(n):
        if i % 2:
            out.append(bar(i, lo + 0.5, hi, lo + 0.4, hi - 0.1))
        else:
            out.append(bar(i, hi - 0.5, hi - 0.4, lo, lo + 0.1))
    return out


def trending(n=40, start=100.0, step=0.5):
    """한 방향으로 쭉 가는 구간. 고저 폭은 크지만 박스가 아니다."""
    out = []
    for i in range(n):
        p = start + i * step
        out.append(bar(i, p, p + 0.3, p - 0.1, p + 0.2))
    return out


class DetectsRealBoxesTests(unittest.TestCase):
    def test_a_range_is_detected(self):
        box = detect(ranging(), lookback=30)
        self.assertIsNotNone(box)
        self.assertAlmostEqual(box.upper, 101.0, places=6)
        self.assertAlmostEqual(box.lower, 99.0, places=6)

    def test_both_edges_are_counted(self):
        box = detect(ranging(), lookback=30)
        self.assertGreaterEqual(box.touches_upper, 2)
        self.assertGreaterEqual(box.touches_lower, 2)

    def test_height_is_reported_as_a_fraction(self):
        box = detect(ranging(), lookback=30)
        self.assertAlmostEqual(box.height_pct, 2.0 / 100.0, places=4)


class RejectsNonBoxesTests(unittest.TestCase):
    def test_a_trend_is_not_a_box(self):
        """**핵심.** 고저 폭만 보면 추세도 박스로 보인다."""
        self.assertIsNone(detect(trending(), lookback=30))

    def test_a_trend_would_pass_if_touches_were_ignored(self):
        """위 테스트가 무엇 덕분에 통과하는지 고정한다.

        경계 접촉 조건을 끄면(min_touches=0) 추세도 통과해야 한다.
        안 그러면 다른 이유로 걸러진 것이고, 접촉 조건은 죽은 코드다.
        """
        loose = detect(trending(), lookback=30, min_touches=0,
                       max_variance_ratio=99.0)
        self.assertIsNotNone(loose, "접촉 조건이 아닌 다른 것이 막고 있습니다")

    def test_a_box_too_narrow_for_fees_is_rejected(self):
        flat = [bar(i, 100.0, 100.05, 99.95, 100.0) for i in range(40)]
        self.assertIsNone(detect(flat, lookback=30, min_height_pct=0.01))
        self.assertIsNotNone(detect(flat, lookback=30, min_height_pct=0.0001))

    def test_touching_only_one_edge_is_not_a_box(self):
        """하단만 여러 번 닿고 상단은 한 번뿐이면 박스가 아니다."""
        c = [bar(i, 99.5, 99.7, 99.0, 99.2) for i in range(29)]
        c.append(bar(29, 99.5, 101.0, 99.4, 100.8))     # 상단 1회
        self.assertIsNone(detect(c, lookback=30, min_touches=2))

    def test_too_few_candles(self):
        self.assertIsNone(detect(ranging(5), lookback=30))

    def test_a_degenerate_window_does_not_divide_by_zero(self):
        same = [bar(i, 100.0, 100.0, 100.0, 100.0) for i in range(40)]
        self.assertIsNone(detect(same, lookback=30))


class CausalityTests(unittest.TestCase):
    def test_only_past_candles_are_used(self):
        """미래 봉을 붙여도 판정이 바뀌면 안 된다."""
        base = ranging(40)
        future = base + trending(20, start=200.0)
        a = detect(base, lookback=30)
        b = detect(base + future[len(base):][:0], lookback=30)
        self.assertEqual((a.lower, a.upper), (b.lower, b.upper))

    def test_the_window_is_the_last_lookback_bars(self):
        c = trending(20, start=50.0, step=1.0) + ranging(30)
        box = detect(c, lookback=30)
        self.assertIsNotNone(box, "최근 30봉이 박스인데 못 찾았습니다")
        self.assertLessEqual(box.upper, 101.0 + 1e-9,
                             "과거 추세 구간이 박스에 섞였습니다")


class PositionTests(unittest.TestCase):
    def setUp(self):
        self.box = Box(lower=100.0, upper=110.0, touches_lower=3,
                       touches_upper=3, variance_ratio=0.9, bars=30)

    def test_bottom_is_zero_and_top_is_one(self):
        self.assertAlmostEqual(self.box.position_of(100.0), 0.0)
        self.assertAlmostEqual(self.box.position_of(110.0), 1.0)
        self.assertAlmostEqual(self.box.position_of(105.0), 0.5)

    def test_outside_the_box_goes_past_the_ends(self):
        self.assertLess(self.box.position_of(95.0), 0.0)
        self.assertGreater(self.box.position_of(115.0), 1.0)

    def test_mid_and_height(self):
        self.assertAlmostEqual(self.box.mid, 105.0)
        self.assertAlmostEqual(self.box.height, 10.0)
        self.assertAlmostEqual(self.box.height_pct, 10.0 / 105.0)
