"""
OASIS 시뮬레이션 실행기
백그라운드에서 시뮬레이션을 실행하고 각 Agent의 액션을 기록하며, 실시간 상태 모니터링을 지원합니다.
"""

import os
import sys
import json
import time
import asyncio
import threading
import subprocess
import signal
import atexit
from typing import Dict, Any, List, Optional, Union
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from queue import Queue

from ..config import Config
from ..utils.logger import get_logger
from .zep_graph_memory_updater import ZepGraphMemoryManager
from .simulation_ipc import SimulationIPCClient, CommandType, IPCResponse

logger = get_logger('mirofish.simulation_runner')

# 이미 표시됨등록정리함수
_cleanup_registered = False

# 플랫폼감지
IS_WINDOWS = sys.platform == 'win32'


class RunnerStatus(str, Enum):
    """ 실행기 상태 """
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class AgentAction:
    """Agent액션기록"""
    round_num: int
    timestamp: str
    platform: str  # twitter / reddit
    agent_id: int
    agent_name: str
    action_type: str  # CREATE_POST, LIKE_POST, etc.
    action_args: Dict[str, Any] = field(default_factory=dict)
    result: Optional[str] = None
    success: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "round_num": self.round_num,
            "timestamp": self.timestamp,
            "platform": self.platform,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "action_type": self.action_type,
            "action_args": self.action_args,
            "result": self.result,
            "success": self.success,
        }


@dataclass
class RoundSummary:
    """ 매 라운드 요약 """
    round_num: int
    start_time: str
    end_time: Optional[str] = None
    simulated_hour: int = 0
    twitter_actions: int = 0
    reddit_actions: int = 0
    active_agents: List[int] = field(default_factory=list)
    actions: List[AgentAction] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "round_num": self.round_num,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "simulated_hour": self.simulated_hour,
            "twitter_actions": self.twitter_actions,
            "reddit_actions": self.reddit_actions,
            "active_agents": self.active_agents,
            "actions_count": len(self.actions),
            "actions": [a.to_dict() for a in self.actions],
        }


@dataclass
class SimulationRunState:
    """시뮬레이션실행상태（실시간）"""
    simulation_id: str
    runner_status: RunnerStatus = RunnerStatus.IDLE
    
    # 진행률정보
    current_round: int = 0
    total_rounds: int = 0
    simulated_hours: int = 0
    total_simulation_hours: int = 0
    
    # 각 플랫폼 독립 라운드 수와 시뮬레이션 시간 (용 듀얼 플랫폼 병렬 표시)
    twitter_current_round: int = 0
    reddit_current_round: int = 0
    twitter_simulated_hours: int = 0
    reddit_simulated_hours: int = 0
    
    # 플랫폼상태
    twitter_running: bool = False
    reddit_running: bool = False
    twitter_actions_count: int = 0
    reddit_actions_count: int = 0
    
    # 플랫폼 완료 상태 (통감지 actions.jsonl 의 simulation_end 이벤트)
    twitter_completed: bool = False
    reddit_completed: bool = False
    
    # 매 라운드 요약
    rounds: List[RoundSummary] = field(default_factory=list)
    
    # 최근 액션 (용 프론트엔드 실시간 표시)
    recent_actions: List[AgentAction] = field(default_factory=list)
    max_recent_actions: int = 50
    
    # 타임스탬프
    started_at: Optional[str] = None
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: Optional[str] = None
    
    # 오류정보
    error: Optional[str] = None
    
    # 프로세스ID（용중지）
    process_pid: Optional[int] = None
    
    def add_action(self, action: AgentAction):
        """ 추가 액션 최근 액션 목록 """
        self.recent_actions.insert(0, action)
        if len(self.recent_actions) > self.max_recent_actions:
            self.recent_actions = self.recent_actions[:self.max_recent_actions]
        
        if action.platform == "twitter":
            self.twitter_actions_count += 1
        else:
            self.reddit_actions_count += 1
        
        self.updated_at = datetime.now().isoformat()
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "simulation_id": self.simulation_id,
            "runner_status": self.runner_status.value,
            "current_round": self.current_round,
            "total_rounds": self.total_rounds,
            "simulated_hours": self.simulated_hours,
            "total_simulation_hours": self.total_simulation_hours,
            "progress_percent": round(self.current_round / max(self.total_rounds, 1) * 100, 1),
            # 각 플랫폼 독립 라운드 수와 시간
            "twitter_current_round": self.twitter_current_round,
            "reddit_current_round": self.reddit_current_round,
            "twitter_simulated_hours": self.twitter_simulated_hours,
            "reddit_simulated_hours": self.reddit_simulated_hours,
            "twitter_running": self.twitter_running,
            "reddit_running": self.reddit_running,
            "twitter_completed": self.twitter_completed,
            "reddit_completed": self.reddit_completed,
            "twitter_actions_count": self.twitter_actions_count,
            "reddit_actions_count": self.reddit_actions_count,
            "total_actions_count": self.twitter_actions_count + self.reddit_actions_count,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "process_pid": self.process_pid,
        }
    
    def to_detail_dict(self) -> Dict[str, Any]:
        """ 포함 최근 액션의 상세 정보 """
        result = self.to_dict()
        result["recent_actions"] = [a.to_dict() for a in self.recent_actions]
        result["rounds_count"] = len(self.rounds)
        return result


