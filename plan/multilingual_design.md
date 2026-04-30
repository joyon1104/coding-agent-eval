# Multilingual 평가 지원 — 개발 설계서 v1

## 0. 문서 목적

기존 Lite/Verified(Python 단일 언어) 평가 파이프라인에 **SWE-bench Multilingual**(Princeton, 300 instances, 9개 언어) 지원을 추가하기 위한 설계 문서입니다. 본 설계의 최우선 제약은 **기존 Lite/Verified 평가 동작에 0의 영향을 보장**하는 것입니다. 주요 검증 대상 언어는 **Java, C++**이지만, 신규 언어를 손쉽게 확장 가능한 구조로 만드는 것을 목표로 합니다.

---

## 1. 목표 / 비목표

### 1.1 목표 (Goals)

1. `--tier multi` 실행 시 SWE-bench Multilingual 데이터셋(300개)에 대해 Step 2(Docker 검증)가 정상 동작
2. Java(apache/druid), C++(fmtlib/fmt)을 포함하여 최소 2개 비-Python 언어가 실제 컨테이너에서 F2P/P2P 테스트 결과를 산출
3. 신규 언어 추가는 **새 LanguageProfile 클래스 1개 + 매핑 항목 1줄** 등록만으로 끝나야 함
4. Step 1(에이전트 실행) / Step 3(리포트)는 변경 없이 그대로 동작
5. 기존 Lite/Verified 회귀 테스트가 **byte-identical** 결과로 통과

### 1.2 비목표 (Non-Goals)

- Multi-SWE-bench(Alibaba/ByteDance, 4-state tracking) 지원 — 본 설계는 Princeton **SWE-bench Multilingual**에 한정
- 새 메트릭 추가 (현재 6개 지표 그대로 사용)
- 멀티언어 전용 리포트 분리 — 동일 6-metric 표를 그대로 사용
- 컨테이너 내 의존성 사전 캐싱 자동화 (Maven 등 네트워크 의존 — Phase 후속에서 처리)
- 에이전트 어댑터 변경 — 에이전트 입장에선 언어 무관, 단순히 다른 레포에서 git diff를 추출할 뿐

---

## 2. 사전조사 결과 요약

`apache/druid`(Java)와 `fmtlib/fmt`(C++) 두 컨테이너를 실제로 풀(pull)하여 내부를 검증한 결과:

| 항목 | Python(Lite/Verified) | Java(druid) | C++(fmt) |
|------|----------------------|-------------|----------|
| 이미지 레지스트리 | `ghcr.io/epoch-research` | **`docker.io/swebench`** | **`docker.io/swebench`** |
| 이미지 prefix | `swe-bench.eval.x86_64` | `sweb.eval.x86_64` | `sweb.eval.x86_64` |
| instance_id 변환 | 그대로 | `__` → `_1776_` | `__` → `_1776_` |
| 작업 디렉토리 | `/testbed` | `/testbed` | `/testbed` |
| 환경 활성화 | conda(`testbed`) 필요 | **불필요** (Docker ENV/PATH로 노출) | **불필요** (Docker ENV/PATH로 노출) |
| 빌드 도구 | pip 사전 설치 | Maven (필요 시 dep download) | CMake + 사전 빌드된 `build/` |
| 테스트 실행 | `tests/runtests.py` 또는 `pytest` | `mvn test -pl <module> -Dtest=<FQCN>#<method>` | `ctest` 또는 `./build/bin/<test-binary> --gtest_filter=...` |
| 테스트 결과 형식 | `... ok` (Django) / `PASSED` (pytest) | Surefire 콘솔 / `target/surefire-reports/*.xml` | gtest `[ OK ]` / `[ FAILED ]` |
| F2P/P2P 명명 | `path/to/test.py::TestClass::test_method` | `com.foo.Bar#testMethod` (FQCN) | `Suite.TestName` (gtest) |
| base_commit git 상태 | clean | **dirty** (`pom.xml` 수정됨, setup_repo.sh) | clean |
| 패치 적용 후 재빌드 | 불필요 (인터프리터) | mvn이 컴파일 포함 | **재컴파일 필요** (`cmake --build build`) |

