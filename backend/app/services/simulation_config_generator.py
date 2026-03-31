"""
시뮬레이션 설정 지능형 생성기
사용LLM에 따라시뮬레이션 요구사항、문서내용、그래프정보자동생성상세한시뮬레이션파라미터
전 과정자동화, 수동 설정 불필요파라미터

채택 단계별 생성 전략, 避免 한 번에 생성 너무 긴 내용 발생 실패：
1. 생성시간설정
2. 생성이벤트설정
3. 분할 생성 Agent 설정
4. 생성플랫폼설정
"""

import json
import math
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, field, asdict
from datetime import datetime

from openai import OpenAI

from ..config import Config
from ..utils.logger import get_logger
from .zep_entity_reader import EntityNode, ZepEntityReader

logger = get_logger('mirofish.simulation_config')

# 중국 작용 시간 설정（베이징 시간）
CHINA_TIMEZONE_CONFIG = {
    # 심야 시간대（거의 활동이 없음）  
    "dead_hours": [0, 1, 2, 3, 4, 5],
    # 아침 시간대（점차 깨어남）
    "morning_hours": [6, 7, 8],
    # 업무 시간대
    "work_hours": [9, 10, 11, 12, 13, 14, 15, 16, 17, 18],
    # 저녁 피크（가장 활발함）
    "peak_hours": [19, 20, 21, 22],
    # 야간 시간대（활성도 감소）
    "night_hours": [23],
    # 활성도 계수
    "activity_multipliers": {
        "dead": 0.05,      # 새벽 거의 활동이 없음  
        "morning": 0.4,    # 아침 점차 활발
        "work": 0.7,       # 업무 시간대중등
        "peak": 1.5,       # 저녁 피크
        "night": 0.5       # 심야 감소
    }
}


@dataclass
class AgentActivityConfig:
    """단일 Agent의 활동 설정"""
    agent_id: int
    entity_uuid: str
    entity_name: str
    entity_type: str
    
    # 활성도설정 (0.0-1.0)
    activity_level: float = 0.5  # 전체 활성도
    
    # 발언 빈도（매시간 예상 발언 횟수）
    posts_per_hour: float = 1.0
    comments_per_hour: float = 2.0
    
    # 활발한 시간대（24시간제, 0-23）
    active_hours: List[int] = field(default_factory=lambda: list(range(8, 23)))
    
    # 응답 속도（핫스팟 이벤트의 반응 지연, 단위：시뮬레이션 분）
    response_delay_min: int = 5
    response_delay_max: int = 60
    
    # 감정 성향 (-1.0 ~ 1.0, 부정적 긍정적)
    sentiment_bias: float = 0.0
    
    # 입장（특정 주제의 태도）
    stance: str = "neutral"  # supportive, opposing, neutral, observer
    
    # 영향력 가중치（발언 기타 Agent의 시각 확률 결정）
    influence_weight: float = 1.0


@dataclass  
class TimeSimulationConfig:
    """시간 시뮬레이션 설정（기반으로 중국인 작용 습관）"""
    # 시뮬레이션 총 지속 시간（시뮬레이션 시간 수）
    total_simulation_hours: int = 72  # 기본값 시뮬레이션 72시간（3일）
    
    # 매 라운드 대표의 시간（시뮬레이션 분）- 기본값 60분（1시간）, 가속 시간 흐름
    minutes_per_round: int = 60
    
    # 매시간 활성화의 Agent 수량 범위
    agents_per_hour_min: int = 5
    agents_per_hour_max: int = 20
    
    # 피크 시간대（저녁 19-22시, 중국인이 가장 활발한 시간）
    peak_hours: List[int] = field(default_factory=lambda: [19, 20, 21, 22])
    peak_activity_multiplier: float = 1.5
    
    # 비활성 시간대（새벽 0-5시, 거의 활동이 없음）
    off_peak_hours: List[int] = field(default_factory=lambda: [0, 1, 2, 3, 4, 5])
    off_peak_activity_multiplier: float = 0.05  # 새벽 활성도 극히 낮음
    
    # 아침 시간대
    morning_hours: List[int] = field(default_factory=lambda: [6, 7, 8])
    morning_activity_multiplier: float = 0.4
    
    # 업무 시간대
    work_hours: List[int] = field(default_factory=lambda: [9, 10, 11, 12, 13, 14, 15, 16, 17, 18])
    work_activity_multiplier: float = 0.7


@dataclass
class EventConfig:
    """이벤트설정"""
    # 초기 이벤트（시뮬레이션 시작 시의 트리거 이벤트）
    initial_posts: List[Dict[str, Any]] = field(default_factory=list)
    
    # 정기 이벤트（특정 시간에 트리거되는 이벤트）
    scheduled_events: List[Dict[str, Any]] = field(default_factory=list)
    
    # 핫스팟 주제 키워드
    hot_topics: List[str] = field(default_factory=list)
    
    # 여론 유도 방향
    narrative_direction: str = ""


@dataclass
class PlatformConfig:
    """플랫폼 특정 설정"""
    platform: str  # twitter or reddit
    
    # 추천 알고리즘 가중치
    recency_weight: float = 0.4  # 시간 신선도
    popularity_weight: float = 0.3  # 열도
    relevance_weight: float = 0.3  # 관련성
    
    # 바이러스 전파 임계값（얼마나 많은 상호작용 후에 전파 트리거）
    viral_threshold: int = 10
    
    # 에코 챔버 효과 강도（유사한 견해 집합 정도）
    echo_chamber_strength: float = 0.5