class SimulationRunner:
    """
    시뮬레이션 실행기
    
    책임:
    1. 백그라운드에서프로세스중실행OASIS시뮬레이션
    2. 실행 로그 파싱, 각 Agent의 액션 기록
    3. 실시간 상태 조회 인터페이스 제공
    4. 일시 정지/중지/재개 작업 지원
    """
    
    # 실행 상태 저장 디렉토리
    RUN_STATE_DIR = os.path.join(
        os.path.dirname(__file__),
        '../../uploads/simulations'
    )
    
    # 스크립트 디렉토리
    SCRIPTS_DIR = os.path.join(
        os.path.dirname(__file__),
        '../../scripts'
    )
    
    # 메모리의 실행 상태
    _run_states: Dict[str, SimulationRunState] = {}
    _processes: Dict[str, subprocess.Popen] = {}
    _action_queues: Dict[str, Queue] = {}
    _monitor_threads: Dict[str, threading.Thread] = {}
    _stdout_files: Dict[str, Any] = {}  # 저장 stdout 파일 핸들
    _stderr_files: Dict[str, Any] = {}  # 저장 stderr 파일 핸들
    
    # 그래프기억업데이트설정
    _graph_memory_enabled: Dict[str, bool] = {}  # simulation_id -> enabled
    
    @classmethod
    def get_run_state(cls, simulation_id: str) -> Optional[SimulationRunState]:
        """가져오기실행상태"""
        if simulation_id in cls._run_states:
            return cls._run_states[simulation_id]
        
        # 시도부터파일로드
        state = cls._load_run_state(simulation_id)
        if state:
            cls._run_states[simulation_id] = state
        return state
    
    @classmethod
    def _load_run_state(cls, simulation_id: str) -> Optional[SimulationRunState]:
        """부터파일로드실행상태"""
        state_file = os.path.join(cls.RUN_STATE_DIR, simulation_id, "run_state.json")
        if not os.path.exists(state_file):
            return None
        
        try:
            with open(state_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            state = SimulationRunState(
                simulation_id=simulation_id,
                runner_status=RunnerStatus(data.get("runner_status", "idle")),
                current_round=data.get("current_round", 0),
                total_rounds=data.get("total_rounds", 0),
                simulated_hours=data.get("simulated_hours", 0),
                total_simulation_hours=data.get("total_simulation_hours", 0),
                # 각 플랫폼 독립 라운드 수와 시간
                twitter_current_round=data.get("twitter_current_round", 0),
                reddit_current_round=data.get("reddit_current_round", 0),
                twitter_simulated_hours=data.get("twitter_simulated_hours", 0),
                reddit_simulated_hours=data.get("reddit_simulated_hours", 0),
                twitter_running=data.get("twitter_running", False),
                reddit_running=data.get("reddit_running", False),
                twitter_completed=data.get("twitter_completed", False),
                reddit_completed=data.get("reddit_completed", False),
                twitter_actions_count=data.get("twitter_actions_count", 0),
                reddit_actions_count=data.get("reddit_actions_count", 0),
                started_at=data.get("started_at"),
                updated_at=data.get("updated_at", datetime.now().isoformat()),
                completed_at=data.get("completed_at"),
                error=data.get("error"),
                process_pid=data.get("process_pid"),
            )
            
            # 최근 액션 로드
            actions_data = data.get("recent_actions", [])
            for a in actions_data:
                state.recent_actions.append(AgentAction(
                    round_num=a.get("round_num", 0),
                    timestamp=a.get("timestamp", ""),
                    platform=a.get("platform", ""),
                    agent_id=a.get("agent_id", 0),
                    agent_name=a.get("agent_name", ""),
                    action_type=a.get("action_type", ""),
                    action_args=a.get("action_args", {}),
                    result=a.get("result"),
                    success=a.get("success", True),
                ))
            
            return state
        except Exception as e:
            logger.error(f"로드실행상태실패: {str(e)}")
            return None
    
    @classmethod
    def _save_run_state(cls, state: SimulationRunState):
        """저장실행상태파일"""
        sim_dir = os.path.join(cls.RUN_STATE_DIR, state.simulation_id)
        os.makedirs(sim_dir, exist_ok=True)
        state_file = os.path.join(sim_dir, "run_state.json")
        
        data = state.to_detail_dict()
        
        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        cls._run_states[state.simulation_id] = state
    
    @classmethod
    def start_simulation(
        cls,
        simulation_id: str,
        platform: str = "parallel",  # twitter / reddit / parallel
        max_rounds: int = None,  # 최대 시뮬레이션 라운드 수 (선택, 너무 긴 시뮬레이션 시 중단)
        enable_graph_memory_update: bool = False,  # 활동 업데이트 Zep 그래프 여부
        graph_id: str = None  # Zep 그래프 ID (활성화 그래프 업데이트 시 필수)
    ) -> SimulationRunState:
        """
        시작시뮬레이션
        
        Args:
            simulation_id: 시뮬레이션ID
            platform: 실행플랫폼 (twitter/reddit/parallel)
            max_rounds: 최대 시뮬레이션 라운드 수 (선택, 너무 긴 시뮬레이션 시 중단)
            enable_graph_memory_update: 활동 동적으로 업데이트 Zep 그래프 여부
            graph_id: Zep 그래프 ID (활성화 그래프 업데이트 시 필수)
            
        Returns:
            SimulationRunState
        """
        # 이미 실행 중인지 확인
        existing = cls.get_run_state(simulation_id)
        if existing and existing.runner_status in [RunnerStatus.RUNNING, RunnerStatus.STARTING]:
            raise ValueError(f"시뮬레이션이미에서실행중: {simulation_id}")
        
        # 로드시뮬레이션설정
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")
        
        if not os.path.exists(config_path):
            raise ValueError(f"시뮬레이션 설정이 존재하지 않음, 먼저 /prepare 인터페이스를 호출하세요.")
        
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        # 초기화실행상태
        time_config = config.get("time_config", {})
        total_hours = time_config.get("total_simulation_hours", 72)
        minutes_per_round = time_config.get("minutes_per_round", 30)
        total_rounds = int(total_hours * 60 / minutes_per_round)
        
        # 만약 지정된 최대 라운드 수가 있다면, 시 중단
        if max_rounds is not None and max_rounds > 0:
            original_rounds = total_rounds
            total_rounds = min(total_rounds, max_rounds)
            if total_rounds < original_rounds:
                logger.info(f"라운드 수 이미 중단: {original_rounds} -> {total_rounds} (max_rounds={max_rounds})")
        
        state = SimulationRunState(
            simulation_id=simulation_id,
            runner_status=RunnerStatus.STARTING,
            total_rounds=total_rounds,
            total_simulation_hours=total_hours,
            started_at=datetime.now().isoformat(),
        )
        
        cls._save_run_state(state)
        
        # 만약 활성화 그래프 기억 업데이트가 필요하다면, 업데이트기를 생성
        if enable_graph_memory_update:
            if not graph_id:
                raise ValueError("활성화 그래프 기억 업데이트 시 반드시 graph_id를 제공해야 합니다.")
            
            try:
                ZepGraphMemoryManager.create_updater(simulation_id, graph_id)
                cls._graph_memory_enabled[simulation_id] = True
                logger.info(f"이미활성화그래프기억업데이트: simulation_id={simulation_id}, graph_id={graph_id}")
            except Exception as e:
                logger.error(f"그래프 기억 업데이트기 생성 실패: {e}")
                cls._graph_memory_enabled[simulation_id] = False
        else:
            cls._graph_memory_enabled[simulation_id] = False
        
        # 어떤 스크립트를 실행할지 확인 (스크립트는 backend/scripts/ 디렉토리에 위치)
        if platform == "twitter":
            script_name = "run_twitter_simulation.py"
            state.twitter_running = True
        elif platform == "reddit":
            script_name = "run_reddit_simulation.py"
            state.reddit_running = True
        else:
            script_name = "run_parallel_simulation.py"
            state.twitter_running = True
            state.reddit_running = True
        
        script_path = os.path.join(cls.SCRIPTS_DIR, script_name)
        
        if not os.path.exists(script_path):
            raise ValueError(f"스크립트가 존재하지 않음: {script_path}")
        
        # 생성액션큐
        action_queue = Queue()
        cls._action_queues[simulation_id] = action_queue
        
        # 시작시뮬레이션프로세스
        try:
            # 실행 명령 구축, 사용 완전 경로
            # 새로운 로그 구조：
            #   twitter/actions.jsonl - Twitter 액션로그
            #   reddit/actions.jsonl  - Reddit 액션로그
            #   simulation.log        - 메인프로세스로그
            
            cmd = [
                sys.executable,  # Python 인터프리터
                script_path,
                "--config", config_path,  # 사용 전체 설정 파일 경로
            ]
            
            # 만약 최대 라운드 수를 지정하면, 추가 명령행 파라미터
            if max_rounds is not None and max_rounds > 0:
                cmd.extend(["--max-rounds", str(max_rounds)])
            
            # 메인 로그 파일 생성, stdout/stderr 파이프 버퍼가 가득 차서 프로세스가 블록되는 것을 방지
            main_log_path = os.path.join(sim_dir, "simulation.log")
            main_log_file = open(main_log_path, 'w', encoding='utf-8')
            
            # 자식 프로세스 환경 변수를 설정하여 Windows에서 UTF-8 인코딩을 사용 보장
            # 이를 통해 서드파티 라이브러리(예: OASIS)가 파일을 읽을 때 인코딩을 지정하지 않은 문제를 수정
            env = os.environ.copy()
            env['PYTHONUTF8'] = '1'  # Python 3.7+ 지원, open() 기본값으로 UTF-8 사용
            env['PYTHONIOENCODING'] = 'utf-8'  # 보장 stdout/stderr 사용 UTF-8
            
            # 작업 디렉토리를 시뮬레이션 디렉토리로 설정 (데이터베이스 등 파일 생성에서 이)
            # start_new_session=True를 사용하여 새로운 프로세스 그룹 생성, os.killpg를 통해 관련 자식 프로세스를 종료 보장
            process = subprocess.Popen(
                cmd,
                cwd=sim_dir,
                stdout=main_log_file,
                stderr=subprocess.STDOUT,  # stderr도 같은 파일에 기록
                text=True,
                encoding='utf-8',  # 인코딩을 명시적으로 지정
                bufsize=1,
                env=env,  # UTF-8 설정을 포함한 환경 변수를 전달
                start_new_session=True,  # 새로운 프로세스 그룹 생성, 서버 종료 시 관련 프로세스를 종료 보장
            )
            
            # 파일 핸들을 저장하여 나중에 닫기
            cls._stdout_files[simulation_id] = main_log_file
            cls._stderr_files[simulation_id] = None  # 더 이상 개별 stderr 필요 없음
            
            state.process_pid = process.pid
            state.runner_status = RunnerStatus.RUNNING
            cls._processes[simulation_id] = process
            cls._save_run_state(state)
            
            # 모니터링 스레드 시작
            monitor_thread = threading.Thread(
                target=cls._monitor_simulation,
                args=(simulation_id,),
                daemon=True
            )
            monitor_thread.start()
            cls._monitor_threads[simulation_id] = monitor_thread
            
            logger.info(f"시뮬레이션시작 성공: {simulation_id}, pid={process.pid}, platform={platform}")
            
        except Exception as e:
            state.runner_status = RunnerStatus.FAILED
            state.error = str(e)
            cls._save_run_state(state)
            raise
        
        return state
    
    @classmethod
    def _monitor_simulation(cls, simulation_id: str):
        """시뮬레이션 프로세스를 모니터링하고 액션 로그를 파싱"""
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        
        # 새로운 로그 구조: 플랫폼별 액션 로그
        twitter_actions_log = os.path.join(sim_dir, "twitter", "actions.jsonl")
        reddit_actions_log = os.path.join(sim_dir, "reddit", "actions.jsonl")
        
        process = cls._processes.get(simulation_id)
        state = cls.get_run_state(simulation_id)
        
        if not process or not state:
            return
        
        twitter_position = 0
        reddit_position = 0
        
        try:
            while process.poll() is None:  # 프로세스가 여전히 실행 중
                # Twitter 액션 로그 읽기
                if os.path.exists(twitter_actions_log):
                    twitter_position = cls._read_action_log(
                        twitter_actions_log, twitter_position, state, "twitter"
                    )
                
                # Reddit 액션 로그 읽기
                if os.path.exists(reddit_actions_log):
                    reddit_position = cls._read_action_log(
                        reddit_actions_log, reddit_position, state, "reddit"
                    )
                
                # 업데이트상태
                cls._save_run_state(state)
                time.sleep(2)
            
            # 프로세스 종료 후, 마지막으로 로그를 한 번 더 읽기
            if os.path.exists(twitter_actions_log):
                cls._read_action_log(twitter_actions_log, twitter_position, state, "twitter")
            if os.path.exists(reddit_actions_log):
                cls._read_action_log(reddit_actions_log, reddit_position, state, "reddit")
            
            # 프로세스 종료
            exit_code = process.returncode
            
            if exit_code == 0:
                state.runner_status = RunnerStatus.COMPLETED
                state.completed_at = datetime.now().isoformat()
                logger.info(f"시뮬레이션완료: {simulation_id}")
            else:
                state.runner_status = RunnerStatus.FAILED
                # 메인 로그 파일에서 오류 정보 읽기
                main_log_path = os.path.join(sim_dir, "simulation.log")
                error_info = ""
                try:
                    if os.path.exists(main_log_path):
                        with open(main_log_path, 'r', encoding='utf-8') as f:
                            error_info = f.read()[-2000:]  # 마지막 2000자 가져오기
                except Exception:
                    pass
                state.error = f"프로세스 종료 코드: {exit_code}, 오류: {error_info}"
                logger.error(f"시뮬레이션실패: {simulation_id}, error={state.error}")
            
            state.twitter_running = False
            state.reddit_running = False
            cls._save_run_state(state)
            
        except Exception as e:
            logger.error(f"모니터링 스레드 예외: {simulation_id}, error={str(e)}")
            state.runner_status = RunnerStatus.FAILED
            state.error = str(e)
            cls._save_run_state(state)
        
        finally:
            # 중지 그래프 기억 업데이트기
            if cls._graph_memory_enabled.get(simulation_id, False):
                try:
                    ZepGraphMemoryManager.stop_updater(simulation_id)
                    logger.info(f"이미중지그래프기억업데이트: simulation_id={simulation_id}")
                except Exception as e:
                    logger.error(f"중지 그래프 기억 업데이트기 실패: {e}")
                cls._graph_memory_enabled.pop(simulation_id, None)
            
            # 프로세스 자원 정리
            cls._processes.pop(simulation_id, None)
            cls._action_queues.pop(simulation_id, None)
            
            # 로그 파일 핸들 닫기
            if simulation_id in cls._stdout_files:
                try:
                    cls._stdout_files[simulation_id].close()
                except Exception:
                    pass
                cls._stdout_files.pop(simulation_id, None)
            if simulation_id in cls._stderr_files and cls._stderr_files[simulation_id]:
                try:
                    cls._stderr_files[simulation_id].close()
                except Exception:
                    pass
                cls._stderr_files.pop(simulation_id, None)
    
    @classmethod
    def _read_action_log(
        cls, 
        log_path: str, 
        position: int, 
        state: SimulationRunState,
        platform: str
    ) -> int:
        """
        액션 로그 파일 읽기
        
        Args:
            log_path: 로그파일경로
            position: 마지막 읽기 위치
            state: 실행 상태 객체
            platform: 플랫폼이름 (twitter/reddit)
            
        Returns:
            새로운 읽기 위치
        """
        # 비활성화 그래프 기억 업데이트 확인
        graph_memory_enabled = cls._graph_memory_enabled.get(state.simulation_id, False)
        graph_updater = None
        if graph_memory_enabled:
            graph_updater = ZepGraphMemoryManager.get_updater(state.simulation_id)
        
        try:
            with open(log_path, 'r', encoding='utf-8') as f:
                f.seek(position)
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            action_data = json.loads(line)
                            
                            # 처리 이벤트 유형의 개수
                            if "event_type" in action_data:
                                event_type = action_data.get("event_type")
                                
                                # simulation_end 이벤트 감지, 플랫폼 완료 표시
                                if event_type == "simulation_end":
                                    if platform == "twitter":
                                        state.twitter_completed = True
                                        state.twitter_running = False
                                        logger.info(f"Twitter 시뮬레이션완료: {state.simulation_id}, total_rounds={action_data.get('total_rounds')}, total_actions={action_data.get('total_actions')}")
                                    elif platform == "reddit":
                                        state.reddit_completed = True
                                        state.reddit_running = False
                                        logger.info(f"Reddit 시뮬레이션완료: {state.simulation_id}, total_rounds={action_data.get('total_rounds')}, total_actions={action_data.get('total_actions')}")
                                    
                                    # 활성화된 플랫폼이 모두 완료되었는지 확인
                                    # 만약 하나의 플랫폼만 실행하면, 그 플랫폼만 확인
                                    # 만약 두 개의 플랫폼을 실행하면, 두 개 모두 완료해야 함
                                    all_completed = cls._check_all_platforms_completed(state)
                                    if all_completed:
                                        state.runner_status = RunnerStatus.COMPLETED
                                        state.completed_at = datetime.now().isoformat()
                                        logger.info(f"모든 플랫폼 시뮬레이션 완료: {state.simulation_id}")
                                
                                # 업데이트 라운드 정보 (부터 round_end 이벤트)
                                elif event_type == "round_end":
                                    round_num = action_data.get("round", 0)
                                    simulated_hours = action_data.get("simulated_hours", 0)
                                    
                                    # 업데이트 각 플랫폼 독립의 라운드와 시간
                                    if platform == "twitter":
                                        if round_num > state.twitter_current_round:
                                            state.twitter_current_round = round_num
                                        state.twitter_simulated_hours = simulated_hours
                                    elif platform == "reddit":
                                        if round_num > state.reddit_current_round:
                                            state.reddit_current_round = round_num
                                        state.reddit_simulated_hours = simulated_hours
                                    
                                    # 전체 라운드 수는 두 개 플랫폼의 최대값을 취함
                                    if round_num > state.current_round:
                                        state.current_round = round_num
                                    # 전체 시간은 두 개 플랫폼의 최대값을 취함
                                    state.simulated_hours = max(state.twitter_simulated_hours, state.reddit_simulated_hours)
                                
                                continue
                            
                            action = AgentAction(
                                round_num=action_data.get("round", 0),
                                timestamp=action_data.get("timestamp", datetime.now().isoformat()),
                                platform=platform,
                                agent_id=action_data.get("agent_id", 0),
                                agent_name=action_data.get("agent_name", ""),
                                action_type=action_data.get("action_type", ""),
                                action_args=action_data.get("action_args", {}),
                                result=action_data.get("result"),
                                success=action_data.get("success", True),
                            )
                            state.add_action(action)
                            
                            # 업데이트 라운드 수
                            if action.round_num and action.round_num > state.current_round:
                                state.current_round = action.round_num
                            
                            # 만약활성화그래프기억업데이트，활동전송Zep
                            if graph_updater:
                                graph_updater.add_activity_from_dict(action_data, platform)
                            
                        except json.JSONDecodeError:
                            pass
                return f.tell()
        except Exception as e:
            logger.warning(f"액션 로그 읽기 실패: {log_path}, error={e}")
            return position
    
    @classmethod
    def _check_all_platforms_completed(cls, state: SimulationRunState) -> bool:
        """
        활성화된 플랫폼이 모두 시뮬레이션을 완료했는지 확인
        
        모든 활성화된 플랫폼이 actions.jsonl 파일에 존재하는지 확인
        
        Returns:
            True 만약 활성화된 플랫폼이 모두 완료됨
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, state.simulation_id)
        twitter_log = os.path.join(sim_dir, "twitter", "actions.jsonl")
        reddit_log = os.path.join(sim_dir, "reddit", "actions.jsonl")
        
        # 어떤 플랫폼이 활성화되었는지 확인 (파일에 존재하는지 판단)
        twitter_enabled = os.path.exists(twitter_log)
        reddit_enabled = os.path.exists(reddit_log)
        
        # 만약 플랫폼이 활성화되었지만 완료되지 않았다면, False 반환
        if twitter_enabled and not state.twitter_completed:
            return False
        if reddit_enabled and not state.reddit_completed:
            return False
        
        # 최소 하나의 플랫폼이 활성화되고 완료됨
        return twitter_enabled or reddit_enabled
    
    @classmethod
    def _terminate_process(cls, process: subprocess.Popen, simulation_id: str, timeout: int = 10):
        """
        모든 플랫폼의 프로세스 및 그 자식 프로세스 종료
        
        Args:
            process: 종료할 프로세스
            simulation_id: 시뮬레이션ID（용로그）
            timeout: 프로세스 종료 대기 시간 초과 시간 (초)
        """
        if IS_WINDOWS:
            # Windows: taskkill 명령어로 프로세스 트리 종료
            # /F = 강제 종료, /T = 프로세스 트리 종료 (자식 프로세스 포함)
            logger.info(f"프로세스 트리 종료 (Windows): simulation={simulation_id}, pid={process.pid}")
            try:
                # 먼저 우아하게 종료 시도
                subprocess.run(
                    ['taskkill', '/PID', str(process.pid), '/T'],
                    capture_output=True,
                    timeout=5
                )
                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    # 강제 종료
                    logger.warning(f"프로세스가 응답하지 않음, 강제 종료: {simulation_id}")
                    subprocess.run(
                        ['taskkill', '/F', '/PID', str(process.pid), '/T'],
                        capture_output=True,
                        timeout=5
                    )
                    process.wait(timeout=5)
            except Exception as e:
                logger.warning(f"taskkill 실패，시도 terminate: {e}")
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
        else:
            # Unix: 프로세스 그룹 종료
            # start_new_session=True로 인해 프로세스 그룹 ID는 메인 프로세스 PID와 같음
            pgid = os.getpgid(process.pid)
            logger.info(f"프로세스 그룹 종료 (Unix): simulation={simulation_id}, pgid={pgid}")
            
            # 먼저 SIGTERM 신호를 전체 프로세스 그룹에 전송
            os.killpg(pgid, signal.SIGTERM)
            
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                # 만약 시간 초과 후에도 종료되지 않았다면, SIGKILL 신호를 강제로 전송
                logger.warning(f"프로세스 그룹이 SIGTERM에 응답하지 않음, 강제 종료: {simulation_id}")
                os.killpg(pgid, signal.SIGKILL)
                process.wait(timeout=5)
    
    @classmethod
    def stop_simulation(cls, simulation_id: str) -> SimulationRunState:
        """중지시뮬레이션"""
        state = cls.get_run_state(simulation_id)
        if not state:
            raise ValueError(f"시뮬레이션존재하지 않음: {simulation_id}")
        
        if state.runner_status not in [RunnerStatus.RUNNING, RunnerStatus.PAUSED]:
            raise ValueError(f"시뮬레이션이 실행되지 않음: {simulation_id}, status={state.runner_status}")
        
        state.runner_status = RunnerStatus.STOPPING
        cls._save_run_state(state)
        
        # 프로세스 종료
        process = cls._processes.get(simulation_id)
        if process and process.poll() is None:
            try:
                cls._terminate_process(process, simulation_id)
            except ProcessLookupError:
                # 프로세스가 이미 존재하지 않음
                pass
            except Exception as e:
                logger.error(f"프로세스 그룹 종료 실패: {simulation_id}, error={e}")
                # 직접 프로세스를 종료로 되돌리기
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except Exception:
                    process.kill()
        
        state.runner_status = RunnerStatus.STOPPED
        state.twitter_running = False
        state.reddit_running = False
        state.completed_at = datetime.now().isoformat()
        cls._save_run_state(state)
        
        # 그래프 기억 업데이트기 중지
        if cls._graph_memory_enabled.get(simulation_id, False):
            try:
                ZepGraphMemoryManager.stop_updater(simulation_id)
                logger.info(f"이미중지그래프기억업데이트: simulation_id={simulation_id}")
            except Exception as e:
                logger.error(f"그래프 기억 업데이트기 중지 실패: {e}")
            cls._graph_memory_enabled.pop(simulation_id, None)
        
        logger.info(f"시뮬레이션이미중지: {simulation_id}")
        return state
    
    @classmethod
    def _read_actions_from_file(
        cls,
        file_path: str,
        default_platform: Optional[str] = None,
        platform_filter: Optional[str] = None,
        agent_id: Optional[int] = None,
        round_num: Optional[int] = None
    ) -> List[AgentAction]:
        """
        단일 액션 파일에서 액션 읽기
        
        Args:
            file_path: 액션로그파일경로
            default_platform: 기본값 플랫폼 (액션 기록 중 platform 필드가 없으면 사용)
            platform_filter: 필터플랫폼
            agent_id: 필터 Agent ID
            round_num: 필터 라운드 수
        """
        if not os.path.exists(file_path):
            return []
        
        actions = []
        
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                try:
                    data = json.loads(line)
                    
                    # 비액션 기록(예: simulation_start, round_start, round_end 등의 이벤트)
                    if "event_type" in data:
                        continue
                    
                    # 없으면 agent_id의 기록(비 Agent 액션)
                    if "agent_id" not in data:
                        continue
                    
                    # 가져오기 플랫폼: 우선 사용 기록의 platform, 그렇지 않으면 기본값 플랫폼 사용
                    record_platform = data.get("platform") or default_platform or ""
                    
                    # 필터
                    if platform_filter and record_platform != platform_filter:
                        continue
                    if agent_id is not None and data.get("agent_id") != agent_id:
                        continue
                    if round_num is not None and data.get("round") != round_num:
                        continue
                    
                    actions.append(AgentAction(
                        round_num=data.get("round", 0),
                        timestamp=data.get("timestamp", ""),
                        platform=record_platform,
                        agent_id=data.get("agent_id", 0),
                        agent_name=data.get("agent_name", ""),
                        action_type=data.get("action_type", ""),
                        action_args=data.get("action_args", {}),
                        result=data.get("result"),
                        success=data.get("success", True),
                    ))
                    
                except json.JSONDecodeError:
                    continue
        
        return actions
    
    @classmethod
    def get_all_actions(
        cls,
        simulation_id: str,
        platform: Optional[str] = None,
        agent_id: Optional[int] = None,
        round_num: Optional[int] = None
    ) -> List[AgentAction]:
        """
        가져오기 소 플랫폼의 완전 액션 역사(무 페이지 제한)
        
        Args:
            simulation_id: 시뮬레이션ID
            platform: 필터플랫폼（twitter/reddit）
            agent_id: 필터Agent
            round_num: 필터 라운드 차수
            
        Returns:
            완전 액션 목록(시간 정렬, 새로운 것에서 앞)
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        actions = []
        
        # Twitter 액션 파일 읽기(파일 경로에 따라 자동 설정 platform으로 twitter)
        twitter_actions_log = os.path.join(sim_dir, "twitter", "actions.jsonl")
        if not platform or platform == "twitter":
            actions.extend(cls._read_actions_from_file(
                twitter_actions_log,
                default_platform="twitter",  # 자동으로 채우기 platform 필드
                platform_filter=platform,
                agent_id=agent_id, 
                round_num=round_num
            ))
        
        # Reddit 액션 파일 읽기(파일 경로에 따라 자동 설정 platform으로 reddit)
        reddit_actions_log = os.path.join(sim_dir, "reddit", "actions.jsonl")
        if not platform or platform == "reddit":
            actions.extend(cls._read_actions_from_file(
                reddit_actions_log,
                default_platform="reddit",  # 자동으로 채우기 platform 필드
                platform_filter=platform,
                agent_id=agent_id,
                round_num=round_num
            ))
        
        # 만약 분 플랫폼 파일이 존재하지 않음, 시도 읽기 구형 단일 파일 형식
        if not actions:
            actions_log = os.path.join(sim_dir, "actions.jsonl")
            actions = cls._read_actions_from_file(
                actions_log,
                default_platform=None,  # 구형식 파일 중에 있어야 할 platform 필드
                platform_filter=platform,
                agent_id=agent_id,
                round_num=round_num
            )
        
        # 시간 정렬(새로운 것에서 앞)
        actions.sort(key=lambda x: x.timestamp, reverse=True)
        
        return actions
    
    @classmethod
    def get_actions(
        cls,
        simulation_id: str,
        limit: int = 100,
        offset: int = 0,
        platform: Optional[str] = None,
        agent_id: Optional[int] = None,
        round_num: Optional[int] = None
    ) -> List[AgentAction]:
        """
        가져오기 액션 역사(포함 페이지)
        
        Args:
            simulation_id: 시뮬레이션ID
            limit: 돌아가기 수량 제한
            offset: 편차
            platform: 필터플랫폼
            agent_id: 필터Agent
            round_num: 필터 라운드 차수
            
        Returns:
            액션목록
        """
        actions = cls.get_all_actions(
            simulation_id=simulation_id,
            platform=platform,
            agent_id=agent_id,
            round_num=round_num
        )
        
        # 페이지
        return actions[offset:offset + limit]
    
    @classmethod
    def get_timeline(
        cls,
        simulation_id: str,
        start_round: int = 0,
        end_round: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        가져오기 시뮬레이션 시간선(라운드 차수에 따라 총합)
        
        Args:
            simulation_id: 시뮬레이션ID
            start_round: 시작 라운드 차수
            end_round: 종료 라운드 차수
            
        Returns:
            각 라운드의 총합 정보
        """
        actions = cls.get_actions(simulation_id, limit=10000)
        
        # 라운드 차수로 그룹화
        rounds: Dict[int, Dict[str, Any]] = {}
        
        for action in actions:
            round_num = action.round_num
            
            if round_num < start_round:
                continue
            if end_round is not None and round_num > end_round:
                continue
            
            if round_num not in rounds:
                rounds[round_num] = {
                    "round_num": round_num,
                    "twitter_actions": 0,
                    "reddit_actions": 0,
                    "active_agents": set(),
                    "action_types": {},
                    "first_action_time": action.timestamp,
                    "last_action_time": action.timestamp,
                }
            
            r = rounds[round_num]
            
            if action.platform == "twitter":
                r["twitter_actions"] += 1
            else:
                r["reddit_actions"] += 1
            
            r["active_agents"].add(action.agent_id)
            r["action_types"][action.action_type] = r["action_types"].get(action.action_type, 0) + 1
            r["last_action_time"] = action.timestamp
        
        # 목록으로 변환
        result = []
        for round_num in sorted(rounds.keys()):
            r = rounds[round_num]
            result.append({
                "round_num": round_num,
                "twitter_actions": r["twitter_actions"],
                "reddit_actions": r["reddit_actions"],
                "total_actions": r["twitter_actions"] + r["reddit_actions"],
                "active_agents_count": len(r["active_agents"]),
                "active_agents": list(r["active_agents"]),
                "action_types": r["action_types"],
                "first_action_time": r["first_action_time"],
                "last_action_time": r["last_action_time"],
            })
        
        return result
    
    @classmethod
    def get_agent_stats(cls, simulation_id: str) -> List[Dict[str, Any]]:
        """
        가져오기 각 Agent의 통계 정보
        
        Returns:
            Agent통계목록
        """
        actions = cls.get_actions(simulation_id, limit=10000)
        
        agent_stats: Dict[int, Dict[str, Any]] = {}
        
        for action in actions:
            agent_id = action.agent_id
            
            if agent_id not in agent_stats:
                agent_stats[agent_id] = {
                    "agent_id": agent_id,
                    "agent_name": action.agent_name,
                    "total_actions": 0,
                    "twitter_actions": 0,
                    "reddit_actions": 0,
                    "action_types": {},
                    "first_action_time": action.timestamp,
                    "last_action_time": action.timestamp,
                }
            
            stats = agent_stats[agent_id]
            stats["total_actions"] += 1
            
            if action.platform == "twitter":
                stats["twitter_actions"] += 1
            else:
                stats["reddit_actions"] += 1
            
            stats["action_types"][action.action_type] = stats["action_types"].get(action.action_type, 0) + 1
            stats["last_action_time"] = action.timestamp
        
        # 총 액션 수로 정렬
        result = sorted(agent_stats.values(), key=lambda x: x["total_actions"], reverse=True)
        
        return result
    
    @classmethod
    def cleanup_simulation_logs(cls, simulation_id: str) -> Dict[str, Any]:
        """
        정리 시뮬레이션의 실행 로그(강제로 재시뮬레이션 시작)
        
        삭제할 파일:
        - run_state.json
        - twitter/actions.jsonl
        - reddit/actions.jsonl
        - simulation.log
        - stdout.log / stderr.log
        - twitter_simulation.db（시뮬레이션데이터베이스）
        - reddit_simulation.db（시뮬레이션데이터베이스）
        - env_status.json（환경상태）
        
        주의: 삭제하지 않을 설정 파일(simulation_config.json)과 profile 파일
        
        Args:
            simulation_id: 시뮬레이션ID
            
        Returns:
            정리결과정보
        """
        import shutil
        
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        
        if not os.path.exists(sim_dir):
            return {"success": True, "message": "시뮬레이션 디렉토리 존재하지 않음, 정리할 필요 없음"}
        
        cleaned_files = []
        errors = []
        
        # 삭제할 파일 목록(데이터베이스 파일 포함)
        files_to_delete = [
            "run_state.json",
            "simulation.log",
            "stdout.log",
            "stderr.log",
            "twitter_simulation.db",  # Twitter 플랫폼데이터베이스
            "reddit_simulation.db",   # Reddit 플랫폼데이터베이스
            "env_status.json",        # 환경상태파일
        ]
        
        # 삭제의디렉토리목록（포함액션로그）
        dirs_to_clean = ["twitter", "reddit"]
        
        # 삭제파일
        for filename in files_to_delete:
            file_path = os.path.join(sim_dir, filename)
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    cleaned_files.append(filename)
                except Exception as e:
                    errors.append(f"삭제 {filename} 실패: {str(e)}")
        
        # 정리플랫폼디렉토리의액션로그
        for dir_name in dirs_to_clean:
            dir_path = os.path.join(sim_dir, dir_name)
            if os.path.exists(dir_path):
                actions_file = os.path.join(dir_path, "actions.jsonl")
                if os.path.exists(actions_file):
                    try:
                        os.remove(actions_file)
                        cleaned_files.append(f"{dir_name}/actions.jsonl")
                    except Exception as e:
                        errors.append(f"삭제 {dir_name}/actions.jsonl 실패: {str(e)}")
        
        # 메모리의 실행 상태 정리
        if simulation_id in cls._run_states:
            del cls._run_states[simulation_id]
        
        logger.info(f"정리시뮬레이션로그완료: {simulation_id}, 삭제파일: {cleaned_files}")
        
        return {
            "success": len(errors) == 0,
            "cleaned_files": cleaned_files,
            "errors": errors if errors else None
        }
    
    # 중복 정리를 방지하는 플래그
    _cleanup_done = False
    
    @classmethod
    def cleanup_all_simulations(cls):
        """
        정리할 시뮬레이션 프로세스
        
        서버 닫기 시 호출, 자식 프로세스 종료 보장
        """
        # 중복 정리 방지
        if cls._cleanup_done:
            return
        cls._cleanup_done = True
        
        # 정리할 내용이 필요한지 확인(빈 프로세스의 프로세스 출력 무용 로그 방지)
        has_processes = bool(cls._processes)
        has_updaters = bool(cls._graph_memory_enabled)
        
        if not has_processes and not has_updaters:
            return  # 정리할 내용이 없으면, 조용히 돌아가기
        
        logger.info("진행 중: 정리할 시뮬레이션 프로세스...")
        
        # 먼저 그래프 기억 업데이트기를 중지(stop_all 내부 출력 로그)
        try:
            ZepGraphMemoryManager.stop_all()
        except Exception as e:
            logger.error(f"중지 그래프 기억 업데이트 실패: {e}")
        cls._graph_memory_enabled.clear()
        
        # 복사 딕셔너리하여 반복 시 수정 방지
        processes = list(cls._processes.items())
        
        for simulation_id, process in processes:
            try:
                if process.poll() is None:  # 프로세스가 여전히 실행 중
                    logger.info(f"시뮬레이션 프로세스 종료: {simulation_id}, pid={process.pid}")
                    
                    try:
                        # 크로스 플랫폼의 프로세스 종료 메서드 사용
                        cls._terminate_process(process, simulation_id, timeout=5)
                    except (ProcessLookupError, OSError):
                        # 프로세스가 이미 존재하지 않음, 직접 종료 시도
                        try:
                            process.terminate()
                            process.wait(timeout=3)
                        except Exception:
                            process.kill()
                    
                    # 업데이트 run_state.json
                    state = cls.get_run_state(simulation_id)
                    if state:
                        state.runner_status = RunnerStatus.STOPPED
                        state.twitter_running = False
                        state.reddit_running = False
                        state.completed_at = datetime.now().isoformat()
                        state.error = "서버 종료, 시뮬레이션 종료"
                        cls._save_run_state(state)
                    
                    # 동시에 state.json 업데이트, 상태를 stopped로 설정
                    try:
                        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
                        state_file = os.path.join(sim_dir, "state.json")
                        logger.info(f"시도업데이트 state.json: {state_file}")
                        if os.path.exists(state_file):
                            with open(state_file, 'r', encoding='utf-8') as f:
                                state_data = json.load(f)
                            state_data['status'] = 'stopped'
                            state_data['updated_at'] = datetime.now().isoformat()
                            with open(state_file, 'w', encoding='utf-8') as f:
                                json.dump(state_data, f, indent=2, ensure_ascii=False)
                            logger.info(f"이미업데이트 state.json 상태로 stopped: {simulation_id}")
                        else:
                            logger.warning(f"state.json 존재하지 않음: {state_file}")
                    except Exception as state_err:
                        logger.warning(f"업데이트 state.json 실패: {simulation_id}, error={state_err}")
                        
            except Exception as e:
                logger.error(f"정리프로세스실패: {simulation_id}, error={e}")
        
        # 파일 핸들 정리
        for simulation_id, file_handle in list(cls._stdout_files.items()):
            try:
                if file_handle:
                    file_handle.close()
            except Exception:
                pass
        cls._stdout_files.clear()
        
        for simulation_id, file_handle in list(cls._stderr_files.items()):
            try:
                if file_handle:
                    file_handle.close()
            except Exception:
                pass
        cls._stderr_files.clear()
        
        # 메모리 상태 정리
        cls._processes.clear()
        cls._action_queues.clear()
        
        logger.info("시뮬레이션프로세스정리완료")
    
    @classmethod
    def register_cleanup(cls):
        """
        등록정리함수
        
        Flask 앱 시작 시 호출, 서버 종료 시 시뮬레이션 프로세스 정리 보장
        """
        global _cleanup_registered
        
        if _cleanup_registered:
            return
        
        # Flask debug 모드에서, reloader 자식 프로세스 중 등록 정리 (실제 실행 앱의 프로세스)
        # WERKZEUG_RUN_MAIN=true는 reloader 자식 프로세스를 나타냄
        # 만약 debug 모드가 아니면, 이 환경 변수가 없으면 등록 필요
        is_reloader_process = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
        is_debug_mode = os.environ.get('FLASK_DEBUG') == '1' or os.environ.get('WERKZEUG_RUN_MAIN') is not None
        
        # debug 모드에서, reloader 자식 프로세스 중 등록; 비 debug 모드에서는 항상 등록
        if is_debug_mode and not is_reloader_process:
            _cleanup_registered = True  # 이미 등록된 표시, 자식 프로세스의 재시도를 방지
            return
        
        # 원래의 신호 처리기 저장
        original_sigint = signal.getsignal(signal.SIGINT)
        original_sigterm = signal.getsignal(signal.SIGTERM)
        # SIGHUP은 Unix 시스템에서만 존재 (macOS/Linux), Windows에서는 없음
        original_sighup = None
        has_sighup = hasattr(signal, 'SIGHUP')
        if has_sighup:
            original_sighup = signal.getsignal(signal.SIGHUP)
        
        def cleanup_handler(signum=None, frame=None):
            """신호 처리기: 먼저 시뮬레이션 프로세스 정리, 그 다음 원래 처리기 호출"""
            # 프로세스가 정리할 필요가 있을 때만 로그 출력
            if cls._processes or cls._graph_memory_enabled:
                logger.info(f"신호 {signum} 수신, 정리 시작...")
            cls.cleanup_all_simulations()
            
            # 원래의 신호 처리기 호출, Flask 정상 종료
            if signum == signal.SIGINT and callable(original_sigint):
                original_sigint(signum, frame)
            elif signum == signal.SIGTERM and callable(original_sigterm):
                original_sigterm(signum, frame)
            elif has_sighup and signum == signal.SIGHUP:
                # SIGHUP: 터미널 종료 시 전송
                if callable(original_sighup):
                    original_sighup(signum, frame)
                else:
                    # 기본값: 정상 종료
                    sys.exit(0)
            else:
                # 만약 원래 처리기가 호출되지 않으면 (예: SIG_DFL), 기본값 사용
                raise KeyboardInterrupt
        
        # atexit 처리기 등록 (백업으로 사용)
        atexit.register(cls.cleanup_all_simulations)
        
        # 신호 처리기 등록 (오직 메인 스레드에서만)
        try:
            # SIGTERM: kill 명령의 기본 신호
            signal.signal(signal.SIGTERM, cleanup_handler)
            # SIGINT: Ctrl+C
            signal.signal(signal.SIGINT, cleanup_handler)
            # SIGHUP: 터미널 종료 (오직 Unix 시스템에서)
            if has_sighup:
                signal.signal(signal.SIGHUP, cleanup_handler)
        except ValueError:
            # 메인 스레드에서만, atexit만 사용
            logger.warning("신호 처리기를 등록할 수 없습니다 (메인 스레드에서), atexit만 사용")
        
        _cleanup_registered = True
    
    @classmethod
    def get_running_simulations(cls) -> List[str]:
        """
        현재 진행 중: 실행 중인 시뮬레이션 ID 목록
        """
        running = []
        for sim_id, process in cls._processes.items():
            if process.poll() is None:
                running.append(sim_id)
        return running
    
    # ============== Interview 기능 ==============
    
    @classmethod
    def check_env_alive(cls, simulation_id: str) -> bool:
        """
        시뮬레이션 환경이 살아 있는지 확인 (인터뷰 명령 수신 대기)

        Args:
            simulation_id: 시뮬레이션ID

        Returns:
            True는 환경이 살아 있음을 나타내고, False는 환경이 이미 종료됨을 나타냄
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        if not os.path.exists(sim_dir):
            return False

        ipc_client = SimulationIPCClient(sim_dir)
        return ipc_client.check_env_alive()

    @classmethod
    def get_env_status_detail(cls, simulation_id: str) -> Dict[str, Any]:
        """
        시뮬레이션 환경의 상세 상태 정보 가져오기

        Args:
            simulation_id: 시뮬레이션ID

        Returns:
            상태 상세 딕셔너리, 포함 status, twitter_available, reddit_available, timestamp
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        status_file = os.path.join(sim_dir, "env_status.json")
        
        default_status = {
            "status": "stopped",
            "twitter_available": False,
            "reddit_available": False,
            "timestamp": None
        }
        
        if not os.path.exists(status_file):
            return default_status
        
        try:
            with open(status_file, 'r', encoding='utf-8') as f:
                status = json.load(f)
            return {
                "status": status.get("status", "stopped"),
                "twitter_available": status.get("twitter_available", False),
                "reddit_available": status.get("reddit_available", False),
                "timestamp": status.get("timestamp")
            }
        except (json.JSONDecodeError, OSError):
            return default_status

    @classmethod
    def interview_agent(
        cls,
        simulation_id: str,
        agent_id: int,
        prompt: str,
        platform: str = None,
        timeout: float = 60.0
    ) -> Dict[str, Any]:
        """
        단일 에이전트 인터뷰

        Args:
            simulation_id: 시뮬레이션ID
            agent_id: Agent ID
            prompt: 인터뷰 질문
            platform: 지정 플랫폼 (선택)
                - "twitter": 오직 Twitter 플랫폼에서 인터뷰
                - "reddit": Reddit 플랫폼만 인터뷰
                - None: 듀얼 플랫폼 시뮬레이션 시 동시에 두 개 플랫폼 인터뷰, 결과 통합
            timeout: 시간 초과시간（초）

        Returns:
            인터뷰 결과 딕셔너리

        Raises:
            ValueError: 시뮬레이션이 존재하지 않거나 환경이 실행되지 않음
            TimeoutError: 대기응답시간 초과
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        if not os.path.exists(sim_dir):
            raise ValueError(f"시뮬레이션존재하지 않음: {simulation_id}")

        ipc_client = SimulationIPCClient(sim_dir)

        if not ipc_client.check_env_alive():
            raise ValueError(f"시뮬레이션 환경이 실행되지 않거나 이미 닫힘, 인터뷰 실행 불가: {simulation_id}")

        logger.info(f"전송Interview명령: simulation_id={simulation_id}, agent_id={agent_id}, platform={platform}")

        response = ipc_client.send_interview(
            agent_id=agent_id,
            prompt=prompt,
            platform=platform,
            timeout=timeout
        )

        if response.status.value == "completed":
            return {
                "success": True,
                "agent_id": agent_id,
                "prompt": prompt,
                "result": response.result,
                "timestamp": response.timestamp
            }
        else:
            return {
                "success": False,
                "agent_id": agent_id,
                "prompt": prompt,
                "error": response.error,
                "timestamp": response.timestamp
            }
    
    @classmethod
    def interview_agents_batch(
        cls,
        simulation_id: str,
        interviews: List[Dict[str, Any]],
        platform: str = None,
        timeout: float = 120.0
    ) -> Dict[str, Any]:
        """
        여러 개의 Agent에 대해 배치 인터뷰

        Args:
            simulation_id: 시뮬레이션ID
            interviews: 인터뷰 목록, 각 요소는 {"agent_id": int, "prompt": str, "platform": str(선택)}
            platform: 기본값 플랫폼(선택, 각 인터뷰 항목의 platform을 덮어씀)
                - "twitter": 기본값으로 Twitter 플랫폼만 인터뷰
                - "reddit": 기본값으로 Reddit 플랫폼만 인터뷰
                - None: 듀얼 플랫폼 시뮬레이션 시 각 Agent가 동시에 두 개 플랫폼 인터뷰
            timeout: 시간 초과시간（초）

        Returns:
            배치 인터뷰 결과 딕셔너리

        Raises:
            ValueError: 시뮬레이션이 존재하지 않거나 환경이 실행되지 않음
            TimeoutError: 대기응답시간 초과
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        if not os.path.exists(sim_dir):
            raise ValueError(f"시뮬레이션존재하지 않음: {simulation_id}")

        ipc_client = SimulationIPCClient(sim_dir)

        if not ipc_client.check_env_alive():
            raise ValueError(f"시뮬레이션 환경이 실행되지 않거나 이미 닫힘, 인터뷰 실행 불가: {simulation_id}")

        logger.info(f"배치 인터뷰 명령 전송: simulation_id={simulation_id}, count={len(interviews)}, platform={platform}")

        response = ipc_client.send_batch_interview(
            interviews=interviews,
            platform=platform,
            timeout=timeout
        )

        if response.status.value == "completed":
            return {
                "success": True,
                "interviews_count": len(interviews),
                "result": response.result,
                "timestamp": response.timestamp
            }
        else:
            return {
                "success": False,
                "interviews_count": len(interviews),
                "error": response.error,
                "timestamp": response.timestamp
            }
    
    @classmethod
    def interview_all_agents(
        cls,
        simulation_id: str,
        prompt: str,
        platform: str = None,
        timeout: float = 180.0
    ) -> Dict[str, Any]:
        """
        모든 Agent에 대한 인터뷰(전역 인터뷰)

        동일한 질문으로 인터뷰 시뮬레이션의 모든 Agent 사용

        Args:
            simulation_id: 시뮬레이션ID
            prompt: 인터뷰 질문(모든 Agent가 동일한 질문 사용)
            platform: 지정 플랫폼(선택)
                - "twitter": Twitter 플랫폼만 인터뷰
                - "reddit": Reddit 플랫폼만 인터뷰
                - None: 듀얼 플랫폼 시뮬레이션 시 각 Agent가 동시에 두 개 플랫폼 인터뷰
            timeout: 시간 초과시간（초）

        Returns:
            전역 인터뷰 결과 딕셔너리
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        if not os.path.exists(sim_dir):
            raise ValueError(f"시뮬레이션존재하지 않음: {simulation_id}")

        # 설정 파일에서 모든 Agent 정보 가져오기
        config_path = os.path.join(sim_dir, "simulation_config.json")
        if not os.path.exists(config_path):
            raise ValueError(f"시뮬레이션설정존재하지 않음: {simulation_id}")

        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)

        agent_configs = config.get("agent_configs", [])
        if not agent_configs:
            raise ValueError(f"시뮬레이션설정중없으면Agent: {simulation_id}")

        # 배치 인터뷰 목록 구축
        interviews = []
        for agent_config in agent_configs:
            agent_id = agent_config.get("agent_id")
            if agent_id is not None:
                interviews.append({
                    "agent_id": agent_id,
                    "prompt": prompt
                })

        logger.info(f"전역 인터뷰 명령 전송: simulation_id={simulation_id}, agent_count={len(interviews)}, platform={platform}")

        return cls.interview_agents_batch(
            simulation_id=simulation_id,
            interviews=interviews,
            platform=platform,
            timeout=timeout
        )
    
    @classmethod
    def close_simulation_env(
        cls,
        simulation_id: str,
        timeout: float = 30.0
    ) -> Dict[str, Any]:
        """
        닫기시뮬레이션환경（대신중지시뮬레이션프로세스）
        
        시뮬레이션에 종료 환경 명령 전송, 우아하게 대기 명령 모드 종료
        
        Args:
            simulation_id: 시뮬레이션ID
            timeout: 시간 초과시간（초）
            
        Returns:
            작업 결과 딕셔너리
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        if not os.path.exists(sim_dir):
            raise ValueError(f"시뮬레이션존재하지 않음: {simulation_id}")
        
        ipc_client = SimulationIPCClient(sim_dir)
        
        if not ipc_client.check_env_alive():
            return {
                "success": True,
                "message": "환경이 이미 닫힘"
            }
        
        logger.info(f"전송닫기환경명령: simulation_id={simulation_id}")
        
        try:
            response = ipc_client.send_close_env(timeout=timeout)
            
            return {
                "success": response.status.value == "completed",
                "message": "환경닫기명령이미전송",
                "result": response.result,
                "timestamp": response.timestamp
            }
        except TimeoutError:
            # 시간 초과로 인해 환경 진행 중: 닫기
            return {
                "success": True,
                "message": "환경닫기명령이미전송（대기응답시간 초과，환경진행 중: 닫기）"
            }
    
    @classmethod
    def _get_interview_history_from_db(
        cls,
        db_path: str,
        platform_name: str,
        agent_id: Optional[int] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """단일 데이터베이스에서 인터뷰 역사 가져오기"""
        import sqlite3
        
        if not os.path.exists(db_path):
            return []
        
        results = []
        
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            if agent_id is not None:
                cursor.execute("""
                    SELECT user_id, info, created_at
                    FROM trace
                    WHERE action = 'interview' AND user_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (agent_id, limit))
            else:
                cursor.execute("""
                    SELECT user_id, info, created_at
                    FROM trace
                    WHERE action = 'interview'
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (limit,))
            
            for user_id, info_json, created_at in cursor.fetchall():
                try:
                    info = json.loads(info_json) if info_json else {}
                except json.JSONDecodeError:
                    info = {"raw": info_json}
                
                results.append({
                    "agent_id": user_id,
                    "response": info.get("response", info),
                    "prompt": info.get("prompt", ""),
                    "timestamp": created_at,
                    "platform": platform_name
                })
            
            conn.close()
            
        except Exception as e:
            logger.error(f"인터뷰 역사 읽기 실패 ({platform_name}): {e}")
        
        return results

    @classmethod
    def get_interview_history(
        cls,
        simulation_id: str,
        platform: str = None,
        agent_id: Optional[int] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        인터뷰 역사 기록 가져오기(데이터베이스에서 읽기)
        
        Args:
            simulation_id: 시뮬레이션ID
            platform: 플랫폼유형（reddit/twitter/None）
                - "reddit": Reddit 플랫폼의 역사만 가져오기
                - "twitter": Twitter 플랫폼의 역사만 가져오기
                - None: 두 개 플랫폼의 모든 역사 가져오기
            agent_id: 지정 Agent ID(선택, 해당 Agent의 역사만 가져오기)
            limit: 각 플랫폼에 대한 가져오기 수량 제한
            
        Returns:
            인터뷰 역사 기록 목록
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        
        results = []
        
        # 확인조회의플랫폼
        if platform in ("reddit", "twitter"):
            platforms = [platform]
        else:
            # platform을 지정하지 않으면 두 개 플랫폼 조회
            platforms = ["twitter", "reddit"]
        
        for p in platforms:
            db_path = os.path.join(sim_dir, f"{p}_simulation.db")
            platform_results = cls._get_interview_history_from_db(
                db_path=db_path,
                platform_name=p,
                agent_id=agent_id,
                limit=limit
            )
            results.extend(platform_results)
        
        # 시간 내림차순 정렬  
        results.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        
        # 만약 조회가 여러 플랫폼이라면, 총 수를 제한한다.
        if len(platforms) > 1 and len(results) > limit:
            results = results[:limit]
        
        return results

