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

> ⚠ **이 저장소에는 검증을 통과한 전략이 없습니다.** 아래 설정은 한때
> 통과했다고 발표했다가 **기각한** 것입니다(파일 이름이 `.rejected`인 이유).
> 코드와 안전장치를 시험해보는 용도로만 쓰세요. 이유는 맨 아래 "마지막으로".

```bash
cp config/donchian12.rejected.yaml config/config.yaml
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

직접 매매 설정을 쓴다면 이걸 복사하세요 (4시간봉·ATR×2.0·3배):

```bash
cp config/trading.example.yaml config/config.yaml
```

**시작 전 3분 점검**

```bash
# 1) 의존성·설정·거래소 연결
PYTHONPATH=src python3 -m dorothy.cli doctor --config config/config.yaml

# 2) 이 배율에서 손절이 청산보다 먼저 걸리는지
PYTHONPATH=src python3 -m dorothy.cli liqcheck \
    --config config/config.yaml --csv data/btc_4h.csv
#    → "설정값 3배 → ✓ 손절이 먼저 걸립니다" 가 나와야 합니다

# 3) 같은 설정으로 과거를 먼저 돌려 기준선을 잡아둡니다
PYTHONPATH=src python3 -m dorothy.cli paper \
    --config config/config.yaml --csv data/btc_4h.csv --offline
```

3번의 결과를 적어두세요. 실시간 페이퍼가 이것과 **크게 다르면** 설정이
어긋난 것입니다(수수료 등급, 심볼, 타임프레임).

**실행**

```bash
PYTHONPATH=src python3 -m dorothy.cli paper --config config/config.yaml
```

- **API 키가 필요 없습니다.** 공개 시세만 읽습니다
- 주문은 **내부에서만** 처리됩니다. 거래소로 나가지 않습니다
- `Ctrl+C`로 멈춥니다. 기록은 SQLite에 남습니다

**무엇을 보게 되는가** (4시간봉 기준)

- 4시간에 한 번만 판단합니다. 폴링은 15초마다 하지만 **새 마감봉이
  없으면 아무것도 하지 않습니다.** 로그가 조용한 게 정상입니다.
- 100일에 약 **9.7건** 매매합니다. 첫 며칠은 한 건도 없을 수 있습니다.
- 100일 뒤 수익 확률은 **58%**, 중앙값 +0.9%입니다. 첫 100일에 42% 확률로
  마이너스인데, 그건 전략이 틀렸다는 뜻이 아니라 **9.7건으로는 아무것도
  알 수 없다**는 뜻입니다.
- 백테스트 안에서도 **905일** 동안 이전 고점 아래에 있던 구간이 있었습니다.

> ⚠ **페이퍼 중에 설정을 바꾸면 그때까지의 기록이 의미를 잃습니다.**
> 바꾸고 싶으면 `db_path`를 새 파일로 두고 처음부터 다시 세세요.

**멈췄다 다시 켜도 안전합니다.** 봇은 어디까지 판단했는지를 SQLite에
남깁니다(`bot_state.last_candle_ts`). 그래서 재시작해도 마지막 마감봉을
다시 판단하지 않습니다 — 방금 손절당한 그 봉에 다시 들어가는 일이
없다는 뜻입니다. 연속 손실 카운터와 자본 고점도 같이 복구됩니다.

> `db_path`의 SQLite 파일을 지우면 이 기억도 같이 지워집니다.
> 기록을 초기화하고 싶으면 매매를 쉬는 동안 하세요.

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

**멈춰야 할 때**

```bash
touch KILL      # 포지션을 정리하고 봇이 멈춥니다 (kill_switch_file 설정값)
```

`KILL` 파일은 다시 시작하기 전에 지우세요.

**몇 주 뒤에 볼 것**

| 보는 것 | 정상 | 이상하면 |
|---|---|---|
| 체결 건수 | 100일에 9~10건 | 0건이면 `diagnose`로 어디서 막히는지 |
| 1건당 손익 | ±0.5% 근처 | 백테스트와 크게 다르면 수수료 설정 확인 |
| 손절/익절 비율 | 손절이 조금 더 많음 | 손절이 안 잡히면 `stop_loss` 사유가 없는지 |
| 로그의 `⚠`·`✗` | 없어야 함 | 있으면 그 줄을 먼저 읽으세요 |

```bash
grep -E "⚠|✗|ERROR" paper.log        # 경고만 추려 보기
```

---

## 5. 호가 수집기 — 몇 달 모으기

지금 모아두지 않으면 나중에 살 수 없는 데이터입니다. 봉 데이터로 파는 길은
막혔고(202개+144개 전부 탈락), 호가·체결 흐름이 아직 손 안 댄 유일한 쪽입니다.

### ⚠ 먼저 알아야 할 것 — 용량이 진짜 제약입니다

실측 **81바이트/행**입니다. `depth@100ms`는 초당 10메시지고, 메시지 하나에
갱신 레벨이 수십 개 옵니다. 아무 설정 없이 켜두면:

```
              하루      3개월
