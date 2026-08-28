# 내 컴퓨터에서 돌리기

이 문서는 **실시간 페이퍼 트레이딩**까지 가는 순서입니다.
각 단계마다 "됐는지 확인하는 법"을 같이 적었습니다.

> ⚠ 실전(`live`) 전에 반드시 페이퍼를 몇 달 돌리세요.
> 12시간봉이라 통계로 볼 만한 표본이 쌓이는 데 그만큼 걸립니다.

---

## 0. 먼저 점검

```bash
git clone https://github.com/siwoo2223/dorothy-brew.git
cd dorothy-brew
python3 -m dorothy.cli doctor --offline
```

`PYTHONPATH=src`가 필요하다고 나오면 아래처럼 쓰세요 (설치를 안 한 경우).

```bash
PYTHONPATH=src python3 -m dorothy.cli doctor --offline
```

빠진 항목마다 **고치는 명령이 화살표로 나옵니다.** 그대로 따라가면 됩니다.

---

## 1. 설치

파이썬 3.10 이상이 필요합니다.

```bash
python3 -m venv .venv
source .venv/bin/activate        # 윈도우: .venv\Scripts\activate

pip install pyyaml ccxt          # 설정 읽기 + 거래소 연결
pip install -r requirements-collect.txt   # (선택) 호가 수집기
pip install -r requirements-ml.txt        # (선택) 메타라벨링
```

**확인**

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -q
```

694개가 전부 통과해야 합니다. 하나라도 실패하면 그 환경에서는 결과를 믿지 마세요.

---

## 2. 설정

```bash
cp config/donchian12.example.yaml config/config.yaml
```

파일을 열어 두 줄만 확인하세요.

```yaml
mode: paper                 # backtest → paper 로 바꿉니다
initial_equity: 200.0       # 페이퍼 시작 자본 (실제 돈이 아닙니다)
```

**수수료를 본인 등급으로 맞추세요.** 이 저장소의 모든 결론이 수수료에 달려 있습니다.

```yaml
exchange:
  taker_fee: 0.0006         # 비트겟 내 등급 화면에서 확인
  maker_fee: 0.0002
  slippage: 0.0005          # 넉넉히 잡는 편이 안전합니다
```

**확인**

```bash
PYTHONPATH=src python3 -m dorothy.cli doctor --config config/config.yaml
```

`거래소 연결 ✓`에 최근 종가가 찍히면 준비된 것입니다.

---

## 3. 먼저 과거 데이터로 (네트워크 없이)

실시간에 붙기 전에 **같은 코드 경로**를 과거 데이터로 돌려봅니다.

```bash
# 캔들 받기 (API 키 불필요). 저장 경로는 --out 입니다
PYTHONPATH=src python3 -m dorothy.cli fetch \
    --config config/config.yaml --days 700 --out data/btc_12h.csv

# 리플레이 — 실전 루프를 그대로 탑니다
PYTHONPATH=src python3 -m dorothy.cli paper \
    --config config/config.yaml --csv data/btc_12h.csv --offline
```

`리플레이 완료 — 체결 N건, 최종 자본 ...`이 나오면 코드 경로가 정상입니다.

**백테스트와 비교해 보세요.** 크게 다르면 설정이 어긋난 것입니다.

```bash
PYTHONPATH=src python3 -m dorothy.cli backtest \
    --config config/config.yaml --csv data/btc_12h.csv
```

체결 수는 같아야 하고 자본은 1% 안이어야 합니다
(그 불변식은 `test_paper_matches_backtest.py`가 지킵니다).

---

## 4. 실시간 페이퍼

```bash
PYTHONPATH=src python3 -m dorothy.cli paper --config config/config.yaml
```

- **API 키가 필요 없습니다.** 공개 시세만 읽습니다
- 주문은 **내부에서만** 처리됩니다. 거래소로 나가지 않습니다
- `Ctrl+C`로 멈춥니다. 기록은 SQLite에 남습니다

오래 켜두려면:

```bash
# 리눅스·맥
nohup env PYTHONPATH=src python3 -m dorothy.cli paper \
    --config config/config.yaml > paper.log 2>&1 &

tail -f paper.log
```

**확인**

```bash
PYTHONPATH=src python3 -m dorothy.cli status --config config/config.yaml
```

---

## 5. 같이 돌리면 좋은 것 — 호가 수집기

지금 모아두지 않으면 나중에 살 수 없는 데이터입니다.

```bash
# 먼저 몇 초만 받아 눈으로 확인
PYTHONPATH=src python3 -m dorothy.cli collect --probe

# 확인됐으면 계속
nohup env PYTHONPATH=src python3 -m dorothy.cli collect \
    --db data/collect.db --no-book > collect.log 2>&1 &

PYTHONPATH=src python3 -m dorothy.cli collect-status --db data/collect.db
```

`--no-book`이면 체결만 모아 용량이 1/100입니다. 하루 수십~수백 MB.

---

## 6. 실전으로 넘어갈 때

**페이퍼 결과를 먼저 보세요.** 백테스트와 방향이 다르면 실전에 가지 마세요.

```bash
# .env 파일 (git에 올라가지 않습니다)
BITGET_API_KEY=...
BITGET_API_SECRET=...
BITGET_API_PASSPHRASE=...
```

> **API 키를 만들 때**
> - **출금 권한은 반드시 끄세요.** 매매에는 필요 없습니다
> - **IP 화이트리스트**를 거세요
> - 키를 채팅·이슈·커밋에 붙여넣지 마세요

```bash
PYTHONPATH=src python3 -m dorothy.cli doctor --config config/config.yaml   # API 키 ✓ 확인
PYTHONPATH=src python3 -m dorothy.cli live --config config/config.yaml
```

**멈추는 법**: 저장소 최상위에 `KILL`이라는 빈 파일을 만듭니다.

```bash
touch KILL
```

동작이 두 갈래입니다. 알고 쓰셔야 합니다.

| 그때 상태 | 결과 |
|---|---|
| 포지션 보유 중 | 시장가로 정리하고 **프로세스가 멈춥니다** |
| 포지션 없음 | 신규 진입만 막습니다. **프로세스는 계속 돕니다** |

두 번째 경우 완전히 끄려면 `Ctrl+C`(또는 프로세스 종료)까지 해야 합니다.
다시 시작하기 전에 `KILL` 파일을 지우세요 — 남아 있으면 진입이 계속 막힙니다.

---

## 자주 막히는 곳

| 증상 | 원인 |
|---|---|
| `ModuleNotFoundError: dorothy` | `PYTHONPATH=src`를 붙이세요 |
| `PyYAML이 필요합니다` | `pip install pyyaml` |
| `거래소 연결 ✗ NetworkError` | 방화벽·VPN·회사망. `doctor`로 확인하세요 |
| 페이퍼가 백테스트보다 좋음 | 설정이 다릅니다. 수수료·펀딩비·최소 수량을 맞추세요 |
| 체결이 0건 | `diagnose`로 어느 조건에서 막히는지 보세요 |

```bash
PYTHONPATH=src python3 -m dorothy.cli diagnose --config config/config.yaml --csv data/btc_12h.csv
```

---

## 마지막으로

이 저장소에서 유일하게 검증을 통과한 설정은 **12시간봉 돈치안40 롱 전용**입니다
(291건, t=2.87). 그것도 **최근 절반에서 우위가 약해졌습니다**(t=1.20).

README의 "유일하게 전체 검증을 통과한 것" 절을 읽고 시작하세요.
숫자가 어디서 왔고 무엇이 확인되지 않았는지 적어뒀습니다.
