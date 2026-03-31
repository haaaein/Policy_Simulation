"""
OASIS 시뮬레이션 관리자  
관리Twitter와Reddit듀얼플랫폼병렬시뮬레이션
사용프리셋 스크립트 + LLM지능형생성설정파라미터
"""

import os
import json
import shutil
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..config import Config
from ..utils.logger import get_logger
from .zep_entity_reader import ZepEntityReader, FilteredEntities
from .oasis_profile_generator import OasisProfileGenerator, OasisAgentProfile
from .simulation_config_generator import SimulationConfigGenerator, SimulationParameters

logger = get_logger('mirofish.simulation')


class SimulationStatus(str, Enum):
    """시뮬레이션상태"""
    CREATED = "created"
    PREPARING = "preparing"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"      # 시뮬레이션수동으로중지
    COMPLETED = "completed"  # 시뮬레이션자연스럽게완료
    FAILED = "failed"


class PlatformType(str, Enum):
    """플랫폼유형"""
    TWITTER = "twitter"
    REDDIT = "reddit"


@dataclass
class SimulationState:
    """시뮬레이션상태"""
    simulation_id: str
    project_id: str
    graph_id: str
    
    # 시뮬레이션 모드: "social_media" 또는 "stakeholder_meeting"
    simulation_mode: str = "social_media"

    # 플랫폼활성화상태
    enable_twitter: bool = True
    enable_reddit: bool = True
    
    # 상태
    status: SimulationStatus = SimulationStatus.CREATED
    
    # 준비 단계 데이터
    entities_count: int = 0
    profiles_count: int = 0
    entity_types: List[str] = field(default_factory=list)
    
    # 설정생성정보
    config_generated: bool = False
    config_reasoning: str = ""
    
    # 실행 시 데이터
    current_round: int = 0
    twitter_status: str = "not_started"
    reddit_status: str = "not_started"
    
    # 시간 스탬프
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    
    # 오류정보
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """완전 상태 사전(내부 사용)"""
        return {
            "simulation_id": self.simulation_id,
            "project_id": self.project_id,
            "graph_id": self.graph_id,
            "simulation_mode": self.simulation_mode,
            "enable_twitter": self.enable_twitter,
            "enable_reddit": self.enable_reddit,
            "status": self.status.value,
            "entities_count": self.entities_count,
            "profiles_count": self.profiles_count,
            "entity_types": self.entity_types,
            "config_generated": self.config_generated,
            "config_reasoning": self.config_reasoning,
            "current_round": self.current_round,
            "twitter_status": self.twitter_status,
            "reddit_status": self.reddit_status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
        }
    
    def to_simple_dict(self) -> Dict[str, Any]:
        """간소화 상태 사전(API 반환용)"""
        return {
            "simulation_id": self.simulation_id,
            "project_id": self.project_id,
            "graph_id": self.graph_id,
            "simulation_mode": self.simulation_mode,
            "status": self.status.value,
            "entities_count": self.entities_count,
            "profiles_count": self.profiles_count,
            "entity_types": self.entity_types,
            "config_generated": self.config_generated,
            "error": self.error,
        }


