import json
import os
import tempfile
import unittest

from dorothy_brew import data, synth
from dorothy_brew.core import Candle, Series


class TestParsing(unittest.TestCase):
    def test_parse_ts_formats(self):
        self.assertEqual(data.parse_ts(1700000000), 1700000000)
        self.assertEqual(data.parse_ts(1700000000000), 1700000000)      # ms
        self.assertEqual(data.parse_ts("1700000000"), 1700000000)
        self.assertEqual(data.parse_ts("2023-11-14T22:13:20Z"), 1700000000)
        self.assertEqual(data.parse_ts("2023-11-14 22:13:20"), 1700000000)
        self.assertEqual(data.parse_ts("garbage"), 0)
        self.assertEqual(data.parse_ts(""), 0)

    def test_csv_round_trip(self):
        original = synth.bull_flag()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "btc.csv")
            data.write_csv(original, path)
            loaded = data.load(path, timeframe="1h")
        self.assertEqual(len(loaded), len(original))
        self.assertEqual(loaded.symbol, "btc")
        for a, b in zip(original, loaded):
            self.assertAlmostEqual(a.close, b.close)
            self.assertAlmostEqual(a.volume, b.volume, places=6)

    def test_header_aliases_and_column_order(self):
        rows = [["date", "Close", "Open", "High", "Low", "Vol"],
                ["2023-01-01", "11", "10", "12", "9", "500"],
                ["2023-01-02", "12", "11", "13", "10", "600"]]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.csv")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(",".join(r) for r in rows))
            series = data.load_csv(path)
        self.assertEqual(len(series), 2)
        self.assertEqual(series[0].open, 10)
        self.assertEqual(series[0].close, 11)
        self.assertEqual(series[1].volume, 600)

    def test_headerless_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "raw.csv")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("1700000000,10,12,9,11,100\n1700003600,11,13,10,12,120\n")
            series = data.load_csv(path)
        self.assertEqual(len(series), 2)
        self.assertEqual(series[1].high, 13)

    def test_malformed_rows_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.csv")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("timestamp,open,high,low,close,volume\n"
                         "1700000000,10,12,9,11,100\n"
                         "1700003600,,,,,\n"
                         "\n"
                         "1700007200,11,13,10,12,120\n")
            series = data.load_csv(path)
        self.assertEqual(len(series), 2)

    def test_json_list_and_dict_forms(self):
        with tempfile.TemporaryDirectory() as tmp:
            klines = os.path.join(tmp, "k.json")
            with open(klines, "w", encoding="utf-8") as fh:
                json.dump([[1700000000000, "10", "12", "9", "11", "100"]], fh)
            self.assertEqual(len(data.load(klines)), 1)

            dicts = os.path.join(tmp, "d.json")
            with open(dicts, "w", encoding="utf-8") as fh:
                json.dump({"data": [{"time": 1700000000, "open": 10, "high": 12,
                                     "low": 9, "close": 11, "volume": 100}]}, fh)
            series = data.load(dicts)
        self.assertEqual(series[0].close, 11)

    def test_sorted_by_timestamp(self):
        rows = [[2, 10, 12, 9, 11, 1], [1, 9, 11, 8, 10, 1]]
        series = data.from_rows(rows)
        self.assertLess(series[0].ts, series[1].ts)


class TestResample(unittest.TestCase):
    def test_aggregation(self):
        candles = [Candle(i * 60, 10 + i, 11 + i, 9 + i, 10.5 + i, 100) for i in range(12)]
        series = Series(candles, "X", "1m")
        hourly = data.resample(series, 4, "4m")
        self.assertEqual(len(hourly), 3)
        self.assertEqual(hourly[0].open, candles[0].open)
        self.assertEqual(hourly[0].close, candles[3].close)
        self.assertEqual(hourly[0].high, max(c.high for c in candles[:4]))
        self.assertEqual(hourly[0].low, min(c.low for c in candles[:4]))
        self.assertEqual(hourly[0].volume, 400)
        self.assertEqual(hourly.timeframe, "4m")

    def test_factor_one_is_identity(self):
        series = synth.bull_flag()
        self.assertIs(data.resample(series, 1), series)


if __name__ == "__main__":
    unittest.main()