### 2.1 핵심 발견점

- **레지스트리 자체가 다름**: Python은 GHCR(epoch-research) 미러, 비-Python은 Docker Hub(swebench) 본진. 단순 분기 필요.
- **instance_id 변환 규칙**: Docker Hub 측은 `__`(이중 언더스코어) → `_1776_` 치환 규칙 사용. Python GHCR은 그대로 사용.
- **`CONDA_ACTIVATE` prefix는 Python 전용**: 비-Python 컨테이너에서 prefix를 붙이면 `conda activate testbed`가 실패하여 모든 exec이 깨짐. 반드시 언어별로 분기.
- **테스트 출력 파서가 결정적 차이**: `_parse_test_output()`은 현재 Django/pytest만 인식. Surefire/ctest는 다른 패턴. 이 부분이 가장 큰 작업량.
- **C++ 한정 이슈**: gtest suite name (`PrintfTest`)에서 실행 binary 경로(`build/bin/printf-test`)를 추론하는 로직이 자명하지 않음. 컨테이너 내 `find build -type f -executable`로 매핑 테이블 1회 구축 필요.

---

## 3. 설계 원칙

### 3.1 Zero-Impact (최우선)

- 기존 Lite/Verified 코드 경로의 **함수 시그니처/반환값/사이드이펙트가 일체 변하지 않아야** 함
- Python 동작은 새로 분리되는 `PythonProfile` 클래스에 **1:1 그대로 이관**되며, 이 클래스 단독 호출 결과가 현행 코드와 **byte-identical**
- 기본값(default dispatch)은 **항상 PythonProfile** — 매핑이 없을 때도 기존 동작과 동일하게 작동
- `--tier multi`가 아닌 모든 tier는 dispatch 단계에서 **Python으로 강제 고정**(추가 분기 없음)

### 3.2 Extensibility (확장성)

- 언어 추가 = 새 `*.py` 파일 1개 + 레포→언어 매핑 1줄
- `docker_evaluator.py` 본문은 언어별 코드 0줄, 모든 언어 차이는 `LanguageProfile` 인터페이스 뒤로 캡슐화
- 신규 언어가 기존 4개 변경 영역(get_image_name / shell prefix / exec / parse) 외에 새 hook을 요구하면 인터페이스 확장 (단 기본 구현은 항상 제공)

### 3.3 Fail-Safe Dispatch

- 알 수 없는 레포는 **Python으로 fallback**하지 않고 **명시적 에러** — 잘못된 결과보다 명시적 실패가 안전
- 이미지 풀 실패 / 테스트 실행 실패는 기존 메커니즘과 동일 EvalResult 포맷으로 반환

---

## 4. 아키텍처

### 4.1 디렉토리 구조 (신규)

```
src/evaluator/
├── docker_evaluator.py        # (수정) 언어별 dispatch만 담당, 4곳 변경
├── swebench_harness.py        # (변경 없음)
├── patch_extractor.py         # (변경 없음)
├── registry_utils.py          # (변경 없음)
└── languages/                 # (신규) 언어별 Profile
    ├── __init__.py
    ├── profile.py             # LanguageProfile ABC + 공통 dataclass
    ├── dispatch.py            # repo/instance_id → Profile 매핑
    ├── python.py              # PythonProfile (기존 로직 1:1 이관)
    ├── java.py                # JavaProfile (Maven/Surefire)
    ├── cpp.py                 # CppProfile (CMake/ctest/gtest)
    └── (향후) javascript.py, go.py, rust.py, ...
```

### 4.2 LanguageProfile 인터페이스

