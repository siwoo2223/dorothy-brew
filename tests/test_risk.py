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
