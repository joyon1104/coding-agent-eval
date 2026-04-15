# Phase 1 — 시스템 아키텍처 & 개발계획서 v2

## 1. 프로젝트 개요

### 1.1 목표

CLI 기반 AI 코딩 에이전트(Claude Code, OpenCode, Codex)의 성능을 SWE-bench 데이터셋으로 자동 평가하고, 7개 지표에 대한 비교 리포트를 생성하는 시스템을 구축합니다.

### 1.2 운영 환경

이 시스템은 **두 가지 이상의 환경**에서 실행됩니다.

| 구분 | 사외망 환경 | 사내망 환경 |
|------|-------------|-------------|
| OS | Windows + WSL2 (Ubuntu) | Ubuntu 22.04+ (Native Linux) |
| 네트워크 | 인터넷 자유 접근 | 제한적 (사내 프록시, API 우회 필요 가능) |
| 디스크 | 제한적 (30~50GB 여유 가정) | 제한적 (20~40GB 여유 가정) |
| API 접근 | Anthropic, OpenAI 직접 접근 | VPN/프록시 경유 또는 일부 차단 가능 |
| 평가 대상 | 사외망에서 접근 가능한 서비스 전체 | 사내에서 접근 가능한 서비스만 |
| Docker | WSL2 내 Docker 또는 Docker Desktop | 네이티브 Docker |

> 핵심 설계 원칙: **동일한 코드베이스**가 양쪽 환경에서 설정만 바꿔 실행되어야 합니다.

### 1.3 평가 대상 (Phase 1: CLI 전용)

| 서비스 | 비대화 실행 방식 | 출력 형식 |
|--------|------------------|-----------|
| Claude Code | `claude -p "prompt" --output-format json --allowedTools "Bash,Read,Write,Edit"` | JSON (usage, cost 포함) |
| OpenCode | `opencode` (설정 파일 기반) | stdout |
| Codex (OpenAI) | `codex -q "prompt" --full-auto` | stdout + git diff |

> IDE 기반 서비스(Roo Code, Continue 등)는 Phase 2+로 이관

### 1.4 Phase 1 측정 지표 (7개)

| # | 지표 | 데이터 소스 |
|---|------|-------------|
| 1 | Task Resolution Rate | SWE-bench 테스트 하네스 |
| 2 | Regression Safety | SWE-bench 테스트 하네스 (PASS_TO_PASS) |
| 3 | Token Efficiency | 에이전트 실행 로그 |
| 4 | Cost per Resolved Task | 토큰 로그 + 모델 단가표 |
| 5 | E2E Completion Time | 타임스탬프 |
| 6 | Time to First Action | 트라젝토리 로그 |
| 7 | Convergence Steps | 트라젝토리 로그 |

---

## 2. 데이터셋 전략 (용량 제약 대응)

### 2.1 데이터셋 티어

디스크 용량에 따라 3개 티어를 선택할 수 있습니다. 시스템이 자동으로 여유 공간을 감지하여 적절한 티어를 추천합니다.