```python
# src/evaluator/languages/profile.py
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass(frozen=True)
class TestOutcome:
    """단일 테스트의 결과 (언어 무관)."""
    name: str
    passed: bool
    raw_output: str | None = None  # 디버깅용

class LanguageProfile(ABC):
    """언어별 Docker 컨테이너 운영 정책."""

    name: str  # "python", "java", "cpp", ...

    @abstractmethod
    def get_image_name(self, instance_id: str) -> str:
        """instance_id → 컨테이너 이미지 풀 경로."""

    @abstractmethod
    def shell_prefix(self) -> str:
        """모든 docker exec 명령 앞에 붙는 prefix.
        Python: 'source .../conda.sh && conda activate testbed && '
        Java/C++: '' (빈 문자열)
        """

    @abstractmethod
    def build_test_command(self, test_names: list[str], task: EvalTask) -> str:
        """F2P 또는 P2P 테스트 이름 목록 → 컨테이너 내 실행 셸 명령."""

    @abstractmethod
    def parse_test_output(
        self, stdout: str, stderr: str, expected: list[str]
    ) -> list[TestOutcome]:
        """테스트 실행 결과 파싱 → 표준화된 TestOutcome 목록."""

    def post_patch_hook(self, container_id: str) -> None:
        """패치 적용 후 추가 작업 (기본: no-op).
        C++: cmake --build build 재컴파일.
        """
        pass

    def expected_dirty_at_base(self) -> bool:
        """base_commit에서 git tree가 dirty해도 정상으로 간주할지.
        Java(druid)는 setup_repo.sh가 pom.xml을 수정하므로 True.
        """
        return False
```

### 4.3 Dispatch 매핑

```python
# src/evaluator/languages/dispatch.py
from src.evaluator.languages.python import PythonProfile
from src.evaluator.languages.java import JavaProfile
from src.evaluator.languages.cpp import CppProfile

# repo (owner/name) → Profile
REPO_LANGUAGE: dict[str, type[LanguageProfile]] = {
    # Java
    "apache/druid": JavaProfile,
    "elastic/elasticsearch": JavaProfile,
    # C++
    "fmtlib/fmt": CppProfile,
    "nlohmann/json": CppProfile,
    # ... (multi tier 41개 레포 전체 매핑)
}

def get_profile(task: EvalTask, tier: str) -> LanguageProfile:
    """Tier가 multi가 아니면 무조건 Python.
    multi인데 매핑에 없으면 명시적 에러."""
    if tier != "multi":
        return PythonProfile()
    cls = REPO_LANGUAGE.get(task.repo)
    if cls is None:
        raise ValueError(
            f"No language profile registered for repo={task.repo} (tier=multi). "
            f"Add a mapping in src/evaluator/languages/dispatch.py"
        )
    return cls()
```

### 4.4 docker_evaluator.py 변경 영역 (정확히 4곳)

| # | 위치 | 변경 내용 | 영향 |
|---|------|----------|------|
| 1 | `get_image_name()` (L41-43) | profile.get_image_name() 위임 | 레지스트리 분기 |
| 2 | `CONDA_ACTIVATE` 상수 (L38) | 제거하고 profile.shell_prefix() 사용 | 환경 활성화 분기 |
| 3 | `_docker_exec()` (L138-144) | prefix를 인자로 받도록 변경 | exec 모든 호출 영향 |
| 4 | `_run_tests_in_container()` (L206-293) | profile.build_test_command + parse_test_output 위임 | 테스트 실행 분기 |

**나머지 코드(컨테이너 라이프사이클, 패치 적용 3-tier fallback, 타임아웃 등)는 변경 없음.**

### 4.5 호출 흐름 (변경 후)

