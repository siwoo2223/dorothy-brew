"""여러 번 재면 우연히 통과하는 것이 나온다.

**이 모듈이 막는 사고:**
설정 100개를 재서 t=2.3짜리 하나를 찾았다고 하자. 우위가 전혀 없어도
|t| >= 2는 100번 중 약 5번 나온다. 5개쯤 나오는 게 정상인 상황에서
1개를 찾고 "찾았다"고 하면 오히려 기대보다 못한 것이다.

그래서 탐색을 하려면 **몇 번 쟀는지를 같이 세야 한다.** 세지 않으면
어떤 임계값도 의미가 없다. 이 저장소는 이미 그 실수를 두 번 했다.

여기서 쓰는 보정은 본페로니다. 보수적이지만 설명이 쉽고, 탐색 결과를
"이건 확실하다"가 아니라 "이건 그나마 살아남았다"로 말하게 해준다.
"""

from __future__ import annotations

import math
from functools import lru_cache


def _log_beta(a: float, b: float) -> float:
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def _betacf(a: float, b: float, x: float, iterations: int = 200) -> float:
    """정규화 불완전 베타 함수의 연분수 전개 (Lentz 방법)."""
    tiny = 1e-30
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, iterations + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-16:
            break
    return h


def _betainc(a: float, b: float, x: float) -> float:
    """정규화 불완전 베타 I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(a * math.log(x) + b * math.log(1.0 - x) - _log_beta(a, b))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_pvalue(t: float, df: int) -> float:
    """t 통계량의 양측 p값.

    표본이 작을 때(겹침을 빼면 흔하다) 정규근사를 쓰면 p를 실제보다 작게
    보고한다 — **통과 쪽으로 틀리는 방향**이라 그대로 쓰면 안 된다.
    그래서 스튜던트 t 분포를 직접 계산한다.
    """
    if df <= 0:
        return 1.0
    t = abs(t)
    if t == 0.0:
        return 1.0
    if math.isinf(t):
        return 0.0
    return _betainc(df / 2.0, 0.5, df / (df + t * t))


@lru_cache(maxsize=4096)
def bonferroni_threshold(n_tests: int, df: int, alpha: float = 0.05) -> float:
    """n번 재고도 family-wise 오류율을 alpha로 유지하려면 넘어야 할 |t|.

    이분법으로 t_pvalue를 뒤집는다. 닫힌 식이 없어서가 아니라,
    분위수 근사식을 따로 검증하느니 이미 검증한 함수를 뒤집는 편이 낫다.
    """
    if n_tests < 1 or df <= 0:
        return float("inf")
    target = alpha / n_tests
    lo, hi = 0.0, 2.0
    while t_pvalue(hi, df) > target:
        hi *= 2.0
        if hi > 1e6:
            return float("inf")
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if t_pvalue(mid, df) > target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def expected_false_positives(
    n_tests: int, alpha: float = 0.05, *, directional: bool = True
) -> float:
    """우위가 전혀 없을 때 순진한 임계값을 통과할 것으로 기대되는 개수.

    directional=True는 "돈을 벌면서(net>0) |t|>=임계값"을 통과로 볼 때다.
    양측 검정에서 |t|가 임계값을 넘는 경우의 **절반만** 부호가 양수이므로
    기대치는 n*alpha가 아니라 n*alpha/2다. 여기를 n*alpha로 두면 우연
    기대치를 두 배로 부풀려, 실제로는 우연보다 많이 나온 결과를 두고도
    "우연이다"라고 잘못 기각하게 된다.
    """
    return n_tests * (alpha / 2 if directional else alpha)


def verdict(
    n_tests: int, n_survivors: int, alpha: float = 0.05, *, directional: bool = True
) -> str:
    """찾아낸 개수가 우연으로 설명되는지 한 줄로 말한다."""
    expected = expected_false_positives(n_tests, alpha, directional=directional)
    if n_survivors == 0:
        return f"✗ {n_tests}개 중 0개 통과 (우연이라면 {expected:.1f}개쯤 나왔을 자리)"
    if n_survivors <= expected:
        return (
            f"✗ {n_tests}개 중 {n_survivors}개 통과 — 우연이라면 {expected:.1f}개가 "
            f"나옵니다. **기대보다 많지 않으므로 근거가 못 됩니다**"
        )
    return (
        f"? {n_tests}개 중 {n_survivors}개 통과 (우연 기대치 {expected:.1f}개). "
        "기대치보다 많지만, 본페로니 임계값과 검증기간을 함께 보세요"
    )