```
┌─────────────────────────────────────────────────────────────────────┐
│                     데이터셋 티어 선택                                │
│                                                                     │
│  ┌─── Tier S: Micro (디스크 <15GB 여유) ─────────────────────────┐   │
│  │  인스턴스: 10개 (Mini에서 django 레포만 선별)                   │   │
│  │  Docker 이미지: ~2GB (django 환경 1개)                         │   │
│  │  용도: 파이프라인 개발, 디버깅, 스모크 테스트                    │   │
│  │  통계적 유의성: 낮음 (서비스 간 경향만 확인)                     │   │
│  └────────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─── Tier A: Mini (디스크 15~120GB 여유) ────────────────────────┐   │
│  │  인스턴스: 50개 (SWE-bench Verified Mini)                      │   │
│  │  Docker 이미지: ~5GB (django + sphinx 환경)                    │   │
│  │  용도: Phase 1 기본 평가                                       │   │
│  │  통계적 유의성: 중간 (16개 모델로 검증된 대표성)                 │   │
│  └────────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─── Tier B: Full (디스크 120GB+ 여유) ─────────────────────────┐   │
│  │  인스턴스: 500개 (SWE-bench Verified 전체)                     │   │
│  │  Docker 이미지: ~130GB (12개 레포 전체 환경)                    │   │
│  │  용도: 공식 벤치마크, 리더보드 비교                              │   │
│  │  통계적 유의성: 높음                                            │   │
│  └────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 Tier S (Micro) 데이터셋 구성법

용량이 가장 제한적인 환경에서 사용합니다. Mini 50개에서 django 레포 10개만 선별하여 Docker 이미지 1개로 동작합니다.

```python
def create_micro_dataset(mini_dataset, n=10, seed=42):
    """
    Mini 50개에서 django 레포 인스턴스 10개를 선별.
    Docker 이미지 1개(~2GB)로 동작하도록 단일 레포 고정.
    난이도 분포: easy 4 + medium 4 + hard 2
    """
    import random
    random.seed(seed)
    
    django_only = [x for x in mini_dataset if "django" in x["repo"]]
    
    by_diff = {"easy": [], "medium": [], "hard": []}
    for item in django_only:
        by_diff.get(item.get("difficulty", "medium"), by_diff["medium"]).append(item)
    
    sampled = []
    for diff, count in [("easy", 4), ("medium", 4), ("hard", 2)]:
        pool = by_diff[diff]
        sampled.extend(random.sample(pool, min(count, len(pool))))
    
    return sampled[:n]
```

### 2.3 오프라인 데이터셋 (사내망 대응)

사내망에서 HuggingFace에 접근할 수 없는 경우를 대비합니다.

```bash
# === 사외망에서 사전 내보내기 ===
python scripts/export_dataset.py --tier mini --output data/
# → data/swebench_mini.jsonl 생성

# Docker 이미지도 파일로 저장
docker save ghcr.io/swe-bench/swe-bench-django-env:latest \
    | gzip > data/docker_images/django_env.tar.gz

# === 사내망에서 오프라인 로드 ===
docker load < data/docker_images/django_env.tar.gz
python scripts/run_eval.py --tier mini --offline
```

---

## 3. 시스템 아키텍처

### 3.1 전체 구조

```
cape-eval/
├── config/
│   ├── eval_config.yaml           # 티어별 기본값 포함
│   ├── pricing.yaml               # 모델별 토큰 단가표
│   ├── environments/              # ★ 환경별 설정 분리
│   │   ├── native_linux.yaml      #   네이티브 Linux (사외망)
│   │   ├── wsl.yaml               #   WSL2 (사내망)
│   │   └── common.yaml            #   공통 설정
│   └── agents/                    # 에이전트별 설정
│       ├── claude_code.yaml
│       ├── aider.yaml
│       ├── opencode.yaml
│       └── codex.yaml
│
├── data/                          # ★ 오프라인 데이터
│   ├── swebench_mini.jsonl        #   Mini 데이터셋 로컬 복사본
│   ├── swebench_micro.jsonl       #   Micro 데이터셋 (10개)
│   └── docker_images/             #   Docker 이미지 tar.gz
│
├── src/
│   ├── core/
│   │   ├── models.py              # 데이터 모델
│   │   ├── config.py              # ★ 환경 감지 + 설정 로드
│   │   └── env_detect.py          # ★ Linux/WSL/디스크 자동 감지
│   │
│   ├── dataset/
│   │   ├── loader.py              # ★ 온라인/오프라인 겸용 로더
│   │   └── sampler.py             # ★ Micro/Mini/Full 티어 샘플러
│   │
│   ├── adapters/
│   │   ├── base.py                # AgentAdapter 베이스 클래스
│   │   ├── claude_code.py
│   │   ├── aider.py
│   │   ├── opencode.py
│   │   └── codex.py
│   │
│   ├── runner/
│   │   ├── orchestrator.py        # ★ 중단 후 재개 지원
│   │   ├── sandbox.py             # ★ 디스크 관리 포함
│   │   └── logger.py
│   │
│   ├── evaluator/
│   │   ├── swebench_harness.py
│   │   └── patch_extractor.py
│   │
│   ├── metrics/
│   │   ├── accuracy.py            # TRR, Regression Safety
│   │   ├── cost.py                # Token Efficiency, CRT
│   │   ├── latency.py             # E2E Time, TTFA
│   │   └── process.py             # Convergence Steps
│   │
│   └── reporter/
│       ├── scorer.py              # 등급 산출
│       ├── comparator.py          # ★ 멀티 환경 결과 병합
│       └── formatter.py           # Markdown, JSON, CSV
│
├── results/                       # 실행 결과 (환경 간 이동 가능)
│   └── runs/{run_id}/{agent_name}/{instance_id}.json
│
├── scripts/
│   ├── setup_env.sh               # ★ 환경별 분기 설치
│   ├── export_dataset.py          # ★ 오프라인용 데이터 내보내기
│   ├── run_eval.py                # 평가 실행 진입점
│   └── generate_report.py         # 리포트 생성
│
├── pyproject.toml
└── README.md
```

### 3.2 핵심 모듈: 환경 감지

```python
# src/core/env_detect.py — 시스템 시작 시 자동 실행

