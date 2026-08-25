"""커맨드라인 진입점.

    python -m dorothy backtest --synthetic
    python -m dorothy fetch --days 180 --out data/btc_5m.csv
    python -m dorothy backtest --csv data/btc_5m.csv --config config/config.yaml
    python -m dorothy paper --offline --csv data/btc_5m.csv
    python -m dorothy live --config config/config.yaml --yes-i-understand-the-risk
"""

from __future__ import annotations

import argparse
import logging
import sys

from . import __version__
from .backtest import compare as compare_mod
from .backtest import diagnostics
from .backtest import engine as backtest_engine
from .backtest import walkforward
from .config import Config, load_config
from .data import loader
from .engine import TradingEngine
from .exchange.paper import PaperExchange, ReplayExchange
from .journal.store import Journal
from .logging_setup import setup as setup_logging
from .strategy.base import get_strategy

log = logging.getLogger(__name__)


def _build_config(args) -> Config:
    cfg = load_config(args.config) if args.config else Config()
    if getattr(args, "symbol", None):
        cfg.exchange.symbol = args.symbol
    if getattr(args, "timeframe", None):
        cfg.exchange.timeframe = args.timeframe
    if getattr(args, "equity", None):
        cfg.initial_equity = args.equity
    if getattr(args, "strategy", None):
        cfg.strategy.name = args.strategy
        if args.config is None:
            cfg.strategy.params = {}   # 설정 파일 없이 전략만 바꾸면 기본 파라미터를 쓴다
    return cfg


def _load_candles(args, cfg: Config):
    if args.csv:
        candles = loader.load_csv(args.csv)
        log.info("CSV에서 %d개 캔들 로드", len(candles))
    elif args.synthetic:
        candles = loader.synthetic(n=args.bars, timeframe=cfg.exchange.timeframe)
        log.warning("합성 데이터입니다. 수익률에 아무 의미가 없습니다 — 파이프라인 점검용.")
    else:
        candles = loader.fetch_history(
            cfg.exchange.symbol, cfg.exchange.timeframe, days=args.days
        )
        log.info("거래소에서 %d개 캔들 수집", len(candles))
    return candles


def cmd_backtest(args) -> int:
    cfg = _build_config(args)
    cfg.mode = "backtest"
    errors = cfg.validate()
    if errors:
        for e in errors:
            log.error("설정 오류: %s", e)
        return 1

    candles = _load_candles(args, cfg)
    strategy = get_strategy(cfg.strategy.name, **cfg.strategy.params)
    log.info("전략: %s %s", strategy.name, cfg.strategy.params or "(기본값)")

    result = backtest_engine.run(candles, strategy, cfg)
    print(result.report())
    return 0


def cmd_diagnose(args) -> int:
    """진입이 왜 안 나오는지 단계별로 보여준다."""
    cfg = _build_config(args)
    cfg.mode = "backtest"
    candles = _load_candles(args, cfg)
    strategy = get_strategy(cfg.strategy.name, **cfg.strategy.params)
    log.info("전략: %s %s", strategy.name, cfg.strategy.params or "(기본값)")
    print(diagnostics.funnel(candles, strategy, step=args.step).report())
    return 0


def cmd_ablate(args) -> int:
    """요소를 하나씩 꺼가며 기여도를 측정한다."""
    cfg = _build_config(args)
    cfg.mode = "backtest"
    candles = _load_candles(args, cfg)
    log.info("제거 실험 시작 — 구성 %d개를 각각 백테스트합니다", len(diagnostics.ABLATIONS))
    rows = diagnostics.ablate(candles, cfg)
    print(diagnostics.ablation_report(rows))
    return 0


def cmd_compare(args) -> int:
    """여러 전략을 기준선과 함께 나란히 돌린다."""
    cfg = _build_config(args)
    cfg.mode = "backtest"
    candles = _load_candles(args, cfg)

    entries = None
    if args.only:
        entries = {name: {} for name in args.only}
    rows = compare_mod.compare(candles, cfg, entries=entries)
    print(compare_mod.comparison_report(rows))
    return 0


