"""실행 전 점검 테스트.

이 명령은 사용자가 처음 만나는 화면이다. 여기가 틀리면 멀쩡한 환경을
문제 있다고 하거나, 문제 있는 환경을 통과시킨다. 둘 다 나쁘다.
"""

import tempfile
import unittest
from pathlib import Path

from dorothy import doctor


class CheckTests(unittest.TestCase):
    def test_python_version_passes_here(self):
        self.assertTrue(doctor._python().ok)

    def test_missing_module_reports_how_to_install(self):
        check = doctor._module("절대없는모듈", "테스트", "some-package")
        self.assertFalse(check.ok)
        self.assertIn("pip install some-package", check.fix)

    def test_present_module_reports_version(self):
        check = doctor._module("json", "테스트", "json")
        self.assertTrue(check.ok)

    def test_optional_module_is_not_fatal(self):
        check = doctor._module("절대없는모듈", "테스트", "x", fatal=False)
        self.assertFalse(check.ok)
        self.assertFalse(check.fatal)


class ConfigCheckTests(unittest.TestCase):
    def test_missing_path_tells_you_to_pass_one(self):
        check = doctor._config(None)
        self.assertFalse(check.ok)
        self.assertIn("--config", check.fix)

    def test_nonexistent_file_suggests_copying_an_example(self):
        check = doctor._config("/없는/경로/config.yaml")
        self.assertFalse(check.ok)
        self.assertIn("cp config/", check.fix)

    def test_valid_config_summarises_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.yaml"
            path.write_text(
                "mode: paper\n"
                "exchange:\n  symbol: \"ETH/USDT:USDT\"\n  timeframe: \"4h\"\n"
                "strategy:\n  name: donchian\n",
                encoding="utf-8",
            )
            check = doctor._config(str(path))
            self.assertTrue(check.ok, check.detail)
            self.assertIn("ETH/USDT:USDT", check.detail)
            self.assertIn("4h", check.detail)

    def test_broken_yaml_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.yaml"
            path.write_text("mode: [unclosed\n", encoding="utf-8")
            check = doctor._config(str(path))
            self.assertFalse(check.ok)


class SecretsCheckTests(unittest.TestCase):
    def test_paper_does_not_need_keys(self):
        check = doctor._secrets("paper")
        self.assertTrue(check.ok)
        self.assertIn("불필요", check.detail)

    def test_backtest_does_not_need_keys(self):
        self.assertTrue(doctor._secrets("backtest").ok)

    def test_live_without_keys_fails_and_warns_about_withdrawal(self):
        import os

        saved = {k: os.environ.pop(k, None)
                 for k in ("BITGET_API_KEY", "BITGET_API_SECRET",
                           "BITGET_API_PASSPHRASE")}
        try:
            check = doctor._secrets("live")
            self.assertFalse(check.ok)
            self.assertIn("출금", check.fix)
            self.assertIn("IP", check.fix)
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v


class ReportTests(unittest.TestCase):
    def checks(self, *specs):
        return [doctor.Check(n, ok, d, fix, fatal)
                for n, ok, d, fix, fatal in specs]

    def test_all_good_says_ready(self):
        report = doctor.report(self.checks(("파이썬", True, "3.11", "", True)))
        self.assertIn("실행 준비가 됐습니다", report)

    def test_blocking_failure_is_counted(self):
        report = doctor.report(self.checks(
            ("파이썬", True, "3.11", "", True),
            ("설정", False, "없음", "cp ...", True),
        ))
        self.assertIn("해결해야 할 항목 1개", report)
        self.assertIn("cp ...", report)

    def test_optional_failure_does_not_block(self):
        report = doctor.report(self.checks(
            ("파이썬", True, "3.11", "", True),
            ("websockets", False, "없음", "pip install websockets", False),
        ))
        self.assertIn("실행 준비가 됐습니다", report)
        self.assertIn("없어도 매매는 됩니다", report)

    def test_every_failure_shows_a_fix(self):
        """고치는 법 없이 '틀렸다'고만 하면 사용자가 막힌다."""
        checks = doctor.run(None, check_network=False)
        for check in checks:
            if not check.ok:
                self.assertTrue(check.fix, f"{check.name}에 해결 방법이 없습니다")


class RunTests(unittest.TestCase):
    def test_offline_skips_the_network_check(self):
        names = [c.name for c in doctor.run(None, check_network=False)]
        self.assertNotIn("거래소 연결", names)

    def test_covers_the_essentials(self):
        names = " ".join(c.name for c in doctor.run(None, check_network=False))
        for expected in ("파이썬", "yaml", "ccxt", "설정 파일", "API 키"):
            self.assertIn(expected, names)


