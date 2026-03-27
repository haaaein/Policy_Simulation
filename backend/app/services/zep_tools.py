"""
Zep검색도구서비스
래핑그래프검색、노드읽기、엣지조회등 도구, 제공Report Agent사용

핵심검색도구（최적화 후）：
1. InsightForge（심층통찰검색）- 가장 강력한 하이브리드검색, 자동생성하위 질문 및 다차원검색
2. PanoramaSearch(넓이검색) - 전체 보기 가져오기, 만료된 내용 포함  
3. QuickSearch（간단검색）- 빠른검색
"""

import time
import json
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field

from zep_cloud.client import Zep

from ..config import Config
from ..utils.logger import get_logger
from ..utils.llm_client import LLMClient
from ..utils.zep_paging import fetch_all_nodes, fetch_all_edges

logger = get_logger('mirofish.zep_tools')


@dataclass
class SearchResult:
    """검색결과"""
    facts: List[str]
    edges: List[Dict[str, Any]]
    nodes: List[Dict[str, Any]]
    query: str
    total_count: int
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "facts": self.facts,
            "edges": self.edges,
            "nodes": self.nodes,
            "query": self.query,
            "total_count": self.total_count
        }
    
    def to_text(self) -> str:
        """텍스트형식으로 변환, 제공LLM 이해"""
        text_parts = [f"검색조회: {self.query}", f"찾 {self.total_count} 개 관련정보"]
        
        if self.facts:
            text_parts.append("\n### 관련 사실:")
            for i, fact in enumerate(self.facts, 1):
                text_parts.append(f"{i}. {fact}")
        
        return "\n".join(text_parts)


@dataclass
class NodeInfo:
    """노드정보"""
    uuid: str
    name: str
    labels: List[str]
    summary: str
    attributes: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "uuid": self.uuid,
            "name": self.name,
            "labels": self.labels,
            "summary": self.summary,
            "attributes": self.attributes
        }
    
    def to_text(self) -> str:
        """텍스트형식으로 변환"""
        entity_type = next((l for l in self.labels if l not in ["Entity", "Node"]), "알 수 없음유형")
        return f"개체: {self.name} (유형: {entity_type})\n요약: {self.summary}"


@dataclass
class EdgeInfo:
    """엣지정보"""
    uuid: str
    name: str
    fact: str
    source_node_uuid: str
    target_node_uuid: str
    source_node_name: Optional[str] = None
    target_node_name: Optional[str] = None
    # 시간정보
    created_at: Optional[str] = None
    valid_at: Optional[str] = None
    invalid_at: Optional[str] = None
    expired_at: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "uuid": self.uuid,
            "name": self.name,
            "fact": self.fact,
            "source_node_uuid": self.source_node_uuid,
            "target_node_uuid": self.target_node_uuid,
            "source_node_name": self.source_node_name,
            "target_node_name": self.target_node_name,
            "created_at": self.created_at,
            "valid_at": self.valid_at,
            "invalid_at": self.invalid_at,
            "expired_at": self.expired_at
        }
    
    def to_text(self, include_temporal: bool = False) -> str:
        """텍스트형식으로 변환"""
        source = self.source_node_name or self.source_node_uuid[:8]
        target = self.target_node_name or self.target_node_uuid[:8]
        base_text = f"관계: {source} --[{self.name}]--> {target}\n사실: {self.fact}"
        
        if include_temporal:
            valid_at = self.valid_at or "알 수 없음"
            invalid_at = self.invalid_at or "현재까지"
            base_text += f"\n시효: {valid_at} - {invalid_at}"
            if self.expired_at:
                base_text += f" (이미만료된: {self.expired_at})"
        
        return base_text
    
    @property
    def is_expired(self) -> bool:
        """이미 만료됨"""
        return self.expired_at is not None
    
    @property
    def is_invalid(self) -> bool:
        """이미 실효됨"""
        return self.invalid_at is not None


@dataclass
class InsightForgeResult:
    """
    심층통찰검색결과 (InsightForge)
    여러 하위 질문의 검색결과를 포함하여 및 종합분석
    """
    query: str
    simulation_requirement: str
    sub_queries: List[str]
    
    # 각 차원 검색결과
    semantic_facts: List[str] = field(default_factory=list)  # 의미 검색결과
    entity_insights: List[Dict[str, Any]] = field(default_factory=list)  # 개체 통찰
    relationship_chains: List[str] = field(default_factory=list)  # 관계 체인
    
    # 통계정보
    total_facts: int = 0
    total_entities: int = 0
    total_relationships: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "simulation_requirement": self.simulation_requirement,
            "sub_queries": self.sub_queries,
            "semantic_facts": self.semantic_facts,
            "entity_insights": self.entity_insights,
            "relationship_chains": self.relationship_chains,
            "total_facts": self.total_facts,
            "total_entities": self.total_entities,
            "total_relationships": self.total_relationships
        }
    
    def to_text(self) -> str:
        """자세한 텍스트형식으로 변환, 제공LLM 이해"""
        text_parts = [
            f"## 예측되지 않은 심층 분석",
            f"분석 질문: {self.query}",
            f"예측 시나리오: {self.simulation_requirement}",
            f"\n### 예측 데이터 통계",
            f"- 관련 예측 사실: {self.total_facts}개",
            f"- 관련 개체: {self.total_entities}개",
            f"- 관계 체인: {self.total_relationships}개"
        ]
        
        # 하위 질문
        if self.sub_queries:
            text_parts.append(f"\n### 분석의하위 질문")
            for i, sq in enumerate(self.sub_queries, 1):
                text_parts.append(f"{i}. {sq}")
        
        # 의미 검색결과
        if self.semantic_facts:
            text_parts.append(f"\n### 【중요 사실】(보고서 중에서 이 원문을 인용해 주세요)")
            for i, fact in enumerate(self.semantic_facts, 1):
                text_parts.append(f"{i}. \"{fact}\"")
        
        # 개체 통찰
        if self.entity_insights:
            text_parts.append(f"\n### 【핵심개체】")
            for entity in self.entity_insights:
                text_parts.append(f"- **{entity.get('name', '알 수 없음')}** ({entity.get('type', '개체')})")
                if entity.get('summary'):
                    text_parts.append(f"  요약: \"{entity.get('summary')}\"")
                if entity.get('related_facts'):
                    text_parts.append(f"  관련 사실: {len(entity.get('related_facts', []))}개")
        
        # 관계 체인
        if self.relationship_chains:
            text_parts.append(f"\n### 【관계 체인】")
            for chain in self.relationship_chains:
                text_parts.append(f"- {chain}")
        
        return "\n".join(text_parts)


