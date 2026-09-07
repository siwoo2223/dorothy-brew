# dorothy-brew

패턴별 자동 매매 AI. 차트 플레이북 3종(클래식 차트 패턴 · 스마트머니 · 하모닉/피보나치)의
**30개 패턴을 각각 독립 탐지기로 구현**하고, 탐지 → 신뢰도 채점 → 진입/손절/목표가 →
포지션 사이징까지 자동으로 뽑아준다.

- 의존성 0 (파이썬 표준 라이브러리만, 3.9+). numpy/pandas 불필요
- 패턴 카드의 **key focus 체크리스트를 그대로 점수화** → 그 평균이 신뢰도(confidence)
- 실행 규칙 반영: 패턴을 미리 진입하지 않음. `forming`(관찰) / `confirmed`(돌파) / `retest`(재테스트)로 상태 구분
- 손절이 이미 깨졌거나 목표가를 이미 지나친 신호는 자동 폐기

## 설치

```bash
git clone <repo> && cd dorothy-brew
pip install -e .          # 또는 그냥 python -m dorothy_brew ... 로 바로 실행
```

## 빠른 시작

```bash
# 내장 합성 차트로 즉시 확인 (데이터 없어도 됨)
python -m dorothy_brew demo bull_flag

# 내 CSV 스캔
python -m dorothy_brew scan btc_1h.csv --tf 1H --equity 10000 --risk 1

# 확정 신호만, JSON으로 (봇 연동용)
python -m dorothy_brew scan btc_1h.csv --no-forming --json

# 멀티 타임프레임 (상위 TF 편향으로 하위 TF 신호 가중)
python -m dorothy_brew multi 15m:btc_15m.csv 1H:btc_1h.csv 4H:btc_4h.csv

# 패턴 카탈로그 / 개별 패턴 설명
python -m dorothy_brew patterns
python -m dorothy_brew explain bat
```

출력 예시:

```
------------------------------------------------------------------------------
 BULLFLAG | 1h | 62 bars | net bias LONG (+0.78) | long 5.61 / short 0.69
------------------------------------------------------------------------------

EXECUTABLE (2) — breakout or retest already triggered
  [EXECUTE] Bull Flag (불 플래그) | LONG | score 0.79 conf 0.99
    Classic Chart Patterns #1 - Bullish Continuation | analysis 4H/1D -> entry 15m/1H | hold Hours - Days
    entry 115.62   stop 110.52   risk 5.1005   size 19.6061 @ 1.0% of 10,000
    TP1 124.02 (1.6R, 50%)  TP2 129.22 (2.7R, 30%)  TP3 137.62 (4.3R, 20%)
      ########## 1.00  Strong pole momentum
      ########## 1.00  Falling volume in flag
      ########## 1.00  Breakout with volume
      #########. 0.92  Measured move target
    zones: flag channel 110.52-113.96
    note: pole 13.6 over 19 bars, 19-bar flag, retrace 4%
```

## 파이썬 API

```python
from dorothy_brew import load, scan, plans, render_report, ScanConfig

series = load("btc_1h.csv", "BTCUSDT", "1H")
cfg = ScanConfig(min_confidence=0.6, equity=10_000, risk_pct=1.0, require_volume=True)
report = scan(series, cfg)

print(render_report(report, cfg))
print(report.bias())                      # {'long': .., 'short': .., 'net': +0.78}

for plan in plans(report, cfg):           # 실행 가능한 신호만
    s = plan.signal
    print(s.pattern_id, s.direction, s.entry, s.stop, s.targets,
          plan.size, [t["rr"] for t in plan.take_profits])
```

신호 하나는 이렇게 생겼다:

| 필드 | 내용 |
|---|---|
| `pattern_id` / `direction` / `status` | 패턴, 롱/숏, forming·confirmed·retest |
| `entry` / `stop` / `targets` | 진입가, 무효화 손절, 단계별 목표가 |
| `confidence` / `checks` | 0~1 신뢰도와 카드 체크리스트 항목별 점수 |
| `zones` | OB·FVG·PRZ·채널 등 가격 밴드 |
| `score()` / `reward_risk` / `position_size(equity, risk_pct)` | 랭킹 점수, 손익비, 수량 |
| `meta` | 비율·피벗 인덱스·상위TF 정렬도 등 근거 데이터 |

## 지원 패턴 30종

