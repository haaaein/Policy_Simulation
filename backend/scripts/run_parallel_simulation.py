"""
OASIS 듀얼플랫폼병렬시뮬레이션프리셋 스크립트
동시에 실행 Twitter와 Reddit 시뮬레이션, 읽기 동일한 설정 파일

기능특성:
- 듀얼플랫폼（Twitter + Reddit）병렬시뮬레이션
- 완료 시뮬레이션 후 즉시 하지 않음 닫기 환경, 진입 대기 명령 모드
- 을 통해 지원IPC수신Interview명령
- 지원 단개 Agent 인터뷰와 배치 인터뷰
- 지원 원격 닫기 환경 명령

사용 방식:
    python run_parallel_simulation.py --config simulation_config.json
    python run_parallel_simulation.py --config simulation_config.json --no-wait  # 완료즉시닫기
    python run_parallel_simulation.py --config simulation_config.json --twitter-only
    python run_parallel_simulation.py --config simulation_config.json --reddit-only

로그구조:
    sim_xxx/
    ├── twitter/
    │   └── actions.jsonl    # Twitter 플랫폼액션로그
    ├── reddit/
    │   └── actions.jsonl    # Reddit 플랫폼액션로그
    ├── simulation.log       # 메인시뮬레이션프로세스로그
    └── run_state.json       # 실행 상태（API 조회용）
"""

# ============================================================
# 해결 Windows 인코딩 문제：모든 import 전에 설정 UTF-8 인코딩
# 이로 수정 OASIS 제3자 라이브러리 읽기 파일 시 미지정 인코딩의 문제
# ============================================================
import sys
import os

if sys.platform == 'win32':
    # 설정 Python 기본값 I/O 인코딩으로 UTF-8
    # 이 영향 소 미지정 인코딩의 open() 호출
    os.environ.setdefault('PYTHONUTF8', '1')
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
    
    # 재설정 표준 출력 스트림을 UTF-8（해결 콘솔 중문 난코드）
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    
    # 강제 설정 기본값 인코딩（영향 open() 함수의 기본값 인코딩）
    # 주의：이 필요에서 Python 시작 시 설정, 실행 시 설정 안 생효
    # 소하여 우리도 monkey-patch 내장 open 함수
    import builtins
    _original_open = builtins.open
    
    def _utf8_open(file, mode='r', buffering=-1, encoding=None, errors=None, 
                   newline=None, closefd=True, opener=None):
        """
        포장 open() 함수, 텍스트 모드 기본값 사용 UTF-8 인코딩
        이하여 수정 제3자 라이브러리（예: OASIS）읽기 파일 시 미지정 인코딩의 문제
        """
        # 단지 텍스트 모드(비이진)이고 미지정 인코딩의 경우 설정 기본값 인코딩
        if encoding is None and 'b' not in mode:
            encoding = 'utf-8'
        return _original_open(file, mode, buffering, encoding, errors, 
                              newline, closefd, opener)
    
    builtins.open = _utf8_open

import argparse
import asyncio
import json
import logging
import multiprocessing
import random
import signal
import sqlite3
import warnings
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple


# 전역 변수：용 신호 처리
_shutdown_event = None
_cleanup_done = False

# 추가 backend 디렉토리경로
# 스크립트 고정 위치는 backend/scripts/ 디렉토리
_scripts_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.abspath(os.path.join(_scripts_dir, '..'))
_project_root = os.path.abspath(os.path.join(_backend_dir, '..'))
sys.path.insert(0, _scripts_dir)
sys.path.insert(0, _backend_dir)

# 프로젝트 루트 로드디렉토리의 .env 파일（포함 LLM_API_KEY 등설정）
from dotenv import load_dotenv
_env_file = os.path.join(_project_root, '.env')
if os.path.exists(_env_file):
    load_dotenv(_env_file)
    print(f"이미로드환경설정: {_env_file}")
else:
    # 시도로드 backend/.env
    _backend_env = os.path.join(_backend_dir, '.env')
    if os.path.exists(_backend_env):
        load_dotenv(_backend_env)
        print(f"이미로드환경설정: {_backend_env}")


class MaxTokensWarningFilter(logging.Filter):
    """필터掉 camel-ai 관련 max_tokens 의 경고(우리는故意 안 설정 max_tokens, 모델이 자체 결정)"""
    
    def filter(self, record):
        # 필터掉 포함 max_tokens 경고의 로그
        if "max_tokens" in record.getMessage() and "Invalid or missing" in record.getMessage():
            return False
        return True


# 에서 모듈 로드 시 즉시 추가 필터러, 보장하여 camel 대코드 실행 전 생효
logging.getLogger().addFilter(MaxTokensWarningFilter())


def disable_oasis_logging():
    """
    비활성화 OASIS 라이브러리의 상세 로그 출력
    OASIS 의 로그 너무冗余(기록 매개 agent 의 관찰과 액션), 우리는 사용 자기의 action_logger
    """
    # 비활성화 OASIS 의 소 로그기
    oasis_loggers = [
        "social.agent",
        "social.twitter", 
        "social.rec",
        "oasis.env",
        "table",
    ]
    
    for logger_name in oasis_loggers:
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.CRITICAL)  # 단지 기록 심각 오류
        logger.handlers.clear()
        logger.propagate = False


def init_logging_for_simulation(simulation_dir: str):
    """
    초기화시뮬레이션의로그설정
    
    Args:
        simulation_dir: 시뮬레이션디렉토리경로
    """
    # 비활성화 OASIS 의 상세 로그
    disable_oasis_logging()
    
    # 정리 구식의 log 디렉토리(만약存에서)
    old_log_dir = os.path.join(simulation_dir, "log")
    if os.path.exists(old_log_dir):
        import shutil
        shutil.rmtree(old_log_dir, ignore_errors=True)


from action_logger import SimulationLogManager, PlatformActionLogger

try:
    from camel.models import ModelFactory
    from camel.types import ModelPlatformType
    import oasis
    from oasis import (
        ActionType,
        LLMAction,
        ManualAction,
        generate_twitter_agent_graph,
        generate_reddit_agent_graph
    )
except ImportError as e:
    print(f"오류: 缺적은依赖 {e}")
    print("먼저 설치하세요: pip install oasis-ai camel-ai")
    sys.exit(1)


# Twitter 사용 가능한 액션（안 포함 INTERVIEW, INTERVIEW 단지 통 ManualAction 수동 트리거）
TWITTER_ACTIONS = [
    ActionType.CREATE_POST,
    ActionType.LIKE_POST,
    ActionType.REPOST,
    ActionType.FOLLOW,
    ActionType.DO_NOTHING,
    ActionType.QUOTE_POST,
]

