"""
OASIS 이해관계자 회의 시뮬레이션 프리셋 스크립트
이 스크립트는 설정 파일의 파라미터를 읽어 회의 시뮬레이션을 실행하며, 전 과정을 자동화합니다.

기능특성:
- OASIS ActionType을 회의 행동으로 매핑 (PROPOSE, SUPPORT, AMEND, QUESTION, ABSTAIN)
- 모든 에이전트가 매 라운드 참석 (시간 기반 필터링 없음)
- 짧은 라운드 구조 (기본 8라운드)
- 완료 시뮬레이션 후 즉시 닫지 않고 대기 명령 모드 진입
- IPC를 통해 Interview 명령 수신 지원
- 단일 Agent 인터뷰와 배치 인터뷰 지원
- 원격 환경 닫기 명령 지원

사용 방식:
    python run_stakeholder_meeting.py --config /path/to/simulation_config.json
    python run_stakeholder_meeting.py --config /path/to/simulation_config.json --no-wait  # 완료즉시닫기
"""

import argparse
import asyncio
import json
import logging
import os
import random
import signal
import sys
import sqlite3
from datetime import datetime
from typing import Dict, Any, List, Optional

# 전역 변수：용 신호 처리
_shutdown_event = None
_cleanup_done = False

# 프로젝트 경로 추가
_scripts_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.abspath(os.path.join(_scripts_dir, '..'))
_project_root = os.path.abspath(os.path.join(_backend_dir, '..'))
sys.path.insert(0, _scripts_dir)
sys.path.insert(0, _backend_dir)

# 프로젝트 루트 디렉토리의 .env 파일 로드 (LLM_API_KEY 등 설정 포함)
from dotenv import load_dotenv
_env_file = os.path.join(_project_root, '.env')
if os.path.exists(_env_file):
    load_dotenv(_env_file)
else:
    _backend_env = os.path.join(_backend_dir, '.env')
    if os.path.exists(_backend_env):
        load_dotenv(_backend_env)


import re


class UnicodeFormatter(logging.Formatter):
    """사용자 정의 형식화기, Unicode 전환 시퀀스를 읽기 문자로 변환"""

    UNICODE_ESCAPE_PATTERN = re.compile(r'\\u([0-9a-fA-F]{4})')

    def format(self, record):
        result = super().format(record)

        def replace_unicode(match):
            try:
                return chr(int(match.group(1), 16))
            except (ValueError, OverflowError):
                return match.group(0)

        return self.UNICODE_ESCAPE_PATTERN.sub(replace_unicode, result)


class MaxTokensWarningFilter(logging.Filter):
    """필터링하여 camel-ai 관련 max_tokens의 경고(우리는 고의로 max_tokens를 설정하지 않음, 모델이 스스로 결정)"""

    def filter(self, record):
        # 필터링하여 포함된 max_tokens 경고의 로그
        if "max_tokens" in record.getMessage() and "Invalid or missing" in record.getMessage():
            return False
        return True


# 모듈 로드 시 즉시 추가 필터기, 보장하여 camel 대 코드 실행 전 생효
logging.getLogger().addFilter(MaxTokensWarningFilter())


def setup_oasis_logging(log_dir: str):
    """설정 OASIS 의 로그, 사용 고정 이름의 로그 파일"""
    os.makedirs(log_dir, exist_ok=True)

    # 정리 구의 로그 파일
    for f in os.listdir(log_dir):
        old_log = os.path.join(log_dir, f)
        if os.path.isfile(old_log) and f.endswith('.log'):
            try:
                os.remove(old_log)
            except OSError:
                pass

    formatter = UnicodeFormatter("%(levelname)s - %(asctime)s - %(name)s - %(message)s")

    loggers_config = {
        "social.agent": os.path.join(log_dir, "social.agent.log"),
        "social.twitter": os.path.join(log_dir, "social.twitter.log"),
        "social.rec": os.path.join(log_dir, "social.rec.log"),
        "oasis.env": os.path.join(log_dir, "oasis.env.log"),
        "table": os.path.join(log_dir, "table.log"),
    }

    for logger_name, log_file in loggers_config.items():
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.DEBUG)
        logger.handlers.clear()
        file_handler = logging.FileHandler(log_file, encoding='utf-8', mode='w')
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        logger.propagate = False