class SimulationManager:
    """
    시뮬레이션 관리자
    
    핵심기능：
    1. 부터 Zep 그래프 읽기 개체 및 필터
    2. 생성OASIS Agent Profile
    3. 사용LLM지능형생성시뮬레이션설정파라미터
    4. 준비 프리셋 스크립트에 필요한 파일
    """
    
    # 시뮬레이션 데이터 저장 디렉토리
    SIMULATION_DATA_DIR = os.path.join(
        os.path.dirname(__file__), 
        '../../uploads/simulations'
    )
    
    def __init__(self):
        # 보장 디렉토리 저장에서
        os.makedirs(self.SIMULATION_DATA_DIR, exist_ok=True)
        
        # 메모리의 시뮬레이션 상태 캐시
        self._simulations: Dict[str, SimulationState] = {}
    
    def _get_simulation_dir(self, simulation_id: str) -> str:
        """가져오기시뮬레이션데이터디렉토리"""
        sim_dir = os.path.join(self.SIMULATION_DATA_DIR, simulation_id)
        os.makedirs(sim_dir, exist_ok=True)
        return sim_dir
    
    def _save_simulation_state(self, state: SimulationState):
        """저장시뮬레이션상태파일"""
        sim_dir = self._get_simulation_dir(state.simulation_id)
        state_file = os.path.join(sim_dir, "state.json")
        
        state.updated_at = datetime.now().isoformat()
        
        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)
        
        self._simulations[state.simulation_id] = state
    
    def _load_simulation_state(self, simulation_id: str) -> Optional[SimulationState]:
        """부터파일로드시뮬레이션상태"""
        if simulation_id in self._simulations:
            return self._simulations[simulation_id]
        
        sim_dir = self._get_simulation_dir(simulation_id)
        state_file = os.path.join(sim_dir, "state.json")
        
        if not os.path.exists(state_file):
            return None
        
        with open(state_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        state = SimulationState(
            simulation_id=simulation_id,
            project_id=data.get("project_id", ""),
            graph_id=data.get("graph_id", ""),
            simulation_mode=data.get("simulation_mode", "social_media"),
            enable_twitter=data.get("enable_twitter", True),
            enable_reddit=data.get("enable_reddit", True),
            status=SimulationStatus(data.get("status", "created")),
            entities_count=data.get("entities_count", 0),
            profiles_count=data.get("profiles_count", 0),
            entity_types=data.get("entity_types", []),
            config_generated=data.get("config_generated", False),
            config_reasoning=data.get("config_reasoning", ""),
            current_round=data.get("current_round", 0),
            twitter_status=data.get("twitter_status", "not_started"),
            reddit_status=data.get("reddit_status", "not_started"),
            created_at=data.get("created_at", datetime.now().isoformat()),
            updated_at=data.get("updated_at", datetime.now().isoformat()),
            error=data.get("error"),
        )
        
        self._simulations[simulation_id] = state
        return state
    
    def create_simulation(
        self,
        project_id: str,
        graph_id: str,
        enable_twitter: bool = True,
        enable_reddit: bool = True,
        simulation_mode: str = "social_media",
    ) -> SimulationState:
        """
        새로운 시뮬레이션 생성

        Args:
            project_id: 프로젝트 ID
            graph_id: Zep 그래프 ID
            enable_twitter: Twitter 시뮬레이션 활성화 여부
            enable_reddit: Reddit 시뮬레이션 활성화 여부
            simulation_mode: "social_media" 또는 "stakeholder_meeting"

        Returns:
            SimulationState
        """
        import uuid
        simulation_id = f"sim_{uuid.uuid4().hex[:12]}"

        # 이해관계자 회의 모드: 단일 플랫폼(포럼)만 사용
        if simulation_mode == "stakeholder_meeting":
            enable_twitter = True   # 포럼 역할
            enable_reddit = False   # 미사용

        state = SimulationState(
            simulation_id=simulation_id,
            project_id=project_id,
            graph_id=graph_id,
            simulation_mode=simulation_mode,
            enable_twitter=enable_twitter,
            enable_reddit=enable_reddit,
            status=SimulationStatus.CREATED,
        )
        
        self._save_simulation_state(state)
        logger.info(f"생성시뮬레이션: {simulation_id}, project={project_id}, graph={graph_id}")
        
        return state
    
    def prepare_simulation(
        self,
        simulation_id: str,
        simulation_requirement: str,
        document_text: str,
        defined_entity_types: Optional[List[str]] = None,
        use_llm_for_profiles: bool = True,
        progress_callback: Optional[callable] = None,
        parallel_profile_count: int = 3
    ) -> SimulationState:
        """
        준비 시뮬레이션 환경(전 과정 자동화)
        
        단계：
        1. 부터Zep그래프읽고필터개체
        2. 로 각 개체 생성 OASIS Agent Profile(선택 LLM 강화, 지원 병렬)
        3. 사용 LLM 지능형 생성 시뮬레이션 설정 파라미터(시간, 활성도, 발언 빈도 등)
        4. 저장설정파일와Profile파일
        5. 복사프리셋 스크립트시뮬레이션디렉토리
        
        Args:
            simulation_id: 시뮬레이션ID
            simulation_requirement: 시뮬레이션 요구사항설명（용LLM생성설정）
            document_text: 원본 문서 내용(용 LLM 이해 배경)
            defined_entity_types: 미리 정의된 개체 유형(선택)
            use_llm_for_profiles: 아니오 사용 LLM 생성 상세 페르소나
            progress_callback: 진행률 콜백 함수 (stage, progress, message)
            parallel_profile_count: 병렬생성페르소나의수량，기본값3
            
        Returns:
            SimulationState
        """
        state = self._load_simulation_state(simulation_id)
        if not state:
            raise ValueError(f"시뮬레이션존재하지 않음: {simulation_id}")
        
        try:
            state.status = SimulationStatus.PREPARING
            self._save_simulation_state(state)
            
            sim_dir = self._get_simulation_dir(simulation_id)
            
            # ========== 단계 1: 읽고 필터 개체 ==========
            if progress_callback:
                progress_callback("reading", 0, "진행 중: 연결Zep그래프...")
            
            reader = ZepEntityReader()
            
            if progress_callback:
                progress_callback("reading", 30, "진행 중: 노드 데이터 읽기...")
            
            filtered = reader.filter_defined_entities(
                graph_id=state.graph_id,
                defined_entity_types=defined_entity_types,
                enrich_with_edges=True
            )
            
            state.entities_count = filtered.filtered_count
            state.entity_types = list(filtered.entity_types)
            
            if progress_callback:
                progress_callback(
                    "reading", 100, 
                    f"완료, 총 {filtered.filtered_count} 개 개체",
                    current=filtered.filtered_count,
                    total=filtered.filtered_count
                )
            
            if filtered.filtered_count == 0:
                state.status = SimulationStatus.FAILED
                state.error = "없으면 찾을 수 없는 개체의 개체, 그래프가 올바르게 구축되었는지 확인"
                self._save_simulation_state(state)
                return state
            
            # ========== 단계 2: 생성 Agent Profile ==========
            total_entities = len(filtered.entities)
            
            if progress_callback:
                progress_callback(
                    "generating_profiles", 0, 
                    "시작생성...",
                    current=0,
                    total=total_entities
                )
            
            # graph_id를 전달하여 활성화 Zep 검색 기능, 더 풍부한 컨텍스트 가져오기
            generator = OasisProfileGenerator(graph_id=state.graph_id)
            
            def profile_progress(current, total, msg):
                if progress_callback:
                    progress_callback(
                        "generating_profiles", 
                        int(current / total * 100), 
                        msg,
                        current=current,
                        total=total,
                        item_name=msg
                    )
            
            # 실시간 저장의 파일 경로 설정(우선적으로 Reddit JSON 형식 사용)
            realtime_output_path = None
            realtime_platform = "reddit"
            if state.enable_reddit:
                realtime_output_path = os.path.join(sim_dir, "reddit_profiles.json")
                realtime_platform = "reddit"
            elif state.enable_twitter:
                realtime_output_path = os.path.join(sim_dir, "twitter_profiles.csv")
                realtime_platform = "twitter"
            
            profiles = generator.generate_profiles_from_entities(
                entities=filtered.entities,
                use_llm=use_llm_for_profiles,
                progress_callback=profile_progress,
                graph_id=state.graph_id,  # graph_id를 전달하여 Zep 검색
                parallel_count=parallel_profile_count,  # 병렬생성수량
                realtime_output_path=realtime_output_path,  # 실시간저장경로
                output_platform=realtime_platform  # 출력형식
            )
            
            state.profiles_count = len(profiles)
            
            # Profile 파일 저장(주의: Twitter는 CSV 형식 사용, Reddit은 JSON 형식 사용)
            # Reddit은 이미 생성 중 실시간 저장, 여기서 다시 저장하여 완전성 보장
            if progress_callback:
                progress_callback(
                    "generating_profiles", 95, 
                    "저장Profile파일...",
                    current=total_entities,
                    total=total_entities
                )
            
            if state.enable_reddit:
                generator.save_profiles(
                    profiles=profiles,
                    file_path=os.path.join(sim_dir, "reddit_profiles.json"),
                    platform="reddit"
                )
            
            if state.enable_twitter:
                # Twitter는 CSV 형식 사용! 이것은 OASIS의 요구 사항
                generator.save_profiles(
                    profiles=profiles,
                    file_path=os.path.join(sim_dir, "twitter_profiles.csv"),
                    platform="twitter"
                )
            
            if progress_callback:
                progress_callback(
                    "generating_profiles", 100, 
                    f"완료, 총 {len(profiles)} 개 Profile",
                    current=len(profiles),
                    total=len(profiles)
                )
            
            # ========== 단계 3: LLM 지능형 생성 시뮬레이션 설정 ==========
            if progress_callback:
                progress_callback(
                    "generating_config", 0, 
                    "진행 중: 분석시뮬레이션 요구사항...",
                    current=0,
                    total=3
                )
            
            config_generator = SimulationConfigGenerator()
            
            if progress_callback:
                progress_callback(
                    "generating_config", 30, 
                    "진행 중: 호출LLM생성설정...",
                    current=1,
                    total=3
                )
            
            sim_params = config_generator.generate_config(
                simulation_id=simulation_id,
                project_id=state.project_id,
                graph_id=state.graph_id,
                simulation_requirement=simulation_requirement,
                document_text=document_text,
                entities=filtered.entities,
                enable_twitter=state.enable_twitter,
                enable_reddit=state.enable_reddit
            )
            
            if progress_callback:
                progress_callback(
                    "generating_config", 70, 
                    "진행 중: 저장설정파일...",
                    current=2,
                    total=3
                )
            
            # 저장설정파일
            config_path = os.path.join(sim_dir, "simulation_config.json")
            with open(config_path, 'w', encoding='utf-8') as f:
                f.write(sim_params.to_json())
            
            state.config_generated = True
            state.config_reasoning = sim_params.generation_reasoning
            
            if progress_callback:
                progress_callback(
                    "generating_config", 100, 
                    "설정생성완료",
                    current=3,
                    total=3
                )
            
            # 주의: 실행 스크립트는 backend/scripts/ 디렉토리에 보관, 시뮬레이션 디렉토리에 복사하지 않음
            # 시뮬레이션 시작 시, simulation_runner는 scripts/ 디렉토리에서 실행 스크립트
            
            # 업데이트상태
            state.status = SimulationStatus.READY
            self._save_simulation_state(state)
            
            logger.info(f"시뮬레이션준비 완료: {simulation_id}, "
                       f"entities={state.entities_count}, profiles={state.profiles_count}")
            
            return state
            
        except Exception as e:
            logger.error(f"시뮬레이션준비실패: {simulation_id}, error={str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            state.status = SimulationStatus.FAILED
            state.error = str(e)
            self._save_simulation_state(state)
            raise
    
    def get_simulation(self, simulation_id: str) -> Optional[SimulationState]:
        """가져오기시뮬레이션상태"""
        return self._load_simulation_state(simulation_id)
    
    def list_simulations(self, project_id: Optional[str] = None) -> List[SimulationState]:
        """모든 시뮬레이션 나열"""
        simulations = []
        
        if os.path.exists(self.SIMULATION_DATA_DIR):
            for sim_id in os.listdir(self.SIMULATION_DATA_DIR):
                # 숨기기 파일(예: .DS_Store)와 비 디렉토리 파일
                sim_path = os.path.join(self.SIMULATION_DATA_DIR, sim_id)
                if sim_id.startswith('.') or not os.path.isdir(sim_path):
                    continue
                
                state = self._load_simulation_state(sim_id)
                if state:
                    if project_id is None or state.project_id == project_id:
                        simulations.append(state)
        
        return simulations
    
    def get_profiles(self, simulation_id: str, platform: str = "reddit") -> List[Dict[str, Any]]:
        """가져오기시뮬레이션의Agent Profile"""
        state = self._load_simulation_state(simulation_id)
        if not state:
            raise ValueError(f"시뮬레이션존재하지 않음: {simulation_id}")
        
        sim_dir = self._get_simulation_dir(simulation_id)
        profile_path = os.path.join(sim_dir, f"{platform}_profiles.json")
        
        if not os.path.exists(profile_path):
            return []
        
        with open(profile_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def get_simulation_config(self, simulation_id: str) -> Optional[Dict[str, Any]]:
        """가져오기시뮬레이션설정"""
        sim_dir = self._get_simulation_dir(simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")
        
        if not os.path.exists(config_path):
            return None
        
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def get_run_instructions(self, simulation_id: str) -> Dict[str, str]:
        """가져오기 실행 설명"""
        sim_dir = self._get_simulation_dir(simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")
        scripts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../scripts'))
        
        return {
            "simulation_dir": sim_dir,
            "scripts_dir": scripts_dir,
            "config_file": config_path,
            "commands": {
                "twitter": f"python {scripts_dir}/run_twitter_simulation.py --config {config_path}",
                "reddit": f"python {scripts_dir}/run_reddit_simulation.py --config {config_path}",
                "parallel": f"python {scripts_dir}/run_parallel_simulation.py --config {config_path}",
            },
            "instructions": (
                f"1. conda 환경 활성화: conda activate MiroFish\n"
                f"2. 실행 시뮬레이션 (스크립트 위치: {scripts_dir}):\n"
                f"   - 개별 실행 Twitter: python {scripts_dir}/run_twitter_simulation.py --config {config_path}\n"
                f"   - 개별 실행 Reddit: python {scripts_dir}/run_reddit_simulation.py --config {config_path}\n"
                f"   - 병렬실행듀얼플랫폼: python {scripts_dir}/run_parallel_simulation.py --config {config_path}"
            )
        }