```
DockerEvaluator.evaluate(task, patch, tier)
    │
    ├─ profile = dispatch.get_profile(task, tier)
    │   └─ tier!=multi → PythonProfile  (기존과 동일)
    │
    ├─ image = profile.get_image_name(task.instance_id)
    ├─ pull / start container
    ├─ apply test_patch
    ├─ apply agent_patch
    ├─ profile.post_patch_hook(container_id)  # C++만 재빌드, 나머지 no-op
    │
    ├─ for tests in [F2P, P2P]:
    │   ├─ cmd = profile.build_test_command(tests, task)
    │   ├─ stdout = _docker_exec(cid, profile.shell_prefix() + cmd)
    │   └─ outcomes = profile.parse_test_output(stdout, stderr, tests)
    │
    └─ return EvalResult(...)
```

---

## 5. 언어별 Profile 명세

### 5.1 PythonProfile (기존 로직 이관)

- **목표**: 기존 동작과 byte-identical
- **get_image_name**: `f"ghcr.io/epoch-research/swe-bench.eval.x86_64.{instance_id}:latest"`
- **shell_prefix**: `"source /opt/miniconda3/etc/profile.d/conda.sh && conda activate testbed && "`
- **build_test_command**: 기존 `_run_tests_in_container()` 내 Django/pytest 분기 그대로
- **parse_test_output**: 기존 `_parse_test_output()` 정규식 그대로
- **post_patch_hook**: no-op
- **회귀 검증**: 기존 mock-claude-code/mock-codex e2e 결과와 diff = 0

### 5.2 JavaProfile (druid 검증 완료)

- **get_image_name**: `instance_id`에 `__` → `_1776_` 치환 후 `f"docker.io/swebench/sweb.eval.x86_64.{transformed}:latest"`
- **shell_prefix**: `""` — Maven/JDK는 컨테이너 ENV/PATH로 이미 노출
- **build_test_command**:
  - F2P/P2P는 `com.foo.Bar#testMethod` FQCN 형식
  - 모듈(예: druid의 `processing`)은 클래스 파일 경로로부터 추론 (사전 매핑 또는 `find . -name 'Bar.java'`로 동적 발견)
  - `cd /testbed && mvn test -pl <module> -Dtest='Bar#testMethod' -DfailIfNoTests=false`
  - 다중 테스트는 `,`로 join
- **parse_test_output**:
  - 우선 `target/surefire-reports/TEST-*.xml`을 `cat` 또는 `docker cp`로 읽어 정확 파싱
  - Fallback: 콘솔 `Tests run: X, Failures: Y, Errors: Z` 패턴 + `<<< FAILURE!` 시그널
- **post_patch_hook**: no-op (mvn test가 컴파일 포함)
- **expected_dirty_at_base**: `True` (druid pom.xml 수정 이슈)
- **알려진 제약**: Maven Central 네트워크 필요 — 사내망/오프라인은 별도 캐시 전략 필요

### 5.3 CppProfile (fmt 검증 완료)

- **get_image_name**: Java와 동일한 `__` → `_1776_` 변환 + Docker Hub 레지스트리
- **shell_prefix**: `""`
- **build_test_command**:
  - F2P/P2P는 gtest 형식 `Suite.TestName`
  - 컨테이너 내 `build/` 디렉토리에 사전 빌드된 바이너리 존재
  - 바이너리 추론: suite name → snake-case → `build/bin/<snake>` 또는 `build/bin/<snake>-test`
  - 1차: `ctest --test-dir build -R '<Suite>\.<Test>$' --output-on-failure` (가장 안전)
  - 2차 fallback: `./build/bin/<binary> --gtest_filter='<Suite>.<Test>'`
- **parse_test_output**:
  - gtest 형식: `[       OK ] Suite.Test (Xms)` / `[  FAILED  ] Suite.Test`
  - 정규식: `r"\[\s+(OK|FAILED)\s+\]\s+(\S+)"`
- **post_patch_hook**: `cmake --build build -j$(nproc)` — 패치된 소스를 재컴파일
- **expected_dirty_at_base**: `False`
- **알려진 제약**: 패치가 CMakeLists.txt를 수정하면 `cmake -B build`부터 재실행 필요. 1차 구현은 단순 `cmake --build`만 수행하고 실패 시 로그 기록.