# Reddit 사용 가능한 액션（안 포함 INTERVIEW, INTERVIEW 단지 통 ManualAction 수동 트리거）
REDDIT_ACTIONS = [
    ActionType.LIKE_POST,
    ActionType.DISLIKE_POST,
    ActionType.CREATE_POST,
    ActionType.CREATE_COMMENT,
    ActionType.LIKE_COMMENT,
    ActionType.DISLIKE_COMMENT,
    ActionType.SEARCH_POSTS,
    ActionType.SEARCH_USER,
    ActionType.TREND,
    ActionType.REFRESH,
    ActionType.DO_NOTHING,
    ActionType.FOLLOW,
    ActionType.MUTE,
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


class ParallelIPCHandler:
    """
    듀얼 플랫폼 IPC 명령 처리기
    
    관리 두 개 플랫폼의 환경, 처리 Interview 명령
    """
    
    def __init__(
        self,
        simulation_dir: str,
        twitter_env=None,
        twitter_agent_graph=None,
        reddit_env=None,
        reddit_agent_graph=None
    ):
        self.simulation_dir = simulation_dir
        self.twitter_env = twitter_env
        self.twitter_agent_graph = twitter_agent_graph
        self.reddit_env = reddit_env
        self.reddit_agent_graph = reddit_agent_graph
        
        self.commands_dir = os.path.join(simulation_dir, IPC_COMMANDS_DIR)
        self.responses_dir = os.path.join(simulation_dir, IPC_RESPONSES_DIR)
        self.status_file = os.path.join(simulation_dir, ENV_STATUS_FILE)
        
        # 보장 디렉토리存에서
        os.makedirs(self.commands_dir, exist_ok=True)
        os.makedirs(self.responses_dir, exist_ok=True)
    
    def update_status(self, status: str):
        """업데이트환경상태"""
        with open(self.status_file, 'w', encoding='utf-8') as f:
            json.dump({
                "status": status,
                "twitter_available": self.twitter_env is not None,
                "reddit_available": self.reddit_env is not None,
                "timestamp": datetime.now().isoformat()
            }, f, ensure_ascii=False, indent=2)
    
    def poll_command(self) -> Optional[Dict[str, Any]]:
        """라운드 질문 가져오기 대기 처리 명령"""
        if not os.path.exists(self.commands_dir):
            return None
        
        # 가져오기 명령 파일(기준으로시간 정렬)
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
    
    def _get_env_and_graph(self, platform: str):
        """
        가져오기 지정 플랫폼의 환경와 agent_graph
        
        Args:
            platform: 플랫폼이름 ("twitter" 또는 "reddit")
            
        Returns:
            (env, agent_graph, platform_name) 또는 (None, None, None)
        """
        if platform == "twitter" and self.twitter_env:
            return self.twitter_env, self.twitter_agent_graph, "twitter"
        elif platform == "reddit" and self.reddit_env:
            return self.reddit_env, self.reddit_agent_graph, "reddit"
        else:
            return None, None, None
    
    async def _interview_single_platform(self, agent_id: int, prompt: str, platform: str) -> Dict[str, Any]:
        """
        에서 단일 플랫폼 상 실행 Interview
        
        Returns:
            포함 결과의 딕셔너리, 또는 포함 error의 딕셔너리
        """
        env, agent_graph, actual_platform = self._get_env_and_graph(platform)
        
        if not env or not agent_graph:
            return {"platform": platform, "error": f"{platform} 플랫폼 안 사용"}
        
        try:
            agent = agent_graph.get_agent(agent_id)
            interview_action = ManualAction(
                action_type=ActionType.INTERVIEW,
                action_args={"prompt": prompt}
            )
            actions = {agent: interview_action}
            await env.step(actions)
            
            result = self._get_interview_result(agent_id, actual_platform)
            result["platform"] = actual_platform
            return result
            
        except Exception as e:
            return {"platform": platform, "error": str(e)}
    
    async def handle_interview(self, command_id: str, agent_id: int, prompt: str, platform: str = None) -> bool:
        """
        처리 단일 Agent 인터뷰 명령
        
        Args:
            command_id: 명령ID
            agent_id: Agent ID
            prompt: 인터뷰 질문
            platform: 지정 플랫폼 (선택)
                - "twitter": 단지 인터뷰 Twitter 플랫폼
                - "reddit": 단지 인터뷰 Reddit 플랫폼
                - None/안 지정: 동시에 인터뷰 두 개 플랫폼, 돌아가기 통합 결과
            
        Returns:
            True는 성공을 나타내고, False는 실패를 나타냅니다.
        """
        # 만약 지정 플랫폼, 단지 인터뷰 해당 플랫폼
        if platform in ("twitter", "reddit"):
            result = await self._interview_single_platform(agent_id, prompt, platform)
            
            if "error" in result:
                self.send_response(command_id, "failed", error=result["error"])
                print(f"  Interview실패: agent_id={agent_id}, platform={platform}, error={result['error']}")
                return False
            else:
                self.send_response(command_id, "completed", result=result)
                print(f"  Interview완료: agent_id={agent_id}, platform={platform}")
                return True
        
        # 미지정 플랫폼: 동시에 인터뷰 두 개 플랫폼
        if not self.twitter_env and not self.reddit_env:
            self.send_response(command_id, "failed", error="없으면 사용의 시뮬레이션 환경")
            return False
        
        results = {
            "agent_id": agent_id,
            "prompt": prompt,
            "platforms": {}
        }
        success_count = 0
        
        # 병렬 인터뷰 두 개 플랫폼
        tasks = []
        platforms_to_interview = []
        
        if self.twitter_env:
            tasks.append(self._interview_single_platform(agent_id, prompt, "twitter"))
            platforms_to_interview.append("twitter")
        
        if self.reddit_env:
            tasks.append(self._interview_single_platform(agent_id, prompt, "reddit"))
            platforms_to_interview.append("reddit")
        
        # 병렬실행
        platform_results = await asyncio.gather(*tasks)
        
        for platform_name, platform_result in zip(platforms_to_interview, platform_results):
            results["platforms"][platform_name] = platform_result
            if "error" not in platform_result:
                success_count += 1
        
        if success_count > 0:
            self.send_response(command_id, "completed", result=results)
            print(f"  Interview완료: agent_id={agent_id}, 성공플랫폼수={success_count}/{len(platforms_to_interview)}")
            return True
        else:
            errors = [f"{p}: {r.get('error', '알 수 없음오류')}" for p, r in results["platforms"].items()]
            self.send_response(command_id, "failed", error="; ".join(errors))
            print(f"  Interview 실패: agent_id={agent_id}, 모든 플랫폼 다 실패")
            return False
    
    async def handle_batch_interview(self, command_id: str, interviews: List[Dict], platform: str = None) -> bool:
        """
        처리 배치 인터뷰 명령
        
        Args:
            command_id: 명령ID
            interviews: [{"agent_id": int, "prompt": str, "platform": str(optional)}, ...]
            platform: 기본값 플랫폼 (각 인터뷰 항목 덮어쓰기)
                - "twitter": 단지 인터뷰 Twitter 플랫폼
                - "reddit": 단지 인터뷰 Reddit 플랫폼
                - None/안 지정: 각 Agent 동시에 인터뷰 두 개 플랫폼
        """
        # 플랫폼 별로 그룹화
        twitter_interviews = []
        reddit_interviews = []
        both_platforms_interviews = []  # 동시에 인터뷰 두 개 플랫폼의
        
        for interview in interviews:
            item_platform = interview.get("platform", platform)
            if item_platform == "twitter":
                twitter_interviews.append(interview)
            elif item_platform == "reddit":
                reddit_interviews.append(interview)
            else:
                # 미지정 플랫폼: 두 개 플랫폼 모두 인터뷰
                both_platforms_interviews.append(interview)
        
        # both_platforms_interviews 분할 두 개 플랫폼
        if both_platforms_interviews:
            if self.twitter_env:
                twitter_interviews.extend(both_platforms_interviews)
            if self.reddit_env:
                reddit_interviews.extend(both_platforms_interviews)
        
        results = {}
        
        # 처리 Twitter 플랫폼의 인터뷰
        if twitter_interviews and self.twitter_env:
            try:
                twitter_actions = {}
                for interview in twitter_interviews:
                    agent_id = interview.get("agent_id")
                    prompt = interview.get("prompt", "")
                    try:
                        agent = self.twitter_agent_graph.get_agent(agent_id)
                        twitter_actions[agent] = ManualAction(
                            action_type=ActionType.INTERVIEW,
                            action_args={"prompt": prompt}
                        )
                    except Exception as e:
                        print(f"  경고: 가져오기 불가능 Twitter Agent {agent_id}: {e}")
                
                if twitter_actions:
                    await self.twitter_env.step(twitter_actions)
                    
                    for interview in twitter_interviews:
                        agent_id = interview.get("agent_id")
                        result = self._get_interview_result(agent_id, "twitter")
                        result["platform"] = "twitter"
                        results[f"twitter_{agent_id}"] = result
            except Exception as e:
                print(f"  Twitter 배치 Interview 실패: {e}")
        
        # 처리 Reddit 플랫폼의 인터뷰
        if reddit_interviews and self.reddit_env:
            try:
                reddit_actions = {}
                for interview in reddit_interviews:
                    agent_id = interview.get("agent_id")
                    prompt = interview.get("prompt", "")
                    try:
                        agent = self.reddit_agent_graph.get_agent(agent_id)
                        reddit_actions[agent] = ManualAction(
                            action_type=ActionType.INTERVIEW,
                            action_args={"prompt": prompt}
                        )
                    except Exception as e:
                        print(f"  경고: 가져오기 불가능 Reddit Agent {agent_id}: {e}")
                
                if reddit_actions:
                    await self.reddit_env.step(reddit_actions)
                    
                    for interview in reddit_interviews:
                        agent_id = interview.get("agent_id")
                        result = self._get_interview_result(agent_id, "reddit")
                        result["platform"] = "reddit"
                        results[f"reddit_{agent_id}"] = result
            except Exception as e:
                print(f"  Reddit 배치 Interview 실패: {e}")
        
        if results:
            self.send_response(command_id, "completed", result={
                "interviews_count": len(results),
                "results": results
            })
            print(f"  배치 Interview 완료: {len(results)} 개 Agent")
            return True
        else:
            self.send_response(command_id, "failed", error="없으면 성공의 인터뷰")
            return False
    
    def _get_interview_result(self, agent_id: int, platform: str) -> Dict[str, Any]:
        """부터 데이터베이스 가져오기 최신의 Interview 결과"""
        db_path = os.path.join(self.simulation_dir, f"{platform}_simulation.db")
        
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
        처리 소待 처리 명령
        
        Returns:
            True는 계속 실행을 나타내고, False는 종료해야 함을 나타냅니다.
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
                args.get("prompt", ""),
                args.get("platform")
            )
            return True
            
        elif command_type == CommandType.BATCH_INTERVIEW:
            await self.handle_batch_interview(
                command_id,
                args.get("interviews", []),
                args.get("platform")
            )
            return True
            
        elif command_type == CommandType.CLOSE_ENV:
            print("수닫기환경명령")
            self.send_response(command_id, "completed", result={"message": "환경 즉 닫기"})
            return False
        
        else:
            self.send_response(command_id, "failed", error=f"알 수 없음명령유형: {command_type}")
            return True