try:
    from camel.models import ModelFactory
    from camel.types import ModelPlatformType
    import oasis
    from oasis import (
        ActionType,
        LLMAction,
        ManualAction,
        generate_twitter_agent_graph
    )
except ImportError as e:
    print(f"오류: 의존성 부족 {e}")
    print("먼저 설치하십시오: pip install oasis-ai camel-ai")
    sys.exit(1)


# OASIS ActionType을 회의 행동으로 매핑
MEETING_ACTION_LABELS = {
    'CREATE_POST': 'PROPOSE',       # 안건/의견 제안
    'LIKE_POST': 'SUPPORT',         # 동의
    'DISLIKE_POST': 'OPPOSE',       # 반대
    'QUOTE_POST': 'AMEND',          # 수정안 제안
    'CREATE_COMMENT': 'QUESTION',   # 질문
    'DO_NOTHING': 'ABSTAIN',        # 보류
}

MEETING_ACTIONS = [
    ActionType.CREATE_POST,    # PROPOSE
    ActionType.LIKE_POST,      # SUPPORT
    ActionType.QUOTE_POST,     # AMEND
    ActionType.CREATE_COMMENT, # QUESTION
    ActionType.DO_NOTHING,     # ABSTAIN
]


# IPC 관련 상수
IPC_COMMANDS_DIR = "ipc_commands"
IPC_RESPONSES_DIR = "ipc_responses"
ENV_STATUS_FILE = "env_status.json"

class CommandType:
    """명령 유형 상수"""
    INTERVIEW = "interview"
    BATCH_INTERVIEW = "batch_interview"
    CLOSE_ENV = "close_env"