### 5.4 향후 언어 (참고 구현 가이드)

| 언어 | 주 빌드 도구 | 테스트 출력 파서 힌트 | 재컴파일 |
|------|-------------|---------------------|---------|
| JavaScript/TS | npm/jest | `PASS`/`FAIL` + `✓`/`✗` 콘솔 마커 | 불필요 |
| Go | `go test` | `--- PASS:` / `--- FAIL:` | go test가 포함 |
| Rust | cargo | `test foo ... ok` / `... FAILED` | cargo가 포함 |

각 언어 추가 시 (a) Profile 클래스 작성, (b) dispatch 매핑 추가, (c) 해당 레포 1개 e2e 검증 — 본 설계가 1개 PR/Day 단위로 커지지 않도록 보장.

---

## 6. 단계별 롤아웃

| Day | 산출물 | 검증 | Lite/Verified 영향 |
|-----|--------|------|--------------------|
| **D1** | `languages/` 패키지 + `LanguageProfile` ABC + `PythonProfile`(기존 로직 이관) + `dispatch.py`(Python only) + `docker_evaluator.py` 4곳 위임 | mock-claude-code/mock-codex e2e가 **byte-identical** 통과 | **0** (PythonProfile 단독 경로) |
| **D2** | `JavaProfile` + druid 1 instance 매핑 추가 | `--tier multi --sample-size 1` (druid)로 F2P/P2P 통과 확인 | 0 (multi tier만 분기) |
| **D3** | `CppProfile` + fmt 1 instance 매핑 추가 | `--tier multi --sample-size 1` (fmt)로 F2P/P2P 통과 확인 | 0 |
| **D4** | multi tier 300개 instance 전체 dispatch 매핑 작성 | check_docker_images.py로 매니페스트 reachable 확인 | 0 |
| **D5** | JS/Go/Rust Profile 중 1~2개 추가 (선택) | 각 언어 1 instance 검증 | 0 |

각 Day는 독립 커밋. D1만으로도 기존 시스템이 **개선** 없이 정확히 동작해야 통과.

---

## 7. 회귀 방지 전략

### 7.1 PythonProfile 1:1 이관 검증

D1 머지 전 필수:

1. master 브랜치에서 mock e2e 실행 → `results/runs/e2e-test-baseline/` 보관
2. 작업 브랜치에서 동일 e2e 실행 → 결과 JSON과 baseline의 **재귀 diff = 0** 확인
3. 차이가 있으면(타임스탬프/리포트 시각 제외) 즉시 PR 차단

### 7.2 Dispatch 기본값 = Python

- `tier in {local, lite, verified, full}` → 분기 1줄로 PythonProfile 즉시 반환
- `multi` 외 tier에서는 `REPO_LANGUAGE` 매핑을 **읽지도 않음** — 매핑 오타나 import 실패가 기존 평가에 영향 불가

### 7.3 테스트 분리

- `tests/test_evaluator/test_python_profile.py` — 기존 동작 회귀 (Lite/Verified 보호)
- `tests/test_evaluator/test_java_profile.py`, `test_cpp_profile.py` — 신규 언어 단위 테스트
- CI에서 두 그룹은 독립 실행, Java/C++ 그룹 실패가 Python 그룹을 차단하지 않음

### 7.4 코드 경로 격리

- `docker_evaluator.py`의 변경 영역은 **모두 위임** 형태 — 본문 로직 추가 없음
- 새 코드는 `languages/` 패키지에만 존재 → import 안 하면 영향 0

---

## 8. 알려진 제약 / 리스크