전부 저장     3.9 GB    351 GB     ← 디스크가 먼저 죽습니다
±1.0%만       2.7 GB    245 GB
±0.5%만       2.1 GB    192 GB
±0.1%만       0.4 GB     35 GB
  + 500ms     0.08 GB     7 GB     ← 권장
```

중간가에서 ±0.1%면 BTC 7만 달러 기준 ±70달러입니다. 미시구조 신호
(호가 불균형, 체결 압력)는 그 안쪽에서 나오므로 먼 호가는 버려도 됩니다.

### 실행

```bash
pip install -r requirements-collect.txt

# 1) 몇 초만 받아 눈으로 확인
PYTHONPATH=src python3 -m dorothy.cli collect --probe

# 2) 확인됐으면 몇 달 돌리기 (권장 설정)
nohup env PYTHONPATH=src python3 -m dorothy.cli collect \
    --db data/collect.db \
    --speed 500ms --near-pct 0.001 --max-gb 20 \
    > collect.log 2>&1 &
```

- `--near-pct 0.001` 중간가 ±0.1% 밖의 호가는 버립니다 (0을 주면 전부 저장)
- `--max-gb 20` 20GB에 도달하면 **깔끔하게 멈춥니다.** 없으면 디스크가 찰 때
  SQLite 쓰기 실패가 '끊김'으로 오인되어 영원히 재접속만 시도합니다
- `--no-book` 체결만 모읍니다. 용량이 1/100이지만 호가 신호는 못 봅니다

### 며칠 뒤 확인

```bash
PYTHONPATH=src python3 -m dorothy.cli collect-status --db data/collect.db
```

`증가율` 줄에 하루·30일·90일 예상치가 나옵니다. **첫날 이걸 보고
디스크에 맞는지 판단하세요.** 안 맞으면 `--near-pct`를 줄이거나
`--speed`를 늘려 다시 시작하면 됩니다.

`빠짐` 줄도 꼭 보세요. 재접속하는 동안 놓친 구간이 기록됩니다 —
나중에 분석할 때 "그 시각엔 조용했다"와 "우리가 못 받았다"를 섞으면
결론이 조용히 틀립니다.

> 📌 수집기는 **바이낸스** 공개 스트림을 씁니다(API 키 불필요).
> 비트겟과 호가는 다르지만 BTC 미시구조는 거래소 간 상관이 높아
> 신호 탐색용으로는 쓸 만합니다. 실전 체결은 비트겟에서 하시면 됩니다.

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
| 재시작 후 바로 진입 안 함 | 정상입니다. 이미 판단한 봉은 건너뜁니다 (위 4절) |

```bash
PYTHONPATH=src python3 -m dorothy.cli diagnose --config config/config.yaml --csv data/btc_12h.csv
```

---

## 마지막으로

**실전에 넣을 신호가 없습니다.**

한때 "유일하게 검증을 통과한 것"으로 12시간봉 돈치안40 롱을 발표했습니다
(291건, t=2.87). **그 t값이 틀렸습니다.** 겹치는 신호를 독립 표본으로 셌고,
겹침을 빼면 115건 t=0.99로 통과하지 못합니다. 봇은 한 번에 포지션 하나만
드니까 보유 중에 들어온 신호 176건(60%)은 애초에 잡을 수 없었습니다.

직접 확인해보세요:

```bash
PYTHONPATH=src python3 -m dorothy.cli edge \
    --config config/donchian12.rejected.yaml --csv data/btc_12h.csv --max-bars 60
```

그 뒤에 제대로 찾아봤습니다. 전략·파라미터·타임프레임 **202개**를 훑고,
탐색기간/검증기간을 나누고, 몇 번 쟀는지까지 반영했습니다. **0개 통과입니다.**
탐색기간 1등(t=3.18)도 처음 보는 구간에서는 1.22였습니다.

```bash
PYTHONPATH=src python3 -m dorothy.cli search --config config/config.yaml \
    --csv data/btc_12h.csv --param channel --values 10,20,30,40,60,80
```

README의 "통과했다고 발표했던 것, 그리고 그게 왜 틀렸나" 절에 다시 잰
표를 전부 적어뒀습니다.

**그래서 이 안내서는 이렇게 쓰세요.** 봇을 돌려보고, 안전장치가 작동하는지
보고, 페이퍼로 익히는 데까지는 그대로 유용합니다. **실전 자금은 넣지 마세요.**
넣을 것이 생기려면 먼저 겹침을 뺀 t가 2를 넘는 신호를 찾아야 합니다.