def load_config(config_path: str) -> Dict[str, Any]:
    """로드설정파일"""
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)


# 필요 필터掉의 비핵심 액션 유형(이러한 액션 분석 가치가 낮음)
FILTERED_ACTIONS = {'refresh', 'sign_up'}

# 액션 유형 매핑 표 (데이터베이스의 이름 -> 표준 이름)
ACTION_TYPE_MAP = {
    'create_post': 'CREATE_POST',
    'like_post': 'LIKE_POST',
    'dislike_post': 'DISLIKE_POST',
    'repost': 'REPOST',
    'quote_post': 'QUOTE_POST',
    'follow': 'FOLLOW',
    'mute': 'MUTE',
    'create_comment': 'CREATE_COMMENT',
    'like_comment': 'LIKE_COMMENT',
    'dislike_comment': 'DISLIKE_COMMENT',
    'search_posts': 'SEARCH_POSTS',
    'search_user': 'SEARCH_USER',
    'trend': 'TREND',
    'do_nothing': 'DO_NOTHING',
    'interview': 'INTERVIEW',
}


def get_agent_names_from_config(config: Dict[str, Any]) -> Dict[int, str]:
    """
    부터 simulation_config 중 가져오기 agent_id -> entity_name 의 매핑
    
    이렇게 해서 actions.jsonl 중 표시 실제의 개체 이름, 대신 "Agent_0" 이렇게의 대호
    
    Args:
        config: simulation_config.json 의내용
        
    Returns:
        agent_id -> entity_name 의 매핑 딕셔너리
    """
    agent_names = {}
    agent_configs = config.get("agent_configs", [])
    
    for agent_config in agent_configs:
        agent_id = agent_config.get("agent_id")
        entity_name = agent_config.get("entity_name", f"Agent_{agent_id}")
        if agent_id is not None:
            agent_names[agent_id] = entity_name
    
    return agent_names


def fetch_new_actions_from_db(
    db_path: str,
    last_rowid: int,
    agent_names: Dict[int, str]
) -> Tuple[List[Dict[str, Any]], int]:
    """
    부터 데이터베이스 중 가져오기 새로운 액션 기록, 그리고 보충 완전한의 컨텍스트 정보
    
    Args:
        db_path: 데이터베이스파일경로
        last_rowid: 마지막 읽기의 최대 rowid 값 (사용 rowid 대신 created_at, 인 로안 동일 플랫폼의 created_at 형식 안 동일)
        agent_names: agent_id -> agent_name 매핑
        
    Returns:
        (actions_list, new_last_rowid)
        - actions_list: 액션 목록, 각 요소 포함 agent_id, agent_name, action_type, action_args (포함 컨텍스트 정보)
        - new_last_rowid: 새로운 최대 rowid 값
    """
    actions = []
    new_last_rowid = last_rowid
    
    if not os.path.exists(db_path):
        return actions, new_last_rowid
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 사용 rowid 추적 이미 처리의 기록 (rowid SQLite 의 내장 자증 필드)
        # 이렇게 해서 피하기 created_at 형식 차이 문제 (Twitter 용 정수, Reddit 용 날짜시간 문자열)
        cursor.execute("""
            SELECT rowid, user_id, action, info
            FROM trace
            WHERE rowid > ?
            ORDER BY rowid ASC
        """, (last_rowid,))
        
        for rowid, user_id, action, info_json in cursor.fetchall():
            # 업데이트최대 rowid
            new_last_rowid = rowid
            
            # 필터 비핵심 액션
            if action in FILTERED_ACTIONS:
                continue
            
            # 파싱액션파라미터
            try:
                action_args = json.loads(info_json) if info_json else {}
            except json.JSONDecodeError:
                action_args = {}
            
            # 정리 action_args, 단지 보존 핵심 필드 (보존 완전 내용, 안 잘림)
            simplified_args = {}
            if 'content' in action_args:
                simplified_args['content'] = action_args['content']
            if 'post_id' in action_args:
                simplified_args['post_id'] = action_args['post_id']
            if 'comment_id' in action_args:
                simplified_args['comment_id'] = action_args['comment_id']
            if 'quoted_id' in action_args:
                simplified_args['quoted_id'] = action_args['quoted_id']
            if 'new_post_id' in action_args:
                simplified_args['new_post_id'] = action_args['new_post_id']
            if 'follow_id' in action_args:
                simplified_args['follow_id'] = action_args['follow_id']
            if 'query' in action_args:
                simplified_args['query'] = action_args['query']
            if 'like_id' in action_args:
                simplified_args['like_id'] = action_args['like_id']
            if 'dislike_id' in action_args:
                simplified_args['dislike_id'] = action_args['dislike_id']
            
            # 변환 액션 유형 이름
            action_type = ACTION_TYPE_MAP.get(action, action.upper())
            
            # 보충 컨텍스트 정보 (게시물 내용, 사용자 이름 등)
            _enrich_action_context(cursor, action_type, simplified_args, agent_names)
            
            actions.append({
                'agent_id': user_id,
                'agent_name': agent_names.get(user_id, f'Agent_{user_id}'),
                'action_type': action_type,
                'action_args': simplified_args,
            })
        
        conn.close()
    except Exception as e:
        print(f"읽기 데이터베이스 액션 실패: {e}")
    
    return actions, new_last_rowid