@dataclass
class SimulationParameters:
    """완전한 시뮬레이션 파라미터 설정"""
    # 기본 정보
    simulation_id: str
    project_id: str
    graph_id: str
    simulation_requirement: str
    
    # 시간설정
    time_config: TimeSimulationConfig = field(default_factory=TimeSimulationConfig)
    
    # Agent설정목록
    agent_configs: List[AgentActivityConfig] = field(default_factory=list)
    
    # 이벤트설정
    event_config: EventConfig = field(default_factory=EventConfig)
    
    # 플랫폼설정
    twitter_config: Optional[PlatformConfig] = None
    reddit_config: Optional[PlatformConfig] = None

    # 회의 모드 설정 (stakeholder_meeting일 때만 사용)
    meeting_config: Optional[Dict[str, Any]] = None
    
    # LLM설정
    llm_model: str = ""
    llm_base_url: str = ""
    
    # 생성 원 데이터
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    generation_reasoning: str = ""  # LLM의 추론 설명
    
    def to_dict(self) -> Dict[str, Any]:
        """변환으로 딕셔너리"""
        time_dict = asdict(self.time_config)
        return {
            "simulation_id": self.simulation_id,
            "project_id": self.project_id,
            "graph_id": self.graph_id,
            "simulation_requirement": self.simulation_requirement,
            "time_config": time_dict,
            "agent_configs": [asdict(a) for a in self.agent_configs],
            "event_config": asdict(self.event_config),
            "twitter_config": asdict(self.twitter_config) if self.twitter_config else None,
            "reddit_config": asdict(self.reddit_config) if self.reddit_config else None,
            "meeting_config": self.meeting_config,
            "llm_model": self.llm_model,
            "llm_base_url": self.llm_base_url,
            "generated_at": self.generated_at,
            "generation_reasoning": self.generation_reasoning,
        }
    
    def to_json(self, indent: int = 2) -> str:
        """변환으로 JSON 문자열"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


class SimulationConfigGenerator:
    """
    시뮬레이션 설정 지능형 생성기
    
    사용LLM분석시뮬레이션 요구사항、문서내용、그래프개체정보, 
    자동 생성 최적의 시뮬레이션 파라미터 설정
    
    채택단계별생성전략：
    1. 생성 시간 설정과 이벤트 설정 (경량급)
    2. 분할 생성 Agent 설정 (매 배치 10-20개)
    3. 생성플랫폼설정
    """
    
    # 컨텍스트 최대 문자 수
    MAX_CONTEXT_LENGTH = 50000
    # 매 배치 생성의 Agent 수량
    AGENTS_PER_BATCH = 15
    
    # 각 단계의 컨텍스트 절단 길이 (문자 수)
    TIME_CONFIG_CONTEXT_LENGTH = 10000   # 시간설정
    EVENT_CONFIG_CONTEXT_LENGTH = 8000   # 이벤트설정
    ENTITY_SUMMARY_LENGTH = 300          # 개체요약
    AGENT_SUMMARY_LENGTH = 300           # Agent설정의개체요약
    ENTITIES_PER_TYPE_DISPLAY = 20       # 매 클래스 개체 표시 수량
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model_name: Optional[str] = None
    ):
        self.api_key = api_key or Config.LLM_API_KEY
        self.base_url = base_url or Config.LLM_BASE_URL
        self.model_name = model_name or Config.LLM_MODEL_NAME
        
        if not self.api_key:
            raise ValueError("LLM_API_KEY 미설정")
        
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )
    
    def generate_config(
        self,
        simulation_id: str,
        project_id: str,
        graph_id: str,
        simulation_requirement: str,
        document_text: str,
        entities: List[EntityNode],
        enable_twitter: bool = True,
        enable_reddit: bool = True,
        simulation_mode: str = "social_media",
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> SimulationParameters:
        """
        지능형 생성 완전한 시뮬레이션 설정 (단계별 생성)
        
        Args:
            simulation_id: 시뮬레이션ID
            project_id: 프로젝트 ID
            graph_id: 그래프ID
            simulation_requirement: 시뮬레이션 요구사항설명
            document_text: 원본 문서 내용
            entities: 필터후의개체목록
            enable_twitter: 아니요 비활성화 Twitter
            enable_reddit: 아니요 비활성화 Reddit
            progress_callback: 진행률 콜백 함수 (current_step, total_steps, message)
            
        Returns:
            SimulationParameters: 완전한 시뮬레이션 파라미터
        """
        logger.info(f"시작지능형생성시뮬레이션설정: simulation_id={simulation_id}, 개체수={len(entities)}")
        
        # 총 단계 수 계산
        num_batches = math.ceil(len(entities) / self.AGENTS_PER_BATCH)
        total_steps = 3 + num_batches  # 시간 설정 + 이벤트 설정 + N 배치 Agent + 플랫폼 설정
        current_step = 0
        
        def report_progress(step: int, message: str):
            nonlocal current_step
            current_step = step
            if progress_callback:
                progress_callback(step, total_steps, message)
            logger.info(f"[{step}/{total_steps}] {message}")
        
        # 1. 구축 기본 컨텍스트 정보
        context = self._build_context(
            simulation_requirement=simulation_requirement,
            document_text=document_text,
            entities=entities
        )
        
        reasoning_parts = []
        
        # ========== 단계 1: 생성 시간 설정 ==========
        report_progress(1, "생성시간설정...")
        num_entities = len(entities)
        if simulation_mode == "stakeholder_meeting":
            # 회의 모드: 짧은 라운드, 전원 활성화
            time_config_result = {
                "total_simulation_hours": 4,   # 4시간 회의
                "minutes_per_round": 30,       # 30분당 1라운드
                "agents_per_hour_min": num_entities,  # 전원 활성화
                "agents_per_hour_max": num_entities,
                "peak_hours": list(range(0, 24)),      # 모든 시간이 활성
                "peak_activity_multiplier": 1.0,
                "off_peak_hours": [],
                "off_peak_activity_multiplier": 1.0,
                "reasoning": "이해관계자 회의 모드: 전원 참석, 8라운드"
            }
            time_config = self._parse_time_config(time_config_result, num_entities)
        else:
            time_config_result = self._generate_time_config(context, num_entities)
            time_config = self._parse_time_config(time_config_result, num_entities)
        reasoning_parts.append(f"시간설정: {time_config_result.get('reasoning', '성공')}")
        
        # ========== 단계 2: 생성 이벤트 설정 ==========
        report_progress(2, "생성 이벤트 설정과 핫 이슈...")
        event_config_result = self._generate_event_config(context, simulation_requirement, entities)
        event_config = self._parse_event_config(event_config_result)
        reasoning_parts.append(f"이벤트설정: {event_config_result.get('reasoning', '성공')}")
        
        # ========== 단계 3-N: 분할 생성 Agent 설정 ==========
        all_agent_configs = []
        for batch_idx in range(num_batches):
            start_idx = batch_idx * self.AGENTS_PER_BATCH
            end_idx = min(start_idx + self.AGENTS_PER_BATCH, len(entities))
            batch_entities = entities[start_idx:end_idx]
            
            report_progress(
                3 + batch_idx,
                f"생성Agent설정 ({start_idx + 1}-{end_idx}/{len(entities)})..."
            )
            
            batch_configs = self._generate_agent_configs_batch(
                context=context,
                entities=batch_entities,
                start_idx=start_idx,
                simulation_requirement=simulation_requirement
            )
            all_agent_configs.extend(batch_configs)
        
        reasoning_parts.append(f"Agent설정: 성공생성 {len(all_agent_configs)} 개")
        
        # ========== 로 초기 게시물 배정 발행자 Agent ==========
        logger.info("로 초기 게시물 배정 적합한 발행자 Agent...")
        event_config = self._assign_initial_post_agents(event_config, all_agent_configs)
        assigned_count = len([p for p in event_config.initial_posts if p.get("poster_agent_id") is not None])
        reasoning_parts.append(f"초기 게시물 배정: {assigned_count} 개 게시물이 이미 배정된 발행자")
        
        # ========== 마지막 단계: 생성 플랫폼 설정 ==========
        report_progress(total_steps, "생성플랫폼설정...")
        twitter_config = None
        reddit_config = None
        
        if enable_twitter:
            twitter_config = PlatformConfig(
                platform="twitter",
                recency_weight=0.4,
                popularity_weight=0.3,
                relevance_weight=0.3,
                viral_threshold=10,
                echo_chamber_strength=0.5
            )
        
        if enable_reddit:
            reddit_config = PlatformConfig(
                platform="reddit",
                recency_weight=0.3,
                popularity_weight=0.4,
                relevance_weight=0.3,
                viral_threshold=15,
                echo_chamber_strength=0.6
            )
        
        # ========== 회의 모드: 의제 생성 ==========
        meeting_config = None
        if simulation_mode == "stakeholder_meeting":
            report_progress(current_step + 1, "회의 의제 생성 중...")
            meeting_config = self._generate_meeting_config(
                simulation_requirement, entities, time_config
            )
            reasoning_parts.append(f"회의 의제: {len(meeting_config.get('agenda_items', []))}개 라운드")

        # 최종 파라미터 구축
        params = SimulationParameters(
            simulation_id=simulation_id,
            project_id=project_id,
            graph_id=graph_id,
            simulation_requirement=simulation_requirement,
            time_config=time_config,
            agent_configs=all_agent_configs,
            event_config=event_config,
            twitter_config=twitter_config,
            reddit_config=reddit_config,
            meeting_config=meeting_config,
            llm_model=self.model_name,
            llm_base_url=self.base_url,
            generation_reasoning=" | ".join(reasoning_parts)
        )
        
        logger.info(f"시뮬레이션설정생성완료: {len(params.agent_configs)} 개Agent설정")
        
        return params
    
    def _build_context(
        self,
        simulation_requirement: str,
        document_text: str,
        entities: List[EntityNode]
    ) -> str:
        """구축 LLM 컨텍스트, 절단 최대 길이"""
        
        # 개체요약
        entity_summary = self._summarize_entities(entities)
        
        # 구축컨텍스트
        context_parts = [
            f"## 시뮬레이션 요구사항\n{simulation_requirement}",
            f"\n## 개체정보 ({len(entities)}개)\n{entity_summary}",
        ]
        
        current_length = sum(len(p) for p in context_parts)
        remaining_length = self.MAX_CONTEXT_LENGTH - current_length - 500  # 500 문자 여유
        
        if remaining_length > 0 and document_text:
            doc_text = document_text[:remaining_length]
            if len(document_text) > remaining_length:
                doc_text += "\n...(문서가 이미 절단됨)"
            context_parts.append(f"\n## 원본 문서 내용\n{doc_text}")
        
        return "\n".join(context_parts)
    
    def _generate_meeting_config(
        self,
        simulation_requirement: str,
        entities: List[EntityNode],
        time_config: TimeSimulationConfig
    ) -> Dict[str, Any]:
        """이해관계자 회의 의제 생성"""
        total_rounds = time_config.total_simulation_hours * 60 // time_config.minutes_per_round
        if total_rounds < 1:
            total_rounds = 8

        participant_names = [e.name for e in entities[:20]]

        prompt = f"""정책 이해관계자 회의의 라운드별 의제를 생성하세요.