if __name__ == "__main__":
    unittest.main()


class RunningGuideTests(unittest.TestCase):
    """RUNNING.md에 적힌 명령·플래그가 실제로 존재하는지.

    안내서가 틀리면 사용자가 첫 단계에서 막힌다. 실제로 fetch를 --csv로
    적었다가 걸렸다 (진짜 플래그는 --out).

    ⚠ 이 테스트를 처음 짰을 때 정규식이 줄바꿈 계속(\\)과 한글을 못 봐서
       아무것도 못 잡았다. 그래서 아래 test_the_checks_have_teeth로
       '틀린 안내서를 넣으면 실제로 실패하는지'를 함께 검사한다.
    """

    def guide(self) -> str:
        path = Path(__file__).resolve().parent.parent / "RUNNING.md"
        return path.read_text(encoding="utf-8")

    @staticmethod
    def commands(text: str):
        """안내서에서 (명령, 플래그들)을 뽑는다. 줄바꿈 계속을 먼저 이어붙인다."""
        import re

        joined = re.sub(r"\\\s*\n\s*", " ", text)      # 줄 끝 \ 를 이어붙인다
        out = []
        for line in joined.splitlines():
            if "dorothy.cli" not in line:
                continue
            after = line.split("dorothy.cli", 1)[1].strip()
            parts = after.split()
            if not parts:
                continue
            name = parts[0].strip("`")
            flags = [p for p in parts[1:] if p.startswith("--")]
            out.append((name, flags))
        return out

    def available(self):
        from dorothy.cli import build_parser

        parser = build_parser()
        actions = [a for a in parser._subparsers._group_actions if a.choices]
        self.assertTrue(actions, "CLI에서 하위 명령을 못 읽었습니다")
        return actions[0].choices

    def test_guide_exists(self):
        self.assertIn("내 컴퓨터에서 돌리기", self.guide())

    def test_it_actually_found_commands(self):
        """뽑아낸 게 없으면 아래 검사들이 전부 무의미해진다."""
        found = self.commands(self.guide())
        self.assertGreaterEqual(len(found), 8, f"명령을 {len(found)}개밖에 못 찾았습니다")
        self.assertTrue(any(flags for _, flags in found), "플래그를 하나도 못 찾았습니다")

    def test_every_documented_subcommand_exists(self):
        available = self.available()
        missing = {n for n, _ in self.commands(self.guide()) if n not in available}
        self.assertFalse(missing, f"안내서에 없는 명령이 적혀 있습니다: {sorted(missing)}")

    def test_documented_flags_exist(self):
        available = self.available()
        problems = []
        for name, flags in self.commands(self.guide()):
            if name not in available:
                continue
            known = set()
            for action in available[name]._actions:
                known.update(action.option_strings)
            problems += [f"{name} {f}" for f in flags if f not in known]
        self.assertFalse(problems, f"없는 플래그: {problems}")

    def test_the_checks_have_teeth(self):
        """틀린 안내서를 넣으면 실제로 잡히는지. 이게 없으면 위 둘이
        통과만 하는 껍데기인지 알 수 없다 — 처음에 실제로 그랬다."""
        available = self.available()

        bad_command = "PYTHONPATH=src python3 -m dorothy.cli 없는명령 --config x"
        found = self.commands(bad_command)
        self.assertEqual(found[0][0], "없는명령")
        self.assertNotIn(found[0][0], available)

        wrapped = ("PYTHONPATH=src python3 -m dorothy.cli fetch \\\n"
                   "    --config config/config.yaml --csv data/btc.csv")
        found = self.commands(wrapped)
        self.assertEqual(found[0][0], "fetch")
        self.assertIn("--csv", found[0][1],
                      "줄바꿈 뒤의 플래그를 못 봤습니다")
        known = set()
        for action in available["fetch"]._actions:
            known.update(action.option_strings)
        self.assertNotIn("--csv", known, "fetch에 --csv가 생겼다면 안내서를 되돌리세요")

    def test_warns_about_withdrawal_permission(self):
        guide = self.guide()
        self.assertIn("출금 권한", guide)
        self.assertIn("IP", guide)

    def test_tells_users_to_paper_trade_first(self):
        self.assertIn("페이퍼", self.guide())

    def test_documents_both_kill_switch_outcomes(self):
        """포지션이 없으면 프로세스가 계속 돈다. 이걸 빠뜨리면
        껐다고 믿고 자리를 뜬다."""
        guide = self.guide()
        self.assertIn("KILL", guide)
        self.assertIn("계속 돕니다", guide)
