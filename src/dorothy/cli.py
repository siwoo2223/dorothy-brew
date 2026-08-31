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
from .backtest import montecarlo
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


def cmd_journal(args) -> int:
    """실제 매매일지를 분석해 내 우위가 어디에 있는지 본다."""
    from .journal.analyze import Analysis, report
    from .journal.records import load_csv, load_json

    path = args.file
    trades = load_json(path) if str(path).endswith(".json") else load_csv(path)
    if not trades:
        log.error("읽어들인 매매 기록이 없습니다: %s", path)
        return 1
    log.info("매매 기록 %d건 로드", len(trades))
    print(report(Analysis(trades)))
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


def cmd_fetch_funding(args) -> int:
    """거래소에서 과거 펀딩률을 받아 CSV로 저장한다 (API 키 불필요)."""
    from .data import funding as funding_mod

    cfg = _build_config(args)
    series = funding_mod.fetch_history(cfg.exchange.symbol, days=args.days)
    funding_mod.save_csv(series, args.out)
    print(f"펀딩률 {len(series)}개 저장 완료: {args.out}")
    if series:
        first, last = series.points[0], series.points[-1]
        print(f"  기간: {first.ts} ~ {last.ts}")
        print(f"  최근 펀딩률: {last.rate * 100:.4f}%")
    return 0


def cmd_session(args) -> int:
    """전략이 어떤 시간대에서 벌고 잃는지 분해한다 (다중검정 보정 포함)."""
    from .backtest import session_report

    cfg = _build_config(args)
    cfg.mode = "backtest"
    candles = _load_candles(args, cfg)
    strategy = get_strategy(cfg.strategy.name, **cfg.strategy.params)
    print(session_report.analyse(candles, strategy, cfg).report())
    return 0


def cmd_regime(args) -> int:
    """전략이 어떤 시장 국면에서 벌고 잃는지 분해한다."""
    from .backtest import regime_report

    cfg = _build_config(args)
    cfg.mode = "backtest"
    candles = _load_candles(args, cfg)
    strategy = get_strategy(cfg.strategy.name, **cfg.strategy.params)
    print(regime_report.analyse(candles, strategy, cfg, window=args.window).report())
    return 0


def cmd_doctor(args) -> int:
    """실행 전 점검 — 뭐가 빠졌는지 먼저 알려준다."""
    from . import doctor

    checks = doctor.run(args.config, check_network=not args.offline)
    print(doctor.report(checks))
    return 0 if all(c.ok or not c.fatal for c in checks) else 1


def cmd_collect(args) -> int:
    """거래소 체결·호가를 실시간으로 모은다. API 키가 필요 없습니다."""
    from .collect.runner import CollectSpec, run
    from .collect.store import Store

    spec = CollectSpec(
        venue=args.venue, symbol=args.symbol,
        trades=not args.no_trades, book=not args.no_book,
        book_speed=args.speed, max_seconds=args.seconds,
    )
    print(f"거래소 {spec.venue}  종목 {spec.symbol}")
    print(f"스트림 {', '.join(spec.streams())}")
    print(f"저장   {args.db}")
    if args.probe:
        print("\n점검 모드 — 몇 초만 받아보고 끝냅니다.\n")

    with Store(args.db) as store:
        try:
            run(spec, store, probe=args.probe)
        except KeyboardInterrupt:
            print("\n중단 — 저장하고 종료합니다.")
        print()
        print(store.summary())
    return 0


def cmd_collect_status(args) -> int:
    """모아둔 데이터가 얼마나 되는지 본다."""
    from .collect.store import Store

    with Store(args.db) as store:
        print(store.summary())
        for key in ("venue", "symbol", "streams"):
            value = store.get_meta(key)
            if value:
                print(f"  {key:<10}  {value}")
    return 0


def cmd_grid(args) -> int:
    """격자 매매 — 방향을 맞히지 않고 진동을 먹는다. 전부 지정가."""
    from .strategy.grid import GridSpec, simulate

    cfg = _build_config(args)
    cfg.mode = "backtest"
    candles = _load_candles(args, cfg)
    spec = GridSpec(
        levels=args.levels, step_atr=args.step, size_per_level=args.size,
        close_daily=not args.no_daily_close, max_hold_bars=args.max_hold,
    )
    result = simulate(
        candles, spec, maker_fee=cfg.exchange.maker_fee,
        taker_fee=cfg.exchange.taker_fee, slippage=cfg.exchange.slippage,
    )
    print(result.report(spec, {
        "maker": cfg.exchange.maker_fee, "taker": cfg.exchange.taker_fee,
        "slippage": cfg.exchange.slippage,
    }))
    return 0