회의 주제: {simulation_requirement}
총 라운드 수: {total_rounds}
참석자: {', '.join(participant_names)}

다음 흐름으로 의제를 구성하세요:
- 초반: 문제 제기 및 현황 공유
- 중반: 핵심 이슈 분석, 이해관계자별 입장 발표
- 후반: 토론, 수정안, 반론
- 마지막: 합의 시도 및 마무리

JSON 형식으로 반환:
{{
    "meeting_title": "<회의 제목>",
    "max_rounds": {total_rounds},
    "agenda_items": [
        "라운드 1 의제: ...",
        "라운드 2 의제: ...",
        ...
    ]
}}"""

        system_prompt = "당신은 정책 회의 진행자입니다. 순수 JSON으로 반환하세요. 모든 텍스트는 한국어로 작성하세요."

        try:
            result = self._call_llm_with_retry(prompt, system_prompt)
            agenda_items = result.get("agenda_items", [])
            # 라운드 수에 맞게 조정
            while len(agenda_items) < total_rounds:
                agenda_items.append(f"라운드 {len(agenda_items)+1}: 추가 토론")
            return {
                "meeting_title": result.get("meeting_title", simulation_requirement),
                "max_rounds": total_rounds,
                "agenda_items": agenda_items[:total_rounds],
            }
        except Exception as e:
            logger.warning(f"회의 의제 LLM 생성 실패: {e}, 기본값 사용")
            return {
                "meeting_title": simulation_requirement,
                "max_rounds": total_rounds,
                "agenda_items": [f"라운드 {i+1}: 토론" for i in range(total_rounds)],
            }

    def _summarize_entities(self, entities: List[EntityNode]) -> str:
        """생성개체요약"""
        lines = []
        
        # 유형별로 그룹화
        by_type: Dict[str, List[EntityNode]] = {}
        for e in entities:
            t = e.get_entity_type() or "Unknown"
            if t not in by_type:
                by_type[t] = []
            by_type[t].append(e)
        
        for entity_type, type_entities in by_type.items():
            lines.append(f"\n### {entity_type} ({len(type_entities)}개)")
            # 사용설정의표시수량와요약길이
            display_count = self.ENTITIES_PER_TYPE_DISPLAY
            summary_len = self.ENTITY_SUMMARY_LENGTH
            for e in type_entities[:display_count]:
                summary_preview = (e.summary[:summary_len] + "...") if len(e.summary) > summary_len else e.summary
                lines.append(f"- {e.name}: {summary_preview}")
            if len(type_entities) > display_count:
                lines.append(f"  ... 아직 {len(type_entities) - display_count} 개")
        
        return "\n".join(lines)
    
    def _call_llm_with_retry(self, prompt: str, system_prompt: str) -> Dict[str, Any]:
        """포함 재시도의 LLM 호출, 포함 JSON 수정 로직"""
        import re
        
        max_attempts = 3
        last_error = None
        
        for attempt in range(max_attempts):
            try:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.7 - (attempt * 0.1)  # 매번 재시도 시 온도 감소
                    # max_tokens를 설정하지 않으면, LLM이 자유롭게 진행합니다.
                )
                
                content = response.choices[0].message.content
                finish_reason = response.choices[0].finish_reason
                
                # 잘림 여부 확인
                if finish_reason == 'length':
                    logger.warning(f"LLM 출력이 잘림 (시도 {attempt+1})")
                    content = self._fix_truncated_json(content)
                
                # 시도파싱JSON
                try:
                    return json.loads(content)
                except json.JSONDecodeError as e:
                    logger.warning(f"JSON파싱실패 (attempt {attempt+1}): {str(e)[:80]}")
                    
                    # JSON 수정을 시도합니다.
                    fixed = self._try_fix_config_json(content)
                    if fixed:
                        return fixed
                    
                    last_error = e
                    
            except Exception as e:
                logger.warning(f"LLM호출실패 (attempt {attempt+1}): {str(e)[:80]}")
                last_error = e
                import time
                time.sleep(2 * (attempt + 1))
        
        raise last_error or Exception("LLM호출실패")
    
    def _fix_truncated_json(self, content: str) -> str:
        """잘림된 JSON 수정하기"""
        content = content.strip()
        
        # 닫히지 않은 괄호 계산
        open_braces = content.count('{') - content.count('}')
        open_brackets = content.count('[') - content.count(']')
        
        # 닫히지 않은 문자열 확인
        if content and content[-1] not in '",}]':
            content += '"'
        
        # 닫는 괄호
        content += ']' * open_brackets
        content += '}' * open_braces
        
        return content
    
    def _try_fix_config_json(self, content: str) -> Optional[Dict[str, Any]]:
        """설정 JSON 수정을 시도합니다."""
        import re
        
        # 잘림된 경우 수정
        content = self._fix_truncated_json(content)
        
        # JSON 부분 추출
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            json_str = json_match.group()
            
            # 문자열의 줄바꿈 문자 제거
            def fix_string(match):
                s = match.group(0)
                s = s.replace('\n', ' ').replace('\r', ' ')
                s = re.sub(r'\s+', ' ', s)
                return s
            
            json_str = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', fix_string, json_str)
            
            try:
                return json.loads(json_str)
            except:
                # 제어 문자를 제거하는 시도
                json_str = re.sub(r'[\x00-\x1f\x7f-\x9f]', ' ', json_str)
                json_str = re.sub(r'\s+', ' ', json_str)
                try:
                    return json.loads(json_str)
                except:
                    pass
        
        return None
    
    def _generate_time_config(self, context: str, num_entities: int) -> Dict[str, Any]:
        """생성시간설정"""
        # 설정의 컨텍스트 잘림 길이
        context_truncated = context[:self.TIME_CONFIG_CONTEXT_LENGTH]
        
        # 최대 허용 값 계산 (에이전트 수의 80%)
        max_agents_allowed = max(1, int(num_entities * 0.9))
        
        prompt = f"""기반으로 하여 아래 시뮬레이션 요구사항, 생성 시간 시뮬레이션 설정.