@dataclass
class EnvironmentInfo:
    os_type: str           # "native_linux" | "wsl" | "macos"
    network_zone: str      # "external" | "internal"
    available_disk_gb: float
    total_ram_gb: float
    docker_available: bool
    recommended_tier: str  # "micro" | "mini" | "full"

def detect_environment() -> EnvironmentInfo:
    os_type = _detect_os()        # /proc/version에서 WSL 판별
    network = _detect_network()   # api.anthropic.com 접근 테스트
    disk = _get_free_disk_gb()    # shutil.disk_usage
    ram = _get_ram_gb()           # /proc/meminfo
    docker = _check_docker()      # docker info 실행
    
    # 디스크 기반 티어 자동 결정
    tier = "full" if disk >= 120 else "mini" if disk >= 15 else "micro"
    
    return EnvironmentInfo(os_type, network, disk, ram, docker, tier)
```

### 3.3 핵심 모듈: 디스크 관리 샌드박스

```python
# src/runner/sandbox.py

class DiskAwareSandbox:
    def before_run(self, instance_id):
        free_gb = shutil.disk_usage(os.getcwd()).free / (1024**3)
        if free_gb < 3.0:
            self._cleanup_old_images()
            if shutil.disk_usage(os.getcwd()).free / (1024**3) < 3.0:
                raise DiskSpaceError(f"디스크 여유 부족: {free_gb:.1f}GB")
    
    def after_run(self, instance_id):
        if self.config.clean_after_run:
            self._remove_instance_image(instance_id)
```

### 3.4 핵심 모듈: 중단 후 재개

```python
# src/runner/orchestrator.py

class Orchestrator:
    def run(self, tasks, agents, run_id):
        for agent in agents:
            for task in tasks:
                result_path = f"results/runs/{run_id}/{agent.name}/{task['instance_id']}.json"
                
                if os.path.exists(result_path):
                    print(f"⏭️  건너뜀 (완료): {task['instance_id']}")
                    continue
                
                self.sandbox.before_run(task["instance_id"])
                try:
                    result = agent.run(task["problem_statement"], ...)
                    save_json(result, result_path)  # 즉시 저장
                except Exception as e:
                    save_json({"error": str(e)}, result_path)
                finally:
                    self.sandbox.after_run(task["instance_id"])
```

### 3.5 핵심 모듈: 결과 병합

```python
# src/reporter/comparator.py