def cmd_costfloor(args) -> int:
    """이 타임프레임에서 수수료를 넘는 것이 애초에 가능한지 본다."""
    from .backtest import cost_floor

    cfg = _build_config(args)
    cfg.mode = "backtest"
    candles = _load_candles(args, cfg)
    print(cost_floor.analyse(candles, cfg, timeframes=tuple(args.timeframes)).report())
    return 0


def cmd_leverage(args) -> int:
    """배율을 올리면 정말 더 버는지 — 변동성 드래그·펀딩비·청산까지 넣고 잰다."""
    from .backtest.leverage import analyse_leverage

    cfg = _build_config(args)
    cfg.mode = "backtest"
    candles = _load_candles(args, cfg)
    print(analyse_leverage(
        candles, cfg, levels=tuple(args.levels), target_vol=args.target_vol,
        lookback=args.lookback, rebalance_band=args.band,
    ).report())
    return 0


def cmd_voltarget(args) -> int:
    """방향을 맞히지 않고 변동성에 반비례해 노출만 조절했을 때를 본다."""
    from .backtest import vol_target

    cfg = _build_config(args)
    cfg.mode = "backtest"
    candles = _load_candles(args, cfg)
    print(vol_target.analyse(
        candles, cfg, target_vol=args.target_vol, lookback=args.lookback,
        max_leverage=args.max_leverage, rebalance_band=args.band, venue=args.venue,
    ).report())
    return 0


def cmd_side(args) -> int:
    """전략 신호를 롱/숏으로 갈라 어느 쪽이 우위를 내는지 본다."""
    from .backtest import side_report

    cfg = _build_config(args)
    cfg.mode = "backtest"
    candles = _load_candles(args, cfg)
    strategy = get_strategy(cfg.strategy.name, **cfg.strategy.params)
    print(side_report.analyse(candles, strategy, cfg, max_bars=args.max_bars).report())
    return 0


def cmd_edge(args) -> int:
    """신호의 우위가 겹쳐 세서 생긴 것인지 확인한다.

    돌파 신호는 서로 겹친다. 겹친 채로 t를 내면 같은 가격 움직임을 여러 번
    세게 되어 t가 부풀려진다. 이 저장소가 실제로 그렇게 잘못 발표했다.
    """
    from .analysis.concurrency import analyze, signal_outcomes
    from .models import Side

    cfg = _build_config(args)
    cfg.mode = "backtest"
    candles = _load_candles(args, cfg)
    strategy = get_strategy(cfg.strategy.name, **cfg.strategy.params)
    cost = 2 * (cfg.exchange.taker_fee + cfg.exchange.slippage)

    outcomes = signal_outcomes(candles, strategy, max_bars=args.max_bars, step=args.step)
    print(f"왕복 비용 {cost * 100:.2f}%  ·  삼중 장벽 {args.max_bars}봉\n")
    for side, label in ((Side.LONG, "롱"), (Side.SHORT, "숏")):
        got = outcomes[side]
        if len(got) < 3:
            print(f"[{label}] 신호 {len(got)}건 — 판단 불가\n")
            continue
        print(f"[{label}]")
        print(analyze(got, cost).render())
        print()
    return 0


