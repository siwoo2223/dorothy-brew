import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from dorothy.config import RiskConfig
from dorothy.models import Side, Trade
from dorothy.risk.manager import RiskManager


def _rm(**overrides) -> RiskManager:
    cfg = RiskConfig(**overrides)
    return RiskManager(cfg, kill_switch_file="/nonexistent-kill")


class TestRiskSizing(unittest.TestCase):
    def test_size_is_risk_amount_divided_by_stop_distance(self):
        rm = _rm(risk_per_trade=0.01)
        d = rm.evaluate_entry(
            equity=10_000, price=50_000, side=Side.LONG, stop_loss=49_000, leverage=3
        )
        self.assertTrue(d.approved)
        # 10,000 × 1% = 100 리스크, 손절폭 1,000 → 0.1
        self.assertAlmostEqual(d.size, 0.1, places=6)

    def test_entry_without_stop_is_rejected(self):
        d = _rm().evaluate_entry(
            equity=1000, price=100, side=Side.LONG, stop_loss=None, leverage=1
        )
        self.assertFalse(d.approved)
        self.assertIn("손절가", d.reason)

    def test_stop_on_wrong_side_is_rejected(self):
        rm = _rm()
        self.assertFalse(
            rm.evaluate_entry(equity=1000, price=100, side=Side.LONG, stop_loss=101, leverage=1)
        )
        rm.state.open_positions = 0
        self.assertFalse(
            rm.evaluate_entry(equity=1000, price=100, side=Side.SHORT, stop_loss=99, leverage=1)
        )

    def test_too_tight_stop_is_rejected(self):
        d = _rm().evaluate_entry(
            equity=1000, price=100, side=Side.LONG, stop_loss=99.99, leverage=1
        )
        self.assertFalse(d.approved)
        self.assertIn("손절폭", d.reason)

    def test_notional_cap_limits_size(self):
        # 손절이 아주 가까우면 사이징 공식만으로는 수량이 폭발한다. 상한이 잡아야 한다.
        rm = _rm(risk_per_trade=0.05, max_position_pct=0.10, max_leverage=2)
        d = rm.evaluate_entry(
            equity=1000, price=100, side=Side.LONG, stop_loss=99, leverage=2
        )
        self.assertTrue(d.approved)
        self.assertLessEqual(d.size * 100, 1000 * 0.10 * 2 + 1e-9)

    def test_leverage_is_capped_at_max(self):
        rm = _rm(risk_per_trade=0.05, max_position_pct=10.0, max_leverage=2)
        d = rm.evaluate_entry(
            equity=1000, price=100, side=Side.LONG, stop_loss=99.5, leverage=50
        )
        self.assertLessEqual(d.size * 100, 1000 * 2 + 1e-6)


class TestRiskHalts(unittest.TestCase):
    def test_daily_loss_limit_halts_entries(self):
        rm = _rm(max_daily_loss_pct=0.03, max_open_positions=99)
        rm.roll_day(1000)
        rm.record_trade(Trade("BTC", Side.LONG, 1, 100, 60, 0, 1))   # -40 = -4%
        self.assertIn("일일 손실 한도", rm.halt_reason(960))

    def test_consecutive_losses_halt_then_reset_next_day(self):
        clock = {"ms": 1_700_000_000_000}
        rm = RiskManager(
            RiskConfig(max_consecutive_losses=3, max_open_positions=99),
            kill_switch_file="/nonexistent-kill",
            clock=lambda: clock["ms"],
        )
        rm.roll_day(1000)
        for _ in range(3):
            rm.record_trade(Trade("BTC", Side.LONG, 1, 100, 99.9, 0, 1))
        self.assertIn("연속", rm.halt_reason(1000))

        clock["ms"] += 86_400_000   # 다음 날
        rm.roll_day(1000)
        self.assertEqual(rm.halt_reason(1000), "")

    def test_kill_switch_file_halts_entries(self):
        with TemporaryDirectory() as tmp:
            kill = Path(tmp) / "KILL"
            rm = RiskManager(RiskConfig(), kill_switch_file=str(kill))
            rm.roll_day(1000)
            self.assertEqual(rm.halt_reason(1000), "")
            kill.touch()
            self.assertIn("킬스위치", rm.halt_reason(1000))

    def test_max_open_positions_blocks_second_entry(self):
        rm = _rm(max_open_positions=1)
        first = rm.evaluate_entry(
            equity=1000, price=100, side=Side.LONG, stop_loss=95, leverage=1
        )
        self.assertTrue(first.approved)
        second = rm.evaluate_entry(
            equity=1000, price=100, side=Side.LONG, stop_loss=95, leverage=1
        )
        self.assertFalse(second.approved)
        self.assertIn("동시 보유", second.reason)

    def test_release_returns_the_reserved_slot(self):
        rm = _rm(max_open_positions=1)
        rm.evaluate_entry(equity=1000, price=100, side=Side.LONG, stop_loss=95, leverage=1)
        rm.release()
        self.assertTrue(
            rm.evaluate_entry(equity=1000, price=100, side=Side.LONG, stop_loss=95, leverage=1)
        )