@dataclass
class PanoramaResult:
    """
    넓이검색결과 (Panorama)
    관련 정보를 포함하며, 만료된 내용 포함  
    """
    query: str
    
    # 전체 노드
    all_nodes: List[NodeInfo] = field(default_factory=list)
    # 전체 엣지（포함 만료된 것）
    all_edges: List[EdgeInfo] = field(default_factory=list)
    # 현재 유효한 사실
    active_facts: List[str] = field(default_factory=list)
    # 이미 만료된/실효된 사실（역사 기록）
    historical_facts: List[str] = field(default_factory=list)
    
    # 통계
    total_nodes: int = 0
    total_edges: int = 0
    active_count: int = 0
    historical_count: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "all_nodes": [n.to_dict() for n in self.all_nodes],
            "all_edges": [e.to_dict() for e in self.all_edges],
            "active_facts": self.active_facts,
            "historical_facts": self.historical_facts,
            "total_nodes": self.total_nodes,
            "total_edges": self.total_edges,
            "active_count": self.active_count,
            "historical_count": self.historical_count
        }
    
    def to_text(self) -> str:
        """변환하여 텍스트 형식(전체 버전, 잘림 없음)"""
        text_parts = [
            f"## 넓이 검색 결과(미전경 뷰)",
            f"조회: {self.query}",
            f"\n### 통계정보",
            f"- 총노드수: {self.total_nodes}",
            f"- 총 변의 수: {self.total_edges}",
            f"- 현재 유효한 사실: {self.active_count}개",
            f"- 역사/만료된 사실: {self.historical_count}개"
        ]
        
        # 현재 유효한 사실(전체 출력, 잘림 없음)
        if self.active_facts:
            text_parts.append(f"\n### 【현재 유효한 사실】(시뮬레이션 결과 원문)")
            for i, fact in enumerate(self.active_facts, 1):
                text_parts.append(f"{i}. \"{fact}\"")
        
        # 역사/만료된 사실(전체 출력, 잘림 없음)
        if self.historical_facts:
            text_parts.append(f"\n### 【역사/만료된 사실】(변화 기록)")
            for i, fact in enumerate(self.historical_facts, 1):
                text_parts.append(f"{i}. \"{fact}\"")
        
        # 주요 개체(전체 출력, 잘림 없음)
        if self.all_nodes:
            text_parts.append(f"\n### 【관련 개체】")
            for node in self.all_nodes:
                entity_type = next((l for l in node.labels if l not in ["Entity", "Node"]), "개체")
                text_parts.append(f"- **{node.name}** ({entity_type})")
        
        return "\n".join(text_parts)


@dataclass
class AgentInterview:
    """단일 Agent의 인터뷰 결과"""
    agent_name: str
    agent_role: str  # 역할 유형(예: 학생, 교사, 미디어 등)
    agent_bio: str  # 소개
    question: str  # 인터뷰 질문
    response: str  # 인터뷰 답변
    key_quotes: List[str] = field(default_factory=list)  # 주요 인용
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "agent_role": self.agent_role,
            "agent_bio": self.agent_bio,
            "question": self.question,
            "response": self.response,
            "key_quotes": self.key_quotes
        }
    
    def to_text(self) -> str:
        text = f"**{self.agent_name}** ({self.agent_role})\n"
        # 전체의 agent_bio 표시, 잘림 없음
        text += f"_소개: {self.agent_bio}_\n\n"
        text += f"**Q:** {self.question}\n\n"
        text += f"**A:** {self.response}\n"
        if self.key_quotes:
            text += "\n**주요 인용:**\n"
            for quote in self.key_quotes:
                # 다양한 인용 정리
                clean_quote = quote.replace('\u201c', '').replace('\u201d', '').replace('"', '')
                clean_quote = clean_quote.replace('\u300c', '').replace('\u300d', '')
                clean_quote = clean_quote.strip()
                # 시작의 구두점 제거
                while clean_quote and clean_quote[0] in ', ,；;：:、。！？\n\r\t ':
                    clean_quote = clean_quote[1:]
                # 문제 번호가 포함된 쓰레기 내용 필터링(문제 1-9)
                skip = False
                for d in '123456789':
                    if f'\u95ee\u9898{d}' in clean_quote:
                        skip = True
                        break
                if skip:
                    continue
                # 너무 긴 내용 잘림(마침표로 잘림, 강제 잘림 아님)
                if len(clean_quote) > 150:
                    dot_pos = clean_quote.find('\u3002', 80)
                    if dot_pos > 0:
                        clean_quote = clean_quote[:dot_pos + 1]
                    else:
                        clean_quote = clean_quote[:147] + "..."
                if clean_quote and len(clean_quote) >= 10:
                    text += f'> "{clean_quote}"\n'
        return text


@dataclass
class InterviewResult:
    """
    인터뷰 결과 (Interview)
    여러 시뮬레이션 Agent의 인터뷰 답변 포함
    """
    interview_topic: str  # 인터뷰 주요 주제
    interview_questions: List[str]  # 인터뷰 질문 목록
    
    # 인터뷰 선택의 Agent
    selected_agents: List[Dict[str, Any]] = field(default_factory=list)
    # 각 Agent의 인터뷰 답변
    interviews: List[AgentInterview] = field(default_factory=list)
    
    # 선택 Agent의 이유
    selection_reasoning: str = ""
    # 통합 후의 인터뷰 요약
    summary: str = ""
    
    # 통계
    total_agents: int = 0
    interviewed_count: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "interview_topic": self.interview_topic,
            "interview_questions": self.interview_questions,
            "selected_agents": self.selected_agents,
            "interviews": [i.to_dict() for i in self.interviews],
            "selection_reasoning": self.selection_reasoning,
            "summary": self.summary,
            "total_agents": self.total_agents,
            "interviewed_count": self.interviewed_count
        }
    
    def to_text(self) -> str:
        """변환하여 상세한 텍스트 형식, LLM 이해와 보고서 인용 제공"""
        text_parts = [
            "## 심층 인터뷰 보고서",
            f"**인터뷰 주요 주제:** {self.interview_topic}",
            f"**인터뷰 인원 수:** {self.interviewed_count} / {self.total_agents} 명 시뮬레이션 Agent",
            "\n### 인터뷰 선택 이유",
            self.selection_reasoning or "（자동 선택）",
            "\n---",
            "\n### 인터뷰 실록",
        ]

        if self.interviews:
            for i, interview in enumerate(self.interviews, 1):
                text_parts.append(f"\n#### 인터뷰 #{i}: {interview.agent_name}")
                text_parts.append(interview.to_text())
                text_parts.append("\n---")
        else:
            text_parts.append("（인터뷰 기록 없음）\n\n---")

        text_parts.append("\n### 인터뷰 요약과 핵심 관점")  
        text_parts.append(self.summary or "（요약 없음）")

        return "\n".join(text_parts)


