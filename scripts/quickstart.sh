#!/usr/bin/env bash
# 설치 없이 파이프라인이 도는지 30초 안에 확인하는 스크립트.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src

echo "▶ 1/3  테스트"
python3 -m unittest discover -s tests -q

echo
echo "▶ 2/3  합성 데이터 백테스트 (수익률에 의미 없음 — 파이프라인 점검용)"
python3 -m dorothy backtest --synthetic --bars 5000 --log-level WARNING

echo
echo "▶ 3/3  오프라인 페이퍼 트레이딩 리플레이"
python3 -m dorothy paper --offline --synthetic --bars 2000 --log-level WARNING

echo
echo "✅ 전체 경로 정상. 다음 단계는 README의 '실제 데이터로 백테스트' 참고."