def merge_results(result_dirs: list[str]) -> dict:
    """사외망 결과 + 사내망 결과를 합쳐서 리포트 생성"""
    all_results = {}
    for run_dir in result_dirs:
        for agent_dir in Path(run_dir).iterdir():
            agent_name = agent_dir.name
            all_results.setdefault(agent_name, [])
            for f in agent_dir.glob("*.json"):
                all_results[agent_name].append(json.loads(f.read_text()))
    return all_results
```

### 3.6 Agent Adapter (Claude Code)

```python
class ClaudeCodeAdapter(AgentAdapter):
    """
    실행: claude -p "{prompt}" --output-format json
          --max-turns 50 --max-budget-usd 5.00
          --allowedTools "Bash,Read,Write,Edit" --cwd /workspace/repo
    
    출력: {"type":"result","usage":{"input_tokens":...,"output_tokens":...},
           "total_cost_usd":0.47, ...}
    """
    
    def run(self, problem_statement, repo_path, timeout):
        cmd = [
            "claude", "-p", problem_statement,
            "--output-format", "json",
            "--max-turns", str(self.config.max_turns),
            "--max-budget-usd", str(self.config.max_budget),
            "--allowedTools", "Bash,Read,Write,Edit",
            "--cwd", repo_path,
        ]
        
        t_start = time.time()
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, text=True)
        t_end = time.time()
        
        output = json.loads(proc.stdout)
        patch = self._extract_patch(repo_path)
        
        return AgentResult(
            patch=patch,
            token_usage=TokenUsage(
                input_tokens=output["usage"]["input_tokens"],
                output_tokens=output["usage"]["output_tokens"],
                cache_read_tokens=output["usage"].get("cache_read_input_tokens", 0),
            ),
            timestamps=Timestamps(task_start=t_start, task_end=t_end, ...),
            ...
        )
```

---

## 4. 개발 환경 구축

### 4.1 하드웨어 요구사항 (환경별)

| 항목 | Native Linux (사내) | WSL2 (사외) | Micro 최소 |
|------|---------------------|-------------|------------|
| 디스크 여유 | 15GB+ | 15GB+ | 5GB |
| RAM | 8GB+ | 8GB+ | 4GB |
| CPU | 4코어+ | 4코어+ | 2코어 |

#### WSL2 리소스 설정

```ini
# Windows: %USERPROFILE%\.wslconfig
[wsl2]
memory=8GB
swap=4GB
processors=4
localhostForwarding=true
```

### 4.2 통합 설치 스크립트

```bash
#!/bin/bash
# scripts/setup_env.sh — OS/디스크/네트워크 자동 감지 후 최적 설치

set -e
echo "=== CAPE Eval 환경 구축 ==="

# ── OS 감지 ──
if grep -qi microsoft /proc/version 2>/dev/null; then
    ENV_TYPE="wsl"; echo "🖥️  WSL2 감지"
elif [[ "$(uname)" == "Linux" ]]; then
    ENV_TYPE="native_linux"; echo "🐧 Native Linux 감지"
else
    ENV_TYPE="unknown"; echo "⚠️  미식별 OS"
fi

# ── 디스크 여유 → 티어 결정 ──
FREE_GB=$(df -BG . | tail -1 | awk '{print $4}' | tr -d 'G')
echo "💾 디스크 여유: ${FREE_GB}GB"
if [ "$FREE_GB" -ge 120 ]; then TIER="full"
elif [ "$FREE_GB" -ge 15 ]; then TIER="mini"
else TIER="micro"; fi
echo "📦 추천 티어: $TIER"

# ── 시스템 패키지 ──
sudo apt update && sudo apt install -y \
    python3.11 python3.11-venv python3-pip git curl wget jq

# Docker (없으면 설치)
if ! command -v docker &>/dev/null; then
    if [[ "$ENV_TYPE" == "wsl" ]]; then
        echo "⚠️  WSL: Docker Desktop 또는 sudo apt install docker.io"
    else
        sudo apt install -y docker.io
        sudo usermod -aG docker $USER
    fi
fi

# ── Python 환경 ──
python3.11 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip
pip install swebench datasets pyyaml click rich python-dotenv pytest