def _enrich_action_context(
    cursor,
    action_type: str,
    action_args: Dict[str, Any],
    agent_names: Dict[int, str]
) -> None:
    """
    로 액션 보충 컨텍스트 정보 (게시물 내용, 사용자 이름 등)
    
    Args:
        cursor: 데이터베이스 커서
        action_type: 액션유형
        action_args: 액션파라미터（수정）
        agent_names: agent_id -> agent_name 매핑
    """
    try:
        # 좋아요/싫어요 게시물: 보충 게시물 내용와 저자
        if action_type in ('LIKE_POST', 'DISLIKE_POST'):
            post_id = action_args.get('post_id')
            if post_id:
                post_info = _get_post_info(cursor, post_id, agent_names)
                if post_info:
                    action_args['post_content'] = post_info.get('content', '')
                    action_args['post_author_name'] = post_info.get('author_name', '')
        
        # 게시물 리포스트: 보충 원 게시물 내용와 저자
        elif action_type == 'REPOST':
            new_post_id = action_args.get('new_post_id')
            if new_post_id:
                # 게시물 리포스트의 original_post_id 지시 원 게시물
                cursor.execute("""
                    SELECT original_post_id FROM post WHERE post_id = ?
                """, (new_post_id,))
                row = cursor.fetchone()
                if row and row[0]:
                    original_post_id = row[0]
                    original_info = _get_post_info(cursor, original_post_id, agent_names)
                    if original_info:
                        action_args['original_content'] = original_info.get('content', '')
                        action_args['original_author_name'] = original_info.get('author_name', '')
        
        # 게시물 인용: 보충 원 게시물 내용, 저자와 인용 댓글
        elif action_type == 'QUOTE_POST':
            quoted_id = action_args.get('quoted_id')
            new_post_id = action_args.get('new_post_id')
            
            if quoted_id:
                original_info = _get_post_info(cursor, quoted_id, agent_names)
                if original_info:
                    action_args['original_content'] = original_info.get('content', '')
                    action_args['original_author_name'] = original_info.get('author_name', '')
            
            # 가져오기 게시물 인용의 댓글 내용 (quote_content)
            if new_post_id:
                cursor.execute("""
                    SELECT quote_content FROM post WHERE post_id = ?
                """, (new_post_id,))
                row = cursor.fetchone()
                if row and row[0]:
                    action_args['quote_content'] = row[0]
        
        # 팔로우 사용자: 보충 팔로우 사용자의 이름
        elif action_type == 'FOLLOW':
            follow_id = action_args.get('follow_id')
            if follow_id:
                # 부터 follow 표 가져오기 followee_id
                cursor.execute("""
                    SELECT followee_id FROM follow WHERE follow_id = ?
                """, (follow_id,))
                row = cursor.fetchone()
                if row:
                    followee_id = row[0]
                    target_name = _get_user_name(cursor, followee_id, agent_names)
                    if target_name:
                        action_args['target_user_name'] = target_name
        
        # 차단 사용자: 보충 차단 사용자의 이름
        elif action_type == 'MUTE':
            # 부터 action_args 중가져오기 user_id 또는 target_id
            target_id = action_args.get('user_id') or action_args.get('target_id')
            if target_id:
                target_name = _get_user_name(cursor, target_id, agent_names)
                if target_name:
                    action_args['target_user_name'] = target_name
        
        # 좋아요/싫어요 댓글: 보충 댓글 내용와 저자
        elif action_type in ('LIKE_COMMENT', 'DISLIKE_COMMENT'):
            comment_id = action_args.get('comment_id')
            if comment_id:
                comment_info = _get_comment_info(cursor, comment_id, agent_names)
                if comment_info:
                    action_args['comment_content'] = comment_info.get('content', '')
                    action_args['comment_author_name'] = comment_info.get('author_name', '')
        
        # 댓글 작성: 보충 소 댓글의 게시물 정보
        elif action_type == 'CREATE_COMMENT':
            post_id = action_args.get('post_id')
            if post_id:
                post_info = _get_post_info(cursor, post_id, agent_names)
                if post_info:
                    action_args['post_content'] = post_info.get('content', '')
                    action_args['post_author_name'] = post_info.get('author_name', '')
    
    except Exception as e:
        # 보충 컨텍스트 실패 안 영향 메인 프로세스
        print(f"보충 액션 컨텍스트 실패: {e}")


def _get_post_info(
    cursor,
    post_id: int,
    agent_names: Dict[int, str]
) -> Optional[Dict[str, str]]:
    """
    가져오기 게시물 정보
    
    Args:
        cursor: 데이터베이스 커서
        post_id: 게시물 ID
        agent_names: agent_id -> agent_name 매핑
        
    Returns:
        포함 content 와 author_name 의 딕셔너리, 또는 None
    """
    try:
        cursor.execute("""
            SELECT p.content, p.user_id, u.agent_id
            FROM post p
            LEFT JOIN user u ON p.user_id = u.user_id
            WHERE p.post_id = ?
        """, (post_id,))
        row = cursor.fetchone()
        if row:
            content = row[0] or ''
            user_id = row[1]
            agent_id = row[2]
            
            # 우선 사용 agent_names 의 이름
            author_name = ''
            if agent_id is not None and agent_id in agent_names:
                author_name = agent_names[agent_id]
            elif user_id:
                # 부터 user 표 가져오기 이름
                cursor.execute("SELECT name, user_name FROM user WHERE user_id = ?", (user_id,))
                user_row = cursor.fetchone()
                if user_row:
                    author_name = user_row[0] or user_row[1] or ''
            
            return {'content': content, 'author_name': author_name}
    except Exception:
        pass
    return None