def cmd_search(args) -> int:
    """설정을 훑어 겹침을 뺀 우위를 찾는다 — 다중검정 보정과 검증기간 포함.

    많이 재면 우연히 좋아 보이는 것이 반드시 나온다. 그래서 몇 번 쟀는지를
    세서 임계값을 올리고, 후보를 처음 보는 구간에 던져 확인한다.
    """
    from .analysis.search import Candidate, SearchReport, evaluate

    cfg = _build_config(args)
    cfg.mode = "backtest"
    candles = _load_candles(args, cfg)
    cost = 2 * (cfg.exchange.taker_fee + cfg.exchange.slippage)

    values = [v.strip() for v in args.values.split(",") if v.strip()]
    if len(values) < 2:
        print("--values에 최소 2개를 주세요. 하나만 재는 건 탐색이 아닙니다.")
        return 1

    def parse(v: str):
        for cast in (int, float):
            try:
                return cast(v)
            except ValueError:
                continue
        if v.lower() in ("true", "false"):
            return v.lower() == "true"
        return v

    trials = []
    for raw in values:
        params = dict(cfg.strategy.params)
        params[args.param] = parse(raw)
        cand = Candidate(cfg.strategy.name, cfg.exchange.timeframe, params)
        try:
            trials.append(
                evaluate(cand, candles, cost=cost, split=args.split,
                         max_bars=args.max_bars)
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  건너뜀 {cand.label}: {exc}")

    if not trials:
        print("잰 것이 없습니다.")
        return 1
    print()
    print(SearchReport(trials, min_samples=args.min_samples).render(top=len(trials)))
    return 0


def cmd_metalabel(args) -> int:
    """1차 전략의 신호를 모델이 걸러낼 수 있는지 검증한다 (누수 방지 포함)."""
    from .ml.meta import build_dataset, train

    cfg = _build_config(args)
    cfg.mode = "backtest"
    candles = _load_candles(args, cfg)
    strategy = get_strategy(cfg.strategy.name, **cfg.strategy.params)

    print(f"표본을 만드는 중… (캔들 {len(candles)}개, 봉마다 신호 판정)")
    samples = build_dataset(candles, strategy, max_bars=args.max_bars, step=args.step)
    print(f"표본 {len(samples)}개")
    if len(samples) < 60:
        print("표본이 너무 적습니다. 기간을 늘리거나 신호가 잦은 전략을 쓰세요.")
        return 1

    result = train(
        samples, candles=candles, config=cfg, folds=args.folds,
        embargo_bars=args.embargo, threshold=args.threshold, seed=args.seed,
    )
    print(result.report())

    if args.maker:
        from .backtest import maker_report

        keep = {i for i, p in result.oos_predictions.items() if p >= args.threshold}
        print()
        print(maker_report.analyse(
            candles, samples, cfg, offset_atr=args.maker_offset,
            timeout_bars=args.maker_timeout, max_bars=args.max_bars, keep=keep,
        ).report())
    return 0


def cmd_montecarlo(args) -> int:
    """백테스트 매매를 재배열해 결과 분포를 본다."""
    from .backtest.engine import PaperExchange  # noqa: F401

    cfg = _build_config(args)
    cfg.mode = "backtest"
    candles = _load_candles(args, cfg)
    strategy = get_strategy(cfg.strategy.name, **cfg.strategy.params)

    # 백테스트를 돌려 매매 목록을 얻는다
    from .backtest import engine as bt_engine
    from .exchange.paper import PaperExchange as Paper

    exchange = Paper(
        equity=cfg.initial_equity, taker_fee=cfg.exchange.taker_fee,
        slippage=cfg.exchange.slippage, min_size=cfg.exchange.min_order_size,
        size_step=cfg.exchange.size_step, funding_rate=cfg.exchange.funding_rate,
        funding_interval_hours=cfg.exchange.funding_interval_hours,
    )
    bt_engine.run(candles, strategy, cfg)   # 지표 계산용 (결과는 아래에서 다시 뽑는다)

    trades = _collect_trades(candles, strategy, cfg)
    log.info("매매 %d건으로 %d개 경로를 시뮬레이션합니다", len(trades), args.runs)
    result = montecarlo.run(trades, cfg.initial_equity, runs=args.runs, seed=args.seed)
    print(result.report())
    return 0


def _collect_trades(candles, strategy, cfg):
    """백테스트를 한 번 더 돌려 체결 목록을 가져온다."""
    from .backtest import engine as bt_engine

    captured = {}
    original = bt_engine.PaperExchange

    class Capturing(original):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            captured["exchange"] = self

    bt_engine.PaperExchange = Capturing
    try:
        bt_engine.run(candles, strategy, cfg)
    finally:
        bt_engine.PaperExchange = original
    return captured["exchange"].trades


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
            # 펀딩비·최소 주문 수량을 빠뜨리면 페이퍼가 백테스트보다 좋게 나온다.
            # 실전 직전에 딱 속기 좋은 자리다 (실제로 자본이 4.8% 부풀었다).
            funding_rate=cfg.exchange.funding_rate,
            funding_interval_hours=cfg.exchange.funding_interval_hours,
            min_size=cfg.exchange.min_order_size,
            size_step=cfg.exchange.size_step,
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
            funding_rate=cfg.exchange.funding_rate,
            funding_interval_hours=cfg.exchange.funding_interval_hours,
            min_size=cfg.exchange.min_order_size,
            size_step=cfg.exchange.size_step,
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

    jn = sub.add_parser("journal", parents=[common], help="매매일지 분석 (내 실제 우위 찾기)")
    jn.add_argument("file", help="노션에서 내보낸 CSV, 또는 JSON")
    jn.set_defaults(func=cmd_journal)

    cp = sub.add_parser("compare", parents=[common], help="전략 비교 (기준선 포함)")
    add_data_args(cp)
    cp.add_argument("--only", nargs="+", help="비교할 전략 이름들 (생략 시 전체)")
    cp.set_defaults(func=cmd_compare)

    wf = sub.add_parser("walkforward", parents=[common], help="워크포워드 검증 (과최적화 탐지)")
    add_data_args(wf)
    wf.add_argument("--folds", type=int, default=4, help="구간 수")
    wf.add_argument("--train-ratio", type=float, default=0.7, help="구간 내 학습 비율")
    wf.set_defaults(func=cmd_walkforward)

    ff = sub.add_parser("fetch-funding", parents=[common], help="과거 펀딩률 수집")
    ff.add_argument("--days", type=int, default=365)
    ff.add_argument("--out", default="data/funding.csv")
    ff.add_argument("--symbol")
    ff.set_defaults(func=cmd_fetch_funding)

    se = sub.add_parser("session", parents=[common], help="시간대별 성과 분해")
    add_data_args(se)
    se.set_defaults(func=cmd_session)

    rg = sub.add_parser("regime", parents=[common], help="국면별 성과 분해")
    add_data_args(rg)
    rg.add_argument("--window", type=int, default=200, help="국면 판정에 쓸 봉 수")
    rg.set_defaults(func=cmd_regime)

    mc = sub.add_parser("montecarlo", parents=[common], help="매매 재배열로 결과 분포 추정")
    add_data_args(mc)
    mc.add_argument("--runs", type=int, default=5000, help="시뮬레이션 경로 수")
    mc.add_argument("--seed", type=int, default=42)
    mc.set_defaults(func=cmd_montecarlo)

    dr = sub.add_parser("doctor", parents=[common],
                        help="실행 전 점검 — 의존성·설정·거래소 연결")
    dr.add_argument("--offline", action="store_true", help="네트워크 점검 건너뛰기")
    dr.set_defaults(func=cmd_doctor)

    co = sub.add_parser("collect", parents=[common],
                        help="실시간 체결·호가 수집 (websockets 필요, API 키 불필요)")
    co.add_argument("--db", default="data/collect.db", help="저장할 SQLite 경로")
    co.add_argument("--venue", default="binance-futures",
                    choices=("binance-futures", "binance-spot"))
    co.add_argument("--symbol", default="BTCUSDT")
    co.add_argument("--speed", default="100ms", choices=("100ms", "250ms", "500ms"),
                    help="호가 갱신 주기. 느릴수록 용량이 작습니다")
    co.add_argument("--no-trades", action="store_true", help="체결 수집 끄기")
    co.add_argument("--no-book", action="store_true",
                    help="호가 수집 끄기 (용량이 1/100로 줄어듭니다)")
    co.add_argument("--seconds", type=float, help="이 시간 뒤 종료 (미지정이면 계속)")
    co.add_argument("--probe", action="store_true",
                    help="몇 초만 받아보고 눈으로 확인 — 처음엔 이걸로 시작하세요")
    co.set_defaults(func=cmd_collect)

    cs = sub.add_parser("collect-status", parents=[common], help="모은 데이터 현황")
    cs.add_argument("--db", default="data/collect.db")
    cs.set_defaults(func=cmd_collect_status)

    gd = sub.add_parser("grid", parents=[common],
                        help="격자 매매 — 지정가만 쓰고 진동을 먹는다")
    add_data_args(gd)
    gd.add_argument("--levels", type=int, default=5, help="한쪽 격자 개수")
    gd.add_argument("--step", type=float, default=0.25, help="격자 간격 (ATR 배수)")
    gd.add_argument("--size", type=float, default=0.20, help="레벨당 명목가 (자본 대비)")
    gd.add_argument("--max-hold", type=int, default=24, help="보유 한도 (봉)")
    gd.add_argument("--no-daily-close", action="store_true", help="하루 마감 청산 끄기")
    gd.set_defaults(func=cmd_grid)

    cf = sub.add_parser("costfloor", parents=[common],
                        help="타임프레임별 손익분기 승률 — 애초에 가능한가")
    add_data_args(cf)
    cf.add_argument("--timeframes", nargs="+",
                    default=["1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d"],
                    help="비교할 타임프레임들 (원본보다 짧은 것은 건너뜁니다)")
    cf.set_defaults(func=cmd_costfloor)

    lv2 = sub.add_parser("leverage", parents=[common],
                         help="배율별 수익·낙폭·펀딩·청산 비교")
    add_data_args(lv2)
    lv2.add_argument("--levels", type=float, nargs="+",
                     default=[1.0, 1.25, 1.5, 2.0, 3.0], help="비교할 배율들")
    lv2.add_argument("--target-vol", type=float, default=0.50)
    lv2.add_argument("--lookback", type=int, default=120)
    lv2.add_argument("--band", type=float, default=0.30)
    lv2.set_defaults(func=cmd_leverage)

    vt = sub.add_parser("voltarget", parents=[common],
                        help="변동성 타게팅 — 방향 예측 없이 노출만 조절")
    add_data_args(vt)
    vt.add_argument("--target-vol", type=float, default=0.50, help="목표 연변동성 (0.50=50%%)")
    vt.add_argument("--lookback", type=int, default=30, help="변동성 측정 봉 수")
    vt.add_argument("--max-leverage", type=float, default=3.0, help="배율 상한")
    vt.add_argument("--band", type=float, default=0.10, help="재조정 밴드 (0.10=10%%)")
    vt.add_argument("--venue", choices=("spot", "perp"), default="spot",
                    help="spot=1배까지 현물(펀딩 없음) / perp=전체에 펀딩")
    vt.set_defaults(func=cmd_voltarget)

    sd = sub.add_parser("side", parents=[common], help="롱/숏 방향별 우위 분해")
    add_data_args(sd)
    sd.add_argument("--max-bars", type=int, default=168, help="삼중 배리어 시간 한도(봉)")
    sd.set_defaults(func=cmd_side)

    eg = sub.add_parser("edge", parents=[common],
                        help="신호의 우위가 겹쳐 세서 생긴 것인지 확인")
    add_data_args(eg)
    eg.add_argument("--max-bars", type=int, default=168, help="삼중 장벽 시간 한도(봉)")
    eg.add_argument("--step", type=int, default=1, help="몇 봉마다 신호를 볼지")
    eg.set_defaults(func=cmd_edge)

    sr = sub.add_parser("search", parents=[common],
                        help="파라미터를 훑어 우위를 찾는다 (다중검정 보정 포함)")
    add_data_args(sr)
    sr.add_argument("--param", required=True, help="훑을 파라미터 이름 (예: channel)")
    sr.add_argument("--values", required=True, help="쉼표로 구분한 값들 (예: 10,20,40)")
    sr.add_argument("--split", type=float, default=0.6,
                    help="탐색기간 비율. 나머지는 검증기간 (기본 0.6)")
    sr.add_argument("--max-bars", type=int, default=60, help="삼중 장벽 시간 한도(봉)")
    sr.add_argument("--min-samples", type=int, default=30,
                    help="이보다 표본이 적으면 판정에서 제외")
    sr.set_defaults(func=cmd_search)

    ml = sub.add_parser("metalabel", parents=[common],
                        help="모델이 신호를 걸러낼 수 있는지 검증 (numpy·scikit-learn 필요)")
    add_data_args(ml)
    ml.add_argument("--folds", type=int, default=4, help="워크포워드 구간 수")
    ml.add_argument("--embargo", type=int, default=24, help="검증 구간 앞 격리 봉 수")
    ml.add_argument("--threshold", type=float, default=0.55, help="신호를 취할 확률 기준")
    ml.add_argument("--max-bars", type=int, default=168, help="삼중 배리어 시간 한도(봉)")
    ml.add_argument("--step", type=int, default=1, help="표본 추출 간격(봉)")
    ml.add_argument("--seed", type=int, default=42)
    ml.add_argument("--maker", action="store_true",
                    help="지정가 진입을 미체결까지 반영해 비교")
    ml.add_argument("--maker-offset", type=float, default=0.25,
                    help="지정가를 종가에서 몇 ATR 물러나 걸지")
    ml.add_argument("--maker-timeout", type=int, default=3, help="체결 대기 봉 수")
    ml.set_defaults(func=cmd_metalabel)

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