class IPCHandler:
    """IPC 명령 처리기"""

    def __init__(self, simulation_dir: str, env, agent_graph):
        self.simulation_dir = simulation_dir
        self.env = env
        self.agent_graph = agent_graph
        self.commands_dir = os.path.join(simulation_dir, IPC_COMMANDS_DIR)
        self.responses_dir = os.path.join(simulation_dir, IPC_RESPONSES_DIR)
        self.status_file = os.path.join(simulation_dir, ENV_STATUS_FILE)
        self._running = True

        # 보장 디렉토리 저장에서
        os.makedirs(self.commands_dir, exist_ok=True)
        os.makedirs(self.responses_dir, exist_ok=True)

    def update_status(self, status: str):
        """업데이트환경상태"""
        with open(self.status_file, 'w', encoding='utf-8') as f:
            json.dump({
                "status": status,
                "timestamp": datetime.now().isoformat()
            }, f, ensure_ascii=False, indent=2)

    def poll_command(self) -> Optional[Dict[str, Any]]:
        """라운드 질문 가져오기 대기 처리 명령"""
        if not os.path.exists(self.commands_dir):
            return None

        # 가져오기 명령 파일 (시간 정렬)
        command_files = []
        for filename in os.listdir(self.commands_dir):
            if filename.endswith('.json'):
                filepath = os.path.join(self.commands_dir, filename)
                command_files.append((filepath, os.path.getmtime(filepath)))

        command_files.sort(key=lambda x: x[1])

        for filepath, _ in command_files:
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                continue

        return None

    def send_response(self, command_id: str, status: str, result: Dict = None, error: str = None):
        """전송응답"""
        response = {
            "command_id": command_id,
            "status": status,
            "result": result,
            "error": error,
            "timestamp": datetime.now().isoformat()
        }

        response_file = os.path.join(self.responses_dir, f"{command_id}.json")
        with open(response_file, 'w', encoding='utf-8') as f:
            json.dump(response, f, ensure_ascii=False, indent=2)

        # 삭제명령파일
        command_file = os.path.join(self.commands_dir, f"{command_id}.json")
        try:
            os.remove(command_file)
        except OSError:
            pass

    async def handle_interview(self, command_id: str, agent_id: int, prompt: str) -> bool:
        """
        처리 단개 Agent 인터뷰 명령

        Returns:
            True는 성공을 나타내고, False는 실패를 나타냄
        """
        try:
            # 가져오기Agent
            agent = self.agent_graph.get_agent(agent_id)

            # 생성Interview액션
            interview_action = ManualAction(
                action_type=ActionType.INTERVIEW,
                action_args={"prompt": prompt}
            )

            # 실행Interview
            actions = {agent: interview_action}
            await self.env.step(actions)

            # 부터데이터베이스가져오기결과
            result = self._get_interview_result(agent_id)

            self.send_response(command_id, "completed", result=result)
            print(f"  Interview완료: agent_id={agent_id}")
            return True

        except Exception as e:
            error_msg = str(e)
            print(f"  Interview실패: agent_id={agent_id}, error={error_msg}")
            self.send_response(command_id, "failed", error=error_msg)
            return False

    async def handle_batch_interview(self, command_id: str, interviews: List[Dict]) -> bool:
        """
        처리 배치 인터뷰 명령

        Args:
            interviews: [{"agent_id": int, "prompt": str}, ...]
        """
        try:
            # 구축 액션 사전
            actions = {}
            agent_prompts = {}  # 기록 각 agent의 prompt

            for interview in interviews:
                agent_id = interview.get("agent_id")
                prompt = interview.get("prompt", "")

                try:
                    agent = self.agent_graph.get_agent(agent_id)
                    actions[agent] = ManualAction(
                        action_type=ActionType.INTERVIEW,
                        action_args={"prompt": prompt}
                    )
                    agent_prompts[agent_id] = prompt
                except Exception as e:
                    print(f"  경고: 가져오기 Agent {agent_id} 실패: {e}")

            if not actions:
                self.send_response(command_id, "failed", error="없으면유효한Agent")
                return False

            # 실행 배치 Interview
            await self.env.step(actions)

            # 가져오기 결과
            results = {}
            for agent_id in agent_prompts.keys():
                result = self._get_interview_result(agent_id)
                results[agent_id] = result

            self.send_response(command_id, "completed", result={
                "interviews_count": len(results),
                "results": results
            })
            print(f"  배치 Interview 완료: {len(results)} 개 Agent")
            return True

        except Exception as e:
            error_msg = str(e)
            print(f"  배치 Interview 실패: {error_msg}")
            self.send_response(command_id, "failed", error=error_msg)
            return False

    def _get_interview_result(self, agent_id: int) -> Dict[str, Any]:
        """부터 데이터베이스 가져오기 최신의 Interview 결과"""
        db_path = os.path.join(self.simulation_dir, "twitter_simulation.db")

        result = {
            "agent_id": agent_id,
            "response": None,
            "timestamp": None
        }

        if not os.path.exists(db_path):
            return result

        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()

            # 조회 최신의 Interview 기록
            cursor.execute("""
                SELECT user_id, info, created_at
                FROM trace
                WHERE action = ? AND user_id = ?
                ORDER BY created_at DESC
                LIMIT 1
            """, (ActionType.INTERVIEW.value, agent_id))

            row = cursor.fetchone()
            if row:
                user_id, info_json, created_at = row
                try:
                    info = json.loads(info_json) if info_json else {}
                    result["response"] = info.get("response", info)
                    result["timestamp"] = created_at
                except json.JSONDecodeError:
                    result["response"] = info_json

            conn.close()

        except Exception as e:
            print(f"  읽기 Interview 결과 실패: {e}")

        return result

    async def process_commands(self) -> bool:
        """
        처리 소 대기 처리 명령

        Returns:
            True는 계속 실행을 나타내고, False는 종료해야 함을 나타냄
        """
        command = self.poll_command()
        if not command:
            return True

        command_id = command.get("command_id")
        command_type = command.get("command_type")
        args = command.get("args", {})

        print(f"\n수IPC명령: {command_type}, id={command_id}")

        if command_type == CommandType.INTERVIEW:
            await self.handle_interview(
                command_id,
                args.get("agent_id", 0),
                args.get("prompt", "")
            )
            return True

        elif command_type == CommandType.BATCH_INTERVIEW:
            await self.handle_batch_interview(
                command_id,
                args.get("interviews", [])
            )
            return True

        elif command_type == CommandType.CLOSE_ENV:
            print("수닫기환경명령")
            self.send_response(command_id, "completed", result={"message": "환경 즉 닫기"})
            return False

        else:
            self.send_response(command_id, "failed", error=f"알 수 없음명령유형: {command_type}")
            return True