def _get_user_name(
    cursor,
    user_id: int,
    agent_names: Dict[int, str]
) -> Optional[str]:
    """
    가져오기사용자이름
    
    Args:
        cursor: 데이터베이스 커서
        user_id: 사용자ID
        agent_names: agent_id -> agent_name 매핑
        
    Returns:
        사용자이름, 또는 None
    """
    try:
        cursor.execute("""
            SELECT agent_id, name, user_name FROM user WHERE user_id = ?
        """, (user_id,))
        row = cursor.fetchone()
        if row:
            agent_id = row[0]
            name = row[1]
            user_name = row[2]
            
            # 우선 사용 agent_names 의 이름
            if agent_id is not None and agent_id in agent_names:
                return agent_names[agent_id]
            return name or user_name or ''
    except Exception:
        pass
    return None


def _get_comment_info(
    cursor,
    comment_id: int,
    agent_names: Dict[int, str]
) -> Optional[Dict[str, str]]:
    """
    가져오기댓글정보
    
    Args:
        cursor: 데이터베이스游标
        comment_id: 댓글 ID
        agent_names: agent_id -> agent_name 매핑
        
    Returns:
        포함 content 와 author_name 의字典, 또는 None
    """
    try:
        cursor.execute("""
            SELECT c.content, c.user_id, u.agent_id
            FROM comment c
            LEFT JOIN user u ON c.user_id = u.user_id
            WHERE c.comment_id = ?
        """, (comment_id,))
        row = cursor.fetchone()
        if row:
            content = row[0] or ''
            user_id = row[1]
            agent_id = row[2]
            
            # 우선 agent_names 의 이름 사용
            author_name = ''
            if agent_id is not None and agent_id in agent_names:
                author_name = agent_names[agent_id]
            elif user_id:
                # 부터 user 표에서 이름 가져오기
                cursor.execute("SELECT name, user_name FROM user WHERE user_id = ?", (user_id,))
                user_row = cursor.fetchone()
                if user_row:
                    author_name = user_row[0] or user_row[1] or ''
            
            return {'content': content, 'author_name': author_name}
    except Exception:
        pass
    return None


def create_model(config: Dict[str, Any], use_boost: bool = False):
    """
    생성LLM모델
    
    지원 듀얼 LLM 설정, 용병렬 시뮬레이션 시提速:
    - 통용설정：LLM_API_KEY, LLM_BASE_URL, LLM_MODEL_NAME
    - 가속설정（선택）：LLM_BOOST_API_KEY, LLM_BOOST_BASE_URL, LLM_BOOST_MODEL_NAME
    
    만약 설정 가속 LLM, 병렬 시뮬레이션 시하여 안同 플랫폼 사용 안同의 API 서비스商,提높은그리고发力.
    
    Args:
        config: 시뮬레이션 설정字典
        use_boost: 否 사용 가속 LLM 설정(만약用)
    """
    # 체크否 가속 설정
    boost_api_key = os.environ.get("LLM_BOOST_API_KEY", "")
    boost_base_url = os.environ.get("LLM_BOOST_BASE_URL", "")
    boost_model = os.environ.get("LLM_BOOST_MODEL_NAME", "")
    has_boost_config = bool(boost_api_key)
    
    # 에 따라 파라미터와 설정 상황 선택 사용哪개 LLM
    if use_boost and has_boost_config:
        # 사용가속설정
        llm_api_key = boost_api_key
        llm_base_url = boost_base_url
        llm_model = boost_model or os.environ.get("LLM_MODEL_NAME", "")
        config_label = "[加速LLM]"
    else:
        # 사용통용설정
        llm_api_key = os.environ.get("LLM_API_KEY", "")
        llm_base_url = os.environ.get("LLM_BASE_URL", "")
        llm_model = os.environ.get("LLM_MODEL_NAME", "")
        config_label = "[通用LLM]"
    
    # 만약 .env 중 없으면 모델명, 사용 config 作로备用
    if not llm_model:
        llm_model = config.get("llm_model", "gpt-4o-mini")
    
    # camel-ai에 필요한 환경변수 설정
    proxy_url = os.environ.get("LLM_PROXY_URL", "")
    is_luxia = 'luxiacloud' in (llm_base_url or '')

    if llm_api_key:
        if is_luxia and not proxy_url:
            proxy_url = "http://localhost:8788/v1"
            os.environ["OPENAI_API_KEY"] = "proxy-key"
            print(f"[LuxiaCloud 감지] 프록시 사용: {proxy_url}")
        else:
            os.environ["OPENAI_API_KEY"] = llm_api_key

    if not os.environ.get("OPENAI_API_KEY"):
        raise ValueError("API Key 설정이 누락되었습니다. .env 파일에서 LLM_API_KEY를 설정해 주세요.")

    if proxy_url:
        os.environ["OPENAI_API_BASE_URL"] = proxy_url
    elif llm_base_url:
        os.environ["OPENAI_API_BASE_URL"] = llm_base_url

    effective_url = proxy_url or llm_base_url or '기본값'
    print(f"{config_label} model={llm_model}, base_url={effective_url[:50]}...")
    
    return ModelFactory.create(
        model_platform=ModelPlatformType.OPENAI,
        model_type=llm_model,
    )


def get_active_agents_for_round(
    env,
    config: Dict[str, Any],
    current_hour: int,
    round_num: int
) -> List:
    """에 따라 시간와 설정 결정 본 라운드激活哪些 Agent"""
    time_config = config.get("time_config", {})
    agent_configs = config.get("agent_configs", [])
    
    base_min = time_config.get("agents_per_hour_min", 5)
    base_max = time_config.get("agents_per_hour_max", 20)
    
    peak_hours = time_config.get("peak_hours", [9, 10, 11, 14, 15, 20, 21, 22])
    off_peak_hours = time_config.get("off_peak_hours", [0, 1, 2, 3, 4, 5])
    
    if current_hour in peak_hours:
        multiplier = time_config.get("peak_activity_multiplier", 1.5)
    elif current_hour in off_peak_hours:
        multiplier = time_config.get("off_peak_activity_multiplier", 0.3)
    else:
        multiplier = 1.0
    
    target_count = int(random.uniform(base_min, base_max) * multiplier)
    
    candidates = []
    for cfg in agent_configs:
        agent_id = cfg.get("agent_id", 0)
        active_hours = cfg.get("active_hours", list(range(8, 23)))
        activity_level = cfg.get("activity_level", 0.5)
        
        if current_hour not in active_hours:
            continue
        
        if random.random() < activity_level:
            candidates.append(agent_id)
    
    selected_ids = random.sample(
        candidates, 
        min(target_count, len(candidates))
    ) if candidates else []
    
    active_agents = []
    for agent_id in selected_ids:
        try:
            agent = env.agent_graph.get_agent(agent_id)
            active_agents.append((agent_id, agent))
        except Exception:
            pass
    
    return active_agents


class PlatformSimulation:
    """플랫폼 시뮬레이션 결과容器"""
    def __init__(self):
        self.env = None
        self.agent_graph = None
        self.total_actions = 0


