# 사내망 모드(`--corp`) 환경변수 사용 시점

`--corp` 플래그로 활성화되는 프록시·CA·미러 설정이 평가 파이프라인의 **어느 단계, 어느 동작에서 실제로 쓰이는지** 정리한 문서입니다.

직관적으로 "이미 Docker 이미지에 의존성이 다 설치돼 있는데 왜 미러 설정이 필요한가?"라는 의문이 생기기 쉽습니다. 결론부터 말하면 **두 단계 모두에서 부분적으로만 쓰입니다**.

---

## Step 1 — 패치 생성

에이전트 subprocess는 **호스트 머신** 위에서 실행됩니다. 네트워크 호출이 일어나는 시점은 세 가지입니다.

### A. Anthropic API 호출 (필수 — 매번)

Claude Code CLI가 모델을 호출할 때마다 `api.anthropic.com`(또는 vLLM 엔드포인트)에 HTTPS 요청을 보냅니다.

→ **`HTTPS_PROXY`, `CORP_CA_BUNDLE_PATH` 필수**

가장 자주 보이는 사용처입니다. 프록시 설정이 없으면 사내망에서 **모든 태스크가 즉시 실패**합니다.

### B. 에이전트의 Bash 툴 호출 (가끔)

에이전트는 코드 탐색 중 `Bash` 툴로 임의 명령을 실행할 수 있습니다. 예:

| 명령 | 필요 변수 |
|---|---|
| `pip install ipdb` (디버깅용 설치 시도) | `PIP_INDEX_URL` |
| `pytest` 로 부분 동작 확인 | 보통 네트워크 안 씀 |
| `npm install` (새 라이브러리 시험) | `NPM_CONFIG_REGISTRY` |
| `git fetch origin` (다른 브랜치 확인) | `GIT_SSL_CAINFO` |

> **빈도**: SWE-bench 태스크는 대부분 기존 코드의 버그 수정이라 새 패키지 설치는 드뭅니다. 다만 일어났을 때 해당 변수가 없으면 그 태스크는 실패합니다.

### C. 패치 추출 (네트워크 없음)

`git diff` 는 100% 로컬 작업입니다. 프록시 사용 안 함.

---

## Step 2 — Docker 검증

### A. `docker pull` (앱이 관여 안 함)

`docker pull`은 **Docker 데몬이 직접 실행**합니다. 평가 하네스의 `--corp` 플래그는 **여기에 관여하지 못합니다.** 데몬 자체 프록시 설정(`/etc/docker/daemon.json` 또는 `~/.docker/config.json`)이 별도로 필요합니다.

### B. 컨테이너 시작 직후 — 환경 체크 / 사내망 부트스트랩

`docker_evaluator.py`가 `docker run -e ... -v ...` 로 컨테이너 시작할 때 일어나는 일:

- `-e` 플래그로 `HTTPS_PROXY`, `PIP_INDEX_URL`, `REQUESTS_CA_BUNDLE` 등을 **컨테이너 환경 변수로 주입**
- `-v` 플래그로 CA 인증서를 `/etc/ssl/corp-ca.pem`에 **마운트**
- `LanguageProfile.pre_test_hook()`이 컨테이너 안에서 `~/.m2/settings.xml`, `~/.cargo/config.toml`, `composer config`, `/etc/apt/sources.list` 등을 **작성**

이건 **사전 준비**일 뿐, 아직 네트워크를 안 씁니다.

### C. 패치 적용 (네트워크 없음)

`git apply /tmp/agent.patch` — 100% 로컬.

### D. 테스트 실행 — 핵심 쟁점

베이스 의존성은 이미 설치돼 있어서 **보통 네트워크가 필요 없습니다.** 그러나 다음 경우엔 네트워크 호출이 발생합니다:

| 경우 | 발생 빈도 | 사용되는 변수 |
|---|---|---|
| 패치가 `requirements.txt` / `setup.py` 의존성을 수정 | 드물지만 있음 | `PIP_INDEX_URL`, CA |
| 패치가 `package.json` 수정 → `npm install` 재실행 | 드물지만 있음 | `NPM_CONFIG_REGISTRY` |
| **Rust `cargo test`** — 새 crate 참조가 추가되면 자동 fetch | **자주** | `CARGO_REGISTRY_URL`, `CARGO_HTTP_CAINFO` |
| **Go `go test`** — 새 import가 있으면 module fetch | **자주** | `GOPROXY` |
| Java `mvn test` — `pom.xml` 변경 시 의존성 재해결 | 가끔 | `CORP_MAVEN_MIRROR_URL` |
| C++ `post_patch_hook` — `cmake --build` | 거의 안 함 (로컬) | — |
| Python `post_patch_hook` — no-op | 안 함 | — |
| 테스트 자체가 외부 API 호출 (드문 케이스) | 매우 드묾 | 프록시 |

### E. 패치가 의존성 파일을 안 건드린 단순 버그 수정 (대다수)

**네트워크 호출 없음.** 즉 이 케이스에선 Step 2의 사내망 변수가 전혀 안 쓰입니다.

---

## 정리 — 각 변수의 실제 사용 시점

| 변수 | Step 1 | Step 2 | 실제 빈도 |
|---|---|---|---|
| `HTTPS_PROXY` | Anthropic API 호출 시 | 컨테이너 내부 fetch 시 | **거의 매번 (Step 1)** |
| `CORP_CA_BUNDLE_PATH` | API/git/pip의 SSL 검증 | 컨테이너 내부 도구의 SSL 검증 | **거의 매번 (Step 1)** |
| `PIP_INDEX_URL` | 에이전트가 `pip install` 시 | 패치가 deps 수정 시 | 가끔 |
| `NPM_CONFIG_REGISTRY` | 에이전트가 `npm install` 시 | 패치가 `package.json` 수정 시 | multi tier에서 자주 |
| `GOPROXY` | (Step 1에서 Go 코드 다루면) | `go test` 가 module fetch | **multi tier Go에서 자주** |
| `CARGO_REGISTRY_URL` | 거의 안 씀 | `cargo test` 가 crate fetch | **multi tier Rust에서 자주** |
| `CORP_MAVEN_MIRROR_URL` | 거의 안 씀 | `mvn test` 가 deps 재해결 | multi tier Java에서 가끔 |
| `CORP_APT_MIRROR_URL` | 거의 안 씀 | 패치가 시스템 패키지 의존 시 | 매우 드묾 |

---

## 핵심 요약

"이미 설치돼있으니 미러 설정이 필요 없지 않나?"라는 직관은 **절반만 맞습니다**:

- 베이스 의존성에 대해서는 맞음 — 이미 설치된 의존성을 쓰는 경우엔 네트워크가 필요 없습니다.
- 하지만 두 가지 이유로 변수 설정이 필요합니다:

  1. **Step 1의 Anthropic API 호출은 항상 일어남** — 가장 중요한 사용처. 프록시 없으면 평가 자체가 시작되지 않습니다.
  2. **나머지는 "안전망"** — multi tier의 Rust/Go처럼 fetch가 실제로 자주 일어나는 경우와, 패치가 의존성을 건드리는 드문 경우를 대비합니다. lite/verified(Python)에서는 거의 발동하지 않습니다.

### 최소 설정 권장

| 평가 대상 | 필요한 .env 변수 |
|---|---|
| **lite / verified (Python 전용)** | `HTTPS_PROXY`, `CORP_CA_BUNDLE_PATH`, `PIP_INDEX_URL` — 이 세 개로 거의 모든 케이스 커버 |
| **multi tier** | 위 세 개 + 사용하는 언어의 미러(예: Rust면 `CARGO_REGISTRY_URL`, Go면 `GOPROXY`) |

### 참고

- 사내망 모드 설계 전체: [`plan/corp_network_design.md`](../plan/corp_network_design.md)
- `--corp` 플래그 사용법: [`README.md`의 "사내망(Corporate-network) 모드" 섹션](../README.md)
