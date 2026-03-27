# PolicySim

정책 문서(PDF) 기반 파급효과 시뮬레이터. [MiroFish](https://github.com/666ghj/MiroFish) 기반.

## 셋업

### 필요한 것

- **Node.js** 20.19+ (`node -v`)
- **Python** 3.10+ (`python3 --version`)
- **LLM API Key** — LuxiaCloud Bridge (팀 슬랙에서 공유)
- **Zep API Key** — https://www.getzep.com/ 에서 무료 발급

### 설치

```bash
git clone https://github.com/haaaein/Policy_Simulation.git
cd Policy_Simulation

# 의존성 설치
cd frontend && npm install && cd ..
cd backend && pip3 install -r requirements.txt && cd ..

# 환경 변수 설정
cp .env.example .env
# .env 파일에 LLM_API_KEY, ZEP_API_KEY 입력
```

### 실행

```bash
# 터미널 1: 백엔드 (포트 5001)
cd backend && python3 run.py

# 터미널 2: 프론트엔드 (포트 3000)
cd frontend && npm run dev -- --host
```

http://localhost:3000 접속.

## .env 설정

```env
LLM_API_KEY=팀_슬랙에서_공유된_키
LLM_BASE_URL=https://bridge.luxiacloud.com/llm/openai
LLM_MODEL_NAME=gpt-4o-mini
ZEP_API_KEY=본인_Zep_키
```

> 나머지 설정(`FLASK_DEBUG`, `SECRET_KEY` 등)은 기본값이 있어서 안 넣어도 됨.

## Contributors

<table>
  <tr>
    <td align="center"><b>여해인</b><br/>프로젝트 리드</td>
  </tr>
</table>