async def run_twitter_simulation(
    config: Dict[str, Any], 
    simulation_dir: str,
    action_logger: Optional[PlatformActionLogger] = None,
    main_logger: Optional[SimulationLogManager] = None,
    max_rounds: Optional[int] = None
) -> PlatformSimulation:
    """실행Twitter시뮬레이션
    
    Args:
        config: 시뮬레이션설정
        simulation_dir: 시뮬레이션디렉토리
        action_logger: 액션로그기록기
        main_logger: 메인 로그 관리器
        max_rounds: 최대 시뮬레이션 라운드 수(선택, 용잘림 너무 긴의 시뮬레이션)
        
    Returns:
        PlatformSimulation: 포함 env와 agent_graph의 결과象
    """
    result = PlatformSimulation()
    
    def log_info(msg):
        if main_logger:
            main_logger.info(f"[Twitter] {msg}")
        print(f"[Twitter] {msg}")
    
    log_info("초기화...")
    
    # Twitter 사용통용 LLM 설정
    model = create_model(config, use_boost=False)
    
    # OASIS Twitter사용CSV형식
    profile_path = os.path.join(simulation_dir, "twitter_profiles.csv")
    if not os.path.exists(profile_path):
        log_info(f"오류: Profile파일이 존재하지 않음: {profile_path}")
        return result
    
    result.agent_graph = await generate_twitter_agent_graph(
        profile_path=profile_path,
        model=model,
        available_actions=TWITTER_ACTIONS,
    )
    
    # 부터 설정 파일 가져오기 Agent 真实 이름 매핑(사용 entity_name 而비 기본값의 Agent_X)
    agent_names = get_agent_names_from_config(config)
    # 만약 설정 중 없으면특정개 agent, 사용 OASIS 의 기본값 이름
    for agent_id, agent in result.agent_graph.get_agents():
        if agent_id not in agent_names:
            agent_names[agent_id] = getattr(agent, 'name', f'Agent_{agent_id}')
    
    db_path = os.path.join(simulation_dir, "twitter_simulation.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    
    result.env = oasis.make(
        agent_graph=result.agent_graph,
        platform=oasis.DefaultPlatformType.TWITTER,
        database_path=db_path,
        semaphore=30,  # 제한 최대 병렬 LLM 요청 수, 방지 API 载
    )
    
    await result.env.reset()
    log_info("환경이미시작")
    
    if action_logger:
        action_logger.log_simulation_start(config)
    
    total_actions = 0
    last_rowid = 0  # 데이터베이스 중 가장 최근 처리의 행 번호（사용 rowid 형식 차이 방지）  
    
    # 실행 초기 이벤트  
    event_config = config.get("event_config", {})
    initial_posts = event_config.get("initial_posts", [])
    
    # 기록 round 0 시작（초기 이벤트 단계）  
    if action_logger:
        action_logger.log_round_start(0, 0)  # round 0, simulated_hour 0
    
    initial_action_count = 0
    if initial_posts:
        initial_actions = {}
        for post in initial_posts:
            agent_id = post.get("poster_agent_id", 0)
            content = post.get("content", "")
            try:
                agent = result.env.agent_graph.get_agent(agent_id)
                initial_actions[agent] = ManualAction(
                    action_type=ActionType.CREATE_POST,
                    action_args={"content": content}
                )
                
                if action_logger:
                    action_logger.log_action(
                        round_num=0,
                        agent_id=agent_id,
                        agent_name=agent_names.get(agent_id, f"Agent_{agent_id}"),
                        action_type="CREATE_POST",
                        action_args={"content": content}
                    )
                    total_actions += 1
                    initial_action_count += 1
            except Exception:
                pass
        
        if initial_actions:
            await result.env.step(initial_actions)
            log_info(f"이미 발표 {len(initial_actions)} 개 초기 게시물")  
    
    # 기록 round 0 종료  
    if action_logger:
        action_logger.log_round_end(0, initial_action_count)
    
    # 메인 시뮬레이션 루프  
    time_config = config.get("time_config", {})
    total_hours = time_config.get("total_simulation_hours", 72)
    minutes_per_round = time_config.get("minutes_per_round", 30)
    total_rounds = (total_hours * 60) // minutes_per_round
    
    # 만약 지정 최대 라운드 수, 그러면 절단  
    if max_rounds is not None and max_rounds > 0:
        original_rounds = total_rounds
        total_rounds = min(total_rounds, max_rounds)
        if total_rounds < original_rounds:
            log_info(f"라운드 수 이미 절단: {original_rounds} -> {total_rounds} (max_rounds={max_rounds})")  
    
    start_time = datetime.now()
    
    for round_num in range(total_rounds):
        # 체크 종료 신호
        if _shutdown_event and _shutdown_event.is_set():
            if main_logger:
                main_logger.info(f"종료 신호,에서 {round_num + 1} 라운드 중지 시뮬레이션")  
            break
        
        simulated_minutes = round_num * minutes_per_round
        simulated_hour = (simulated_minutes // 60) % 24
        simulated_day = simulated_minutes // (60 * 24) + 1
        
        active_agents = get_active_agents_for_round(
            result.env, config, simulated_hour, round_num
        )
        
        # 무관하게 활성 agent, 기록 round 시작
        if action_logger:
            action_logger.log_round_start(round_num + 1, simulated_hour)
        
        if not active_agents:
            # 없으면 활성 agent 시에도 기록 round 종료（actions_count=0）
            if action_logger:
                action_logger.log_round_end(round_num + 1, 0)
            continue
        
        actions = {agent: LLMAction() for _, agent in active_agents}
        await result.env.step(actions)
        
        # 데이터베이스에서 가져오기 실제 실행의 액션 및 기록
        actual_actions, last_rowid = fetch_new_actions_from_db(
            db_path, last_rowid, agent_names
        )
        
        round_action_count = 0
        for action_data in actual_actions:
            if action_logger:
                action_logger.log_action(
                    round_num=round_num + 1,
                    agent_id=action_data['agent_id'],
                    agent_name=action_data['agent_name'],
                    action_type=action_data['action_type'],
                    action_args=action_data['action_args']
                )
                total_actions += 1
                round_action_count += 1
        
        if action_logger:
            action_logger.log_round_end(round_num + 1, round_action_count)
        
        if (round_num + 1) % 20 == 0:
            progress = (round_num + 1) / total_rounds * 100
            log_info(f"Day {simulated_day}, {simulated_hour:02d}:00 - Round {round_num + 1}/{total_rounds} ({progress:.1f}%)")
    
    # 주의: 닫지 않는 환경, Interview 사용 유지
    
    if action_logger:
        action_logger.log_simulation_end(total_rounds, total_actions)
    
    result.total_actions = total_actions
    elapsed = (datetime.now() - start_time).total_seconds()
    log_info(f"시뮬레이션 루프 완료! 소요 시간: {elapsed:.1f}초, 총 액션: {total_actions}")
    
    return result


async def run_reddit_simulation(
    config: Dict[str, Any], 
    simulation_dir: str,
    action_logger: Optional[PlatformActionLogger] = None,
    main_logger: Optional[SimulationLogManager] = None,
    max_rounds: Optional[int] = None
) -> PlatformSimulation:
    """실행Reddit시뮬레이션
    
    Args:
        config: 시뮬레이션설정
        simulation_dir: 시뮬레이션디렉토리
        action_logger: 액션 로그 기록기
        main_logger: 메인 로그 관리기
        max_rounds: 최대 시뮬레이션 라운드 수（선택, 너무 긴 시뮬레이션의 경우 절단）  
        
    Returns:
        PlatformSimulation: 포함 env와 agent_graph의 결과 상
    """
    result = PlatformSimulation()
    
    def log_info(msg):
        if main_logger:
            main_logger.info(f"[Reddit] {msg}")
        print(f"[Reddit] {msg}")
    
    log_info("초기화...")
    
    # Reddit 사용 가속 LLM 설정（만약의 경우, 그렇지 않으면 일반 설정으로 돌아감）
    model = create_model(config, use_boost=True)
    
    profile_path = os.path.join(simulation_dir, "reddit_profiles.json")
    if not os.path.exists(profile_path):
        log_info(f"오류: Profile파일이 존재하지 않음: {profile_path}")
        return result
    
    result.agent_graph = await generate_reddit_agent_graph(
        profile_path=profile_path,
        model=model,
        available_actions=REDDIT_ACTIONS,
    )
    
    # 설정 파일에서 가져오기 Agent 실제 이름 매핑（사용 entity_name 대신 기본값의 Agent_X）
    agent_names = get_agent_names_from_config(config)
    # 만약 설정 중 없으면 특정 agent, 그렇지 않으면 OASIS의 기본값 이름 사용
    for agent_id, agent in result.agent_graph.get_agents():
        if agent_id not in agent_names:
            agent_names[agent_id] = getattr(agent, 'name', f'Agent_{agent_id}')
    
    db_path = os.path.join(simulation_dir, "reddit_simulation.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    
    result.env = oasis.make(
        agent_graph=result.agent_graph,
        platform=oasis.DefaultPlatformType.REDDIT,
        database_path=db_path,
        semaphore=30,  # 최대 동시 LLM 요청 수 제한, API 과부하 방지
    )
    
    await result.env.reset()
    log_info("환경이미시작")
    
    if action_logger:
        action_logger.log_simulation_start(config)
    
    total_actions = 0
    last_rowid = 0  # 데이터베이스 중 마지막 처리의 행 번호 추적（사용 rowid 형식 차이 방지）
    
    # 실행 초기 이벤트
    event_config = config.get("event_config", {})
    initial_posts = event_config.get("initial_posts", [])
    
    # 기록 round 0 시작（초기 이벤트 단계）
    if action_logger:
        action_logger.log_round_start(0, 0)  # round 0, simulated_hour 0
    
    initial_action_count = 0
    if initial_posts:
        initial_actions = {}
        for post in initial_posts:
            agent_id = post.get("poster_agent_id", 0)
            content = post.get("content", "")
            try:
                agent = result.env.agent_graph.get_agent(agent_id)
                if agent in initial_actions:
                    if not isinstance(initial_actions[agent], list):
                        initial_actions[agent] = [initial_actions[agent]]
                    initial_actions[agent].append(ManualAction(
                        action_type=ActionType.CREATE_POST,
                        action_args={"content": content}
                    ))
                else:
                    initial_actions[agent] = ManualAction(
                        action_type=ActionType.CREATE_POST,
                        action_args={"content": content}
                    )
                
                if action_logger:
                    action_logger.log_action(
                        round_num=0,
                        agent_id=agent_id,
                        agent_name=agent_names.get(agent_id, f"Agent_{agent_id}"),
                        action_type="CREATE_POST",
                        action_args={"content": content}
                    )
                    total_actions += 1
                    initial_action_count += 1
            except Exception:
                pass
        
        if initial_actions:
            await result.env.step(initial_actions)
            log_info(f"이미 게시 {len(initial_actions)} 개 초기 게시물")
    
    # 기록 round 0 종료
    if action_logger:
        action_logger.log_round_end(0, initial_action_count)
    
    # 메인 시뮬레이션 루프
    time_config = config.get("time_config", {})
    total_hours = time_config.get("total_simulation_hours", 72)
    minutes_per_round = time_config.get("minutes_per_round", 30)
    total_rounds = (total_hours * 60) // minutes_per_round
    
    # 만약 지정 최대 라운드 수, 그러면 절단  
    if max_rounds is not None and max_rounds > 0:
        original_rounds = total_rounds
        total_rounds = min(total_rounds, max_rounds)
        if total_rounds < original_rounds:
            log_info(f"라운드 수 이미 절단: {original_rounds} -> {total_rounds} (max_rounds={max_rounds})")  
    
    start_time = datetime.now()
    
    for round_num in range(total_rounds):
        # 체크 종료 신호
        if _shutdown_event and _shutdown_event.is_set():
            if main_logger:
                main_logger.info(f"종료 신호,에서 {round_num + 1} 라운드 중지 시뮬레이션")  
            break
        
        simulated_minutes = round_num * minutes_per_round
        simulated_hour = (simulated_minutes // 60) % 24
        simulated_day = simulated_minutes // (60 * 24) + 1
        
        active_agents = get_active_agents_for_round(
            result.env, config, simulated_hour, round_num
        )
        
        # 무관하게 활성 agent, 기록 round 시작
        if action_logger:
            action_logger.log_round_start(round_num + 1, simulated_hour)
        
        if not active_agents:
            # 없으면 활성 agent 시에도 기록 round 종료（actions_count=0）
            if action_logger:
                action_logger.log_round_end(round_num + 1, 0)
            continue
        
        actions = {agent: LLMAction() for _, agent in active_agents}
        await result.env.step(actions)
        
        # 데이터베이스에서 가져오기 실제 실행의 액션 및 기록
        actual_actions, last_rowid = fetch_new_actions_from_db(
            db_path, last_rowid, agent_names
        )
        
        round_action_count = 0
        for action_data in actual_actions:
            if action_logger:
                action_logger.log_action(
                    round_num=round_num + 1,
                    agent_id=action_data['agent_id'],
                    agent_name=action_data['agent_name'],
                    action_type=action_data['action_type'],
                    action_args=action_data['action_args']
                )
                total_actions += 1
                round_action_count += 1
        
        if action_logger:
            action_logger.log_round_end(round_num + 1, round_action_count)
        
        if (round_num + 1) % 20 == 0:
            progress = (round_num + 1) / total_rounds * 100
            log_info(f"Day {simulated_day}, {simulated_hour:02d}:00 - Round {round_num + 1}/{total_rounds} ({progress:.1f}%)")
    
    # 주의: 닫지 않는 환경, Interview 사용 유지
    
    if action_logger:
        action_logger.log_simulation_end(total_rounds, total_actions)
    
    result.total_actions = total_actions
    elapsed = (datetime.now() - start_time).total_seconds()
    log_info(f"시뮬레이션 루프 완료! 소요 시간: {elapsed:.1f}초, 총 액션: {total_actions}")
    
    return result


async def main():
    parser = argparse.ArgumentParser(description='OASIS듀얼플랫폼병렬시뮬레이션')
    parser.add_argument(
        '--config', 
        type=str, 
        required=True,
        help='설정파일경로 (simulation_config.json)'
    )
    parser.add_argument(
        '--twitter-only',
        action='store_true',
        help='오직 Twitter 시뮬레이션 실행'
    )
    parser.add_argument(
        '--reddit-only',
        action='store_true',
        help='오직 Reddit 시뮬레이션 실행'
    )
    parser.add_argument(
        '--max-rounds',
        type=int,
        default=None,
        help='최대 시뮬레이션 라운드 수（선택, 너무 긴 시뮬레이션의 경우 절단）'
    )
    parser.add_argument(
        '--no-wait',
        action='store_true',
        default=False,
        help='시뮬레이션 완료 즉시 닫기 환경, 대기 명령 모드에 진입하지 않음'
    )
    
    args = parser.parse_args()
    
    # main 함수 시작 시 생성 shutdown 이벤트, 전체 프로그램이 종료 신호에 응답하도록 보장
    global _shutdown_event
    _shutdown_event = asyncio.Event()
    
    if not os.path.exists(args.config):
        print(f"오류: 설정파일이 존재하지 않음: {args.config}")
        sys.exit(1)
    
    config = load_config(args.config)
    simulation_dir = os.path.dirname(args.config) or "."
    wait_for_commands = not args.no_wait
    
    # 초기화 로그 설정（비활성화 OASIS 로그, 오래된 파일 정리）
    init_logging_for_simulation(simulation_dir)
    
    # 로그 관리기 생성
    log_manager = SimulationLogManager(simulation_dir)
    twitter_logger = log_manager.get_twitter_logger()
    reddit_logger = log_manager.get_reddit_logger()
    
    log_manager.info("=" * 60)
    log_manager.info("OASIS 듀얼플랫폼병렬시뮬레이션")
    log_manager.info(f"설정파일: {args.config}")
    log_manager.info(f"시뮬레이션ID: {config.get('simulation_id', 'unknown')}")
    log_manager.info(f"대기 명령 모드: {'활성화' if wait_for_commands else '비활성화'}")
    log_manager.info("=" * 60)
    
    time_config = config.get("time_config", {})
    total_hours = time_config.get('total_simulation_hours', 72)
    minutes_per_round = time_config.get('minutes_per_round', 30)
    config_total_rounds = (total_hours * 60) // minutes_per_round
    
    log_manager.info(f"시뮬레이션파라미터:")
    log_manager.info(f"  - 총 시뮬레이션 소요 시간: {total_hours}시간")
    log_manager.info(f"  - 매 라운드 시간: {minutes_per_round}분")
    log_manager.info(f"  - 설정총라운드 수: {config_total_rounds}")
    if args.max_rounds:
        log_manager.info(f"  - 최대 라운드 수 제한: {args.max_rounds}")
        if args.max_rounds < config_total_rounds:
            log_manager.info(f"  - 실제 실행 라운드 수: {args.max_rounds} (이미 잘림)")
    log_manager.info(f"  - Agent수량: {len(config.get('agent_configs', []))}")
    
    log_manager.info("로그구조:")
    log_manager.info(f"  - 메인로그: simulation.log")
    log_manager.info(f"  - Twitter액션: twitter/actions.jsonl")
    log_manager.info(f"  - Reddit액션: reddit/actions.jsonl")
    log_manager.info("=" * 60)
    
    start_time = datetime.now()
    
    # 두 개 플랫폼의 시뮬레이션 결과 저장
    twitter_result: Optional[PlatformSimulation] = None
    reddit_result: Optional[PlatformSimulation] = None
    
    if args.twitter_only:
        twitter_result = await run_twitter_simulation(config, simulation_dir, twitter_logger, log_manager, args.max_rounds)
    elif args.reddit_only:
        reddit_result = await run_reddit_simulation(config, simulation_dir, reddit_logger, log_manager, args.max_rounds)
    else:
        # 병렬 실행 (각 플랫폼 사용 독립의 로그 기록기)
        results = await asyncio.gather(
            run_twitter_simulation(config, simulation_dir, twitter_logger, log_manager, args.max_rounds),
            run_reddit_simulation(config, simulation_dir, reddit_logger, log_manager, args.max_rounds),
        )
        twitter_result, reddit_result = results
    
    total_elapsed = (datetime.now() - start_time).total_seconds()
    log_manager.info("=" * 60)
    log_manager.info(f"시뮬레이션 루프 완료! 총 소요 시간: {total_elapsed:.1f}초")
    
    # 진입 대기 명령 모드 여부
    if wait_for_commands:
        log_manager.info("")
        log_manager.info("=" * 60)
        log_manager.info("진입 대기 명령 모드 - 환경 유지 실행")
        log_manager.info("지원 명령: interview, batch_interview, close_env")
        log_manager.info("=" * 60)
        
        # IPC 처리기 생성
        ipc_handler = ParallelIPCHandler(
            simulation_dir=simulation_dir,
            twitter_env=twitter_result.env if twitter_result else None,
            twitter_agent_graph=twitter_result.agent_graph if twitter_result else None,
            reddit_env=reddit_result.env if reddit_result else None,
            reddit_agent_graph=reddit_result.agent_graph if reddit_result else None
        )
        ipc_handler.update_status("alive")
        
        # 대기 명령 루프 (사용 전체 _shutdown_event)
        try:
            while not _shutdown_event.is_set():
                should_continue = await ipc_handler.process_commands()
                if not should_continue:
                    break
                # 사용 wait_for 대신 sleep, 이렇게 하여 응답 shutdown_event
                try:
                    await asyncio.wait_for(_shutdown_event.wait(), timeout=0.5)
                    break  # 수신 종료 신호
                except asyncio.TimeoutError:
                    pass  # 시간 초과 계속 루프
        except KeyboardInterrupt:
            print("\n수신 중단 신호")
        except asyncio.CancelledError:
            print("\n작업취소")
        except Exception as e:
            print(f"\n명령 처리 중 오류: {e}")
        
        log_manager.info("\n닫기환경...")
        ipc_handler.update_status("stopped")
    
    # 닫기환경
    if twitter_result and twitter_result.env:
        await twitter_result.env.close()
        log_manager.info("[Twitter] 환경이미닫기")
    
    if reddit_result and reddit_result.env:
        await reddit_result.env.close()
        log_manager.info("[Reddit] 환경이미닫기")
    
    log_manager.info("=" * 60)
    log_manager.info(f"전체 완료!")
    log_manager.info(f"로그파일:")
    log_manager.info(f"  - {os.path.join(simulation_dir, 'simulation.log')}")
    log_manager.info(f"  - {os.path.join(simulation_dir, 'twitter', 'actions.jsonl')}")
    log_manager.info(f"  - {os.path.join(simulation_dir, 'reddit', 'actions.jsonl')}")
    log_manager.info("=" * 60)


def setup_signal_handlers(loop=None):
    """
    신호 처리기 설정, 보장 수신 SIGTERM/SIGINT 시 올바르게 종료
    
    지속화 시뮬레이션 장면: 시뮬레이션 완료 후 안 종료, 대기 interview 명령
    수신 종료 신호 시, 필요:
    1. asyncio 루프 종료 대기 통지
    2. 프로그램 정상적으로 자원 정리 (데이터베이스 닫기, 환경 등)
    3. 그 다음에야 종료
    """
    def signal_handler(signum, frame):
        global _cleanup_done
        sig_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
        print(f"\n수신 {sig_name} 신호, 진행 중: 종료...")
        
        if not _cleanup_done:
            _cleanup_done = True
            # 이벤트 설정하여 asyncio 루프 종료 (루프 자원 정리)
            if _shutdown_event:
                _shutdown_event.set()
        
        # 직접 sys.exit() 하지 마세요, asyncio 루프 정상 종료 및 자원 정리
        # 만약 반복 수신 신호, 강제로 종료
        else:
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
        # 정리 multiprocessing 자원 추적기 (종료 시 경고 방지)
        try:
            from multiprocessing import resource_tracker
            resource_tracker._resource_tracker._stop()
        except Exception:
            pass
        print("시뮬레이션 프로세스 이미 종료")
