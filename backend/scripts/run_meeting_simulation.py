"""
이해관계자 회의 시뮬레이션 엔진 (OASIS 독립)

OASIS 의존성 없이 OpenAI SDK로 직접 LLM 호출.
기존 프론트엔드와 호환되는 JSONL 포맷 출력.

사용법: python3 run_meeting_simulation.py --config <config.json> [--max-rounds N] [--no-wait]
"""

import asyncio
import argparse
import csv
import json
import os
import re
import signal
import sys
import time
import glob
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import List, Dict, Optional, Any

# .env 로드
from dotenv import load_dotenv
project_root_env = os.path.join(os.path.dirname(__file__), '../../.env')
if os.path.exists(project_root_env):
    load_dotenv(project_root_env, override=True)
else:
    load_dotenv(override=True)

# UTF-8 보장
os.environ.setdefault("PYTHONUTF8", "1")

from openai import OpenAI

logger = logging.getLogger("meeting_simulation")
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

# 유효한 회의 액션
VALID_ACTIONS = {"PROPOSE", "SUPPORT", "OPPOSE", "AMEND", "QUESTION", "ABSTAIN"}


# ============================================================
# 데이터 클래스
# ============================================================

@dataclass
class AgentProfile:
    agent_id: int
    name: str
    persona: str
    bio: str = ""
    profession: str = ""
    entity_type: str = ""


@dataclass
class MeetingStatement:
    agent_id: int
    agent_name: str
    action_type: str
    content: str


# ============================================================
# LLM 클라이언트 (OASIS 없이 직접 호출)
# ============================================================

