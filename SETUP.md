# PolicySim - 로컬 개발 환경 설정 가이드

## 개요
PolicySim은 정책 문서(PDF)를 기반으로 지식 그래프를 자동 구축하고, 이해관계자별 에이전트가 정책 파급효과를 시뮬레이션하는 도구입니다.

MiroFish 오픈소스 프로젝트를 기반으로, 한국 정책 분석에 맞게 커스터마이징되었습니다.

---

## 사전 요구사항

| 항목 | 버전 | 확인 명령어 |
|---|---|---|
| Node.js | 20.19+ 또는 22.12+ | `node -v` |
| Python | 3.10+ | `python3 --version` |
| pip | 최신 | `pip3 --version` |
| Git | 최신 | `git --version` |

---

## 1. 클론 및 설치

```bash
# 저장소 클론
git clone https://github.com/haaaein/Policy_Simulation.git
cd Policy_Simulation

# 프론트엔드 의존성 설치
cd frontend
npm install
cd ..

# 백엔드 의존성 설치
cd backend
pip3 install -r requirements.txt
cd ..
```

---

## 2. 환경 변수 설정

프로젝트 루트에 `.env` 파일을 생성합니다:

```bash
cp .env.example .env
```

`.env` 파일을 편집하여 API 키를 입력합니다:

```env
# LLM 설정 — LuxiaCloud Bridge API
LLM_API_KEY=여기에_LLM_API_키_입력
LLM_BASE_URL=https://bridge.luxiacloud.com/llm/openai
LLM_MODEL_NAME=gpt-4o-mini

# Zep 설정 (그래프 메모리)
# https://www.getzep.com/ 에서 무료 계정 생성 후 API Key 발급
ZEP_API_KEY=여기에_ZEP_API_키_입력

# Flask 설정
FLASK_DEBUG=True
SECRET_KEY=your-secret-key

# OASIS 시뮬레이션
OASIS_DEFAULT_MAX_ROUNDS=10
```

### API 키 발급 방법

**LLM API (LuxiaCloud)**
- LuxiaCloud Bridge 계정에서 API Key를 발급받습니다
- 다른 OpenAI 호환 API도 사용 가능 (base_url 변경 필요)

**Zep API**
1. https://www.getzep.com/ 접속
2. "Get Started Free" 클릭 (신용카드 불필요)
3. 대시보드에서 Project 생성
4. Settings → API Keys에서 API Key 복사

---

## 3. 실행

터미널 2개를 열어서 각각 실행합니다:

### 터미널 1: 백엔드 서버

```bash
cd backend
python3 run.py
```

정상 실행 시:
```
MiroFish Backend 시작 완료
 * Running on http://127.0.0.1:5001
```

### 터미널 2: 프론트엔드 개발 서버

```bash
cd frontend
npm run dev -- --host
```

정상 실행 시:
```
VITE ready
  ➜  Local:   http://localhost:3000/
```

### 브라우저에서 접속

http://localhost:3000 으로 접속합니다.

---

## 4. 사용 방법

1. **정책 문서 업로드**: 메인 화면에서 PDF, MD, TXT 파일을 드래그 & 드롭
2. **시뮬레이션 프롬프트 입력**: 분석하고 싶은 정책 파급효과를 자연어로 입력
3. **분석 시작**: 자동으로 온톨로지 추출 → 그래프 구축 → 환경 설정 → 시뮬레이션 진행
4. **보고서 확인**: 시뮬레이션 완료 후 파급효과 분석 보고서 생성
5. **심층 상호작용**: 에이전트와 직접 대화하여 추가 분석

---

## 5. 프로젝트 구조

```
Policy_Simulation/
├── frontend/                    # Vue 3 프론트엔드
│   ├── src/
│   │   ├── views/              # 페이지 컴포넌트
│   │   ├── components/         # 재사용 컴포넌트
│   │   ├── api/                # API 호출 모듈
│   │   └── store/              # 상태 관리
│   └── package.json
├── backend/                     # Flask 백엔드
│   ├── app/
│   │   ├── api/                # API 라우트 (graph, simulation, report)
│   │   ├── services/           # 핵심 서비스 로직
│   │   │   ├── ontology_generator.py    # 온톨로지 자동 생성
│   │   │   ├── graph_builder.py         # Zep 기반 그래프 구축
│   │   │   ├── oasis_profile_generator.py # 에이전트 페르소나 생성
│   │   │   ├── simulation_runner.py     # OASIS 시뮬레이션 실행
│   │   │   ├── report_agent.py          # 보고서 생성 에이전트
│   │   │   └── zep_tools.py             # Zep 검색 도구
│   │   ├── models/             # 데이터 모델
│   │   └── utils/              # 유틸리티 (LLM 클라이언트, 파서 등)
│   ├── scripts/                # 시뮬레이션 실행 스크립트
│   └── requirements.txt
├── .env                         # 환경 변수 (git에 포함되지 않음)
├── .env.example                 # 환경 변수 템플릿
└── SETUP.md                     # 이 파일
```

---

## 6. 문제 해결

### Node.js 버전 오류
```
Vite requires Node.js version 20.19+
```
→ Node.js를 최신 LTS 버전으로 업그레이드하세요: `nvm install --lts`

### 백엔드 시작 실패
```
LLM_API_KEY 미설정
```
→ `.env` 파일에 API 키가 올바르게 설정되었는지 확인하세요

### 그래프 구축이 느림
→ Zep 서버에서 NER/관계 추출을 수행하므로 문서 크기에 따라 1~5분 소요될 수 있습니다

### 프론트엔드에서 Network Error
→ 백엔드 서버(포트 5001)가 실행 중인지 확인하세요

---

## 7. 기술 스택

- **프론트엔드**: Vue 3 + Vite + D3.js
- **백엔드**: Flask + Python 3.10+
- **LLM**: OpenAI 호환 API (GPT-4o-mini)
- **그래프 DB**: Zep Cloud (GraphRAG)
- **시뮬레이션**: OASIS (camel-ai 기반 멀티에이전트)