**Classic Chart Patterns** — 구조를 거래하고 노이즈를 거래하지 않는다

| # | id | 패턴 | 성격 | 분석 → 진입 TF | 보유 |
|---|---|---|---|---|---|
| 1 | `bull_flag` | Bull Flag | 상승 지속 | 4H–1D → 15m–1H | Hours–Days |
| 2 | `head_shoulders` | Head & Shoulders (역헤숄 포함) | 반전 | 1D–1W → 1H–4H | Days–Weeks |
| 3 | `ascending_triangle` | Ascending Triangle | 상승 지속 | 4H–1D → 15m–1H | Hours–Days |
| 4 | `rectangle_breakout` | Rectangle Breakout | 레인지 확장 | 1H–4H → 5m–15m | Min–Hours |
| 5 | `cup_handle` | Cup and Handle | 상승 지속 | 1D–1W → 4H–1D | Weeks–Months |
| 6 | `ending_wedge` | Ending Wedge (상승/하락) | 반전 | 4H–1D → 15m–1H | Hours–Days |
| 7 | `double_bottom` | Double Bottom (쌍고점 포함) | 반전 | 1H–4H → 15m–1H | Hours–Days |
| 8 | `diamond_top` | Diamond Top | 하락 반전 | 4H–1D → 1H–4H | Days–Weeks |
| 9 | `triple_touch_channel` | Triple Touch Channel | 상승 지속 | 1D–1W → 1H–4H | Days–Weeks |
| 10 | `broadening_wedge` | Broadening Wedge | 변동성 확장 | 1H–4H → 5m–15m | Min–Hours |

**Smart Money Concepts** — 유동성 · 구조 · 오더플로우

| # | id | 패턴 | 성격 | 분석 → 진입 TF | 보유 |
|---|---|---|---|---|---|
| 1 | `liquidity_sweep` | Liquidity Sweep | 유동성 사냥 | 1H–4H → 1m–15m | Seconds–Minutes |
| 2 | `choch` | Change of Character | 구조 전환 | 4H–1D → 15m–1H | Hours–Days |
| 3 | `order_block` | Order Block | 기관 오더블록 | 1D → 1H–4H | Days–Weeks |
| 4 | `fair_value_gap` | Fair Value Gap | 불균형 | 4H → 5m–1H | Minutes–Hours |
| 5 | `supply_demand_flip` | Supply/Demand Flip | 존 전환 | 4H → 15m–1H | Hours–Days |
| 6 | `bos` | Break of Structure | 추세 지속 | 4H–1D → 15m–1H | Hours–Days |
| 7 | `premium_discount` | Premium & Discount | 피보 존 | 4H–1D → 15m–1H | Hours–Days |
| 8 | `equal_highs_lows` | Equal Highs/Lows | 유동성 풀 | 4H → 15m–1H | Minutes–Hours |
| 9 | `institutional_engulfing` | Institutional Engulfing | 장악형 캔들 | 4H → 15m–1H | Minutes–Hours |
| 10 | `liquidity_compression` | Liquidity Compression | 스퀴즈 | 4H–1D → 15m–1H | Hours–Days |

**Harmonic & Fibonacci** — 정밀 비율

| # | id | 패턴 | 성격 | 분석 → 진입 TF | 보유 |
|---|---|---|---|---|---|
| 1 | `golden_retracement` | Golden Retracement | 61.8% 되돌림 | 4H–1D → 15m–1H | Hours–Days |
| 2 | `bat` | Bat Pattern | 하모닉 반전 | 1D–1W → 1H–4H | Days–Weeks |
| 3 | `butterfly` | Butterfly Pattern | 하모닉 반전 | 4H–1D → 15m–1H | Hours–Days |
| 4 | `crab` | Crab Pattern | 극단 반전 | 1D–1W → 1H–4H | Days–Weeks |
| 5 | `abcd` | AB=CD Harmonic | 대칭 무브 | 1H–4H → 15m–1H | Hours–Days |
| 6 | `fib_extension` | Fibonacci Extension | 1.618 청산 | 4H–1D → 15m–1H | Hours–Days |
| 7 | `deep_crab` | Deep Crab 886 | 가틀리 보강 | 4H–1D → 15m–1H | Hours–Days |
| 8 | `fib_cluster` | Fib Cluster Zone | 컨플루언스 | 1D–1W → 1H–4H | Days–Weeks |
| 9 | `shark` | Shark Pattern | 급반전 | 1H–4H → 5m–15m | Min–Hours |
| 10 | `fib_trend_fan` | Fib Trend Fan | 동적 지지 | 1D–1W → 4H–1D | Weeks–Months |

