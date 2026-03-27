# PolicySim — Evidence-Grounded Policy Simulation

> 정책 문서를 업로드하면, 이해관계자별 에이전트가 파급효과를 시뮬레이션합니다.

PDF 기반 정책 문서에서 **지식 그래프**를 자동 구축하고, 이해관계자별 에이전트가 다중 시나리오에서 정책 반응을 시뮬레이션합니다. **Critic 모듈**이 과거 정책 성과 데이터와 비교하여 예측을 보정합니다.

---

## 개요

PolicySim은 [MiroFish](https://github.com/666ghj/MiroFish) 오픈소스 프로젝트를 기반으로, **한국 정책 파급효과 분석**에 특화되도록 커스터마이징한 시뮬레이션 도구입니다.

### 핵심 기능

- **정책 온톨로지 자동 추출**: PDF 문서에서 이해관계자, 예산, 평가 체계, 제약 조건을 자동으로 구조화
- **지식 그래프 기반 에이전트 생성**: 추출된 엔티티로부터 이해관계자 페르소나를 자동 생성
- **다중 시나리오 시뮬레이션**: 에이전트 간 상호작용을 통한 정책 파급 경로 탐색
- **근거 기반 보고서**: 예측의 근거가 추적 가능한 파급효과 분석 보고서 생성
- **심층 상호작용**: 시뮬레이션 에이전트와 직접 대화하여 추가 분석

### 워크플로

```
01 그래프 구축    정책 문서에서 온톨로지 추출 → 이해관계자·예산·제약 관계 그래프 구축
02 환경 설정      이해관계자별 에이전트 페르소나 생성 → 시나리오 파라미터 설정
03 시뮬레이션     다중 시나리오 병렬 시뮬레이션 → 정책 파급 경로 탐색 → 그래프 실시간 업데이트
04 보고서 생성    Critic 모듈이 예측 검증 → 근거 추적 가능한 파급효과 분석 보고서 생성
05 심층 상호작용  시나리오별 비교 → 반사실적 질의 → 이해관계자 에이전트와 직접 대화
```

---

## 로컬 환경 설정

### 사전 요구사항

| 항목 | 버전 | 확인 명령어 |
|---|---|---|
| Node.js | 20.19+ 또는 22.12+ | `node -v` |
| Python | 3.10+ | `python3 --version` |
| pip | 최신 | `pip3 --version` |

### 1. 클론 및 설치

```bash
git clone https://github.com/haaaein/Policy_Simulation.git
cd Policy_Simulation

# 프론트엔드 의존성 설치
cd frontend && npm install && cd ..

# 백엔드 의존성 설치
cd backend && pip3 install -r requirements.txt && cd ..
```

### 2. 환경 변수 설정

```bash
cp .env.example .env
```

`.env` 파일을 편집하여 API 키를 입력합니다:

```env
# LLM 설정 — OpenAI 호환 API
LLM_API_KEY=your_llm_api_key
LLM_BASE_URL=https://bridge.luxiacloud.com/llm/openai
LLM_MODEL_NAME=gpt-4o-mini

# Zep 설정 — https://www.getzep.com/ 에서 무료 발급
ZEP_API_KEY=your_zep_api_key

# Flask
FLASK_DEBUG=True
SECRET_KEY=your-secret-key
OASIS_DEFAULT_MAX_ROUNDS=10
```

### 3. 실행

터미널 2개를 열어서 각각 실행합니다:

```bash
# 터미널 1: 백엔드
cd backend && python3 run.py

# 터미널 2: 프론트엔드
cd frontend && npm run dev -- --host
```

브라우저에서 **http://localhost:3000** 으로 접속합니다.

---

## 사용 방법

1. 메인 화면에서 **정책 문서**(PDF, MD, TXT)를 드래그 & 드롭
2. **시뮬레이션 프롬프트**를 자연어로 입력 (예: "BK21 사업이 지방대학에 미치는 영향 분석")
3. **분석 시작** 클릭 → 자동으로 온톨로지 추출 → 그래프 구축 → 시뮬레이션 진행
4. 시뮬레이션 완료 후 **파급효과 분석 보고서** 확인
5. 에이전트와 **직접 대화**하여 추가 분석

---

## 프로젝트 구조

```
Policy_Simulation/
├── frontend/                    # Vue 3 프론트엔드
│   ├── src/views/              # 페이지 (Home, Process, MainView 등)
│   ├── src/components/         # 단계별 컴포넌트 (Step1~5, GraphPanel 등)
│   └── src/api/                # API 호출 모듈
├── backend/                     # Flask 백엔드
│   ├── app/api/                # API 라우트 (graph, simulation, report)
│   ├── app/services/           # 핵심 서비스
│   │   ├── ontology_generator.py    # 정책 온톨로지 자동 생성
│   │   ├── graph_builder.py         # Zep 기반 지식 그래프 구축
│   │   ├── oasis_profile_generator.py # 이해관계자 에이전트 페르소나 생성
│   │   ├── simulation_runner.py     # OASIS 멀티에이전트 시뮬레이션
│   │   ├── report_agent.py          # 파급효과 분석 보고서 생성
│   │   └── zep_tools.py             # 그래프 검색 도구 (InsightForge, Panorama)
│   └── scripts/                # 시뮬레이션 실행 스크립트
├── .env.example                 # 환경 변수 템플릿
└── README.md                    # 이 파일
```

---

## 기술 스택

| 영역 | 기술 |
|---|---|
| 프론트엔드 | Vue 3, Vite, D3.js |
| 백엔드 | Flask, Python 3.10+ |
| LLM | OpenAI 호환 API (GPT-4o-mini) |
| 그래프 | Zep Cloud (GraphRAG) |
| 시뮬레이션 | OASIS (camel-ai 기반 멀티에이전트) |

---

## 문제 해결

| 증상 | 해결 |
|---|---|
| `Vite requires Node.js 20.19+` | Node.js 최신 LTS로 업그레이드 |
| `LLM_API_KEY 미설정` | `.env` 파일에 API 키 입력 확인 |
| 그래프 구축이 느림 | Zep 서버 NER 처리 시간 — 문서 크기에 따라 1~5분 소요 |
| 프론트엔드 Network Error | 백엔드(포트 5001)가 실행 중인지 확인 |

---

## 감사의 말

- 시뮬레이션 엔진: **[MiroFish](https://github.com/666ghj/MiroFish)** — 멀티에이전트 시뮬레이션 프레임워크
- 에이전트 프레임워크: **[OASIS](https://github.com/camel-ai/oasis)** by CAMEL-AI
- 그래프 메모리: **[Zep](https://www.getzep.com/)**

---

## Contributors

<table>
  <tr>
    <td align="center"><b>여해인</b><br/>프로젝트 리드</td>
  </tr>
</table>