{context_truncated}

## 작업
생성 시간 설정 JSON을 작성해 주세요.

### 기본 원칙 (참고용으로만 제공되며, 필요에 따라 구체적인 이벤트와 참여 집단에 맞게 조정해야 합니다)：
- 사용자 집단은 중국인으로, 베이징 시간의 생활 습관에 맞춰야 합니다.
- 새벽 0-5시에는 거의 활동이 없습니다 (활성도 계수 0.05)
- 아침 6-8시에는 점차 활발해집니다 (활성도 계수 0.4)
- 근무 시간 9-18시에는 중간 정도로 활발합니다 (활성도 계수 0.7)
- 저녁 19-22시에는 피크 시간대입니다 (활성도 계수 1.5)
- 23시 이후에는 활성도가 감소합니다 (활성도 계수 0.5)
- 일반적인 규칙: 새벽에는 낮은 활성도, 아침에는 점차 증가, 근무 시간대에는 중간, 저녁에는 피크
- **중요**: 아래의 예시 값은 참고용으로만 제공되며, 이벤트 성격과 참여 집단의 특성에 따라 구체적인 시간대를 조정해야 합니다.
  - 예를 들어: 학생 집단의 피크는 21-23시; 미디어는 하루 종일 활발; 공식 기관은 근무 시간에만 활동
  - 예를 들어: 돌발 핫이슈가 심야에도 논의될 수 있으며, off_peak_hours는 적절히 단축합니다.