def cmd_walkforward(args) -> int:
    """학습 구간에서 파라미터를 고르고, 보지 않은 구간에서 성과를 잰다."""
    cfg = _build_config(args)
    cfg.mode = "backtest"
    candles = _load_candles(args, cfg)
    name = args.strategy or cfg.strategy.name
    log.info("워크포워드: %s (구간 %d개)", name, args.folds)
    result = walkforward.run(
        candles, cfg, strategy_name=name, folds=args.folds, train_ratio=args.train_ratio
    )
    print(result.report())
    return 0


def cmd_repaint(args) -> int:
    """엘리엇 카운트가 얼마나 자주 바뀌는지 실측한다.

    파동 카운트를 전략에 넣기 전에 이 숫자를 먼저 보라는 취지의 명령이다.
    """
    from .analysis.elliott import measure_repainting
    from .analysis.swings import find_swings

    cfg = _build_config(args)
    candles = _load_candles(args, cfg)
    swings = find_swings(candles)
    log.info("스윙 %d개로 카운트 안정성을 측정합니다", len(swings))
    print(measure_repainting(candles, swings, start=args.start, step=args.step).report())
    return 0


def cmd_fetch(args) -> int:
    cfg = _build_config(args)
    candles = loader.fetch_history(cfg.exchange.symbol, cfg.exchange.timeframe, days=args.days)
    loader.save_csv(candles, args.out)
    print(f"{len(candles)}개 캔들 저장 완료: {args.out}")
    return 0


def cmd_paper(args) -> int:
    cfg = _build_config(args)
    cfg.mode = "paper"

    if args.offline:
        candles = _load_candles(args, cfg)
        exchange = ReplayExchange(
            candles,
            equity=cfg.initial_equity,
            taker_fee=cfg.exchange.taker_fee,
            slippage=cfg.exchange.slippage,
        )
        cfg.poll_interval_sec = 0
        log.warning("오프라인 리플레이 모드 — 저장된 캔들을 최대 속도로 재생합니다.")
    else:
        from .exchange.bitget import BitgetExchange

        source = BitgetExchange("", "", "")   # 공개 시세만 사용
        exchange = PaperExchange(
            equity=cfg.initial_equity,
            taker_fee=cfg.exchange.taker_fee,
            slippage=cfg.exchange.slippage,
            source=source,
        )

    strategy = get_strategy(cfg.strategy.name, **cfg.strategy.params)
    engine = TradingEngine(cfg, exchange, strategy)

    if args.offline:
        # 리플레이는 캔들이 소진되면 멈춘다
        engine.start_offline_replay()
        print(f"\n리플레이 완료 — 체결 {len(exchange.trades)}건, "
              f"최종 자본 {exchange.total_equity():,.2f}")
    else:
        engine.start()
    return 0


def cmd_live(args) -> int:
    cfg = _build_config(args)
    cfg.mode = "live"

    if not args.yes_i_understand_the_risk:
        print(
            "\n실전 모드는 실제 자금으로 주문을 냅니다.\n"
            "  1) 백테스트를 통과했습니까?\n"
            "  2) 페이퍼 트레이딩을 최소 2주 돌렸습니까?\n"
            "  3) 이 계좌의 돈을 전부 잃어도 괜찮습니까?\n\n"
            "세 가지에 모두 '예'라면 --yes-i-understand-the-risk 를 붙여 다시 실행하세요.\n",
            file=sys.stderr,
        )
        return 2

    errors = cfg.validate()
    if errors:
        for e in errors:
            log.error("설정 오류: %s", e)
        return 1

    from .exchange.bitget import BitgetExchange

    exchange = BitgetExchange(
        cfg.api_key, cfg.api_secret, cfg.api_password, sandbox=args.sandbox
    )
    strategy = get_strategy(cfg.strategy.name, **cfg.strategy.params)
    TradingEngine(cfg, exchange, strategy).start()
    return 0