if __name__ == "__main__":
    unittest.main()


class PeakDrawdownTests(unittest.TestCase):
    """고점 대비 낙폭 한도.

    일일 손실 한도와 연속 손실 한도는 둘 다 하루 단위로 초기화된다.
    그래서 며칠에 한 번 매매하는 전략에서는 **걸릴 기회 자체가 없다.**
    12시간봉 전략(평균 22일에 한 번)을 계측해보니 1회 리스크를 12%로
    올려도 차단이 한 번도 발동하지 않았다.

    이 한도는 날짜와 무관하게 걸린다.
    """

    def make(self, limit, **kw):
        """새 한도만 격리해서 본다.

        기본 일일 한도(3%)를 켜두면 그게 먼저 걸려서 무엇이 막았는지
        구분이 안 된다. 일부러 크게 열어두고 고점 한도만 시험한다.
        """
        from dorothy.config import RiskConfig
        from dorothy.risk.manager import RiskManager

        kw.setdefault("max_daily_loss_pct", 1.0)
        cfg = RiskConfig(max_drawdown_pct=limit, **kw)
        return RiskManager(cfg, kill_switch_file="/nonexistent-kill-switch")

    def test_off_by_default(self):
        from dorothy.config import RiskConfig

        self.assertEqual(RiskConfig().max_drawdown_pct, 0.0)

    def test_zero_limit_never_halts(self):
        risk = self.make(0.0)
        risk.roll_day(1000.0)
        self.assertEqual(risk.halt_reason(1.0), "")

    def test_halts_past_the_limit(self):
        risk = self.make(0.20)
        risk.roll_day(1000.0)
        risk.halt_reason(1000.0)            # 고점 기록
        self.assertEqual(risk.halt_reason(850.0), "")      # -15%
        self.assertIn("고점 대비 낙폭", risk.halt_reason(800.0))   # -20%

    def test_peak_survives_a_day_change(self):
        """이게 핵심이다. 일일 한도가 못 지키는 이유가 날짜 초기화였다."""
        risk = self.make(0.20)
        risk.roll_day(1000.0)
        risk.halt_reason(1000.0)
        risk.state.day = "1999-01-01"        # 날짜가 바뀐 것처럼
        risk.roll_day(800.0)
        self.assertEqual(risk.state.peak_equity, 1000.0, "고점이 초기화됐습니다")
        self.assertIn("고점 대비 낙폭", risk.halt_reason(800.0))

    def test_peak_rises_with_equity(self):
        risk = self.make(0.20)
        risk.roll_day(1000.0)
        risk.halt_reason(2000.0)
        self.assertEqual(risk.state.peak_equity, 2000.0)
        self.assertEqual(risk.halt_reason(1700.0), "")     # 새 고점 대비 -15%
        self.assertIn("고점 대비 낙폭", risk.halt_reason(1600.0))

    def test_message_names_the_numbers(self):
        risk = self.make(0.20)
        risk.roll_day(1000.0)
        risk.halt_reason(1000.0)
        reason = risk.halt_reason(700.0)
        self.assertIn("1,000", reason)
        self.assertIn("700", reason)

    def test_does_not_block_a_low_frequency_strategy_unnecessarily(self):
        """정상 범위에서는 막지 않아야 한다. 과잉 차단도 나쁘다."""
        risk = self.make(0.30)
        risk.roll_day(1000.0)
        for equity in (1000.0, 1100.0, 1050.0, 1200.0, 1000.0, 1300.0):
            self.assertEqual(risk.halt_reason(equity), "", f"자본 {equity}에서 막혔습니다")

    def test_works_when_the_daily_limit_cannot(self):
        """저빈도 시나리오 재현: 하루에 한 번씩만 매매하고 날짜가 계속 바뀐다.
        일일 한도는 매일 초기화되어 못 걸지만, 고점 한도는 걸어야 한다."""
        risk = self.make(0.25, max_daily_loss_pct=0.03)   # 일일 한도도 켜둔 채로
        risk.roll_day(1000.0)
        risk.halt_reason(1000.0)
        equity = 1000.0
        for day in range(6):
            equity *= 0.95                    # 하루 -5%
            risk.state.day = f"2020-01-{day:02d}"
            risk.roll_day(equity)             # 날짜 전환 → 일일 카운터 초기화
        reason = risk.halt_reason(equity)
        self.assertIn("고점 대비 낙폭", reason,
                      f"자본 {equity:.0f} (고점 1000)에서 안 막혔습니다")