class ZepToolsService:
    """
    Zep검색도구서비스
    
    【핵심검색도구 - 최적화 후】
    1. insight_forge - 심층 통찰 검색（가장 강력한, 자동 생성 하위 질문, 다차원 검색）
    2. panorama_search - 넓이 검색（전체 보기 가져오기, 포함 만료된 내용）
    3. quick_search - 간단 검색（빠른 검색）
    4. interview_agents - 심층 인터뷰（인터뷰 시뮬레이션 Agent, 다각적 관점 가져오기）
    
    【기초 도구】
    - search_graph - 그래프 의미 검색
    - get_all_nodes - 그래프의 모든 노드 가져오기
    - get_all_edges - 그래프의 모든 엣지 가져오기（시간 정보 포함）
    - get_node_detail - 노드 상세 정보 가져오기
    - get_node_edges - 노드 관련 엣지 가져오기
    - get_entities_by_type - 유형별 개체 가져오기
    - get_entity_summary - 가져오기개체의관계요약
    """
    
    # 재시도설정
    MAX_RETRIES = 3
    RETRY_DELAY = 2.0
    
    def __init__(self, api_key: Optional[str] = None, llm_client: Optional[LLMClient] = None):
        self.api_key = api_key or Config.ZEP_API_KEY
        if not self.api_key:
            raise ValueError("ZEP_API_KEY 미설정")
        
        self.client = Zep(api_key=self.api_key)
        # LLM클라이언트용InsightForge생성하위 질문
        self._llm_client = llm_client
        logger.info("ZepToolsService 초기화완료")
    
    @property
    def llm(self) -> LLMClient:
        """지연 초기화 LLM 클라이언트"""
        if self._llm_client is None:
            self._llm_client = LLMClient()
        return self._llm_client
    
    def _call_with_retry(self, func, operation_name: str, max_retries: int = None):
        """포함재시도메커니즘의API호출"""
        max_retries = max_retries or self.MAX_RETRIES
        last_exception = None
        delay = self.RETRY_DELAY
        
        for attempt in range(max_retries):
            try:
                return func()
            except Exception as e:
                last_exception = e
                if attempt < max_retries - 1:
                    logger.warning(
                        f"Zep {operation_name} {attempt + 1} 번째 시도 실패: {str(e)[:100]}, "
                        f"{delay:.1f}초후재시도..."
                    )
                    time.sleep(delay)
                    delay *= 2
                else:
                    logger.error(f"Zep {operation_name}에서 {max_retries} 번 시도 후 여전히 실패: {str(e)}")
        
        raise last_exception
    
    def search_graph(
        self, 
        graph_id: str, 
        query: str, 
        limit: int = 10,
        scope: str = "edges"
    ) -> SearchResult:
        """
        그래프 의미 검색
        
        혼합 검색(의미 + BM25) 사용하여 그래프 중 검색 관련 정보.
        만약 Zep Cloud의 search API를 사용하지 않으면, 본 키워드 매칭으로 강등.
        
        Args:
            graph_id: 그래프ID (Standalone Graph)
            query: 검색조회
            limit: 돌아가기결과수량
            scope: 검색 범위, "edges" 또는 "nodes"
            
        Returns:
            SearchResult: 검색결과
        """
        logger.info(f"그래프검색: graph_id={graph_id}, query={query[:50]}...")
        
        # 시도사용Zep Cloud Search API
        try:
            search_results = self._call_with_retry(
                func=lambda: self.client.graph.search(
                    graph_id=graph_id,
                    query=query,
                    limit=limit,
                    scope=scope,
                    reranker="cross_encoder"
                ),
                operation_name=f"그래프검색(graph={graph_id})"
            )
            
            facts = []
            edges = []
            nodes = []
            
            # 엣지 검색 결과 파싱
            if hasattr(search_results, 'edges') and search_results.edges:
                for edge in search_results.edges:
                    if hasattr(edge, 'fact') and edge.fact:
                        facts.append(edge.fact)
                    edges.append({
                        "uuid": getattr(edge, 'uuid_', None) or getattr(edge, 'uuid', ''),
                        "name": getattr(edge, 'name', ''),
                        "fact": getattr(edge, 'fact', ''),
                        "source_node_uuid": getattr(edge, 'source_node_uuid', ''),
                        "target_node_uuid": getattr(edge, 'target_node_uuid', ''),
                    })
            
            # 파싱노드검색결과
            if hasattr(search_results, 'nodes') and search_results.nodes:
                for node in search_results.nodes:
                    nodes.append({
                        "uuid": getattr(node, 'uuid_', None) or getattr(node, 'uuid', ''),
                        "name": getattr(node, 'name', ''),
                        "labels": getattr(node, 'labels', []),
                        "summary": getattr(node, 'summary', ''),
                    })
                    # 노드 요약도 사실로 간주
                    if hasattr(node, 'summary') and node.summary:
                        facts.append(f"[{node.name}]: {node.summary}")
            
            logger.info(f"검색 완료: {len(facts)} 개 관련 사실 찾음")
            
            return SearchResult(
                facts=facts,
                edges=edges,
                nodes=nodes,
                query=query,
                total_count=len(facts)
            )
            
        except Exception as e:
            logger.warning(f"Zep Search API 실패, 본 검색으로 강등: {str(e)}")
            # 강등: 본 키워드 매칭 검색 사용
            return self._local_search(graph_id, query, limit, scope)
    
    def _local_search(
        self, 
        graph_id: str, 
        query: str, 
        limit: int = 10,
        scope: str = "edges"
    ) -> SearchResult:
        """
        본 키워드 매칭 검색（Zep Search API의 강등 방안으로 작용）
        
        엣지/노드 가져오기, 그 다음 본 진행 키워드 매칭
        
        Args:
            graph_id: 그래프ID
            query: 검색조회
            limit: 돌아가기결과수량
            scope: 검색 범위
            
        Returns:
            SearchResult: 검색결과
        """
        logger.info(f"본 검색 사용: query={query[:30]}...")
        
        facts = []
        edges_result = []
        nodes_result = []
        
        # 조회 키워드 추출（간단 분리）
        query_lower = query.lower()
        keywords = [w.strip() for w in query_lower.replace(',', ' ').replace(', ', ' ').split() if len(w.strip()) > 1]
        
        def match_score(text: str) -> int:
            """텍스트와 조회의 매칭 점수 계산"""
            if not text:
                return 0
            text_lower = text.lower()
            # 완전 매칭 조회
            if query_lower in text_lower:
                return 100
            # 키워드 매칭
            score = 0
            for keyword in keywords:
                if keyword in text_lower:
                    score += 10
            return score
        
        try:
            if scope in ["edges", "both"]:
                # 엣지 가져오기 및 매칭
                all_edges = self.get_all_edges(graph_id)
                scored_edges = []
                for edge in all_edges:
                    score = match_score(edge.fact) + match_score(edge.name)
                    if score > 0:
                        scored_edges.append((score, edge))
                
                # 점수로 정렬
                scored_edges.sort(key=lambda x: x[0], reverse=True)
                
                for score, edge in scored_edges[:limit]:
                    if edge.fact:
                        facts.append(edge.fact)
                    edges_result.append({
                        "uuid": edge.uuid,
                        "name": edge.name,
                        "fact": edge.fact,
                        "source_node_uuid": edge.source_node_uuid,
                        "target_node_uuid": edge.target_node_uuid,
                    })
            
            if scope in ["nodes", "both"]:
                # 노드 가져오기 및 매칭
                all_nodes = self.get_all_nodes(graph_id)
                scored_nodes = []
                for node in all_nodes:
                    score = match_score(node.name) + match_score(node.summary)
                    if score > 0:
                        scored_nodes.append((score, node))
                
                scored_nodes.sort(key=lambda x: x[0], reverse=True)
                
                for score, node in scored_nodes[:limit]:
                    nodes_result.append({
                        "uuid": node.uuid,
                        "name": node.name,
                        "labels": node.labels,
                        "summary": node.summary,
                    })
                    if node.summary:
                        facts.append(f"[{node.name}]: {node.summary}")
            
            logger.info(f"본 검색 완료: {len(facts)} 개 관련 사실 찾음")
            
        except Exception as e:
            logger.error(f"본 검색 실패: {str(e)}")
        
        return SearchResult(
            facts=facts,
            edges=edges_result,
            nodes=nodes_result,
            query=query,
            total_count=len(facts)
        )
    
    def get_all_nodes(self, graph_id: str) -> List[NodeInfo]:
        """
        그래프의 모든 노드 가져오기（페이지로 가져오기）

        Args:
            graph_id: 그래프ID

        Returns:
            노드목록
        """
        logger.info(f"가져오기그래프 {graph_id} 의 노드...")

        nodes = fetch_all_nodes(self.client, graph_id)

        result = []
        for node in nodes:
            node_uuid = getattr(node, 'uuid_', None) or getattr(node, 'uuid', None) or ""
            result.append(NodeInfo(
                uuid=str(node_uuid) if node_uuid else "",
                name=node.name or "",
                labels=node.labels or [],
                summary=node.summary or "",
                attributes=node.attributes or {}
            ))

        logger.info(f"가져오기 {len(result)} 개노드")
        return result

    def get_all_edges(self, graph_id: str, include_temporal: bool = True) -> List[EdgeInfo]:
        """
        가져오기그래프의 엣지(페이지 가져오기, 포함 시간 정보)

        Args:
            graph_id: 그래프ID
            include_temporal: 포함 시간 정보 여부 (기본값 True)

        Returns:
            엣지 목록 (포함 created_at, valid_at, invalid_at, expired_at)
        """
        logger.info(f"가져오기 그래프 {graph_id} 의 엣지...")

        edges = fetch_all_edges(self.client, graph_id)

        result = []
        for edge in edges:
            edge_uuid = getattr(edge, 'uuid_', None) or getattr(edge, 'uuid', None) or ""
            edge_info = EdgeInfo(
                uuid=str(edge_uuid) if edge_uuid else "",
                name=edge.name or "",
                fact=edge.fact or "",
                source_node_uuid=edge.source_node_uuid or "",
                target_node_uuid=edge.target_node_uuid or ""
            )

            # 추가시간정보
            if include_temporal:
                edge_info.created_at = getattr(edge, 'created_at', None)
                edge_info.valid_at = getattr(edge, 'valid_at', None)
                edge_info.invalid_at = getattr(edge, 'invalid_at', None)
                edge_info.expired_at = getattr(edge, 'expired_at', None)

            result.append(edge_info)

        logger.info(f"가져오기 {len(result)} 개 엣지")
        return result
    
    def get_node_detail(self, node_uuid: str) -> Optional[NodeInfo]:
        """
        가져오기 단일 노드의 상세 정보
        
        Args:
            node_uuid: 노드UUID
            
        Returns:
            노드정보또는None
        """
        logger.info(f"가져오기노드상세: {node_uuid[:8]}...")
        
        try:
            node = self._call_with_retry(
                func=lambda: self.client.graph.node.get(uuid_=node_uuid),
                operation_name=f"가져오기노드상세(uuid={node_uuid[:8]}...)"
            )
            
            if not node:
                return None
            
            return NodeInfo(
                uuid=getattr(node, 'uuid_', None) or getattr(node, 'uuid', ''),
                name=node.name or "",
                labels=node.labels or [],
                summary=node.summary or "",
                attributes=node.attributes or {}
            )
        except Exception as e:
            logger.error(f"가져오기노드상세실패: {str(e)}")
            return None
    
    def get_node_edges(self, graph_id: str, node_uuid: str) -> List[EdgeInfo]:
        """
        가져오기 노드 관련의 엣지
        
        통 가져오기 그래프의 엣지, 그 다음 필터링하여 지정 노드 관련의 엣지
        
        Args:
            graph_id: 그래프ID
            node_uuid: 노드UUID
            
        Returns:
            엣지 목록
        """
        logger.info(f"가져오기 노드 {node_uuid[:8]}... 의 관련 엣지")
        
        try:
            # 가져오기 그래프의 엣지, 그 다음 필터링
            all_edges = self.get_all_edges(graph_id)
            
            result = []
            for edge in all_edges:
                # 엣지가 지정 노드와 관련 있는지 체크 (출발지 또는 목적지)
                if edge.source_node_uuid == node_uuid or edge.target_node_uuid == node_uuid:
                    result.append(edge)
            
            logger.info(f"찾기 {len(result)} 개와 노드 관련의 엣지")
            return result
            
        except Exception as e:
            logger.warning(f"가져오기 노드 엣지 실패: {str(e)}")
            return []
    
    def get_entities_by_type(
        self, 
        graph_id: str, 
        entity_type: str
    ) -> List[NodeInfo]:
        """
        유형별 가져오기 개체
        
        Args:
            graph_id: 그래프ID
            entity_type: 개체 유형 (예: Student, PublicFigure 등)
            
        Returns:
            해당 유형의 개체 목록
        """
        logger.info(f"가져오기유형로 {entity_type} 의개체...")
        
        all_nodes = self.get_all_nodes(graph_id)
        
        filtered = []
        for node in all_nodes:
            # labels가 지정 유형을 포함하는지 체크
            if entity_type in node.labels:
                filtered.append(node)
        
        logger.info(f"찾기 {len(filtered)} 개 {entity_type} 유형의 개체")
        return filtered
    
    def get_entity_summary(
        self, 
        graph_id: str, 
        entity_name: str
    ) -> Dict[str, Any]:
        """
        가져오기 지정 개체의 관계 요약
        
        검색하여 해당 개체 관련의 정보를 수집하고 요약 생성
        
        Args:
            graph_id: 그래프ID
            entity_name: 개체이름
            
        Returns:
            개체요약정보
        """
        logger.info(f"가져오기개체 {entity_name} 의관계요약...")
        
        # 먼저 해당 개체 관련의 정보 검색
        search_result = self.search_graph(
            graph_id=graph_id,
            query=entity_name,
            limit=20
        )
        
        # 모든 노드 중에서 해당 개체 찾기 시도
        all_nodes = self.get_all_nodes(graph_id)
        entity_node = None
        for node in all_nodes:
            if node.name.lower() == entity_name.lower():
                entity_node = node
                break
        
        related_edges = []
        if entity_node:
            # graph_id 파라미터 전달
            related_edges = self.get_node_edges(graph_id, entity_node.uuid)
        
        return {
            "entity_name": entity_name,
            "entity_info": entity_node.to_dict() if entity_node else None,
            "related_facts": search_result.facts,
            "related_edges": [e.to_dict() for e in related_edges],
            "total_relations": len(related_edges)
        }
    
    def get_graph_statistics(self, graph_id: str) -> Dict[str, Any]:
        """
        가져오기그래프의통계정보
        
        Args:
            graph_id: 그래프ID
            
        Returns:
            통계정보
        """
        logger.info(f"가져오기그래프 {graph_id} 의통계정보...")
        
        nodes = self.get_all_nodes(graph_id)
        edges = self.get_all_edges(graph_id)
        
        # 통계 개체 유형 분포
        entity_types = {}
        for node in nodes:
            for label in node.labels:
                if label not in ["Entity", "Node"]:
                    entity_types[label] = entity_types.get(label, 0) + 1
        
        # 통계 관계 유형 분포
        relation_types = {}
        for edge in edges:
            relation_types[edge.name] = relation_types.get(edge.name, 0) + 1
        
        return {
            "graph_id": graph_id,
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "entity_types": entity_types,
            "relation_types": relation_types
        }
    
    def get_simulation_context(
        self, 
        graph_id: str,
        simulation_requirement: str,
        limit: int = 30
    ) -> Dict[str, Any]:
        """
        가져오기시뮬레이션관련의컨텍스트정보
        
        종합 검색과 시뮬레이션 요구 사항 관련의 정보
        
        Args:
            graph_id: 그래프ID
            simulation_requirement: 시뮬레이션 요구사항설명
            limit: 각 클래스 정보의 수량 제한
            
        Returns:
            시뮬레이션컨텍스트정보
        """
        logger.info(f"가져오기시뮬레이션컨텍스트: {simulation_requirement[:50]}...")
        
        # 검색와시뮬레이션 요구사항관련의정보
        search_result = self.search_graph(
            graph_id=graph_id,
            query=simulation_requirement,
            limit=limit
        )
        
        # 가져오기그래프통계
        stats = self.get_graph_statistics(graph_id)
        
        # 가져오기 소 개체 노드
        all_nodes = self.get_all_nodes(graph_id)
        
        # 실제 유형의 개체 필터링 (비순수 Entity 노드)
        entities = []
        for node in all_nodes:
            custom_labels = [l for l in node.labels if l not in ["Entity", "Node"]]
            if custom_labels:
                entities.append({
                    "name": node.name,
                    "type": custom_labels[0],
                    "summary": node.summary
                })
        
        return {
            "simulation_requirement": simulation_requirement,
            "related_facts": search_result.facts,
            "graph_statistics": stats,
            "entities": entities[:limit],  # 수량 제한
            "total_entities": len(entities)
        }
    
    # ========== 핵심검색도구（최적화 후） ==========
    
    def insight_forge(
        self,
        graph_id: str,
        query: str,
        simulation_requirement: str,
        report_context: str = "",
        max_sub_queries: int = 5
    ) -> InsightForgeResult:
        """
        【InsightForge - 심층 통찰 검색】
        
        가장 강력한 하이브리드 검색 함수, 자동으로 문제를 분해하고 다차원 검색:
        1. 사용자가 LLM 문제 분해로 다수의 하위 질문 생성
        2. 각 하위 질문에 대해 의미 검색 진행
        3. 관련 개체 추출 및 상세 정보 가져오기
        4. 관계 추적
        5. 결과 통합하여 심층 통찰 생성
        
        Args:
            graph_id: 그래프ID
            query: 사용자 질문
            simulation_requirement: 시뮬레이션 요구사항설명
            report_context: 보고서 컨텍스트(선택, 더 정확한 하위 질문 생성)
            max_sub_queries: 최대하위 질문수량
            
        Returns:
            InsightForgeResult: 심층 통찰 검색 결과
        """
        logger.info(f"InsightForge 심층 통찰 검색: {query[:50]}...")
        
        result = InsightForgeResult(
            query=query,
            simulation_requirement=simulation_requirement,
            sub_queries=[]
        )
        
        # Step 1: 사용LLM생성하위 질문
        sub_queries = self._generate_sub_queries(
            query=query,
            simulation_requirement=simulation_requirement,
            report_context=report_context,
            max_queries=max_sub_queries
        )
        result.sub_queries = sub_queries
        logger.info(f"생성 {len(sub_queries)} 개하위 질문")
        
        # Step 2: 각 하위 질문 진행 의미 검색
        all_facts = []
        all_edges = []
        seen_facts = set()
        
        for sub_query in sub_queries:
            search_result = self.search_graph(
                graph_id=graph_id,
                query=sub_query,
                limit=15,
                scope="edges"
            )
            
            for fact in search_result.facts:
                if fact not in seen_facts:
                    all_facts.append(fact)
                    seen_facts.add(fact)
            
            all_edges.extend(search_result.edges)
        
        # 원래 질문도 진행 검색
        main_search = self.search_graph(
            graph_id=graph_id,
            query=query,
            limit=20,
            scope="edges"
        )
        for fact in main_search.facts:
            if fact not in seen_facts:
                all_facts.append(fact)
                seen_facts.add(fact)
        
        result.semantic_facts = all_facts
        result.total_facts = len(all_facts)
        
        # Step 3: 관련 개체 UUID 추출, 이 개체의 정보만 가져오기(모든 노드 가져오지 않기)
        entity_uuids = set()
        for edge_data in all_edges:
            if isinstance(edge_data, dict):
                source_uuid = edge_data.get('source_node_uuid', '')
                target_uuid = edge_data.get('target_node_uuid', '')
                if source_uuid:
                    entity_uuids.add(source_uuid)
                if target_uuid:
                    entity_uuids.add(target_uuid)
        
        # 관련 개체의 상세 정보 가져오기(수량 제한 없음, 완전 출력)
        entity_insights = []
        node_map = {}  # 후속 관계 체인 구축
        
        for uuid in list(entity_uuids):  # 처리할 개체, 잘라내지 않기
            if not uuid:
                continue
            try:
                # 각 관련 노드의 정보를 개별적으로 가져오기
                node = self.get_node_detail(uuid)
                if node:
                    node_map[uuid] = node
                    entity_type = next((l for l in node.labels if l not in ["Entity", "Node"]), "개체")
                    
                    # 해당 개체 관련의 사실 가져오기(잘라내지 않기)
                    related_facts = [
                        f for f in all_facts 
                        if node.name.lower() in f.lower()
                    ]
                    
                    entity_insights.append({
                        "uuid": node.uuid,
                        "name": node.name,
                        "type": entity_type,
                        "summary": node.summary,
                        "related_facts": related_facts  # 완전 출력, 잘라내지 않기
                    })
            except Exception as e:
                logger.debug(f"가져오기노드 {uuid} 실패: {e}")
                continue
        
        result.entity_insights = entity_insights
        result.total_entities = len(entity_insights)
        
        # Step 4: 관계 체인 구축(수량 제한 없음)
        relationship_chains = []
        for edge_data in all_edges:  # 처리할 엣지, 잘라내지 않기
            if isinstance(edge_data, dict):
                source_uuid = edge_data.get('source_node_uuid', '')
                target_uuid = edge_data.get('target_node_uuid', '')
                relation_name = edge_data.get('name', '')
                
                source_name = node_map.get(source_uuid, NodeInfo('', '', [], '', {})).name or source_uuid[:8]
                target_name = node_map.get(target_uuid, NodeInfo('', '', [], '', {})).name or target_uuid[:8]
                
                chain = f"{source_name} --[{relation_name}]--> {target_name}"
                if chain not in relationship_chains:
                    relationship_chains.append(chain)
        
        result.relationship_chains = relationship_chains
        result.total_relationships = len(relationship_chains)
        
        logger.info(f"InsightForge 완료: {result.total_facts}개 사실, {result.total_entities}개 개체, {result.total_relationships}개 관계")
        return result
    
    def _generate_sub_queries(
        self,
        query: str,
        simulation_requirement: str,
        report_context: str = "",
        max_queries: int = 5
    ) -> List[str]:
        """
        사용LLM생성하위 질문
        
        복잡한 문제를 분해하여 여러 개의 독립 검색 하위 질문
        """
        system_prompt = """당신은 전문적인 문제 분석 전문가입니다. 당신의 작업은 복잡한 문제를 분해하여 시뮬레이션 세계에서 독립적으로 관찰하는 하위 질문입니다.

요구사항：
1. 각 하위 질문은 충분히 구체적이어야 하며, 시뮬레이션 세계에서 관련된 Agent 행동이나 이벤트를 찾아야 합니다.
2. 하위 질문은 원래 문제의 동일한 차원을 커버해야 합니다(예: 누구, 무엇, 어떻게, 언제, 어디서).
3. 하위 질문은 시뮬레이션 장면과 관련이 있어야 합니다.
4. 돌아가기JSON형식：{"sub_queries": ["하위 질문1", "하위 질문2", ...]}"""

        user_prompt = f"""시뮬레이션 요구사항배경：
{simulation_requirement}

{f"보고서컨텍스트：{report_context[:500]}" if report_context else ""}

하위 질문 {max_queries}개로 문제를 분해해 주세요:
{query}

돌아가기JSON형식의하위 질문목록。"""

        try:
            response = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3
            )
            
            sub_queries = response.get("sub_queries", [])
            # 보장 문자열 목록
            return [str(sq) for sq in sub_queries[:max_queries]]
            
        except Exception as e:
            logger.warning(f"생성하위 질문실패: {str(e)}, 사용기본값하위 질문")
            # 다운그레이드: 원래 문제의 변형으로 돌아가기
            return [
                query,
                f"{query} 의 주요 참여자",
                f"{query} 의 원인과 영향",
                f"{query} 의 발전 과정"
            ][:max_queries]
    
    def panorama_search(
        self,
        graph_id: str,
        query: str,
        include_expired: bool = True,
        limit: int = 50
    ) -> PanoramaResult:
        """
        【PanoramaSearch - 넓이검색】
        
        전체 보기 뷰 가져오기, 관련 내용과 역사/만료된 정보 포함:
        1. 관련 노드 가져오기
        2. 엣지 가져오기(이미 만료된/실효된 것 포함)
        3. 현재 효력과 역사 정보를 클래스별로 정리
        
        이 도구는 이벤트 전체 보기, 추적 변화를 위한 장면에 적합합니다.
        
        Args:
            graph_id: 그래프ID
            query: 검색 조회(관련성 정렬용)
            include_expired: 만료된 내용 포함 여부(기본값 True)
            limit: 결과 수량 제한
            
        Returns:
            PanoramaResult: 넓이검색결과
        """
        logger.info(f"PanoramaSearch 넓이검색: {query[:50]}...")
        
        result = PanoramaResult(query=query)
        
        # 노드 가져오기
        all_nodes = self.get_all_nodes(graph_id)
        node_map = {n.uuid: n for n in all_nodes}
        result.all_nodes = all_nodes
        result.total_nodes = len(all_nodes)
        
        # 엣지 가져오기(시간 정보 포함)
        all_edges = self.get_all_edges(graph_id, include_temporal=True)
        result.all_edges = all_edges
        result.total_edges = len(all_edges)
        
        # 사실 클래스별로 정리
        active_facts = []
        historical_facts = []
        
        for edge in all_edges:
            if not edge.fact:
                continue
            
            # 사실 추가 개체 이름
            source_name = node_map.get(edge.source_node_uuid, NodeInfo('', '', [], '', {})).name or edge.source_node_uuid[:8]
            target_name = node_map.get(edge.target_node_uuid, NodeInfo('', '', [], '', {})).name or edge.target_node_uuid[:8]
            
            # 만료된/실효 여부 판단
            is_historical = edge.is_expired or edge.is_invalid
            
            if is_historical:
                # 역사/만료된 사실, 추가 시간 마크
                valid_at = edge.valid_at or "알 수 없음"
                invalid_at = edge.invalid_at or edge.expired_at or "알 수 없음"
                fact_with_time = f"[{valid_at} - {invalid_at}] {edge.fact}"
                historical_facts.append(fact_with_time)
            else:
                # 현재 효과 사실
                active_facts.append(edge.fact)
        
        # 기반으로 조회 진행 관련 성 정렬
        query_lower = query.lower()
        keywords = [w.strip() for w in query_lower.replace(',', ' ').replace(', ', ' ').split() if len(w.strip()) > 1]
        
        def relevance_score(fact: str) -> int:
            fact_lower = fact.lower()
            score = 0
            if query_lower in fact_lower:
                score += 100
            for kw in keywords:
                if kw in fact_lower:
                    score += 10
            return score
        
        # 정렬 및 제한 수량
        active_facts.sort(key=relevance_score, reverse=True)
        historical_facts.sort(key=relevance_score, reverse=True)
        
        result.active_facts = active_facts[:limit]
        result.historical_facts = historical_facts[:limit] if include_expired else []
        result.active_count = len(active_facts)
        result.historical_count = len(historical_facts)
        
        logger.info(f"PanoramaSearch 완료: {result.active_count}개 효과, {result.historical_count}개 역사")
        return result
    
    def quick_search(
        self,
        graph_id: str,
        query: str,
        limit: int = 10
    ) -> SearchResult:
        """
        【QuickSearch - 간단 검색】
        
        빠르고, 경량의 검색 도구：
        1. 직접 호출 Zep 의미 검색
        2. 돌아가기 가장 관련의 결과
        3. 적용 간단하고, 직접의 검색 요구
        
        Args:
            graph_id: 그래프ID
            query: 검색조회
            limit: 돌아가기결과수량
            
        Returns:
            SearchResult: 검색결과
        """
        logger.info(f"QuickSearch 간단 검색: {query[:50]}...")
        
        # 직접 호출 현의 search_graph 메서드
        result = self.search_graph(
            graph_id=graph_id,
            query=query,
            limit=limit,
            scope="edges"
        )
        
        logger.info(f"QuickSearch완료: {result.total_count}개결과")
        return result
    
    def interview_agents(
        self,
        simulation_id: str,
        interview_requirement: str,
        simulation_requirement: str = "",
        max_agents: int = 5,
        custom_questions: List[str] = None
    ) -> InterviewResult:
        """
        【InterviewAgents - 심층 인터뷰】
        
        호출 실제의 OASIS 인터뷰 API, 인터뷰 시뮬레이션 중 진행 중: 실행의 Agent：
        1. 자동 읽기 페르소나 파일, 해소 시뮬레이션 Agent
        2. 사용 LLM 분석 인터뷰 요구, 지능형 선택 가장 관련의 Agent
        3. 사용 LLM 생성 인터뷰 질문
        4. 호출 /api/simulation/interview/batch 인터페이스 진행 실제 인터뷰（듀얼 플랫폼 동시에 인터뷰）
        5. 통합 소 인터뷰 결과, 생성 인터뷰 보고서
        
        【중요】이 기능은 시뮬레이션 환경이 실행 상태에 있어야 함（OASIS 환경이 닫히지 않음）
        
        【사용 장면】
        - 필요부터 안 동 역할 뷰 해 이벤트 견해
        - 필요 수집 다방면 의견과 견해
        - 필요 가져오기 시뮬레이션 Agent의 실제 답변（비 LLM 시뮬레이션）
        
        Args:
            simulation_id: 시뮬레이션 ID(용定位 페르소나 파일과 호출 인터뷰 API)
            interview_requirement: 인터뷰 요구 설명（비 구조화, 예: "해 학생 이벤트의 견해"）
            simulation_requirement: 시뮬레이션 요구사항배경（선택）
            max_agents: 최대 인터뷰의 Agent 수량
            custom_questions: 사용자 정의 인터뷰 질문（선택, 만약 안 제시 제공 시 자동 생성）
            
        Returns:
            InterviewResult: 인터뷰 결과
        """
        from .simulation_runner import SimulationRunner
        
        logger.info(f"InterviewAgents 심층 인터뷰（실제 API）: {interview_requirement[:50]}...")
        
        result = InterviewResult(
            interview_topic=interview_requirement,
            interview_questions=custom_questions or []
        )
        
        # Step 1: 읽기 페르소나 파일
        profiles = self._load_agent_profiles(simulation_id)
        
        if not profiles:
            logger.warning(f"찾을 수 없음시뮬레이션 {simulation_id} 의페르소나파일")
            result.summary = "찾을 수 없음 인터뷰의 Agent 페르소나 파일"
            return result
        
        result.total_agents = len(profiles)
        logger.info(f"로드 {len(profiles)} 개Agent페르소나")
        
        # Step 2: 사용 LLM 선택 인터뷰의 Agent（돌아가기 agent_id 목록）
        selected_agents, selected_indices, selection_reasoning = self._select_agents_for_interview(
            profiles=profiles,
            interview_requirement=interview_requirement,
            simulation_requirement=simulation_requirement,
            max_agents=max_agents
        )
        
        result.selected_agents = selected_agents
        result.selection_reasoning = selection_reasoning
        logger.info(f"선택 {len(selected_agents)} 개 Agent 진행 인터뷰: {selected_indices}")
        
        # Step 3: 생성 인터뷰 질문（만약 없으면 제시 제공）
        if not result.interview_questions:
            result.interview_questions = self._generate_interview_questions(
                interview_requirement=interview_requirement,
                simulation_requirement=simulation_requirement,
                selected_agents=selected_agents
            )
            logger.info(f"생성 {len(result.interview_questions)} 개 인터뷰 질문")
        
        # 문제 합병로 한 개 인터뷰 prompt
        combined_prompt = "\n".join([f"{i+1}. {q}" for i, q in enumerate(result.interview_questions)])
        
        # 추가 최적화 전처리, 제약 Agent 응답 형식
        INTERVIEW_PROMPT_PREFIX = (
            "너 진행 중: 인터뷰를 한 번 받는다. 너의 페르소나, 소의 과거 기억과 행동을 결합하여,"
            "하여 순수 텍스트 방식으로 직접 답변하여 아래 질문에 대답하라.\n"
            "답변 요구사항：\n"
            "1. 직접 자연어로 대답하고, 도구를 호출하지 마세요\n"
            "2. 하지 마세요돌아가기JSON형식또는도구호출형식\n"
            "3. 마크다운 제목(예: #, ##, ###)을 사용하지 마세요\n"
            "4. 질문 번호에 따라 하나씩 대답하며, 각 대답은「문제X：」로 시작하세요(X는 문제 번호)\n"
            "5. 각 문제의 대답 사이에 빈 줄로 구분하세요\n"
            "6. 실질적인 내용을 대답하며, 각 문제에 대해 최소 2-3 문장으로 대답하세요\n\n"
        )
        optimized_prompt = f"{INTERVIEW_PROMPT_PREFIX}{combined_prompt}"
        
        # Step 4: 실제 인터뷰 API 호출(플랫폼을 지정하지 않으면 기본값으로 듀얼 플랫폼에서 동시에 인터뷰)
        try:
            # 배치 인터뷰 목록 구축(플랫폼을 지정하지 않으면 듀얼 플랫폼 인터뷰)
            interviews_request = []
            for agent_idx in selected_indices:
                interviews_request.append({
                    "agent_id": agent_idx,
                    "prompt": optimized_prompt  # 사용최적화 후의prompt
                    # 플랫폼을 지정하지 않으면 API에서 트위터와 레딧 두 개 플랫폼 모두 인터뷰
                })
            
            logger.info(f"배치 인터뷰 API 호출(듀얼 플랫폼): {len(interviews_request)} 개 에이전트")
            
            # SimulationRunner의 배치 인터뷰 메서드 호출(플랫폼을 전달하지 않으면 듀얼 플랫폼 인터뷰)
            api_result = SimulationRunner.interview_agents_batch(
                simulation_id=simulation_id,
                interviews=interviews_request,
                platform=None,  # 플랫폼을 지정하지 않으면 듀얼 플랫폼 인터뷰
                timeout=180.0   # 듀얼 플랫폼은 더 긴 시간 초과 필요
            )
            
            logger.info(f"인터뷰 API 돌아가기: {api_result.get('interviews_count', 0)} 개 결과, success={api_result.get('success')}")
            
            # API 호출이 성공했는지 확인
            if not api_result.get("success", False):
                error_msg = api_result.get("error", "알 수 없음오류")
                logger.warning(f"인터뷰 API 돌아가기 실패: {error_msg}")
                result.summary = f"인터뷰 API 호출 실패: {error_msg}。OASIS 시뮬레이션 환경 상태를 확인하세요."
                return result
            
            # Step 5: API 돌아가기 결과 파싱하여 AgentInterview 객체 구축
            # 듀얼 플랫폼 모드 돌아가기 형식: {"twitter_0": {...}, "reddit_0": {...}, "twitter_1": {...}, ...}
            api_data = api_result.get("result", {})
            results_dict = api_data.get("results", {}) if isinstance(api_data, dict) else {}
            
            for i, agent_idx in enumerate(selected_indices):
                agent = selected_agents[i]
                agent_name = agent.get("realname", agent.get("username", f"Agent_{agent_idx}"))
                agent_role = agent.get("profession", "알 수 없음")
                agent_bio = agent.get("bio", "")
                
                # 해당 에이전트에서 두 개 플랫폼의 인터뷰 결과 가져오기
                twitter_result = results_dict.get(f"twitter_{agent_idx}", {})
                reddit_result = results_dict.get(f"reddit_{agent_idx}", {})
                
                twitter_response = twitter_result.get("response", "")
                reddit_response = reddit_result.get("response", "")

                # 정리 도구 호출 JSON 포장
                twitter_response = self._clean_tool_call_response(twitter_response)
                reddit_response = self._clean_tool_call_response(reddit_response)

                # 항상 듀얼 플랫폼 마크 출력
                twitter_text = twitter_response if twitter_response else "（해당 플랫폼에서 응답을 받지 못했습니다）"
                reddit_text = reddit_response if reddit_response else "（해당 플랫폼에서 응답을 받지 못했습니다）"
                response_text = f"【트위터 플랫폼 응답】\n{twitter_text}\n\n【레딧 플랫폼 응답】\n{reddit_text}"

                # 두 개 플랫폼의 응답 중 주요 인용 추출
                import re
                combined_responses = f"{twitter_response} {reddit_response}"

                # 응답 텍스트 정리: 마크업, 번호, 마크다운 등 방해 요소 제거
                clean_text = re.sub(r'#{1,6}\s+', '', combined_responses)
                clean_text = re.sub(r'\{[^}]*tool_name[^}]*\}', '', clean_text)
                clean_text = re.sub(r'[*_`|>~\-]{2,}', '', clean_text)
                clean_text = re.sub(r'문제\d+[：:]\s*', '', clean_text)
                clean_text = re.sub(r'【[^】]+】', '', clean_text)

                # 전략 1(메인): 실질적인 내용의 문장 추출
                sentences = re.split(r'[。！？]', clean_text)
                meaningful = [
                    s.strip() for s in sentences
                    if 20 <= len(s.strip()) <= 150
                    and not re.match(r'^[\s\W, ,；;：:、]+', s.strip())
                    and not s.strip().startswith(('{', '문제'))
                ]
                meaningful.sort(key=len, reverse=True)
                key_quotes = [s + "。" for s in meaningful[:3]]

                # 전략 2(보충): 올바른 쌍의 중문 인용부호「」내 긴 텍스트
                if not key_quotes:
                    paired = re.findall(r'\u201c([^\u201c\u201d]{15,100})\u201d', clean_text)
                    paired += re.findall(r'\u300c([^\u300c\u300d]{15,100})\u300d', clean_text)
                    key_quotes = [q for q in paired if not re.match(r'^[, ,；;：:、]', q)][:3]
                
                interview = AgentInterview(
                    agent_name=agent_name,
                    agent_role=agent_role,
                    agent_bio=agent_bio[:1000],  # bio 길이 제한 확대
                    question=combined_prompt,
                    response=response_text,
                    key_quotes=key_quotes[:5]
                )
                result.interviews.append(interview)
            
            result.interviewed_count = len(result.interviews)
            
        except ValueError as e:
            # 시뮬레이션 환경이 실행되지 않음
            logger.warning(f"인터뷰 API 호출 실패(환경이 실행되지 않았나요？): {e}")
            result.summary = f"인터뷰 실패: {str(e)}。시뮬레이션 환경이 이미 닫혔습니다. OASIS 환경이 진행 중인지 확인하세요: 실행 중입니다."
            return result
        except Exception as e:
            logger.error(f"인터뷰 API 호출 예외: {e}")
            import traceback
            logger.error(traceback.format_exc())
            result.summary = f"인터뷰 과정에서 오류 발생: {str(e)}"
            return result
        
        # Step 6: 인터뷰 요약 생성
        if result.interviews:
            result.summary = self._generate_interview_summary(
                interviews=result.interviews,
                interview_requirement=interview_requirement
            )
        
        logger.info(f"InterviewAgents 완료: 인터뷰 {result.interviewed_count} 개 에이전트(듀얼 플랫폼)")
        return result
    
    @staticmethod
    def _clean_tool_call_response(response: str) -> str:
        """정리 에이전트 응답의 JSON 도구 호출 포장, 실제 내용 추출"""
        if not response or not response.strip().startswith('{'):
            return response
        text = response.strip()
        if 'tool_name' not in text[:80]:
            return response
        import re as _re
        try:
            data = json.loads(text)
            if isinstance(data, dict) and 'arguments' in data:
                for key in ('content', 'text', 'body', 'message', 'reply'):
                    if key in data['arguments']:
                        return str(data['arguments'][key])
        except (json.JSONDecodeError, KeyError, TypeError):
            match = _re.search(r'"content"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
            if match:
                return match.group(1).replace('\\n', '\n').replace('\\"', '"')
        return response

    def _load_agent_profiles(self, simulation_id: str) -> List[Dict[str, Any]]:
        """로드시뮬레이션의Agent페르소나파일"""
        import os
        import csv
        
        # 구축페르소나파일경로
        sim_dir = os.path.join(
            os.path.dirname(__file__), 
            f'../../uploads/simulations/{simulation_id}'
        )
        
        profiles = []
        
        # 우선 Reddit JSON 형식 읽기 시도
        reddit_profile_path = os.path.join(sim_dir, "reddit_profiles.json")
        if os.path.exists(reddit_profile_path):
            try:
                with open(reddit_profile_path, 'r', encoding='utf-8') as f:
                    profiles = json.load(f)
                logger.info(f"부터 reddit_profiles.json 로드 {len(profiles)} 개페르소나")
                return profiles
            except Exception as e:
                logger.warning(f"reddit_profiles.json 읽기 실패: {e}")
        
        # Twitter CSV 형식 읽기 시도
        twitter_profile_path = os.path.join(sim_dir, "twitter_profiles.csv")
        if os.path.exists(twitter_profile_path):
            try:
                with open(twitter_profile_path, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        # CSV 형식을 통합 형식으로 변환
                        profiles.append({
                            "realname": row.get("name", ""),
                            "username": row.get("username", ""),
                            "bio": row.get("description", ""),
                            "persona": row.get("user_char", ""),
                            "profession": "알 수 없음"
                        })
                logger.info(f"부터 twitter_profiles.csv 로드 {len(profiles)} 개페르소나")
                return profiles
            except Exception as e:
                logger.warning(f"twitter_profiles.csv 읽기 실패: {e}")
        
        return profiles
    
    def _select_agents_for_interview(
        self,
        profiles: List[Dict[str, Any]],
        interview_requirement: str,
        simulation_requirement: str,
        max_agents: int
    ) -> tuple:
        """
        사용 LLM 선택 인터뷰의 Agent
        
        Returns:
            tuple: (selected_agents, selected_indices, reasoning)
                - selected_agents: 선택된 Agent의 전체 정보 목록
                - selected_indices: 선택된 Agent의 인덱스 목록 (API 호출 용)
                - reasoning: 선택 이유
        """
        
        # 구축Agent요약목록
        agent_summaries = []
        for i, profile in enumerate(profiles):
            summary = {
                "index": i,
                "name": profile.get("realname", profile.get("username", f"Agent_{i}")),
                "profession": profile.get("profession", "알 수 없음"),
                "bio": profile.get("bio", "")[:200],
                "interested_topics": profile.get("interested_topics", [])
            }
            agent_summaries.append(summary)
        
        system_prompt = """당신은 전문적인 인터뷰 기획 전문가입니다. 당신의 작업에 따라 인터뷰 요구 사항에 맞춰 시뮬레이션 Agent 목록 중 가장 적합한 인터뷰의 대상을 선택하세요.

선택 기준:
1. Agent의 신분/직업과 인터뷰의 주요 주제 관련
2. Agent가 독특하거나 가치 있는 관점을 가지고 있는지
3. 다양한 관점을 선택 (예: 찬성, 반대, 중립, 전문가 등)
4. 우선적으로 사건과 직접 관련된 역할 선택

돌아가기JSON형식：
{
    "selected_indices": [선택된 Agent의 인덱스 목록],
    "reasoning": "선택 이유 설명"
}"""

        user_prompt = f"""인터뷰 요구 사항:
{interview_requirement}

시뮬레이션배경：
{simulation_requirement if simulation_requirement else "제공되지 않음"}

선택된 Agent 목록 (총 {len(agent_summaries)}개):
{json.dumps(agent_summaries, ensure_ascii=False, indent=2)}

최대 {max_agents}개의 가장 적합한 인터뷰의 Agent를 선택하고 선택 이유를 설명해 주세요."""

        try:
            response = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3
            )
            
            selected_indices = response.get("selected_indices", [])[:max_agents]
            reasoning = response.get("reasoning", "기반으로 관련성 자동 선택")
            
            # 선택된 Agent의 전체 정보 가져오기
            selected_agents = []
            valid_indices = []
            for idx in selected_indices:
                if 0 <= idx < len(profiles):
                    selected_agents.append(profiles[idx])
                    valid_indices.append(idx)
            
            return selected_agents, valid_indices, reasoning
            
        except Exception as e:
            logger.warning(f"LLM 선택 Agent 실패, 기본값 선택: {e}")
            # 다운그레이드: 상위 N개 선택
            selected = profiles[:max_agents]
            indices = list(range(min(max_agents, len(profiles))))
            return selected, indices, "기본값 선택 전략 사용"
    
    def _generate_interview_questions(
        self,
        interview_requirement: str,
        simulation_requirement: str,
        selected_agents: List[Dict[str, Any]]
    ) -> List[str]:
        """사용 LLM 생성 인터뷰 질문"""
        
        agent_roles = [a.get("profession", "알 수 없음") for a in selected_agents]
        
        system_prompt = """당신은 전문적인 기자/인터뷰어입니다. 인터뷰 요구 사항에 따라 3-5개의 심층 인터뷰 질문을 생성하세요.

질문 요구 사항:
1. 개방형 질문, 상세한 답변을 유도
2. 같은 역할에 대해 같은 답변을 피할 것
3. 사실, 의견, 감정 등 여러 차원 포함
4. 언어는 자연스럽고, 실제 인터뷰처럼
5. 각 질문은 50자 이내로 간결하게
6. 직접 질문하고 배경 설명이나 접두사는 포함하지 마세요

돌아가기 JSON 형식: {"questions": ["질문1", "질문2", ...]}"""

        user_prompt = f"""인터뷰 요구 사항: {interview_requirement}

시뮬레이션 배경: {simulation_requirement if simulation_requirement else "제공되지 않음"}

인터뷰 대상 역할: {', '.join(agent_roles)}

3-5개의 인터뷰 질문을 생성해 주세요."""

        try:
            response = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.5
            )
            
            return response.get("questions", [f"{interview_requirement}에 대해 당신은 어떤 생각을 가지고 있나요?"])
            
        except Exception as e:
            logger.warning(f"생성 인터뷰 질문 실패: {e}")
            return [
                f"관하여 {interview_requirement}, 당신의 의견은 무엇인가요？",
                "이 사건이 당신 또는 당신이 대표하는 집단에 어떤 영향을 미치나요？",
                "당신은 이 문제를 어떻게 해결하거나 개선해야 한다고 생각하나요？"
            ]
    
    def _generate_interview_summary(
        self,
        interviews: List[AgentInterview],
        interview_requirement: str
    ) -> str:
        """인터뷰 요약 생성"""
        
        if not interviews:
            return "어떤 인터뷰도 완료되지 않음"
        
        # 수집된 인터뷰 내용
        interview_texts = []
        for interview in interviews:
            interview_texts.append(f"【{interview.agent_name}（{interview.agent_role}）】\n{interview.response[:500]}")
        
        system_prompt = """당신은 전문적인 뉴스 편집자입니다. 여러 응답자의 답변에 따라 인터뷰 요약을 생성하세요.

요약요구사항：
1. 각 측의 주요 의견을 정리
2. 의견의 공감대와 차이점 지적
3. 가치 있는 인용 강조
4. 객관적 중립, 어떤 한쪽도 편들지 않기
5. 1000자 이내로 제한

형식 제약（반드시 준수）：
- 순수 텍스트 단락 사용, 같은 부분은 빈 줄로 구분
- Markdown 제목 사용하지 마세요（예: #、##、###）
- 구분선 사용하지 마세요（예: ---、***）
- 응답자의 원문을 인용할 때는 중문 인용부호「」사용
- 키워드는 **굵게** 표시하되, 기타 Markdown 문법은 사용하지 마세요"""

        user_prompt = f"""인터뷰 주요 주제：{interview_requirement}

인터뷰 내용：
{"".join(interview_texts)}

인터뷰 요약을 생성해 주세요。"""

        try:
            summary = self.llm.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3,
                max_tokens=800
            )
            return summary
            
        except Exception as e:
            logger.warning(f"인터뷰 요약 생성 실패: {e}")
            # 다운그레이드: 간단히 연결
            return f"총 {len(interviews)}명의 응답자와 인터뷰하였으며, 포함된 응답자는：" + "、".join([i.agent_name for i in interviews])