def cmd_status(args) -> int:
    cfg = _build_config(args)
    journal = Journal(cfg.db_path)
    rows = journal.recent_trades(args.limit)
    if not rows:
        print("기록된 매매가 없습니다.")
        return 0
    print(f"{'시각':<20} {'심볼':<18} {'방향':<6} {'손익':>12}  사유")
    print("─" * 78)
    total = 0.0
    for r in rows:
        import datetime as dt

        ts = dt.datetime.fromtimestamp(r["closed_at"] / 1000).strftime("%Y-%m-%d %H:%M:%S")
        print(f"{ts:<20} {r['symbol']:<18} {r['side']:<6} {r['net_pnl']:>+12,.2f}  {r['reason']}")
        total += r["net_pnl"]
    print("─" * 78)
    print(f"{'합계':<46}{total:>+12,.2f}")
    journal.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    # 공통 옵션을 부모 파서로 두면 `dorothy --config x backtest` 와
    # `dorothy backtest --config x` 가 모두 동작한다.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="YAML 설정 파일 경로")
    common.add_argument("--log-level", default="INFO", help="DEBUG/INFO/WARNING/ERROR")

    p = argparse.ArgumentParser(
        prog="dorothy", description="Bitget 선물 자동매매 봇", parents=[common]
    )
    p.add_argument("--version", action="version", version=f"dorothy-brew {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def add_data_args(sp):
        sp.add_argument("--csv", help="캔들 CSV 파일")
        sp.add_argument("--synthetic", action="store_true", help="합성 데이터로 파이프라인만 점검")
        sp.add_argument("--bars", type=int, default=3000, help="합성 데이터 캔들 수")
        sp.add_argument("--days", type=int, default=90, help="거래소에서 받아올 기간(일)")
        sp.add_argument("--symbol")
        sp.add_argument("--timeframe")
        sp.add_argument("--equity", type=float, help="시작 자본")
        sp.add_argument("--strategy", help="전략 이름 (ema_cross / ict_confluence)")

    bt = sub.add_parser("backtest", parents=[common], help="과거 데이터로 전략 검증")
    add_data_args(bt)
    bt.set_defaults(func=cmd_backtest)

    dg = sub.add_parser("diagnose", parents=[common], help="진입 조건 깔때기 분석")
    add_data_args(dg)
    dg.add_argument("--step", type=int, default=1, help="몇 봉마다 평가할지 (크면 빠름)")
    dg.set_defaults(func=cmd_diagnose)

    ab = sub.add_parser("ablate", parents=[common], help="요소별 기여도 측정 (제거 실험)")
    add_data_args(ab)
    ab.set_defaults(func=cmd_ablate)

    cp = sub.add_parser("compare", parents=[common], help="전략 비교 (기준선 포함)")
    add_data_args(cp)
    cp.add_argument("--only", nargs="+", help="비교할 전략 이름들 (생략 시 전체)")
    cp.set_defaults(func=cmd_compare)

    wf = sub.add_parser("walkforward", parents=[common], help="워크포워드 검증 (과최적화 탐지)")
    add_data_args(wf)
    wf.add_argument("--folds", type=int, default=4, help="구간 수")
    wf.add_argument("--train-ratio", type=float, default=0.7, help="구간 내 학습 비율")
    wf.set_defaults(func=cmd_walkforward)

    rp = sub.add_parser("repaint", parents=[common], help="엘리엇 카운트 안정성 측정")
    add_data_args(rp)
    rp.add_argument("--start", type=int, default=150, help="측정 시작 봉")
    rp.add_argument("--step", type=int, default=1)
    rp.set_defaults(func=cmd_repaint)

    ft = sub.add_parser("fetch", parents=[common], help="과거 캔들 수집 후 CSV 저장")
    ft.add_argument("--days", type=int, default=180)
    ft.add_argument("--out", default="data/candles.csv")
    ft.add_argument("--symbol")
    ft.add_argument("--timeframe")
    ft.set_defaults(func=cmd_fetch)

    pp = sub.add_parser("paper", parents=[common], help="모의 매매 (주문 없음)")
    add_data_args(pp)
    pp.add_argument("--offline", action="store_true", help="저장된 캔들을 재생 (네트워크 불필요)")
    pp.set_defaults(func=cmd_paper)

    lv = sub.add_parser("live", parents=[common], help="실전 매매 (실제 주문)")
    lv.add_argument("--yes-i-understand-the-risk", action="store_true")
    lv.add_argument("--sandbox", action="store_true", help="거래소 데모/테스트넷 사용")
    lv.add_argument("--symbol")
    lv.add_argument("--timeframe")
    lv.set_defaults(func=cmd_live)

    st = sub.add_parser("status", parents=[common], help="매매 기록 조회")
    st.add_argument("--limit", type=int, default=20)
    st.set_defaults(func=cmd_status)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.log_level)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n중단됨", file=sys.stderr)
        return 130
    except (FileNotFoundError, ValueError, ImportError, KeyError) as exc:
        log.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