class TestWhatTheConfigCommentsClaim(unittest.TestCase):
    """config/*.yaml 주석에 적은 '복리와 레버리지' 설명을 코드에 고정한다.

    설정 파일에 백테스트 숫자를 적어두면, 사이징 규칙이 바뀌었을 때 주석만
    조용히 거짓이 된다. 여기서 주장 자체를 재현해서 그걸 막는다.
    """

    def _size(self, *, equity=10_000.0, risk=0.01, leverage=2.0, max_pos=10.0):
        rm = _rm(risk_per_trade=risk, max_position_pct=max_pos, max_leverage=50.0)
        d = rm.evaluate_entry(
            equity=equity, price=50_000, side=Side.LONG, stop_loss=49_000,
            leverage=leverage,
        )
        self.assertTrue(d.approved, d.reason)
        return d.size

    def test_leverage_is_only_a_ceiling(self):
        """'2배 위로는 아무것도 안 바뀝니다' — 천장이 안 물리면 배율은 무관하다."""
        sizes = {lev: self._size(leverage=lev) for lev in (2, 3, 5, 10, 20)}
        self.assertEqual(len(set(round(s, 9) for s in sizes.values())), 1, sizes)

    def test_low_leverage_is_worse_only_because_the_ceiling_bites(self):
        """'1x가 낮은 건 수익이 아니라 수량이 깎여서입니다' — 그 인과를 확인한다."""
        # 명목가 상한 0.30 × 1배 = 자본의 30%. 리스크 기준 수량은 그보다 크다.
        clipped = self._size(leverage=1, max_pos=0.30)
        full = self._size(leverage=2, max_pos=0.30)
        self.assertLess(clipped, full)
        self.assertAlmostEqual(clipped * 50_000, 10_000 * 0.30 * 1, places=6)

    def test_risk_per_trade_scales_size_proportionally(self):
        """'실제 손잡이는 risk_per_trade' — 두 배면 두 배."""
        base = self._size(risk=0.01)
        self.assertAlmostEqual(self._size(risk=0.02), base * 2, places=9)
        self.assertAlmostEqual(self._size(risk=0.005), base / 2, places=9)

    def test_compounding_is_automatic(self):
        """'자본을 매번 새로 읽으므로 복리는 자동' — 수량이 자본에 비례한다."""
        base = self._size(equity=10_000)
        self.assertAlmostEqual(self._size(equity=20_000), base * 2, places=9)
        self.assertAlmostEqual(self._size(equity=5_000), base / 2, places=9)

    def test_the_notional_cap_flattens_the_risk_curve(self):
        """'2% 위로 눕는 건 전략 한계가 아니라 명목가 상한' — 그 지점을 고정한다."""
        capped = [self._size(risk=r, max_pos=0.30) for r in (0.01, 0.02, 0.04)]
        free = [self._size(risk=r, max_pos=50.0) for r in (0.01, 0.02, 0.04)]
        self.assertAlmostEqual(capped[0], free[0], places=9)   # 1%는 아직 안 물림
        self.assertLess(capped[2], free[2])                    # 4%는 물림
        self.assertAlmostEqual(capped[2], capped[1], places=9)  # 물린 뒤로는 평평