class StakeholderMeetingRunner:
    """이해관계자 회의 시뮬레이션 실행기"""

    # 회의 사용 가능한 액션 (OASIS ActionType을 회의 행동으로 재사용)
    AVAILABLE_ACTIONS = MEETING_ACTIONS

    def __init__(self, config_path: str, wait_for_commands: bool = True):
        """
        초기화 시뮬레이션 실행기

        Args:
            config_path: 설정파일경로 (simulation_config.json)
            wait_for_commands: 시뮬레이션 완료 후 대기 명령 여부 (기본값 True)
        """
        self.config_path = config_path
        self.config = self._load_config()
        self.simulation_dir = os.path.dirname(config_path)
        self.wait_for_commands = wait_for_commands
        self.env = None
        self.agent_graph = None
        self.ipc_handler = None

    def _load_config(self) -> Dict[str, Any]:
        """로드설정파일"""
        with open(self.config_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _get_profile_path(self) -> str:
        """가져오기Profile파일경로 (OASIS Twitter사용CSV형식)"""
        return os.path.join(self.simulation_dir, "twitter_profiles.csv")

    def _get_db_path(self) -> str:
        """가져오기데이터베이스경로"""
        return os.path.join(self.simulation_dir, "twitter_simulation.db")

    def _create_model(self):
        """
        생성 LLM 모델

        통합사용프로젝트 루트 디렉토리 .env 파일의 설정(우선순위 가장 높음):
        - LLM_API_KEY: API키
        - LLM_BASE_URL: API기본URL
        - LLM_MODEL_NAME: 모델이름
        """
        # 우선적으로 .env에서 설정 읽기
        llm_api_key = os.environ.get("LLM_API_KEY", "")
        llm_base_url = os.environ.get("LLM_BASE_URL", "")
        llm_model = os.environ.get("LLM_MODEL_NAME", "")

        # 만약 .env에 없으면, config를 사용하여 대체
        if not llm_model:
            llm_model = self.config.get("llm_model", "gpt-4o-mini")

        # camel-ai에 필요한 환경변수 설정
        # LuxiaCloud 프록시 감지: luxiacloud URL이면 로컬 프록시 사용
        proxy_url = os.environ.get("LLM_PROXY_URL", "")
        is_luxia = 'luxiacloud' in (llm_base_url or '')

        if llm_api_key:
            if is_luxia and not proxy_url:
                # LuxiaCloud는 표준 OpenAI SDK와 호환되지 않으므로 프록시 필요
                proxy_url = "http://localhost:8788/v1"
                os.environ["OPENAI_API_KEY"] = "proxy-key"
                print(f"[LuxiaCloud 감지] 프록시 사용: {proxy_url}")
            else:
                os.environ["OPENAI_API_KEY"] = llm_api_key

        if not os.environ.get("OPENAI_API_KEY"):
            raise ValueError("API Key 설정이 누락되었습니다. 프로젝트 루트 디렉토리 .env 파일에서 LLM_API_KEY를 설정해 주세요.")

        if proxy_url:
            os.environ["OPENAI_API_BASE_URL"] = proxy_url
        elif llm_base_url:
            os.environ["OPENAI_API_BASE_URL"] = llm_base_url

        effective_url = proxy_url or llm_base_url or '기본값'
        print(f"LLM설정: model={llm_model}, base_url={effective_url[:50]}...")

        return ModelFactory.create(
            model_platform=ModelPlatformType.OPENAI,
            model_type=llm_model,
        )

    def _get_active_agents_for_round(self, env, current_hour, round_num):
        """회의 모드: 모든 에이전트가 매 라운드 참석"""
        active_agents = []
        for agent_id in range(len(env.agent_graph.get_agents())):
            try:
                agent = env.agent_graph.get_agent(agent_id)
                active_agents.append((agent_id, agent))
            except Exception:
                pass
        return active_agents

    async def run(self, max_rounds: int = None):
        """실행 이해관계자 회의 시뮬레이션

        Args:
            max_rounds: 최대 시뮬레이션 라운드 수 (선택, 너무 긴 시뮬레이션을 잘라내기 위해)
        """
        print("=" * 60)
        print("OASIS 이해관계자 회의 시뮬레이션")
        print(f"설정파일: {self.config_path}")
        print(f"시뮬레이션ID: {self.config.get('simulation_id', 'unknown')}")
        print(f"대기 명령 모드: {'활성화' if self.wait_for_commands else '비활성화'}")
        print("=" * 60)

        # 회의 라운드 수 결정: config의 max_rounds 또는 기본값 8
        meeting_config = self.config.get("meeting_config", {})
        total_rounds = meeting_config.get("max_rounds",
                       self.config.get("max_rounds", 8))

        # 만약 최대 라운드 수를 지정하면, 잘라내기
        if max_rounds is not None and max_rounds > 0:
            original_rounds = total_rounds
            total_rounds = min(total_rounds, max_rounds)
            if total_rounds < original_rounds:
                print(f"\n라운드 수가 이미 잘라졌습니다: {original_rounds} -> {total_rounds} (max_rounds={max_rounds})")

        print(f"\n회의 시뮬레이션 파라미터:")
        print(f"  - 총 라운드 수: {total_rounds}")
        if max_rounds:
            print(f"  - 최대 라운드 수 제한: {max_rounds}")
        print(f"  - Agent수량: {len(self.config.get('agent_configs', []))}")
        print(f"  - 회의 행동 매핑:")
        for oasis_action, meeting_label in MEETING_ACTION_LABELS.items():
            print(f"      {oasis_action} -> {meeting_label}")

        # 모델 생성
        print("\nLLM 모델 초기화 중...")
        model = self._create_model()

        # Agent 그래프 로드
        print("로드Agent Profile...")
        profile_path = self._get_profile_path()
        if not os.path.exists(profile_path):
            print(f"오류: Profile파일이 존재하지 않음: {profile_path}")
            return

        self.agent_graph = await generate_twitter_agent_graph(
            profile_path=profile_path,
            model=model,
            available_actions=self.AVAILABLE_ACTIONS,
        )

        # 데이터베이스경로
        db_path = self._get_db_path()
        if os.path.exists(db_path):
            os.remove(db_path)
            print(f"이미 삭제된 구 데이터베이스: {db_path}")

        # 생성환경
        print("생성OASIS환경...")
        self.env = oasis.make(
            agent_graph=self.agent_graph,
            platform=oasis.DefaultPlatformType.TWITTER,
            database_path=db_path,
            semaphore=30,  # 최대 동시 LLM 요청 수 제한, API 과부하 방지
        )

        await self.env.reset()
        print("환경초기화완료\n")

        # IPC 처리기 초기화
        self.ipc_handler = IPCHandler(self.simulation_dir, self.env, self.agent_graph)
        self.ipc_handler.update_status("running")

        # 초기 이벤트 실행
        event_config = self.config.get("event_config", {})
        initial_posts = event_config.get("initial_posts", [])

        if initial_posts:
            print(f"초기 안건 제출 중 ({len(initial_posts)}개 초기 안건)...")
            initial_actions = {}
            for post in initial_posts:
                agent_id = post.get("poster_agent_id", 0)
                content = post.get("content", "")
                try:
                    agent = self.env.agent_graph.get_agent(agent_id)
                    initial_actions[agent] = ManualAction(
                        action_type=ActionType.CREATE_POST,
                        action_args={"content": content}
                    )
                except Exception as e:
                    print(f"  경고: Agent {agent_id}의 초기 안건 생성 실패: {e}")

            if initial_actions:
                await self.env.step(initial_actions)
                print(f"  이미 제출된 {len(initial_actions)} 개 초기 안건")

        # 메인 시뮬레이션 루프
        print("\n회의 시뮬레이션 시작...")
        start_time = datetime.now()

        for round_num in range(total_rounds):
            # 회의 모드: 모든 에이전트가 매 라운드 참석
            active_agents = self._get_active_agents_for_round(
                self.env, 0, round_num
            )

            if not active_agents:
                continue

            # 구축액션
            actions = {
                agent: LLMAction()
                for _, agent in active_agents
            }

            # 실행액션
            await self.env.step(actions)

            # 진행률 출력
            elapsed = (datetime.now() - start_time).total_seconds()
            progress = (round_num + 1) / total_rounds * 100
            print(f"  [Round {round_num + 1}/{total_rounds}] ({progress:.1f}%) "
                  f"- {len(active_agents)} agents active "
                  f"- elapsed: {elapsed:.1f}s")

        total_elapsed = (datetime.now() - start_time).total_seconds()
        print(f"\n회의 시뮬레이션 완료!")
        print(f"  - 총 소요 시간: {total_elapsed:.1f}초")
        print(f"  - 데이터베이스: {db_path}")

        # 명령 대기 모드 진입 여부
        if self.wait_for_commands:
            print("\n" + "=" * 60)
            print("명령 대기 모드 - 환경 유지 실행")
            print("지원하는 명령: interview, batch_interview, close_env")
            print("=" * 60)

            self.ipc_handler.update_status("alive")

            # 대기 명령 루프 (사용 전체 _shutdown_event)
            try:
                while not _shutdown_event.is_set():
                    should_continue = await self.ipc_handler.process_commands()
                    if not should_continue:
                        break
                    try:
                        await asyncio.wait_for(_shutdown_event.wait(), timeout=0.5)
                        break  # 수신 종료 신호
                    except asyncio.TimeoutError:
                        pass
            except KeyboardInterrupt:
                print("\n수신 중단 신호")
            except asyncio.CancelledError:
                print("\n작업취소")
            except Exception as e:
                print(f"\n명령 처리 중 오류: {e}")

            print("\n닫기환경...")

        # 닫기환경
        self.ipc_handler.update_status("stopped")
        await self.env.close()

        print("환경이미닫기")
        print("=" * 60)


async def main():
    parser = argparse.ArgumentParser(description='OASIS 이해관계자 회의 시뮬레이션')
    parser.add_argument(
        '--config',
        type=str,
        required=True,
        help='설정파일경로 (simulation_config.json)'
    )
    parser.add_argument(
        '--max-rounds',
        type=int,
        default=None,
        help='최대 시뮬레이션 라운드 수 (선택, 너무 긴 시뮬레이션을 잘라내기 위해)'
    )
    parser.add_argument(
        '--no-wait',
        action='store_true',
        default=False,
        help='시뮬레이션 완료 즉시 환경 닫기, 명령 대기 모드에 진입하지 않음'
    )

    args = parser.parse_args()

    # main 함수 시작 시 생성 shutdown 이벤트
    global _shutdown_event
    _shutdown_event = asyncio.Event()

    if not os.path.exists(args.config):
        print(f"오류: 설정파일이 존재하지 않음: {args.config}")
        sys.exit(1)

    # 초기화 로그 설정 (사용 고정 파일명, 구 로그 정리)
    simulation_dir = os.path.dirname(args.config) or "."
    setup_oasis_logging(os.path.join(simulation_dir, "log"))

    runner = StakeholderMeetingRunner(
        config_path=args.config,
        wait_for_commands=not args.no_wait
    )
    await runner.run(max_rounds=args.max_rounds)


def setup_signal_handlers():
    """
    신호 처리기를 설정하여 수신 SIGTERM/SIGINT 시 올바르게 종료되도록 보장
    프로그램이 정상적으로 자원을 정리 (데이터베이스, 환경 등 닫기)
    """
    def signal_handler(signum, frame):
        global _cleanup_done
        sig_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
        print(f"\n수신 {sig_name} 신호, 진행 중: 종료...")
        if not _cleanup_done:
            _cleanup_done = True
            if _shutdown_event:
                _shutdown_event.set()
        else:
            # 반복 수신 신호일 때 강제 종료
            print("강제 종료...")
            sys.exit(1)

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)


if __name__ == "__main__":
    setup_signal_handlers()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n프로그램 중단")
    except SystemExit:
        pass
    finally:
        print("회의 시뮬레이션 프로세스 이미 종료")