### 돌아가기JSON형식（하지 마세요markdown）

예시:
{{
    "total_simulation_hours": 72,
    "minutes_per_round": 60,
    "agents_per_hour_min": 5,
    "agents_per_hour_max": 50,
    "peak_hours": [19, 20, 21, 22],
    "off_peak_hours": [0, 1, 2, 3, 4, 5],
    "morning_hours": [6, 7, 8],
    "work_hours": [9, 10, 11, 12, 13, 14, 15, 16, 17, 18],
    "reasoning": "해당 이벤트의 시간 설정 설명"
}}

필드 설명：
- total_simulation_hours (int): 시뮬레이션 총 시간, 24-168시간, 돌발 이벤트는 짧고, 지속적인 주제는 길다.
- minutes_per_round (int): 각 라운드의 시간, 30-120분, 60분을 권장합니다.
- agents_per_hour_min (int): 매시간 최소 활성화 에이전트 수 (값의 범위: 1-{max_agents_allowed})
- agents_per_hour_max (int): 매시간 최대 활성화 에이전트 수 (값의 범위: 1-{max_agents_allowed})
- peak_hours (int 배열): 피크 시간대, 이벤트와 참여 집단에 따라 조정
- off_peak_hours (int 배열): 비활성 시간대, 일반적으로 심야
- morning_hours (int 배열): 아침 시간대
- work_hours (int 배열): 근무 시간대
- reasoning (string): 왜 이렇게 설정했는지 간단히 설명합니다."""

        system_prompt = "당신은 소셜 미디어 시뮬레이션 전문가입니다. 순수 JSON 형식으로 반환하세요. 시간 설정은 한국인의 생활 습관에 맞춰야 합니다."
        
        try:
            return self._call_llm_with_retry(prompt, system_prompt)
        except Exception as e:
            logger.warning(f"시간설정LLM생성 실패: {e}, 사용기본값설정")
            return self._get_default_time_config(num_entities)
    
    def _get_default_time_config(self, num_entities: int) -> Dict[str, Any]:
        """기본값 시간 설정 가져오기 (중국인 생활 습관)"""
        return {
            "total_simulation_hours": 72,
            "minutes_per_round": 60,  # 매 라운드 1시간, 시간 흐름 가속
            "agents_per_hour_min": max(1, num_entities // 15),
            "agents_per_hour_max": max(5, num_entities // 5),
            "peak_hours": [19, 20, 21, 22],
            "off_peak_hours": [0, 1, 2, 3, 4, 5],
            "morning_hours": [6, 7, 8],
            "work_hours": [9, 10, 11, 12, 13, 14, 15, 16, 17, 18],
            "reasoning": "기본값 중국인 생활 습관 설정 사용 (매 라운드 1시간)"
        }
    
    def _parse_time_config(self, result: Dict[str, Any], num_entities: int) -> TimeSimulationConfig:
        """시간 설정 결과를 파싱하고 agents_per_hour 값이 총 agent 수를 초과하지 않는지 검증"""
        # 원시 값 가져오기
        agents_per_hour_min = result.get("agents_per_hour_min", max(1, num_entities // 15))
        agents_per_hour_max = result.get("agents_per_hour_max", max(5, num_entities // 5))
        
        # 검증 및 수정: 총 agent 수를 초과하지 않도록 보장
        if agents_per_hour_min > num_entities:
            logger.warning(f"agents_per_hour_min ({agents_per_hour_min}) 총 Agent 수 ({num_entities})를 초과하여 이미 수정됨")
            agents_per_hour_min = max(1, num_entities // 10)
        
        if agents_per_hour_max > num_entities:
            logger.warning(f"agents_per_hour_max ({agents_per_hour_max}) 총 Agent 수 ({num_entities})를 초과하여 이미 수정됨")
            agents_per_hour_max = max(agents_per_hour_min + 1, num_entities // 2)
        
        # 보장 min < max
        if agents_per_hour_min >= agents_per_hour_max:
            agents_per_hour_min = max(1, agents_per_hour_max // 2)
            logger.warning(f"agents_per_hour_min >= max, 이미 수정됨으로 {agents_per_hour_min}")
        
        return TimeSimulationConfig(
            total_simulation_hours=result.get("total_simulation_hours", 72),
            minutes_per_round=result.get("minutes_per_round", 60),  # 기본값 매 라운드 1시간
            agents_per_hour_min=agents_per_hour_min,
            agents_per_hour_max=agents_per_hour_max,
            peak_hours=result.get("peak_hours", [19, 20, 21, 22]),
            off_peak_hours=result.get("off_peak_hours", [0, 1, 2, 3, 4, 5]),
            off_peak_activity_multiplier=0.05,  # 새벽에는 거의 없음
            morning_hours=result.get("morning_hours", [6, 7, 8]),
            morning_activity_multiplier=0.4,
            work_hours=result.get("work_hours", list(range(9, 19))),
            work_activity_multiplier=0.7,
            peak_activity_multiplier=1.5
        )
    
    def _generate_event_config(
        self, 
        context: str, 
        simulation_requirement: str,
        entities: List[EntityNode]
    ) -> Dict[str, Any]:
        """생성이벤트설정"""
        
        # 사용의 개체 유형 목록 가져오기, LLM에 제공 참고
        entity_types_available = list(set(
            e.get_entity_type() or "Unknown" for e in entities
        ))
        
        # 각 유형에 대해 대표적인 개체 이름 나열
        type_examples = {}
        for e in entities:
            etype = e.get_entity_type() or "Unknown"
            if etype not in type_examples:
                type_examples[etype] = []
            if len(type_examples[etype]) < 3:
                type_examples[etype].append(e.name)
        
        type_info = "\n".join([
            f"- {t}: {', '.join(examples)}" 
            for t, examples in type_examples.items()
        ])
        
        # 설정의 컨텍스트 절단 길이
        context_truncated = context[:self.EVENT_CONFIG_CONTEXT_LENGTH]
        
        prompt = f"""기반으로 하여 아래 시뮬레이션 요구 사항, 생성 이벤트 설정.

