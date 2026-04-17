# Coding-Agent-Eval 개발 진행사항 (2026-04-15)

## 목표
phase1.md 기반으로 AI Coding Agent 서비스 평가 시스템 개발 → 간단한 데이터셋으로 풀 E2E 실행

## 현재 상태: 코드 구현 완료, E2E 테스트 실행 전

### 완료된 작업

#### 1. 프로젝트 구조 전체 생성 완료
```
src/
├── core/models.py          # TaskStatus, TokenUsage, Timestamps, AgentResult, EvalTask
├── core/env_detect.py      # OS/네트워크/디스크/Docker 자동 감지
├── core/config.py          # 환경별 YAML 로드 + .env 병합
├── dataset/loader.py       # 온라인/오프라인 겸용 로더
├── dataset/sampler.py      # Micro/Mini/Full 티어 샘플러
├── adapters/base.py        # AgentAdapter 베이스 클래스
├── adapters/claude_code.py # Claude Code CLI 어댑터
├── runner/sandbox.py       # DiskAwareSandbox (디스크 관리)
├── runner/logger.py        # 로깅 + 메타데이터 저장
├── runner/orchestrator.py  # 중단 후 재개 지원 오케스트레이터
├── evaluator/patch_extractor.py  # git diff 패치 추출
├── evaluator/swebench_harness.py # SWE-bench 하네스 래핑
├── metrics/accuracy.py     # TRR, Regression Safety
├── metrics/cost.py         # Token Efficiency, CRT
├── metrics/latency.py      # E2E Time, TTFA
├── metrics/process.py      # Convergence Steps
├── reporter/scorer.py      # 등급 산출 (S/A/B/C/D/F)
├── reporter/comparator.py  # 멀티 환경 결과 병합
└── reporter/formatter.py   # Markdown, JSON, CSV 포맷

scripts/
├── run_eval.py             # 평가 실행 진입점 (CLI)
├── generate_report.py      # 리포트 생성
├── export_dataset.py       # 오프라인용 데이터 내보내기
├── create_test_data.py     # 테스트용 합성 데이터 생성
└── run_e2e_test.py         # Mock 에이전트로 E2E 테스트

config/
├── eval_config.yaml        # 티어별/실행/가격 설정
├── environments/common.yaml
├── environments/wsl.yaml
├── environments/native_linux.yaml
└── agents/claude_code.yaml
```

#### 2. .venv 상태
- .venv가 있지만 python 바이너리가 없는 상태 (깨진 venv)
- **WSL에서 재생성 필요:**
  ```bash
  rm -rf .venv
  python3 -m venv .venv
  source .venv/bin/activate
  pip install pyyaml click rich python-dotenv
  ```

### 다음 해야 할 작업

1. **WSL에서 venv 재생성** (위 명령어)
2. **테스트 데이터 생성**: `python scripts/create_test_data.py`
3. **E2E 테스트 실행**: `python scripts/run_e2e_test.py`
   - Mock 에이전트로 전체 파이프라인 테스트 (실제 에이전트/Docker 불필요)
   - load → sample → run → metrics → score → report 전체 흐름 검증
4. E2E 테스트 실패 시 디버깅 후 수정
5. 성공하면 실제 데이터셋으로 실행 시도

### 실행 환경 문제
- PowerShell에서 Claude Code를 실행해서 bash tool이 WSL PATH를 못 찾음
- **반드시 WSL 터미널에서 Claude Code를 실행해야 함**

### 참고
- 설계 문서: `plan/phase1.md`
- 모든 소스코드는 구현 완료 상태이며, 실행 테스트만 남음
