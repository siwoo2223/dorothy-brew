"""누수 없는 시계열 검증.

**표준 교차검증을 금융 시계열에 그대로 쓰면 안 된다.** 이유가 둘이다.

1. **시간 순서** — 미래로 학습해 과거를 맞히면 의미가 없다.
2. **라벨 겹침** — 삼중 배리어 라벨은 진입부터 청산까지 여러 봉에 걸쳐 있다.
   학습 표본의 라벨 구간이 검증 구간과 겹치면, 모델은 검증 구간의 가격을
   간접적으로 이미 본 셈이다. 이걸 **purging**으로 잘라내야 한다.

거기에 **embargo**를 더한다. 검증 구간 직후 얼마간의 표본도 버린다 —
자기상관 때문에 바로 뒤 표본은 검증 구간 정보를 담고 있다.

(López de Prado, *Advances in Financial Machine Learning*의 표준 처리)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Split:
    train: list[int]
    test: list[int]
    purged: int
    embargoed: int


def purged_walk_forward(
    spans: list[tuple[int, int]], *, folds: int = 4, embargo_bars: int = 24
) -> list[Split]:
    """시간순 분할 + purging + embargo.

    spans[i] = (표본 i의 시작 봉, 라벨 확정 봉)
    학습은 항상 검증보다 과거만 쓴다(anchored walk-forward).
    """
    n = len(spans)
    if n < folds * 4:
        raise ValueError(f"표본이 {n}개뿐입니다. 구간 {folds}개로 나누기에 부족합니다.")

    fold_size = n // (folds + 1)
    splits: list[Split] = []

    for k in range(folds):
        train_end = fold_size * (k + 1)
        test_start = train_end
        test_end = min(test_start + fold_size, n)
        if test_end <= test_start:
            break

        test = list(range(test_start, test_end))
        test_first_bar = min(spans[i][0] for i in test)
        test_last_bar = max(spans[i][1] for i in test)

        train: list[int] = []
        purged = embargoed = 0
        for i in range(train_end):
            start, end = spans[i]
            # purge: 라벨 구간이 검증 구간과 겹치면 버린다
            if end >= test_first_bar:
                purged += 1
                continue
            # embargo: 검증 구간 직전 embargo_bars 안의 표본도 버린다
            if end >= test_first_bar - embargo_bars:
                embargoed += 1
                continue
            train.append(i)

        if train:
            splits.append(Split(train, test, purged, embargoed))

    if not splits:
        raise ValueError("유효한 분할이 없습니다. 표본을 늘리거나 구간 수를 줄이세요.")
    return splits