| 리스크 | 영향 범위 | 대응 |
|--------|----------|------|
| Docker Hub anonymous pull rate limit (100/6hr) | multi tier만 | `docker login` 사전 안내, prepull_images.py 재사용 |
| Maven 의존성 다운로드 네트워크 필요 | Java multi 평가 | 1차는 인터넷 환경 가정. 사내망은 후속 (Maven mirror 또는 사전 캐시 이미지 빌드) |
| C++ binary 명명 추론 실패 가능성 | C++ multi 평가 | 1차는 ctest -R 우선, 실패 시 stderr 로그로 디버깅 — 매핑 누락 케이스를 운영 중 발견 → dispatch에 명시적 매핑 추가 |
| Java git tree dirty (druid) | Step 2 patch 적용 | `expected_dirty_at_base=True`로 patch 적용 전 `git stash` 또는 무시. 검증 완료. |
| 신규 언어 추가 시 4 변경 영역 외 hook 필요 가능성 | 향후 확장 | LanguageProfile에 optional method 추가, 기본 구현은 no-op로 유지 |
| multi tier 300 instance 전부 dispatch 매핑 누락 시 즉시 실패 | multi 운영 | D4에서 `check_docker_images.py`와 동시에 매핑 검증 스크립트 작성 |

---

## 9. 새 언어 추가 가이드 (운영용)

향후 다른 언어를 추가하려는 사람을 위한 체크리스트:

1. `src/evaluator/languages/<lang>.py` 생성 — `LanguageProfile` 상속
2. 5개 메서드 구현: `get_image_name`, `shell_prefix`, `build_test_command`, `parse_test_output`, (필요 시) `post_patch_hook`
3. `dispatch.py`의 `REPO_LANGUAGE`에 해당 레포(들) 매핑 추가
4. `tests/test_evaluator/test_<lang>_profile.py`에 단위 테스트 작성 (output 파싱 위주)
5. `--tier multi --sample-size 1`로 해당 레포 1 instance를 e2e 실행하여 F2P/P2P가 의도대로 통과/실패하는지 확인
6. 결과 JSON을 PR에 첨부

이 5단계 외에는 어떤 파일도 건드리지 말 것 — 그것이 본 설계의 zero-impact 보장 기제.

---

## 10. 결정 사항 / 미결 이슈

### 10.1 결정 사항

- **레지스트리 분기 위치**: `LanguageProfile.get_image_name()` 내부에서 처리 (docker_evaluator.py 본문에 분기 추가하지 않음)
- **Dispatch 키**: `task.repo` (owner/name) — instance_id가 아닌 repo 단위. 한 레포는 한 언어로 단정.
- **기본 fallback 정책**: tier!=multi → Python 강제, tier==multi & 매핑 없음 → 명시적 에러 raise

### 10.2 미결 이슈 (구현 전 확정 필요)

- [ ] Java Maven 캐시 전략 — D2 작업 시 사외망에서 가능 여부 결정
- [ ] C++ ctest -R가 모든 레포에서 통하는지 확인 (json 등 다른 레포는 D5+ 검증)
- [ ] `LanguageProfile.parse_test_output`의 `expected` 인자 사용 여부 — Python에선 불필요했지만 Java surefire-XML 매핑에 도움 가능

---

## 11. 부록: 현행 docker_evaluator.py 영향 라인 매핑

D1 PR에서 정확히 변경되는 라인:

| 현행 라인 | 현행 코드 | D1 변경 후 |
|----------|----------|-----------|
| L34 | `IMAGE_REGISTRY = "ghcr.io/epoch-research"` | (제거) |
| L35 | `IMAGE_PREFIX = f"swe-bench.eval.{ARCH}"` | (제거) |
| L38 | `CONDA_ACTIVATE = "source ..."` | (제거) |
| L41-43 | `def get_image_name(...)` | `profile.get_image_name(...)`로 위임 |
| L138-144 | `_docker_exec` 내 `f"{CONDA_ACTIVATE} && {cmd}"` | `f"{profile.shell_prefix()}{cmd}"` |
| L206-293 | 테스트 실행 본문 | `profile.build_test_command + parse_test_output` 호출 |
| L425-447 | test_patch 적용 후 reset | (변경 없음) |

본문 신규 import는 `from src.evaluator.languages.dispatch import get_profile` 한 줄.