시뮬레이션 요구사항: {simulation_requirement}

{context_truncated}

## 사용 개체 유형 및 예시
{type_info}

## 작업
이벤트 설정 JSON을 생성해 주세요:
- 핫 이슈 키워드 추출
- 여론 발전 방향 설명
- 초기 게시물 내용 설계, **각 게시물은 반드시 poster_type (게시자 유형)을 지정해야 합니다.**

**중요**: poster_type은 반드시 위의 "사용 개체 유형" 중에서 선택해야 하며, 그래야 초기 게시물이 적합한 Agent에 배정됩니다.
예: 공식 성명은 Official/University 유형이 게시해야 하고, 뉴스는 MediaOutlet이 게시하며, 학생 의견은 Student가 게시해야 합니다.

돌아가기JSON형식（하지 마세요markdown）：
{{
    "hot_topics": ["키워드1", "키워드2", ...],
    "narrative_direction": "<여론 발전 방향 설명>",
    "initial_posts": [
        {{"content": "게시물 내용", "poster_type": "개체 유형 (반드시 사용 유형 중에서 선택)" }},
        ...
    ],
    "reasoning": "<간단한 설명>"
}}"""

        system_prompt = "당신은 여론 분석 전문가입니다. 순수 JSON 형식으로 반환하세요. 주의: poster_type은 반드시 사용 개체 유형과 정확히 일치해야 합니다. 모든 텍스트 내용(hot_topics, narrative_direction, initial_posts의 content)은 반드시 한국어로 작성하세요."
        
        try:
            return self._call_llm_with_retry(prompt, system_prompt)
        except Exception as e:
            logger.warning(f"이벤트설정LLM생성 실패: {e}, 사용기본값설정")
            return {
                "hot_topics": [],
                "narrative_direction": "",
                "initial_posts": [],
                "reasoning": "사용기본값설정"
            }
    
    def _parse_event_config(self, result: Dict[str, Any]) -> EventConfig:
        """파싱이벤트설정결과"""
        return EventConfig(
            initial_posts=result.get("initial_posts", []),
            scheduled_events=[],
            hot_topics=result.get("hot_topics", []),
            narrative_direction=result.get("narrative_direction", "")
        )
    
    def _assign_initial_post_agents(
        self,
        event_config: EventConfig,
        agent_configs: List[AgentActivityConfig]
    ) -> EventConfig:
        """
        초기 게시물에 적합한 게시자 Agent 배정
        
        각 게시물의 poster_type에 따라 가장 적합한 agent_id와 매칭
        """
        if not event_config.initial_posts:
            return event_config
        
        # 개체 유형에 따라 agent 인덱스 구축
        agents_by_type: Dict[str, List[AgentActivityConfig]] = {}
        for agent in agent_configs:
            etype = agent.entity_type.lower()
            if etype not in agents_by_type:
                agents_by_type[etype] = []
            agents_by_type[etype].append(agent)
        
        # 유형 매핑표 (처리 LLM 출력의 동일 형식)
        type_aliases = {
            "official": ["official", "university", "governmentagency", "government"],
            "university": ["university", "official"],
            "mediaoutlet": ["mediaoutlet", "media"],
            "student": ["student", "person"],
            "professor": ["professor", "expert", "teacher"],
            "alumni": ["alumni", "person"],
            "organization": ["organization", "ngo", "company", "group"],
            "person": ["person", "student", "alumni"],
        }
        
        # 각 유형의 이미 사용된 agent 인덱스를 기록하여 동일한 개체를 반복 사용하지 않도록 함
        used_indices: Dict[str, int] = {}
        
        updated_posts = []
        for post in event_config.initial_posts:
            poster_type = post.get("poster_type", "").lower()
            content = post.get("content", "")
            
            # 매칭할 agent 찾기 시도
            matched_agent_id = None
            
            # 1. 직접 매칭
            if poster_type in agents_by_type:
                agents = agents_by_type[poster_type]
                idx = used_indices.get(poster_type, 0) % len(agents)
                matched_agent_id = agents[idx].agent_id
                used_indices[poster_type] = idx + 1
            else:
                # 2. 별명 매칭 사용
                for alias_key, aliases in type_aliases.items():
                    if poster_type in aliases or alias_key == poster_type:
                        for alias in aliases:
                            if alias in agents_by_type:
                                agents = agents_by_type[alias]
                                idx = used_indices.get(alias, 0) % len(agents)
                                matched_agent_id = agents[idx].agent_id
                                used_indices[alias] = idx + 1
                                break
                    if matched_agent_id is not None:
                        break
            
            # 3. 만약 여전히 찾을 수 없다면, 영향력이 가장 높은 agent 사용
            if matched_agent_id is None:
                logger.warning(f"유형 '{poster_type}'의 매칭 Agent를 찾을 수 없음, 영향력이 가장 높은 Agent 사용")
                if agent_configs:
                    # 영향력으로 정렬하여 영향력이 가장 높은 것을 선택
                    sorted_agents = sorted(agent_configs, key=lambda a: a.influence_weight, reverse=True)
                    matched_agent_id = sorted_agents[0].agent_id
                else:
                    matched_agent_id = 0
            
            updated_posts.append({
                "content": content,
                "poster_type": post.get("poster_type", "Unknown"),
                "poster_agent_id": matched_agent_id
            })
            
            logger.info(f"초기 게시물 배정: poster_type='{poster_type}' -> agent_id={matched_agent_id}")
        
        event_config.initial_posts = updated_posts
        return event_config
    
    def _generate_agent_configs_batch(
        self,
        context: str,
        entities: List[EntityNode],
        start_idx: int,
        simulation_requirement: str
    ) -> List[AgentActivityConfig]:
        """분할 생성 Agent 설정"""
        
        # 구축개체정보（사용설정의요약길이）
        entity_list = []
        summary_len = self.AGENT_SUMMARY_LENGTH
        for i, e in enumerate(entities):
            entity_list.append({
                "agent_id": start_idx + i,
                "entity_name": e.name,
                "entity_type": e.get_entity_type() or "Unknown",
                "summary": e.summary[:summary_len] if e.summary else ""
            })
        
        prompt = f"""기반으로 하여 아래 정보로 각 개체 생성 소셜 미디어 활동 설정。

