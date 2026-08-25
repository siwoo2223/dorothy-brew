# dorothy-brew

Bitget USDT 무기한선물 자동매매 봇의 골격입니다.
**백테스트 → 페이퍼 트레이딩 → 실전**을 같은 코드 경로로 돌리도록 설계했습니다.

> ⚠️ **먼저 읽어주세요**
> 이 저장소에 들어있는 `ema_cross`는 *동작 확인용 샘플*이지 수익 전략이 아닙니다.
> 합성 데이터 백테스트에서 이미 마이너스가 나옵니다. 그게 정상입니다.
> 이 프로젝트가 제공하는 것은 **전략을 안전하게 실험하고 돌릴 수 있는 틀**이고,
> 돈을 버는 전략은 직접 만들어 넣으셔야 합니다.

---

## 30초 확인

설치할 것 없이 바로 돌아갑니다 (Python 3.10+).

```bash
./scripts/quickstart.sh
```

테스트 62개 → 백테스트 → 오프라인 페이퍼 트레이딩까지 한 번에 돕니다.

---

## 구조

```
캔들 데이터 ──► 전략 ──► 리스크 관리 ──► 주문 실행 ──► 기록/알림
                (신호)     (수량·차단)      (거래소)      (DB·텔레그램)
```

| 모듈 | 역할 |
|---|---|
| `data/` | 캔들 수집(ccxt·CSV·합성), 지표 계산 (EMA/SMA/ATR/RSI) |
| `strategy/` | 신호 생성. **수량과 자본은 절대 건드리지 않는다** |
| `risk/` | 포지션 사이징, 일일 손실 한도, 연속 손실 차단, 킬스위치 |
| `execution/` | 주문 실행, 중복 진입 방지, 청산 재시도 |
| `exchange/` | 거래소 추상화 — `PaperExchange`(모의) / `BitgetExchange`(실전) |
| `backtest/` | 과거 재생 + 성과 지표 (MDD, PF, 승률, 기대손익) |
| `journal/` | SQLite 매매 기록 (재시작 후 손실 카운터 복구) |
| `notify/` | 텔레그램 알림 |

전략이 수량을 정하지 못하게 막은 것이 핵심 설계입니다.
전략이 직접 사이징을 하면 리스크 한도를 우회할 수 있고, 그게 계좌가 사라지는 경로입니다.

---

## 안전장치

이 봇이 자동으로 막는 것들:

| 장치 | 동작 |
|---|---|
| **손절 강제** | 손절가 없는 진입은 **거부**합니다. 최대 손실을 모르는 주문은 내지 않습니다 |
| **포지션 사이징** | `수량 = (자본 × 1%) ÷ 손절까지의 거리`. 손절이 멀면 작게, 가까우면 크게 |
| **일일 손실 한도** | 하루 -3% 도달 시 당일 신규 진입 중단 |
| **연속 손실 차단** | 4연패 시 당일 중단, 다음 날 자동 재개 |
| **명목가·레버리지 상한** | 설정 레버리지가 한도를 넘으면 자동으로 깎음 |
| **킬스위치** | `touch KILL` 한 번이면 포지션 정리 후 정지 |
| **거래소 스탑 동시 등록** | 손절을 봇 메모리가 아니라 **거래소에** 겁니다. 봇이 죽어도 손절은 살아 있습니다 |
| **중복 진입 방지** | 같은 캔들 재진입 차단 + `clientOid` 멱등성 |
| **루프 예외 격리** | 예외가 나도 봇이 죽지 않습니다 (포지션 방치가 가장 위험) |

청산은 **어떤 차단 상태에서도 항상 허용**됩니다. 나가는 문은 잠그지 않습니다.

---

## 사용법

### 1. 실제 데이터로 백테스트

```bash
pip install -r requirements.txt

# 과거 캔들 수집 (API 키 불필요)
python -m dorothy fetch --days 365 --symbol "BTC/USDT:USDT" --timeframe 15m --out data/btc_15m.csv

# 백테스트
cp config/config.example.yaml config/config.yaml
python -m dorothy backtest --csv data/btc_15m.csv --config config/config.yaml
```

결과 읽는 법:

- **PF(손익비) < 1.0** → 손해 보는 전략. 파라미터 만지기 전에 아이디어를 의심하세요
- **거래 30건 미만** → 통계적으로 무의미합니다. 기간을 늘리세요
- **MDD** → "실제로 이 낙폭을 견디고 봇을 안 끌 수 있나?"를 자문하세요. 대부분 못 견딥니다
- 파라미터를 계속 바꿔 수익률을 올리는 건 **과최적화**입니다. 최근 구간을 떼어놓고(out-of-sample) 검증하세요

### 2. 페이퍼 트레이딩

```bash
# 실시간 시세 + 가상 주문 (실제 주문 없음)
python -m dorothy paper --config config/config.yaml

# 네트워크 없이 저장된 캔들 재생
python -m dorothy paper --offline --csv data/btc_15m.csv --config config/config.yaml
```

최소 2주는 돌려보세요. 백테스트에서 안 보이던 문제(체결 지연, 데이터 끊김, 예외)가 여기서 나옵니다.

### 3. 실전

```bash
cp .env.example .env    # 여기에 API 키 입력
python -m dorothy live --config config/config.yaml --sandbox   # 먼저 데모 계정
python -m dorothy live --config config/config.yaml --yes-i-understand-the-risk
```

`--yes-i-understand-the-risk` 없이는 실행되지 않습니다. 의도적입니다.

### 4. 기록 확인

```bash
python -m dorothy status --limit 30
```

---

## API 키 보안

- **출금(Withdraw) 권한은 반드시 끄세요.** 거래 권한만 켭니다. 키가 유출돼도 자산은 못 빼갑니다
- 가능하면 **IP 화이트리스트**를 거세요
- 키는 `.env`에만 둡니다. `config.yaml`에 적지 마세요 (`.gitignore`에 이미 들어 있습니다)
- 이 저장소가 공개라면 키를 커밋한 적 있는지 반드시 확인하세요

---

## 자기 전략 넣기

`src/dorothy/strategy/` 에 파일 하나 추가하면 됩니다.

```python
from ..models import Action, Candle, Position, Signal
from .base import Strategy, register

@register
class MyStrategy(Strategy):
    name = "my_strategy"

    @property
    def warmup(self) -> int:
        return 100          # 필요한 최소 캔들 수

    def generate(self, candles: list[Candle], position: Position | None) -> Signal:
        if position is None and 조건:
            return Signal(Action.ENTER_LONG, "사유", stop_loss=손절가, take_profit=익절가)
        return Signal(Action.HOLD)
```

`config.yaml`의 `strategy.name`을 바꾸면 끝입니다.

지켜야 할 규칙 두 가지:

1. **수량·레버리지를 계산하지 마세요.** 리스크 매니저 몫입니다
2. **`candles[-1]`은 완결된 봉입니다.** 진행 중인 봉으로 판단하면 신호가 켜졌다 꺼지는 리페인팅이 생깁니다

---

## 테스트

```bash
python -m unittest discover -s tests -q     # 설치 불필요
pytest                                       # pytest가 있다면
```

가장 중요한 테스트는 `test_backtest.py::test_no_look_ahead` 입니다.
미래 캔들을 바꿔도 과거 판단이 그대로인지 검증합니다. 이게 깨지면 백테스트 결과는 전부 신기루입니다.

---

## 알려진 한계 / 다음 할 일

- [ ] **펀딩비 미반영** — 선물 포지션을 오래 들고 가면 8시간마다 나갑니다. 스윙 전략이면 반드시 넣어야 합니다
- [ ] **실전 체결 동기화** — 지금은 페이퍼 체결만 저널에 기록됩니다. 실전은 `fetch_my_trades` 연동 필요
- [ ] **부분 청산 / 피라미딩 미지원** — 포지션은 전량 진입·전량 청산입니다
- [ ] **다중 심볼 미지원** — 한 프로세스가 심볼 하나를 봅니다
- [ ] **거래소 스탑 파라미터 검증 필요** — `stopLossPrice` 전달 방식은 ccxt 버전·거래소 정책에 따라 다를 수 있어, 데모 계정에서 실제로 스탑이 걸리는지 눈으로 확인하고 넘어가세요
- [ ] 워크포워드 분석, 파라미터 민감도 리포트

---

## 법적 고지

- 본인 자금을 본인이 운용하는 것은 문제가 없습니다
- 타인 자금 운용(투자일임)이나 매매신호 판매(투자자문)는 **자본시장법상 등록 대상**입니다
- 이 코드는 어떤 수익도 보장하지 않습니다. 선물 거래는 원금 전액을 잃을 수 있습니다