# SWE-bench 소스
[ ! -d "SWE-bench" ] && git clone https://github.com/SWE-bench/SWE-bench.git
cd SWE-bench && pip install -e . && cd ..

# ── CLI 에이전트 (가용한 것만) ──
command -v npm &>/dev/null && npm install -g @anthropic-ai/claude-code 2>/dev/null \
    && echo "✅ Claude Code" || echo "⚠️  Claude Code 건너뜀"
pip install aider-chat 2>/dev/null \
    && echo "✅ Aider" || echo "⚠️  Aider 건너뜀"

# ── 데이터셋 ──
mkdir -p data
if curl -s --connect-timeout 5 https://huggingface.co >/dev/null 2>&1; then
    echo "🌐 온라인 — HuggingFace에서 다운로드"
    python -c "
from datasets import load_dataset
ds = load_dataset('MariusHobbhahn/swe-bench-verified-mini', split='test')
ds.to_json('data/swebench_mini.jsonl')
print(f'✅ {len(ds)}개 저장')
"
else
    echo "🔒 오프라인 — data/swebench_mini.jsonl 필요"
    [ ! -f "data/swebench_mini.jsonl" ] && \
        echo "❌ 사외망에서 scripts/export_dataset.py 실행 후 복사하세요"
fi

# ── 환경 검증 ──
echo ""
echo "=== 검증 ==="
python -c "
import subprocess, shutil, os
for name, cmd, req in [
    ('Python', 'python3.11 --version', True),
    ('Docker', 'docker --version', True),
    ('SWE-bench', 'python -c \"import swebench\"', True),
    ('Claude Code', 'claude --version', False),
    ('Aider', 'aider --version', False),
    ('데이터셋', 'test -f data/swebench_mini.jsonl', True),
]:
    try:
        ok = subprocess.run(cmd, shell=True, capture_output=True, timeout=10).returncode == 0
    except: ok = False
    print(f\"{'✅' if ok else ('❌' if req else '⚠️')} {name} ({'필수' if req else '선택'})\")
free = shutil.disk_usage(os.getcwd()).free / (1024**3)
print(f'💾 여유: {free:.1f}GB')
"
echo ""
echo "=== 완료: $ENV_TYPE / $TIER ==="
echo "  source .venv/bin/activate"
echo "  cp .env.example .env  # API 키"
echo "  python scripts/run_eval.py --tier $TIER --agents claude-code --sample-size 3"
```

### 4.3 API 키 & 프록시

```bash
# .env.example
ANTHROPIC_API_KEY=sk-ant-your-key-here
OPENAI_API_KEY=sk-your-key-here
# HTTPS_PROXY=http://proxy.company.com:8080   # 사내 프록시
```

---

## 5. 개발 계획 (3주 스프린트)

### 5.1 전체 타임라인

```
Sprint 1 (Week 1)        Sprint 2 (Week 2)        Sprint 3 (Week 3)
──────────────────       ──────────────────       ──────────────────
기반 & 환경 적응          어댑터 & 실행 엔진        지표 & 리포트

 ✓ 프로젝트 구조          ✓ Claude Code 어댑터     ✓ 7개 지표 계산기
 ✓ 환경 감지 모듈         ✓ Aider 어댑터           ✓ 등급 산출기
 ✓ 환경별 설정 분리       ✓ 오케스트레이터          ✓ 비교 리포트
 ✓ 데이터셋 로더            (중단/재개 지원)       ✓ 결과 병합
   (온/오프라인)          ✓ 로깅 수집기             ✓ Micro 양쪽 환경 검증
 ✓ 티어별 샘플러          ✓ SWE-bench 하네스        ✓ Mini 본 평가
 ✓ Docker 샌드박스        ✓ 디스크 관리
   (디스크 관리)          ✓ 단위 테스트
 ✓ 단위 테스트
```

### 5.2 Sprint 1: 기반 & 환경 적응 (Week 1)

#### Day 1-2: 프로젝트 기반 + 환경 감지

```
작업:
  1. 디렉토리 구조 생성
  2. pyproject.toml
  3. src/core/env_detect.py — OS/네트워크/디스크/Docker 자동 감지
  4. src/core/config.py — 환경별 YAML 로드 + .env 병합
  5. src/core/models.py — AgentResult, TokenUsage, Timestamps
  6. config/environments/ — common, native_linux, wsl YAML
  7. config/eval_config.yaml — 티어별 기본값
```

설정 파일 구조:

```yaml
# config/eval_config.yaml
tiers:
  micro:
    dataset_source: "local"
    local_path: "data/swebench_micro.jsonl"
    sample_size: 10
    docker_images_budget_gb: 2
  mini:
    dataset_source: "auto"    # 온라인 가능하면 HF, 아니면 로컬
    huggingface_id: "MariusHobbhahn/swe-bench-verified-mini"
    local_path: "data/swebench_mini.jsonl"
    sample_size: 50
    docker_images_budget_gb: 5
  full:
    dataset_source: "auto"
    huggingface_id: "princeton-nlp/SWE-bench_Verified"
    sample_size: 500
    docker_images_budget_gb: 130

execution:
  max_tokens_per_task: 1000000
  max_time_per_task: 1800
  max_turns_per_task: 50
  max_budget_per_task: 5.0
  temperature: 0
```

```yaml
# config/environments/wsl.yaml
extends: common.yaml
environment:
  os: wsl
  label: "사내망 WSL"
docker:
  memory_limit: "4g"
  cpu_limit: 2
  clean_after_run: true
network:
  proxy: null    # 사내 프록시 필요 시 설정
```

#### Day 3-4: 데이터셋 로더 & 샘플러

```
작업:
  1. src/dataset/loader.py
     - 온라인: HuggingFace 로드
     - 오프라인: 로컬 JSONL 로드
     - 자동: 네트워크 판별 후 분기
  2. src/dataset/sampler.py
     - create_micro_dataset(): django only 10개
     - load_mini(): 50개 그대로
     - stratified_sample(): Full에서 층화 샘플링
  3. scripts/export_dataset.py — 사외망→사내망 이동용
  4. 테스트
```

#### Day 5: Docker 샌드박스

```
작업:
  1. src/runner/sandbox.py
     - DiskAwareSandbox (디스크 감시 + 이미지 정리)
     - WSL Docker 호환 (Docker Desktop / WSL 내 Docker)
  2. Micro 1개 인스턴스로 gold patch 검증
```

### 5.3 Sprint 2: 어댑터 & 실행 엔진 (Week 2)

#### Day 1-2: Agent Adapter

```
작업:
  1. src/adapters/base.py — 공통 인터페이스
  2. src/adapters/claude_code.py (최우선)
     - claude -p + --output-format json
     - usage/cost 파싱
     - 프록시 대응 (HTTPS_PROXY 전달)
  3. src/adapters/aider.py
     - aider --message + stdout/git diff 파싱
  4. 단위 테스트 (mock 기반)
```

#### Day 3-4: 오케스트레이터

```
작업:
  1. src/runner/orchestrator.py
     - 태스크 큐 (instance × agent)
     - 중단 후 재개 (완료된 태스크 건너뜀)  ★
     - 태스크별 즉시 저장
     - 실행 간 Docker 이미지 정리
     - 진행률 표시 (rich)
  2. src/runner/logger.py — trajectory, tokens, timestamps
  3. src/evaluator/ — SWE-bench 하네스 래핑, 패치 추출
```

#### Day 5: 통합 테스트

```
작업:
  1. Micro 3개 인스턴스 → Claude Code E2E
  2. 로그 검증 (trajectory, tokens, timestamps)
  3. 가능하면 WSL에서도 동일 테스트
```

### 5.4 Sprint 3: 지표 & 리포트 (Week 3)

#### Day 1-2: 지표 계산기

```
작업:
  1. src/metrics/accuracy.py — TRR, Regression Safety
  2. src/metrics/cost.py — Token Efficiency, CRT
  3. src/metrics/latency.py — E2E Time, TTFA
  4. src/metrics/process.py — Convergence Steps
  5. 단위 테스트
```

#### Day 3: 리포트 + 결과 병합

```
작업:
  1. src/reporter/scorer.py — 등급 산출
  2. src/reporter/comparator.py — 비교 테이블 + 결과 병합
  3. src/reporter/formatter.py — Markdown, JSON, CSV
```

#### Day 4-5: 검증 & 본 평가

```
작업:
  1. Micro(10개) — 양쪽 환경 각각 E2E 실행
  2. Mini(50개) — 가용한 환경에서 본 평가
  3. 결과 병합 → 최종 리포트
  4. 이상치 확인 + SWE-bench 리더보드 비교
```

---

## 6. 환경 간 운용 시나리오

### 시나리오 A: 단일 환경 (사외망)

```bash
source .venv/bin/activate
python scripts/run_eval.py --tier mini --agents claude-code,aider --run-id eval-v1
python scripts/generate_report.py --run-id eval-v1
```

### 시나리오 B: 사내망 오프라인

```bash
# [사외] 데이터 내보내기
python scripts/export_dataset.py --tier mini --output data/
# data/ 폴더를 USB로 사내 이동

# [사내] 오프라인 실행
python scripts/run_eval.py --tier mini --agents claude-code --offline --run-id eval-v1
```

### 시나리오 C: 환경 분할 + 결과 병합

```bash
# [사외] Claude Code, codex
python scripts/run_eval.py --tier mini --agents claude-code, codex --run-id eval-v1

# [사내] OpenCode (사내 전용 서비스)
python scripts/run_eval.py --tier mini --agents opencode --run-id eval-v1

# [어디서든] 결과 합산
python scripts/generate_report.py --run-id eval-v1 \
    --merge-dirs results/runs/eval-v1-ext,results/runs/eval-v1-int
```

---

## 7. 실행 명령어 요약

```bash
# 환경 확인
python -m src.core.env_detect

# 스모크 테스트 (3개)
python scripts/run_eval.py --tier micro --agents claude-code --sample-size 3

# Micro 평가 (10개, 최소 환경)
python scripts/run_eval.py --tier micro --agents claude-code,aider --run-id eval-micro

# Mini 평가 (50개)
python scripts/run_eval.py --tier mini --agents claude-code,aider --run-id eval-mini

# 오프라인 실행
python scripts/run_eval.py --tier mini --offline --agents claude-code --run-id eval-int

# 리포트
python scripts/generate_report.py --run-id eval-mini --format markdown,json
```

---

## 8. 리스크 & 대응

| 리스크 | 영향 | 대응 |
|--------|------|------|
| 디스크 부족 | 평가 중단 | Micro 티어 + 태스크별 이미지 정리 |
| WSL Docker 성능 저하 | 느린 실행 | .wslconfig 메모리 증가, 병렬 수 축소 |
| 사내망 API 차단 | 에이전트 실행 불가 | 프록시 설정, 접근 가능 서비스만 실행 |
| 사내망 HuggingFace 차단 | 데이터셋 못 받음 | 사전 내보내기 → 로컬 JSONL |
| 실행 중 PC 셧다운 | 진행 손실 | 태스크 단위 즉시 저장 + 중단 후 재개 |
| 에이전트 CLI 변경 | 어댑터 깨짐 | 버전 고정 + 독립 테스트 |
| API rate limit | 실행 지연 | 재시도 + 서비스 간 라운드 로빈 |
| 비용 초과 | 예산 초과 | max_budget_per_task + 총 예산 모니터링 |
| 에이전트 무한 루프 | 낭비 | max_turns + max_time 이중 안전장치 |
| 환경 간 결과 불일치 | 리포트 오류 | 통일 JSON 스키마 + 병합 검증 |