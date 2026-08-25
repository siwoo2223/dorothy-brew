import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from dorothy.config import Config, load_config


class TestConfig(unittest.TestCase):
    def test_defaults_are_valid(self):
        self.assertEqual(Config().validate(), [])

    def test_reckless_risk_per_trade_is_rejected(self):
        cfg = Config()
        cfg.risk.risk_per_trade = 0.5
        self.assertTrue(any("risk_per_trade" in e for e in cfg.validate()))

    def test_leverage_above_cap_is_rejected(self):
        cfg = Config()
        cfg.exchange.leverage = 20
        cfg.risk.max_leverage = 3
        self.assertTrue(any("leverage" in e for e in cfg.validate()))

    def test_live_mode_requires_credentials(self):
        cfg = Config()
        cfg.mode = "live"
        self.assertTrue(any("BITGET_API_KEY" in e for e in cfg.validate()))

    def test_unknown_mode_is_rejected(self):
        cfg = Config()
        cfg.mode = "허수아비"
        self.assertTrue(any("mode" in e for e in cfg.validate()))

    def test_yaml_merges_nested_sections(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "c.yaml"
            p.write_text(
                "initial_equity: 5000\n"
                "exchange:\n  symbol: ETH/USDT:USDT\n  leverage: 3\n"
                "risk:\n  risk_per_trade: 0.005\n"
                "strategy:\n  name: ema_cross\n  params:\n    fast: 9\n    slow: 21\n",
                encoding="utf-8",
            )
            cfg = load_config(p)
        self.assertEqual(cfg.initial_equity, 5000)
        self.assertEqual(cfg.exchange.symbol, "ETH/USDT:USDT")
        self.assertEqual(cfg.exchange.leverage, 3)
        self.assertEqual(cfg.risk.risk_per_trade, 0.005)
        self.assertEqual(cfg.strategy.params["fast"], 9)
        self.assertEqual(cfg.exchange.timeframe, "5m")   # 미지정 항목은 기본값 유지

    def test_typo_in_config_key_raises(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "c.yaml"
            p.write_text("exchange:\n  leverag: 3\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(p)

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            load_config("/없는/경로/config.yaml")

    def test_secrets_come_only_from_env(self):
        cfg = Config()
        self.assertNotIn("api_key", cfg.__dict__)   # 필드가 아니라 프로퍼티여야 한다


if __name__ == "__main__":
    unittest.main()