## 동작 방식

```
OHLCV → Series (ATR·RSI·프랙탈 피벗·회귀 추세선)
      → 30개 탐지기 (각자 기하학 검증 + 카드 체크리스트 채점)
      → sanitize (손절 이미 이탈 / 목표 이미 도달한 신호 폐기)
      → 랭킹 (confidence × 상태가중 × 1차 목표 R:R)
      → TradePlan (수량 = 자본 × 리스크% ÷ 손절폭, 50/30/20 분할청산)
```

- **피벗**: 좌우 3봉 프랙탈 → 고점/저점이 교대하는 지그재그 스켈레톤. 삼각형·웨지·하모닉이 모두 이 위에서 계산된다
- **하모닉**: XABCD 5피벗 조합을 비율표(`RATIO_TABLES`)와 대조. 각 구간 비율마다 감쇠 점수를 매기고 PRZ 도달 여부까지 확인해야 `confirmed`
- **멀티 TF**: 상위 타임프레임 순편향과 방향이 일치하면 신뢰도 최대 +20%, 반대면 감점. 각 패턴은 카드가 지정한 진입 TF에서만 돈다 (`--any-timeframe`로 해제)

## 설정 (`ScanConfig`)

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `lookback` | 240 | 탐색 봉 수 |
| `pivot_left` / `pivot_right` | 3 / 3 | 프랙탈 강도(=피벗 확정 지연) |
| `tol_atr` | 0.6 | "같은 레벨"로 볼 허용치 (ATR 배수) |
| `ratio_tol` | 0.10 | 하모닉 비율 허용 오차 |
| `min_volume_ratio` / `require_volume` | 1.2 / False | 돌파 거래량 조건 |
| `include_forming` | True | 미확정(관찰) 신호 포함 |
| `min_confidence` | 0.45 | 신뢰도 하한 |
| `fresh_bars` | 5 | 트리거가 이 안에 발생해야 유효 |
| `equity` / `risk_pct` | 10000 / 1.0 | 포지션 사이징 |

## 데이터 형식

CSV(헤더 자동 인식: `timestamp/date/time`, `open/high/low/close`, `volume`, 대소문자·별칭 허용, 헤더 없으면
`ts,o,h,l,c,v` 순서로 간주) 와 JSON(거래소 kline 배열 또는 dict 배열) 지원.
초/밀리초 epoch, ISO8601, `YYYY-MM-DD HH:MM:SS` 모두 파싱하고 깨진 행은 건너뛴다.
`resample(series, 4, "4H")`로 상위 TF를 즉석에서 만들 수 있다.

거래소 실시간 연동은 포함하지 않는다. 봇에 붙일 때는 캔들을 `Candle` 리스트로 만들어
`Series(candles, symbol, tf)` → `scan()` 을 주기적으로 돌리고, `--json` 출력이나
`report.to_dict()`를 주문 로직에 넘기면 된다.

## 테스트

```bash
python -m unittest discover -s tests -t .    # 73 tests
```

합성 차트 생성기(`dorothy_brew.synth`)가 패턴별 정답 차트를 만들어 각 탐지기가 자기 패턴을
실제로 찾는지, 랜덤워크·평탄·빈 시계열에서 예외 없이 견디는지, 모든 신호의 손절/목표
방향이 일관적인지 검증한다.

## 한계 (알고 쓰자)

- 차트 패턴은 **랜덤워크에서도 나온다**. 300봉 랜덤 시계열에서도 20개 안팎의 신호가 잡히는 게 정상이며,
  이 도구는 "패턴이 있다/없다"가 아니라 **얼마나 교과서적인가(confidence)**를 점수로 줄 뿐이다
- 백테스트 엔진·승률 통계는 포함되어 있지 않다. 신호의 기대값은 직접 검증해야 한다
- 피벗은 우측 3봉이 지나야 확정되므로 반전 패턴은 구조상 3봉의 확인 지연이 있다
- 투자 자문이 아니다. 실계좌 전에 반드시 자기 데이터로 검증할 것
