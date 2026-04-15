#!/bin/bash
# scripts/setup_env.sh — OS/디스크/네트워크 자동 감지 후 최적 설치

set -e
echo "=== CAPE Eval 환경 구축 ==="

# ── OS 감지 ──
if grep -qi microsoft /proc/version 2>/dev/null; then
    ENV_TYPE="wsl"; echo "WSL2 감지"
elif [[ "$(uname)" == "Linux" ]]; then
    ENV_TYPE="native_linux"; echo "Native Linux 감지"
else
    ENV_TYPE="unknown"; echo "미식별 OS"
fi

# ── 디스크 여유 → 티어 결정 ──
FREE_GB=$(df -BG . | tail -1 | awk '{print $4}' | tr -d 'G')
echo "디스크 여유: ${FREE_GB}GB"
if [ "$FREE_GB" -ge 120 ]; then TIER="full"
elif [ "$FREE_GB" -ge 15 ]; then TIER="mini"
else TIER="micro"; fi
echo "추천 티어: $TIER"

# ── Python 환경 ──
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install --upgrade pip -q

pip install -q pyyaml click rich python-dotenv datasets pytest

echo ""
echo "=== 완료: $ENV_TYPE / $TIER ==="
echo "  source .venv/bin/activate"
echo "  cp .env.example .env  # API 키 설정"
echo "  python scripts/run_eval.py --tier $TIER --agents claude-code --sample-size 3"