class LLMClient:
    """OpenAI SDK 기반 LLM 클라이언트 — LuxiaCloud 프록시 자동 감지"""

    def __init__(self):
        api_key = os.environ.get("LLM_API_KEY", "")
        base_url = os.environ.get("LLM_BASE_URL", "")
        self.model = os.environ.get("LLM_MODEL_NAME", "gpt-4o-mini")

        # LuxiaCloud 감지 → 로컬 프록시 사용
        proxy_url = os.environ.get("LLM_PROXY_URL", "")
        is_luxia = 'luxiacloud' in (base_url or '')

        if is_luxia and not proxy_url:
            proxy_url = "http://localhost:8788/v1"
            effective_key = "proxy-key"
            logger.info(f"[LuxiaCloud 감지] 프록시 사용: {proxy_url}")
        else:
            effective_key = api_key

        effective_url = proxy_url or base_url or "https://api.openai.com/v1"

        if not effective_key and not proxy_url:
            raise ValueError("LLM_API_KEY가 설정되지 않았습니다.")

        self.client = OpenAI(api_key=effective_key, base_url=effective_url)
        logger.info(f"LLM 설정: model={self.model}, base_url={effective_url[:50]}...")

    async def chat(self, system_msg: str, user_msg: str, temperature: float = 0.7, max_tokens: int = 1024) -> str:
        """비동기 LLM 호출 (스레드풀에서 실행)"""
        def _call():
            for attempt in range(3):
                try:
                    resp = self.client.chat.completions.create(
                        model=self.model,
                        messages=[
                            {"role": "system", "content": system_msg},
                            {"role": "user", "content": user_msg},
                        ],
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                    content = resp.choices[0].message.content or ""
                    # <think> 태그 제거
                    content = re.sub(r'<think>[\s\S]*?</think>', '', content).strip()
                    return content
                except Exception as e:
                    if attempt < 2:
                        time.sleep(2 ** attempt)
                        continue
                    raise
        return await asyncio.to_thread(_call)

    async def chat_json(self, system_msg: str, user_msg: str) -> Dict:
        """JSON 응답 요청"""
        raw = await self.chat(system_msg, user_msg, temperature=0.3)
        raw = re.sub(r'^```(?:json)?\s*\n?', '', raw, flags=re.IGNORECASE)
        raw = re.sub(r'\n?```\s*$', '', raw)
        return json.loads(raw.strip())


# ============================================================
# JSONL 로거 (기존 포맷 매칭)
# ============================================================

class MeetingActionLogger:
    """twitter/actions.jsonl에 기존 포맷으로 기록"""

    def __init__(self, sim_dir: str):
        self.actions_dir = os.path.join(sim_dir, "twitter")
        os.makedirs(self.actions_dir, exist_ok=True)
        self.actions_path = os.path.join(self.actions_dir, "actions.jsonl")
        self._file = open(self.actions_path, 'w', encoding='utf-8')

    def _write(self, data: dict):
        self._file.write(json.dumps(data, ensure_ascii=False) + "\n")
        self._file.flush()

    def log_simulation_start(self, total_rounds: int, agents_count: int):
        self._write({
            "timestamp": datetime.now().isoformat(),
            "event_type": "simulation_start",
            "platform": "twitter",
            "total_rounds": total_rounds,
            "agents_count": agents_count,
        })

    def log_round_start(self, round_num: int, simulated_hour: int):
        self._write({
            "round": round_num,
            "timestamp": datetime.now().isoformat(),
            "event_type": "round_start",
            "simulated_hour": simulated_hour,
        })

    def log_action(self, round_num: int, agent_id: int, agent_name: str,
                   action_type: str, content: str):
        self._write({
            "round": round_num,
            "timestamp": datetime.now().isoformat(),
            "agent_id": agent_id,
            "agent_name": agent_name,
            "action_type": action_type,
            "action_args": {"content": content},
            "result": None,
            "success": True,
        })

    def log_round_end(self, round_num: int, actions_count: int):
        self._write({
            "round": round_num,
            "timestamp": datetime.now().isoformat(),
            "event_type": "round_end",
            "actions_count": actions_count,
        })

    def log_simulation_end(self, total_rounds: int, total_actions: int):
        self._write({
            "timestamp": datetime.now().isoformat(),
            "event_type": "simulation_end",
            "platform": "twitter",
            "total_rounds": total_rounds,
            "total_actions": total_actions,
        })

    def close(self):
        self._file.close()


# ============================================================
# IPC 핸들러 (인터뷰 지원 — OASIS 없이 직접 LLM 호출)
# ============================================================

class IPCHandler:
    """파일 기반 IPC — 인터뷰 명령 처리"""

    def __init__(self, sim_dir: str, agents: List[AgentProfile], llm: LLMClient):
        self.cmd_dir = os.path.join(sim_dir, "ipc_commands")
        self.resp_dir = os.path.join(sim_dir, "ipc_responses")
        os.makedirs(self.cmd_dir, exist_ok=True)
        os.makedirs(self.resp_dir, exist_ok=True)
        self.agents = {a.agent_id: a for a in agents}
        self.llm = llm

    async def process_commands(self, shutdown_event: asyncio.Event):
        """명령 폴링 루프"""
        while not shutdown_event.is_set():
            cmd_files = sorted(glob.glob(os.path.join(self.cmd_dir, "*.json")))
            for cmd_file in cmd_files:
                try:
                    with open(cmd_file, 'r', encoding='utf-8') as f:
                        cmd = json.load(f)
                    os.remove(cmd_file)

                    cmd_type = cmd.get("command_type", "")
                    cmd_id = cmd.get("command_id", "")

                    if cmd_type == "interview":
                        result = await self._handle_interview(cmd.get("args", {}))
                    elif cmd_type == "batch_interview":
                        result = await self._handle_batch_interview(cmd.get("args", {}))
                    elif cmd_type == "close_env":
                        shutdown_event.set()
                        result = {"status": "closing"}
                    else:
                        result = {"error": f"unknown command: {cmd_type}"}

                    resp = {
                        "command_id": cmd_id,
                        "status": "completed",
                        "result": result,
                        "error": None,
                        "timestamp": datetime.now().isoformat(),
                    }
                    resp_path = os.path.join(self.resp_dir, f"{cmd_id}.json")
                    with open(resp_path, 'w', encoding='utf-8') as f:
                        json.dump(resp, f, ensure_ascii=False)

                except Exception as e:
                    logger.error(f"IPC 명령 처리 오류: {e}")

            await asyncio.sleep(0.5)

    async def _handle_interview(self, args: dict) -> dict:
        agent_id = args.get("agent_id", 0)
        prompt = args.get("prompt", "")
        agent = self.agents.get(agent_id)
        if not agent:
            return {"error": f"agent_id {agent_id} not found"}

        system_msg = f"당신은 {agent.name}입니다. {agent.persona}\n이전 회의 토론 내용을 바탕으로 질문에 답하세요. 한국어로 답변하세요."
        response = await self.llm.chat(system_msg, prompt)
        return {"agent_id": agent_id, "response": response, "timestamp": datetime.now().isoformat()}

    async def _handle_batch_interview(self, args: dict) -> dict:
        interviews = args.get("interviews", [])
        results = []
        for item in interviews:
            result = await self._handle_interview(item)
            results.append(result)
        return {"interviews": results}


# ============================================================
# 메인 회의 시뮬레이션 엔진
# ============================================================

class MeetingSimulation:
    """이해관계자 회의 시뮬레이션 — OASIS 없이 독립 실행"""

    WAVE_COUNT = 3  # 웨이브 수
    SEMAPHORE_LIMIT = 10  # 동시 LLM 호출 제한

    def __init__(self, config_path: str, max_rounds_override: int = None, wait_for_commands: bool = True):
        self.config_path = config_path
        self.sim_dir = os.path.dirname(config_path)
        self.wait_for_commands = wait_for_commands
        self.shutdown_event = asyncio.Event()

        # 설정 로드
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = json.load(f)

        # 라운드 수 결정
        meeting_config = self.config.get("meeting_config") or {}
        default_rounds = meeting_config.get("max_rounds", 8)
        self.total_rounds = max_rounds_override or default_rounds
        self.agenda = meeting_config.get("agenda_items", [])
        self.meeting_title = meeting_config.get("meeting_title", self.config.get("simulation_requirement", "정책 토론"))
        self.simulation_requirement = self.config.get("simulation_requirement", "")

        # 의제 부족 시 채우기
        while len(self.agenda) < self.total_rounds:
            self.agenda.append(f"라운드 {len(self.agenda)+1}: 추가 토론")

        # LLM 클라이언트
        self.llm = LLMClient()

        # 에이전트 로드
        self.agents = self._load_agents()

        # 로거
        self.action_logger = MeetingActionLogger(self.sim_dir)

        # IPC
        self.ipc_handler = IPCHandler(self.sim_dir, self.agents, self.llm)

        # 환경 상태 파일
        self.env_status_path = os.path.join(self.sim_dir, "env_status.json")

        # 통계
        self.total_actions = 0

    def _load_agents(self) -> List[AgentProfile]:
        """twitter_profiles.csv에서 에이전트 로드"""
        csv_path = os.path.join(self.sim_dir, "twitter_profiles.csv")
        agents = []
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                agents.append(AgentProfile(
                    agent_id=int(row.get("user_id", len(agents))),
                    name=row.get("name", row.get("username", f"Agent_{len(agents)}")),
                    persona=row.get("persona", ""),
                    bio=row.get("bio", ""),
                    profession=row.get("profession", ""),
                    entity_type=row.get("entity_type", ""),
                ))
        logger.info(f"에이전트 {len(agents)}명 로드 완료")
        return agents

    def _set_env_status(self, status: str):
        with open(self.env_status_path, 'w') as f:
            json.dump({"status": status}, f)

    def _parse_response(self, text: str) -> tuple:
        """LLM 응답에서 ACTION과 CONTENT 추출"""
        action_match = re.search(r'ACTION:\s*(\w+)', text, re.IGNORECASE)
        content_match = re.search(r'CONTENT:\s*(.+)', text, re.DOTALL | re.IGNORECASE)

        action = action_match.group(1).upper() if action_match else "PROPOSE"
        if action not in VALID_ACTIONS:
            action = "PROPOSE"

        content = content_match.group(1).strip() if content_match else text.strip()
        # content가 비어있으면 전체 텍스트 사용
        if not content or len(content) < 5:
            content = text.strip()

        return action, content

    async def _agent_turn(self, agent: AgentProfile, round_num: int,
                          round_topic: str, previous_statements: List[MeetingStatement],
                          semaphore: asyncio.Semaphore) -> MeetingStatement:
        """에이전트 1명의 발언"""
        async with semaphore:
            # 이전 발언 구성
            prev_text = ""
            if previous_statements:
                lines = []
                for s in previous_statements[-15:]:  # 최근 15개까지
                    lines.append(f"- {s.agent_name}: [{s.action_type}] {s.content[:200]}")
                prev_text = "\n".join(lines)

            system_msg = (
                f"당신은 {agent.name}입니다. {agent.persona}\n"
                f"정책 이해관계자 회의에 참석 중입니다. 반드시 한국어로 발언하세요.\n"
                f"당신의 직업/역할: {agent.profession}"
            )

            user_msg = (
                f"회의 주제: {self.simulation_requirement}\n"
                f"라운드 {round_num}/{self.total_rounds}: {round_topic}\n\n"
            )
            if prev_text:
                user_msg += f"이전 발언:\n{prev_text}\n\n"
            else:
                user_msg += "이번 라운드의 첫 발언입니다.\n\n"

            user_msg += (
                "다음 중 하나를 선택하여 발언하세요:\n"
                "PROPOSE (새로운 제안), SUPPORT (동의), OPPOSE (반대), "
                "AMEND (수정안 제시), QUESTION (질문), ABSTAIN (보류)\n\n"
                "형식:\n"
                "ACTION: 선택한_액션\n"
                "CONTENT: 발언 내용"
            )

            try:
                response = await self.llm.chat(system_msg, user_msg, temperature=0.8, max_tokens=500)
                action, content = self._parse_response(response)
            except Exception as e:
                logger.warning(f"에이전트 {agent.name} LLM 호출 실패: {e}")
                action, content = "ABSTAIN", f"(발언 생성 실패: {str(e)[:50]})"

            return MeetingStatement(
                agent_id=agent.agent_id,
                agent_name=agent.name,
                action_type=action,
                content=content,
            )

    async def _generate_round_summary(self, round_num: int, round_topic: str,
                                       statements: List[MeetingStatement]) -> str:
        """라운드 요약 생성"""
        stmt_text = "\n".join(
            f"- {s.agent_name} [{s.action_type}]: {s.content[:300]}"
            for s in statements if s.action_type != "ABSTAIN"
        )

        system_msg = "당신은 정책 회의 진행자입니다. 회의 라운드를 간결하게 요약하세요. 한국어로 작성."
        user_msg = (
            f"라운드 {round_num} 의제: {round_topic}\n\n"
            f"발언 내용:\n{stmt_text}\n\n"
            f"이 라운드의 핵심 논점과 합의/갈등 사항을 3~5문장으로 요약하세요."
        )

        try:
            return await self.llm.chat(system_msg, user_msg, temperature=0.3, max_tokens=500)
        except Exception as e:
            return f"(요약 생성 실패: {e})"

    async def _generate_consensus(self, all_round_summaries: List[str]) -> str:
        """최종 합의 요약"""
        summaries_text = "\n\n".join(
            f"라운드 {i+1}: {s}" for i, s in enumerate(all_round_summaries)
        )

        system_msg = "당신은 정책 회의 진행자입니다. 전체 회의 결과를 종합하세요. 한국어로 작성."
        user_msg = (
            f"회의 주제: {self.simulation_requirement}\n\n"
            f"각 라운드 요약:\n{summaries_text}\n\n"
            f"전체 회의의 주요 합의사항, 미해결 쟁점, 다음 단계 제안을 작성하세요."
        )

        try:
            return await self.llm.chat(system_msg, user_msg, temperature=0.3, max_tokens=800)
        except Exception as e:
            return f"(합의 요약 생성 실패: {e})"

    async def run(self):
        """메인 시뮬레이션 루프"""
        print("=" * 60)
        print("이해관계자 회의 시뮬레이션 (OASIS 독립 엔진)")
        print(f"설정파일: {self.config_path}")
        print(f"회의 주제: {self.meeting_title}")
        print(f"총 라운드: {self.total_rounds}")
        print(f"참석자: {len(self.agents)}명")
        print(f"웨이브 수: {self.WAVE_COUNT}")
        print(f"대기 명령 모드: {'활성화' if self.wait_for_commands else '비활성화'}")
        print("=" * 60)

        self._set_env_status("running")
        self.action_logger.log_simulation_start(self.total_rounds, len(self.agents))

        semaphore = asyncio.Semaphore(self.SEMAPHORE_LIMIT)
        round_summaries = []

        for round_num in range(1, self.total_rounds + 1):
            if self.shutdown_event.is_set():
                logger.info("종료 신호 감지, 시뮬레이션 중단")
                break

            round_topic = self.agenda[round_num - 1] if round_num <= len(self.agenda) else f"라운드 {round_num} 토론"

            print(f"\n--- [라운드 {round_num}/{self.total_rounds}] {round_topic} ---")
            self.action_logger.log_round_start(round_num, round_num - 1)

            # 웨이브 기반 실행
            all_statements: List[MeetingStatement] = []
            wave_size = max(1, len(self.agents) // self.WAVE_COUNT)
            waves = []
            for i in range(0, len(self.agents), wave_size):
                waves.append(self.agents[i:i + wave_size])

            for wave_idx, wave_agents in enumerate(waves):
                tasks = [
                    self._agent_turn(agent, round_num, round_topic, all_statements, semaphore)
                    for agent in wave_agents
                ]
                wave_results = await asyncio.gather(*tasks, return_exceptions=True)

                for result in wave_results:
                    if isinstance(result, Exception):
                        logger.error(f"웨이브 {wave_idx+1} 에이전트 오류: {result}")
                        continue
                    all_statements.append(result)
                    # JSONL 기록
                    self.action_logger.log_action(
                        round_num, result.agent_id, result.agent_name,
                        result.action_type, result.content
                    )
                    self.total_actions += 1
                    print(f"  {result.agent_name}: [{result.action_type}] {result.content[:80]}...")

            # 라운드 요약
            summary = await self._generate_round_summary(round_num, round_topic, all_statements)
            round_summaries.append(summary)
            self.action_logger.log_action(round_num, -1, "회의진행자", "SUMMARY", summary)
            self.total_actions += 1
            print(f"  📋 요약: {summary[:100]}...")

            self.action_logger.log_round_end(round_num, len(all_statements) + 1)

        # 최종 합의 요약
        if round_summaries and not self.shutdown_event.is_set():
            print("\n--- [최종 합의 요약] ---")
            consensus = await self._generate_consensus(round_summaries)
            self.action_logger.log_action(self.total_rounds, -1, "회의진행자", "CONSENSUS", consensus)
            self.total_actions += 1
            print(f"  🤝 합의: {consensus[:200]}...")

        self.action_logger.log_simulation_end(self.total_rounds, self.total_actions)
        self.action_logger.close()

        print(f"\n✅ 회의 시뮬레이션 완료: {self.total_rounds}라운드, {self.total_actions}개 액션")

        # IPC 대기 모드
        if self.wait_for_commands and not self.shutdown_event.is_set():
            self._set_env_status("alive")
            print("\n대기 명령 모드 진입 (인터뷰 가능)...")
            await self.ipc_handler.process_commands(self.shutdown_event)

        self._set_env_status("stopped")
        print("시뮬레이션 엔진 종료")


# ============================================================
# 엔트리포인트
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="이해관계자 회의 시뮬레이션")
    parser.add_argument("--config", required=True, help="simulation_config.json 경로")
    parser.add_argument("--max-rounds", type=int, default=None, help="최대 라운드 수")
    parser.add_argument("--no-wait", action="store_true", help="완료 후 IPC 대기 안 함")
    args = parser.parse_args()

    simulation = MeetingSimulation(
        config_path=args.config,
        max_rounds_override=args.max_rounds,
        wait_for_commands=not args.no_wait,
    )

    # 시그널 핸들러
    def handle_signal(signum, frame):
        print(f"\n시그널 {signum} 수신, 종료 중...")
        simulation.shutdown_event.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    # 실행
    asyncio.run(simulation.run())


if __name__ == "__main__":
    main()