시뮬레이션 요구사항: {simulation_requirement}

## 개체목록
```json
{json.dumps(entity_list, ensure_ascii=False, indent=2)}
```

## 작업
각 개체 생성 활동 설정, 주의：
- **시간은 중국인 생활 패턴에 맞춰**：새벽 0-5시 거의 활동 없음, 저녁 19-22시 가장 활발
- **공식 기관**（University/GovernmentAgency）：활성도 낮음(0.1-0.3), 근무 시간(9-17) 활동, 응답 느림(60-240분), 영향력 높음(2.5-3.0)
- **미디어**（MediaOutlet）：활성도 중간(0.4-0.6), 전일 활동(8-23), 응답 빠름(5-30분), 영향력 높음(2.0-2.5)
- **개인**（Student/Person/Alumni）：활성도 높음(0.6-0.9), 주로 저녁 활동(18-23), 응답 빠름(1-15분), 영향력 낮음(0.8-1.2)
- **공인/전문가**：활성도 중간(0.4-0.6), 영향력 중간 높음(1.5-2.0)

돌아가기JSON형식（하지 마세요markdown）：
{{
    "agent_configs": [
        {{
            "agent_id": <반드시 입력과 일치>,
            "activity_level": <0.0-1.0>,
            "posts_per_hour": <발포 빈도>,
            "comments_per_hour": <댓글 빈도>,
            "active_hours": [<활동 시간 목록, 중국인 생활 패턴 고려>],
            "response_delay_min": <최소 응답 지연 분>,
            "response_delay_max": <최대응답 지연분>,
            "sentiment_bias": <-1.01.0>,
            "stance": "<supportive/opposing/neutral/observer>",
            "influence_weight": <영향력 가중치>
        }},
        ...
    ]
}}"""

        system_prompt = "당신은 소셜 미디어 행동 분석 전문가입니다. 순수 JSON으로 반환하세요. 설정은 한국인 생활 습관에 맞춰야 합니다. 모든 텍스트는 한국어로 작성하세요."
        
        try:
            result = self._call_llm_with_retry(prompt, system_prompt)
            llm_configs = {cfg["agent_id"]: cfg for cfg in result.get("agent_configs", [])}
        except Exception as e:
            logger.warning(f"Agent 설정 배치 LLM 생성 실패: {e}, 사용 규칙 생성")
            llm_configs = {}
        
        # 구축 AgentActivityConfig 객체
        configs = []
        for i, entity in enumerate(entities):
            agent_id = start_idx + i
            cfg = llm_configs.get(agent_id, {})
            
            # 만약 LLM 없으면 생성, 사용 규칙 생성
            if not cfg:
                cfg = self._generate_agent_config_by_rule(entity)
            
            config = AgentActivityConfig(
                agent_id=agent_id,
                entity_uuid=entity.uuid,
                entity_name=entity.name,
                entity_type=entity.get_entity_type() or "Unknown",
                activity_level=cfg.get("activity_level", 0.5),
                posts_per_hour=cfg.get("posts_per_hour", 0.5),
                comments_per_hour=cfg.get("comments_per_hour", 1.0),
                active_hours=cfg.get("active_hours", list(range(9, 23))),
                response_delay_min=cfg.get("response_delay_min", 5),
                response_delay_max=cfg.get("response_delay_max", 60),
                sentiment_bias=cfg.get("sentiment_bias", 0.0),
                stance=cfg.get("stance", "neutral"),
                influence_weight=cfg.get("influence_weight", 1.0)
            )
            configs.append(config)
        
        return configs
    
    def _generate_agent_config_by_rule(self, entity: EntityNode) -> Dict[str, Any]:
        """기반으로 규칙 생성 단일 Agent 설정（중국인 생활 패턴）"""
        entity_type = (entity.get_entity_type() or "Unknown").lower()
        
        if entity_type in ["university", "governmentagency", "ngo"]:
            # 공식 기관：근무 시간 활동, 저빈도, 고영향력
            return {
                "activity_level": 0.2,
                "posts_per_hour": 0.1,
                "comments_per_hour": 0.05,
                "active_hours": list(range(9, 18)),  # 9:00-17:59
                "response_delay_min": 60,
                "response_delay_max": 240,
                "sentiment_bias": 0.0,
                "stance": "neutral",
                "influence_weight": 3.0
            }
        elif entity_type in ["mediaoutlet"]:
            # 미디어：전일 활동, 중간 빈도, 고영향력
            return {
                "activity_level": 0.5,
                "posts_per_hour": 0.8,
                "comments_per_hour": 0.3,
                "active_hours": list(range(7, 24)),  # 7:00-23:59
                "response_delay_min": 5,
                "response_delay_max": 30,
                "sentiment_bias": 0.0,
                "stance": "observer",
                "influence_weight": 2.5
            }
        elif entity_type in ["professor", "expert", "official"]:
            # 전문가/교수：근무 + 저녁 활동, 중간 빈도
            return {
                "activity_level": 0.4,
                "posts_per_hour": 0.3,
                "comments_per_hour": 0.5,
                "active_hours": list(range(8, 22)),  # 8:00-21:59
                "response_delay_min": 15,
                "response_delay_max": 90,
                "sentiment_bias": 0.0,
                "stance": "neutral",
                "influence_weight": 2.0
            }
        elif entity_type in ["student"]:
            # 학생：저녁으로 주로, 고빈도
            return {
                "activity_level": 0.8,
                "posts_per_hour": 0.6,
                "comments_per_hour": 1.5,
                "active_hours": [8, 9, 10, 11, 12, 13, 18, 19, 20, 21, 22, 23],  # 오전 + 저녁
                "response_delay_min": 1,
                "response_delay_max": 15,
                "sentiment_bias": 0.0,
                "stance": "neutral",
                "influence_weight": 0.8
            }
        elif entity_type in ["alumni"]:
            # 동문：저녁으로 주로
            return {
                "activity_level": 0.6,
                "posts_per_hour": 0.4,
                "comments_per_hour": 0.8,
                "active_hours": [12, 13, 19, 20, 21, 22, 23],  # 점심 시간 + 저녁
                "response_delay_min": 5,
                "response_delay_max": 30,
                "sentiment_bias": 0.0,
                "stance": "neutral",
                "influence_weight": 1.0
            }
        else:
            # 일반인：저녁 피크
            return {
                "activity_level": 0.7,
                "posts_per_hour": 0.5,
                "comments_per_hour": 1.2,
                "active_hours": [9, 10, 11, 12, 13, 18, 19, 20, 21, 22, 23],  # 낮 + 저녁
                "response_delay_min": 2,
                "response_delay_max": 20,
                "sentiment_bias": 0.0,
                "stance": "neutral",
                "influence_weight": 1.0
            }
    

