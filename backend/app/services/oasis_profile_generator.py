"""
OASIS Agent Profile 생성기
Zep 그래프의 엔티티를 OASIS 시뮬레이션 플랫폼에 필요한 Agent Profile 형식으로 변환

최적화 개선:
1. Zep 검색 기능을 호출하여 노드 정보를 추가 보강
2. 프롬프트를 최적화하여 매우 상세한 페르소나 생성
3. 개인 엔티티와 추상 그룹 엔티티 구분
"""

import json
import random
import time
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import datetime

from openai import OpenAI
from zep_cloud.client import Zep

from ..config import Config
from ..utils.logger import get_logger
from .zep_entity_reader import EntityNode, ZepEntityReader

logger = get_logger('mirofish.oasis_profile')


@dataclass
class OasisAgentProfile:
    """OASIS Agent Profile데이터구조"""
    # 공통 필드
    user_id: int
    user_name: str
    name: str
    bio: str
    persona: str
    
    # 선택 필드 - Reddit 스타일
    karma: int = 1000
    
    # 선택 필드 - Twitter 스타일
    friend_count: int = 100
    follower_count: int = 150
    statuses_count: int = 500
    
    # 추가페르소나정보
    age: Optional[int] = None
    gender: Optional[str] = None
    mbti: Optional[str] = None
    country: Optional[str] = None
    profession: Optional[str] = None
    interested_topics: List[str] = field(default_factory=list)
    
    # 원본 개체 정보
    source_entity_uuid: Optional[str] = None
    source_entity_type: Optional[str] = None
    
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    
    def to_reddit_format(self) -> Dict[str, Any]:
        """Reddit 플랫폼 형식으로 변환"""
        profile = {
            "user_id": self.user_id,
            "username": self.user_name,  # OASIS 라이브러리 요구사항 필드명으로 username(언더스코어 없음)
            "name": self.name,
            "bio": self.bio,
            "persona": self.persona,
            "karma": self.karma,
            "created_at": self.created_at,
        }
        
        # 추가추가페르소나정보（만약）
        if self.age:
            profile["age"] = self.age
        if self.gender:
            profile["gender"] = self.gender
        if self.mbti:
            profile["mbti"] = self.mbti
        if self.country:
            profile["country"] = self.country
        if self.profession:
            profile["profession"] = self.profession
        if self.interested_topics:
            profile["interested_topics"] = self.interested_topics
        
        return profile
    
    def to_twitter_format(self) -> Dict[str, Any]:
        """Twitter 플랫폼 형식으로 변환"""
        profile = {
            "user_id": self.user_id,
            "username": self.user_name,  # OASIS 라이브러리 요구사항 필드명으로 username(언더스코어 없음)
            "name": self.name,
            "bio": self.bio,
            "persona": self.persona,
            "friend_count": self.friend_count,
            "follower_count": self.follower_count,
            "statuses_count": self.statuses_count,
            "created_at": self.created_at,
        }
        
        # 추가추가페르소나정보
        if self.age:
            profile["age"] = self.age
        if self.gender:
            profile["gender"] = self.gender
        if self.mbti:
            profile["mbti"] = self.mbti
        if self.country:
            profile["country"] = self.country
        if self.profession:
            profile["profession"] = self.profession
        if self.interested_topics:
            profile["interested_topics"] = self.interested_topics
        
        return profile
    
    def to_dict(self) -> Dict[str, Any]:
        """전체 딕셔너리 형식으로 변환"""
        return {
            "user_id": self.user_id,
            "user_name": self.user_name,
            "name": self.name,
            "bio": self.bio,
            "persona": self.persona,
            "karma": self.karma,
            "friend_count": self.friend_count,
            "follower_count": self.follower_count,
            "statuses_count": self.statuses_count,
            "age": self.age,
            "gender": self.gender,
            "mbti": self.mbti,
            "country": self.country,
            "profession": self.profession,
            "interested_topics": self.interested_topics,
            "source_entity_uuid": self.source_entity_uuid,
            "source_entity_type": self.source_entity_type,
            "created_at": self.created_at,
        }


class OasisProfileGenerator:
    """
    OASIS 프로필 생성기
    
    Zep 그래프의 개체 변환으로 OASIS 시뮬레이션에 필요한 Agent Profile
    
    최적화 특성：
    1. 호출 Zep 그래프 검색 기능 가져오기 더 풍부한 컨텍스트
    2. 생성 매우 상세한 페르소나(기본 정보, 직업 경험, 성격 특성, 소셜 미디어 행로 등 포함)
    3. 영역 분개 개인 개체와 추상 군체 개체
    """
    
    # MBTI유형목록
    MBTI_TYPES = [
        "INTJ", "INTP", "ENTJ", "ENTP",
        "INFJ", "INFP", "ENFJ", "ENFP",
        "ISTJ", "ISFJ", "ESTJ", "ESFJ",
        "ISTP", "ISFP", "ESTP", "ESFP"
    ]
    
    # 일반 국가 목록
    COUNTRIES = [
        "South Korea", "US", "UK", "Japan", "Germany", "France",
        "Canada", "Australia", "China", "India", "Brazil"
    ]
    
    # 개인 유형 개체(구체적인 페르소나 생성 필요)
    INDIVIDUAL_ENTITY_TYPES = [
        "student", "alumni", "professor", "person", "publicfigure", 
        "expert", "faculty", "official", "journalist", "activist"
    ]
    
    # 군체/기관 유형 개체(군체 대표 페르소나 생성 필요)
    GROUP_ENTITY_TYPES = [
        "university", "governmentagency", "organization", "ngo", 
        "mediaoutlet", "company", "institution", "group", "community"
    ]
    
    def __init__(
        self, 
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model_name: Optional[str] = None,
        zep_api_key: Optional[str] = None,
        graph_id: Optional[str] = None
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
        
        # Zep 클라이언트용 검색 풍부한 컨텍스트
        self.zep_api_key = zep_api_key or Config.ZEP_API_KEY
        self.zep_client = None
        self.graph_id = graph_id
        
        if self.zep_api_key:
            try:
                self.zep_client = Zep(api_key=self.zep_api_key)
            except Exception as e:
                logger.warning(f"Zep클라이언트초기화실패: {e}")
    
    def generate_profile_from_entity(
        self, 
        entity: EntityNode, 
        user_id: int,
        use_llm: bool = True
    ) -> OasisAgentProfile:
        """
        부터Zep개체생성OASIS Agent Profile
        
        Args:
            entity: Zep개체 노드
            user_id: 사용자ID（용OASIS）
            use_llm: 아니요 사용 LLM 생성 상세 페르소나
            
        Returns:
            OasisAgentProfile
        """
        entity_type = entity.get_entity_type() or "Entity"
        
        # 기본 정보
        name = entity.name
        user_name = self._generate_username(name)
        
        # 구축컨텍스트정보
        context = self._build_entity_context(entity)
        
        if use_llm:
            # 사용 LLM 생성 상세 페르소나
            profile_data = self._generate_profile_with_llm(
                entity_name=name,
                entity_type=entity_type,
                entity_summary=entity.summary,
                entity_attributes=entity.attributes,
                context=context
            )
        else:
            # 사용 규칙 생성 기본 페르소나
            profile_data = self._generate_profile_rule_based(
                entity_name=name,
                entity_type=entity_type,
                entity_summary=entity.summary,
                entity_attributes=entity.attributes
            )
        
        return OasisAgentProfile(
            user_id=user_id,
            user_name=user_name,
            name=name,
            bio=profile_data.get("bio", f"{entity_type}: {name}"),
            persona=profile_data.get("persona", entity.summary or f"A {entity_type} named {name}."),
            karma=profile_data.get("karma", random.randint(500, 5000)),
            friend_count=profile_data.get("friend_count", random.randint(50, 500)),
            follower_count=profile_data.get("follower_count", random.randint(100, 1000)),
            statuses_count=profile_data.get("statuses_count", random.randint(100, 2000)),
            age=profile_data.get("age"),
            gender=profile_data.get("gender"),
            mbti=profile_data.get("mbti"),
            country=profile_data.get("country"),
            profession=profile_data.get("profession"),
            interested_topics=profile_data.get("interested_topics", []),
            source_entity_uuid=entity.uuid,
            source_entity_type=entity_type,
        )
    
    def _generate_username(self, name: str) -> str:
        """사용자명 생성"""
        # 특수 문자 제거, 소문자로 변환
        username = name.lower().replace(" ", "_")
        username = ''.join(c for c in username if c.isalnum() or c == '_')
        
        # 추가 랜덤 접미사로 중복 방지
        suffix = random.randint(100, 999)
        return f"{username}_{suffix}"
    
    def _search_zep_for_entity(self, entity: EntityNode) -> Dict[str, Any]:
        """
        사용 Zep 그래프 혼합 검색 기능 가져오기 개체 관련의 풍부한 정보
        
        Zep 없으면 내장 혼합 검색 인터페이스, 각각 edges와 nodes 검색 후 결과 병합 필요.
        사용 병렬 요청 동시에 검색, 효율성 향상.
        
        Args:
            entity: 개체 노드 상징
            
        Returns:
            포함 facts, node_summaries, context의 딕셔너리
        """
        import concurrent.futures
        
        if not self.zep_client:
            return {"facts": [], "node_summaries": [], "context": ""}
        
        entity_name = entity.name
        
        results = {
            "facts": [],
            "node_summaries": [],
            "context": ""
        }
        
        # 반드시 graph_id가 설정되어야 검색 진행
        if not self.graph_id:
            logger.debug(f"Zep 검색 중단: graph_id가 설정되지 않음")
            return results
        
        comprehensive_query = f"{entity_name}에 대한 정보, 활동, 이벤트, 관계 및 배경"
        
        def search_edges():
            """엣지 검색(사실/관계) - 포함 재시도 메커니즘"""
            max_retries = 3
            last_exception = None
            delay = 2.0
            
            for attempt in range(max_retries):
                try:
                    return self.zep_client.graph.search(
                        query=comprehensive_query,
                        graph_id=self.graph_id,
                        limit=30,
                        scope="edges",
                        reranker="rrf"
                    )
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        logger.debug(f"Zep 엣지 검색 {attempt + 1}회 실패: {str(e)[:80]}, 재시도 중...")
                        time.sleep(delay)
                        delay *= 2
                    else:
                        logger.debug(f"Zep 엣지 검색에서 {max_retries}회 시도 후 여전히 실패: {e}")
            return None
        
        def search_nodes():
            """검색노드（개체요약）- 포함재시도메커니즘"""
            max_retries = 3
            last_exception = None
            delay = 2.0
            
            for attempt in range(max_retries):
                try:
                    return self.zep_client.graph.search(
                        query=comprehensive_query,
                        graph_id=self.graph_id,
                        limit=20,
                        scope="nodes",
                        reranker="rrf"
                    )
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        logger.debug(f"Zep 노드 검색 {attempt + 1}회 실패: {str(e)[:80]}, 재시도 중...")
                        time.sleep(delay)
                        delay *= 2
                    else:
                        logger.debug(f"Zep 노드 검색에서 {max_retries}회 시도 후 여전히 실패: {e}")
            return None
        
        try:
            # 병렬실행edges와nodes검색
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                edge_future = executor.submit(search_edges)
                node_future = executor.submit(search_nodes)
                
                # 가져오기결과
                edge_result = edge_future.result(timeout=30)
                node_result = node_future.result(timeout=30)
            
            # 엣지 검색 결과 처리
            all_facts = set()
            if edge_result and hasattr(edge_result, 'edges') and edge_result.edges:
                for edge in edge_result.edges:
                    if hasattr(edge, 'fact') and edge.fact:
                        all_facts.add(edge.fact)
            results["facts"] = list(all_facts)
            
            # 처리노드검색결과
            all_summaries = set()
            if node_result and hasattr(node_result, 'nodes') and node_result.nodes:
                for node in node_result.nodes:
                    if hasattr(node, 'summary') and node.summary:
                        all_summaries.add(node.summary)
                    if hasattr(node, 'name') and node.name and node.name != entity_name:
                        all_summaries.add(f"관련개체: {node.name}")
            results["node_summaries"] = list(all_summaries)
            
            # 종합 컨텍스트 구축
            context_parts = []
            if results["facts"]:
                context_parts.append("사실정보:\n" + "\n".join(f"- {f}" for f in results["facts"][:20]))
            if results["node_summaries"]:
                context_parts.append("관련개체:\n" + "\n".join(f"- {s}" for s in results["node_summaries"][:10]))
            results["context"] = "\n\n".join(context_parts)
            
            logger.info(f"Zep혼합검색완료: {entity_name}, 가져오기 {len(results['facts'])} 개사실, {len(results['node_summaries'])} 개관련노드")
            
        except concurrent.futures.TimeoutError:
            logger.warning(f"Zep검색시간 초과 ({entity_name})")
        except Exception as e:
            logger.warning(f"Zep검색실패 ({entity_name}): {e}")
        
        return results
    
    def _build_entity_context(self, entity: EntityNode) -> str:
        """
        구축개체의전체컨텍스트정보
        
        포함：
        1. 개체본신의엣지정보（사실）
        2. 관련노드의상세정보
        3. Zep혼합검색의풍부정보
        """
        context_parts = []
        
        # 1. 추가개체속성정보
        if entity.attributes:
            attrs = []
            for key, value in entity.attributes.items():
                if value and str(value).strip():
                    attrs.append(f"- {key}: {value}")
            if attrs:
                context_parts.append("### 개체속성\n" + "\n".join(attrs))
        
        # 2. 추가관련엣지정보（사실/관계）
        existing_facts = set()
        if entity.related_edges:
            relationships = []
            for edge in entity.related_edges:  # 수량제한없음
                fact = edge.get("fact", "")
                edge_name = edge.get("edge_name", "")
                direction = edge.get("direction", "")
                
                if fact:
                    relationships.append(f"- {fact}")
                    existing_facts.add(fact)
                elif edge_name:
                    if direction == "outgoing":
                        relationships.append(f"- {entity.name} --[{edge_name}]--> (관련개체)")
                    else:
                        relationships.append(f"- (관련개체) --[{edge_name}]--> {entity.name}")
            
            if relationships:
                context_parts.append("### 관련사실과관계\n" + "\n".join(relationships))
        
        # 3. 추가관련노드의상세정보
        if entity.related_nodes:
            related_info = []
            for node in entity.related_nodes:  # 수량제한없음
                node_name = node.get("name", "")
                node_labels = node.get("labels", [])
                node_summary = node.get("summary", "")
                
                # 기본값태그필터링
                custom_labels = [l for l in node_labels if l not in ["Entity", "Node"]]
                label_str = f" ({', '.join(custom_labels)})" if custom_labels else ""
                
                if node_summary:
                    related_info.append(f"- **{node_name}**{label_str}: {node_summary}")
                else:
                    related_info.append(f"- **{node_name}**{label_str}")
            
            if related_info:
                context_parts.append("### 관련개체정보\n" + "\n".join(related_info))
        
        # 4. 사용Zep혼합검색으로더풍부한정보가져오기
        zep_results = self._search_zep_for_entity(entity)
        
        if zep_results.get("facts"):
            # 중복: 이미존재하는사실제외
            new_facts = [f for f in zep_results["facts"] if f not in existing_facts]
            if new_facts:
                context_parts.append("### Zep검색의사실정보\n" + "\n".join(f"- {f}" for f in new_facts[:15]))
        
        if zep_results.get("node_summaries"):
            context_parts.append("### Zep검색의관련노드\n" + "\n".join(f"- {s}" for s in zep_results["node_summaries"][:10]))
        
        return "\n\n".join(context_parts)
    
    def _is_individual_entity(self, entity_type: str) -> bool:
        """개인유형개체인지판별"""
        return entity_type.lower() in self.INDIVIDUAL_ENTITY_TYPES
    
    def _is_group_entity(self, entity_type: str) -> bool:
        """집단/기관유형개체인지판별"""
        return entity_type.lower() in self.GROUP_ENTITY_TYPES
    
    def _generate_profile_with_llm(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any],
        context: str
    ) -> Dict[str, Any]:
        """
        사용LLM생성매우상세한페르소나
        
        에따라개체유형영역구분：
        - 개인개체：구체적인인물설정생성
        - 집단/기관개체：대표성계정설정생성
        """
        
        is_individual = self._is_individual_entity(entity_type)
        
        if is_individual:
            prompt = self._build_individual_persona_prompt(
                entity_name, entity_type, entity_summary, entity_attributes, context
            )
        else:
            prompt = self._build_group_persona_prompt(
                entity_name, entity_type, entity_summary, entity_attributes, context
            )

        # 여러번시도하여생성, 성공하거나최대재시도횟수에도달할때까지
        max_attempts = 3
        last_error = None
        
        for attempt in range(max_attempts):
            try:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": self._get_system_prompt(is_individual)},
                        {"role": "user", "content": prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.7 - (attempt * 0.1)  # 매번재시도시온도하락
                    # max_tokens설정안함, LLM자유롭게활용
                )
                
                content = response.choices[0].message.content
                
                # 잘림여부확인（finish_reason이'stop'인지）
                finish_reason = response.choices[0].finish_reason
                if finish_reason == 'length':
                    logger.warning(f"LLM출력잘림 (attempt {attempt+1}), 수정시도중...")
                    content = self._fix_truncated_json(content)
                
                # 시도파싱JSON
                try:
                    result = json.loads(content)
                    
                    # 필수필드검증
                    if "bio" not in result or not result["bio"]:
                        result["bio"] = entity_summary[:200] if entity_summary else f"{entity_type}: {entity_name}"
                    if "persona" not in result or not result["persona"]:
                        result["persona"] = entity_summary or f"{entity_name}는한{entity_type}입니다."
                    
                    return result
                    
                except json.JSONDecodeError as je:
                    logger.warning(f"JSON파싱실패 (attempt {attempt+1}): {str(je)[:80]}")
                    
                    # JSON수정시도
                    result = self._try_fix_json(content, entity_name, entity_type, entity_summary)
                    if result.get("_fixed"):
                        del result["_fixed"]
                        return result
                    
                    last_error = je
                    
            except Exception as e:
                logger.warning(f"LLM호출실패 (attempt {attempt+1}): {str(e)[:80]}")
                last_error = e
                import time
                time.sleep(1 * (attempt + 1))  # 지수 백오프
        
        logger.warning(f"LLM생성페르소나실패（{max_attempts}번시도）: {last_error}, 규칙사용하여생성")
        return self._generate_profile_rule_based(
            entity_name, entity_type, entity_summary, entity_attributes
        )
    
    def _fix_truncated_json(self, content: str) -> str:
        """잘림의JSON수정（출력max_tokens제한으로잘림）"""
        import re
        
        # 만약JSON잘림, 닫는것시도
        content = content.strip()
        
        # 닫히지않은괄호계산
        open_braces = content.count('{') - content.count('}')
        open_brackets = content.count('[') - content.count(']')
        
        # 닫히지않은문자열확인
        # 간단한검사: 만약마지막따옴표뒤에없으면쉼표또는닫는괄호, 문자열잘림
        if content and content[-1] not in '",}]':
            # 문자열닫는것시도
            content += '"'
        
        # 닫는 괄호
        content += ']' * open_brackets
        content += '}' * open_braces
        
        return content
    
    def _try_fix_json(self, content: str, entity_name: str, entity_type: str, entity_summary: str = "") -> Dict[str, Any]:
        """손상된 JSON 수리 시도"""
        import re
        
        # 1. 먼저 잘린 경우 수리 시도
        content = self._fix_truncated_json(content)
        
        # 2. JSON 부분 추출 시도
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            json_str = json_match.group()
            
            # 3. 처리 문자열의 줄바꿈 문제
            # 문자열 값을 찾아서 줄바꿈 기호를 교체
            def fix_string_newlines(match):
                s = match.group(0)
                # 문자열 내의 실제 줄바꿈 기호로 공백 교체
                s = s.replace('\n', ' ').replace('\r', ' ')
                # 불필요한 공백 교체
                s = re.sub(r'\s+', ' ', s)
                return s
            
            # JSON 문자열 값 매칭
            json_str = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', fix_string_newlines, json_str)
            
            # 4. 시도파싱
            try:
                result = json.loads(json_str)
                result["_fixed"] = True
                return result
            except json.JSONDecodeError as e:
                # 5. 만약 실패하면, 더 공격적인 수리 시도
                try:
                    # 제어 문자 제거
                    json_str = re.sub(r'[\x00-\x1f\x7f-\x9f]', ' ', json_str)
                    # 연속된 공백 교체
                    json_str = re.sub(r'\s+', ' ', json_str)
                    result = json.loads(json_str)
                    result["_fixed"] = True
                    return result
                except:
                    pass
        
        # 6. 내용 중 일부 정보 추출 시도
        bio_match = re.search(r'"bio"\s*:\s*"([^"]*)"', content)
        persona_match = re.search(r'"persona"\s*:\s*"([^"]*)', content)  # 잘림
        
        bio = bio_match.group(1) if bio_match else (entity_summary[:200] if entity_summary else f"{entity_type}: {entity_name}")
        persona = persona_match.group(1) if persona_match else (entity_summary or f"{entity_name} 하나 {entity_type}입니다.")
        
        # 만약 의미 있는 내용을 추출했다면, 이미 수리됨으로 표시
        if bio_match or persona_match:
            logger.info(f"손상된 JSON 중 일부 정보 추출")
            return {
                "bio": bio,
                "persona": persona,
                "_fixed": True
            }
        
        # 7. 완전 실패, 기본 구조로 돌아가기
        logger.warning(f"JSON 수리 실패, 기본 구조로 돌아가기")
        return {
            "bio": entity_summary[:200] if entity_summary else f"{entity_type}: {entity_name}",
            "persona": entity_summary or f"{entity_name} 하나 {entity_type}입니다."
        }
    
    def _get_system_prompt(self, is_individual: bool) -> str:
        """시스템 안내어 가져오기"""
        base_prompt = "당신은 소셜 미디어 사용자 이미지 생성 전문가입니다. 상세하고, 실제의 페르소나를 생성하여 여론 시뮬레이션을 하며, 최대한 현실 상황을 복원합니다. 반드시 유효한 JSON 형식으로 돌아가야 하며, 문자열 값에 이스케이프되지 않은 줄바꿈 기호가 포함되지 않아야 합니다. 사용 중입니다."
        return base_prompt
    
    def _build_individual_persona_prompt(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any],
        context: str
    ) -> str:
        """개체의 상세 페르소나 안내어 구축"""
        
        attrs_str = json.dumps(entity_attributes, ensure_ascii=False) if entity_attributes else "없음"
        context_str = context[:3000] if context else "추가 컨텍스트 없음"
        
        return f"""로 개체 생성 상세의 소셜 미디어 사용자 페르소나, 최대한 현실 상황을 복원합니다.

개체이름: {entity_name}
개체 유형: {entity_type}
개체요약: {entity_summary}
개체 속성: {attrs_str}

컨텍스트정보:
{context_str}

JSON을 생성해 주세요, 다음 필드를 포함하여:

1. bio: 소셜 미디어 소개, 200자
2. persona: 상세 페르소나 설명 (2000자의 순수 텍스트), 포함해야 할 내용:
   - 기본 정보 (나이, 직업, 교육 배경, 거주지)
   - 인물 배경 (중요 경험, 사건과의 연관, 사회 관계)
   - 성격 특성 (MBTI 유형, 핵심 성격, 감정 표현 방식)
   - 소셜 미디어 행동 (게시 빈도, 내용 선호, 상호작용 스타일, 언어 특징)
   - 입장 견해 (대화 주제에 대한 태도, 분노/감동의 내용)
   - 독특한 특성 (자주 쓰는 말, 특별한 경험, 개인 취미)
   - 개인 기억 (페르소나의 중요한 부분, 이 개체와 사건의 연관 소개, 이 개체에서 사건의 행동과 반응)
3. age: 나이 숫자 (반드시 정수)
4. gender: 성별, 반드시 영어로: "male" 또는 "female"
5. mbti: MBTI 유형 (예: INTJ, ENFP 등)
6. country: 국가 (예: "한국")
7. profession: 직업
8. interested_topics: 관심 있는 주제 수집

중요:
- 모든 필드 값은 반드시 문자열 또는 숫자여야 하며, 줄 바꿈 문자를 사용하지 마세요
- persona는 반드시 일관된 텍스트 설명이어야 합니다
- 반드시 한국어로 작성하세요 (성별 필드만 영어 male/female 사용)
- bio, persona, profession, interested_topics 등 모든 텍스트 필드는 한국어로 작성
- 소셜 미디어 게시물도 한국어로 작성하는 캐릭터로 설정
- 내용과 개체 정보는 일치해야 합니다
- age는 반드시 유효한 정수여야 하며, gender는 반드시 "male" 또는 "female"이어야 합니다
"""

    def _build_group_persona_prompt(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any],
        context: str
    ) -> str:
        """기관/집단 개체의 상세 페르소나 안내문"""
        
        attrs_str = json.dumps(entity_attributes, ensure_ascii=False) if entity_attributes else "없음"
        context_str = context[:3000] if context else "추가 컨텍스트 없음"
        
        return f"""로 기관/집단 개체 생성 상세의 소셜 미디어 계정 설정, 최대한 현실 상황을 재현합니다.

개체이름: {entity_name}
개체 유형: {entity_type}
개체요약: {entity_summary}
개체 속성: {attrs_str}

컨텍스트정보:
{context_str}

JSON을 생성해 주세요, 포함하여 아래 필드:

1. bio: 공식 계정 소개, 200자, 전문적
2. persona: 상세 계정 설정 설명(2000자의 순수 텍스트), 포함해야 할 내용:
   - 기관 기본 정보(정식 이름, 기관 성격, 설립 배경, 주요 직무)
   - 계정 위치(계정 유형, 목표 청중, 핵심 기능)  
   - 발언 스타일(언어 특징, 자주 사용하는 표현, 금기 주제)
   - 게시 내용 특징(내용 유형, 게시 빈도, 활동 시간대)
   - 입장 태도(핵심 주제의 공식 입장, 논란 처리 방식)
   - 특별 설명(대표의 집단 이미지, 운영 습관)
   - 기관 기억(기관 페르소나의 중요한 부분, 이 기관과 이벤트의 연관성 소개, 이 기관에서 이벤트의 이미 행동과 반응)
3. age: 고정 입력 30(기관 계정의 가상 나이)
4. gender: 고정 입력 "other"(기관 계정 사용 other는 개인이 아님을 나타냄)
5. mbti: MBTI 유형, 계정 스타일 설명, 예: ISTJ는 엄격하고 보수적임을 나타냄
6. country: 국가 (예: "한국")
7. profession: 기관 직무 설명
8. interested_topics: 관심 분야 수집

중요:
- 모든 필드 값은 반드시 문자열 또는 숫자여야 하며, null 값은 허용되지 않습니다
- persona는 반드시 일관된 텍스트 설명이어야 하며, 줄 바꿈 문자를 사용하지 마세요
- 반드시 한국어로 작성하세요 (성별 필드만 영어 "other" 사용)
- bio, persona, profession, interested_topics 등 모든 텍스트 필드는 한국어로 작성
- 소셜 미디어 게시물도 한국어로 작성하는 계정으로 설정
- age는 반드시 정수 30이어야 하며, gender는 반드시 문자열 "other"이어야 합니다
- 기관 계정 발언은 그 신분에 맞아야 합니다"""
    
    def _generate_profile_rule_based(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any]
    ) -> Dict[str, Any]:
        """사용 규칙 생성 기본 페르소나"""
        
        # 에 따라 개체 유형 생성안 동일한 페르소나
        entity_type_lower = entity_type.lower()
        
        if entity_type_lower in ["student", "alumni"]:
            return {
                "bio": f"{entity_type} with interests in academics and social issues.",
                "persona": f"{entity_name} is a {entity_type.lower()} who is actively engaged in academic and social discussions. They enjoy sharing perspectives and connecting with peers.",
                "age": random.randint(18, 30),
                "gender": random.choice(["male", "female"]),
                "mbti": random.choice(self.MBTI_TYPES),
                "country": random.choice(self.COUNTRIES),
                "profession": "Student",
                "interested_topics": ["Education", "Social Issues", "Technology"],
            }
        
        elif entity_type_lower in ["publicfigure", "expert", "faculty"]:
            return {
                "bio": f"Expert and thought leader in their field.",
                "persona": f"{entity_name} is a recognized {entity_type.lower()} who shares insights and opinions on important matters. They are known for their expertise and influence in public discourse.",
                "age": random.randint(35, 60),
                "gender": random.choice(["male", "female"]),
                "mbti": random.choice(["ENTJ", "INTJ", "ENTP", "INTP"]),
                "country": random.choice(self.COUNTRIES),
                "profession": entity_attributes.get("occupation", "Expert"),
                "interested_topics": ["Politics", "Economics", "Culture & Society"],
            }
        
        elif entity_type_lower in ["mediaoutlet", "socialmediaplatform"]:
            return {
                "bio": f"Official account for {entity_name}. News and updates.",
                "persona": f"{entity_name} is a media entity that reports news and facilitates public discourse. The account shares timely updates and engages with the audience on current events.",
                "age": 30,  # 기관 가상 나이
                "gender": "other",  # 기관 사용 other
                "mbti": "ISTJ",  # 기관 스타일: 엄격하고 보수적
                "country": "중국",
                "profession": "Media",
                "interested_topics": ["General News", "Current Events", "Public Affairs"],
            }
        
        elif entity_type_lower in ["university", "governmentagency", "ngo", "organization"]:
            return {
                "bio": f"Official account of {entity_name}.",
                "persona": f"{entity_name} is an institutional entity that communicates official positions, announcements, and engages with stakeholders on relevant matters.",
                "age": 30,  # 기관 가상 나이
                "gender": "other",  # 기관사용other
                "mbti": "ISTJ",  # 기관스타일：엄격보수
                "country": "중국",
                "profession": entity_type,
                "interested_topics": ["Public Policy", "Community", "Official Announcements"],
            }
        
        else:
            # 기본값페르소나
            return {
                "bio": entity_summary[:150] if entity_summary else f"{entity_type}: {entity_name}",
                "persona": entity_summary or f"{entity_name} is a {entity_type.lower()} participating in social discussions.",
                "age": random.randint(25, 50),
                "gender": random.choice(["male", "female"]),
                "mbti": random.choice(self.MBTI_TYPES),
                "country": random.choice(self.COUNTRIES),
                "profession": entity_type,
                "interested_topics": ["General", "Social Issues"],
            }
    
    def set_graph_id(self, graph_id: str):
        """그래프ID용Zep검색설정"""
        self.graph_id = graph_id
    
    def generate_profiles_from_entities(
        self,
        entities: List[EntityNode],
        use_llm: bool = True,
        progress_callback: Optional[callable] = None,
        graph_id: Optional[str] = None,
        parallel_count: int = 5,
        realtime_output_path: Optional[str] = None,
        output_platform: str = "reddit"
    ) -> List[OasisAgentProfile]:
        """
        배치부터개체생성Agent Profile（지원병렬생성）
        
        Args:
            entities: 개체목록
            use_llm: 아니요사용LLM생성상세페르소나
            progress_callback: 진행률콜백함수 (current, total, message)
            graph_id: 그래프ID，용Zep검색가져오기더풍부한컨텍스트
            parallel_count: 병렬생성수량，기본값5
            realtime_output_path: 실시간쓰기파일경로（만약제공되면,각생성마다한번쓰기）
            output_platform: 출력플랫폼형식 ("reddit" 또는 "twitter")
            
        Returns:
            Agent Profile목록
        """
        import concurrent.futures
        from threading import Lock
        
        # 그래프ID용Zep검색설정
        if graph_id:
            self.graph_id = graph_id
        
        total = len(entities)
        profiles = [None] * total  # 미리할당목록유지순서
        completed_count = [0]  # 사용목록으로하여클로저중수정
        lock = Lock()
        
        # 실시간쓰기파일의보조함수
        def save_profiles_realtime():
            """실시간저장이미생성의 profiles 파일"""
            if not realtime_output_path:
                return
            
            with lock:
                # 필터이미생성된profiles
                existing_profiles = [p for p in profiles if p is not None]
                if not existing_profiles:
                    return
                
                try:
                    if output_platform == "reddit":
                        # Reddit JSON 형식
                        profiles_data = [p.to_reddit_format() for p in existing_profiles]
                        with open(realtime_output_path, 'w', encoding='utf-8') as f:
                            json.dump(profiles_data, f, ensure_ascii=False, indent=2)
                    else:
                        # Twitter CSV 형식
                        import csv
                        profiles_data = [p.to_twitter_format() for p in existing_profiles]
                        if profiles_data:
                            fieldnames = list(profiles_data[0].keys())
                            with open(realtime_output_path, 'w', encoding='utf-8', newline='') as f:
                                writer = csv.DictWriter(f, fieldnames=fieldnames)
                                writer.writeheader()
                                writer.writerows(profiles_data)
                except Exception as e:
                    logger.warning(f"실시간저장 profiles 실패: {e}")
        
        def generate_single_profile(idx: int, entity: EntityNode) -> tuple:
            """단일profile생성의작업함수"""
            entity_type = entity.get_entity_type() or "Entity"
            
            try:
                profile = self.generate_profile_from_entity(
                    entity=entity,
                    user_id=idx,
                    use_llm=use_llm
                )
                
                # 실시간출력생성의페르소나콘솔과로그
                self._print_generated_profile(entity.name, entity_type, profile)
                
                return idx, profile, None
                
            except Exception as e:
                logger.error(f"생성개체 {entity.name} 의페르소나실패: {str(e)}")
                # 하나기본profile생성
                fallback_profile = OasisAgentProfile(
                    user_id=idx,
                    user_name=self._generate_username(entity.name),
                    name=entity.name,
                    bio=f"{entity_type}: {entity.name}",
                    persona=entity.summary or f"A participant in social discussions.",
                    source_entity_uuid=entity.uuid,
                    source_entity_type=entity_type,
                )
                return idx, fallback_profile, str(e)
        
        logger.info(f"시작병렬생성 {total} 개Agent페르소나（병렬수: {parallel_count}）...")
        print(f"\n{'='*60}")
        print(f"시작생성Agent페르소나 - 총 {total} 개체，병렬수: {parallel_count}")
        print(f"{'='*60}\n")
        
        # 사용스레드풀병렬실행
        with concurrent.futures.ThreadPoolExecutor(max_workers=parallel_count) as executor:
            # 제출작업
            future_to_entity = {
                executor.submit(generate_single_profile, idx, entity): (idx, entity)
                for idx, entity in enumerate(entities)
            }
            
            # 결과수집
            for future in concurrent.futures.as_completed(future_to_entity):
                idx, entity = future_to_entity[future]
                entity_type = entity.get_entity_type() or "Entity"
                
                try:
                    result_idx, profile, error = future.result()
                    profiles[result_idx] = profile
                    
                    with lock:
                        completed_count[0] += 1
                        current = completed_count[0]
                    
                    # 실시간쓰기파일
                    save_profiles_realtime()
                    
                    if progress_callback:
                        progress_callback(
                            current, 
                            total, 
                            f"완료 {current}/{total}: {entity.name}（{entity_type}）"
                        )
                    
                    if error:
                        logger.warning(f"[{current}/{total}] {entity.name} 사용예비페르소나: {error}")
                    else:
                        logger.info(f"[{current}/{total}] 성공생성페르소나: {entity.name} ({entity_type})")
                        
                except Exception as e:
                    logger.error(f"처리개체 {entity.name} 시발생예외: {str(e)}")
                    with lock:
                        completed_count[0] += 1
                    profiles[idx] = OasisAgentProfile(
                        user_id=idx,
                        user_name=self._generate_username(entity.name),
                        name=entity.name,
                        bio=f"{entity_type}: {entity.name}",
                        persona=entity.summary or "A participant in social discussions.",
                        source_entity_uuid=entity.uuid,
                        source_entity_type=entity_type,
                    )
                    # 실시간쓰기파일（예비페르소나일지라도）
                    save_profiles_realtime()
        
        print(f"\n{'='*60}")
        print(f"페르소나생성완료！총생성 {len([p for p in profiles if p])} 개Agent")
        print(f"{'='*60}\n")
        
        return profiles
    
    def _print_generated_profile(self, entity_name: str, entity_type: str, profile: OasisAgentProfile):
        """실시간출력생성의페르소나콘솔（완전내용，안잘림）"""
        separator = "-" * 70
        
        # 완전출력내용구축（안잘림）
        topics_str = ', '.join(profile.interested_topics) if profile.interested_topics else '없음'
        
        output_lines = [
            f"\n{separator}",
            f"[이미생성] {entity_name} ({entity_type})",
            f"{separator}",
            f"사용자명: {profile.user_name}",
            f"",
            f"【소개】",
            f"{profile.bio}",
            f"",
            f"【상세페르소나】",
            f"{profile.persona}",
            f"",
            f"【기본속성】",
            f"나이: {profile.age} | 성별: {profile.gender} | MBTI: {profile.mbti}",
            f"직업: {profile.profession} | 국가: {profile.country}",
            f"관심주제: {topics_str}",
            separator
        ]
        
        output = "\n".join(output_lines)
        
        # 콘솔에만출력（중복방지, logger안에서다시출력완전내용）
        print(output)
    
    def save_profiles(
        self,
        profiles: List[OasisAgentProfile],
        file_path: str,
        platform: str = "reddit"
    ):
        """
        프로필파일저장（플랫폼에따라올바른형식선택）
        
        OASIS플랫폼형식요구사항：
        - Twitter: CSV형식
        - Reddit: JSON형식
        
        Args:
            profiles: Profile목록
            file_path: 파일경로
            platform: 플랫폼유형 ("reddit" 또는 "twitter")
        """
        if platform == "twitter":
            self._save_twitter_csv(profiles, file_path)
        else:
            self._save_reddit_json(profiles, file_path)
    
    def _save_twitter_csv(self, profiles: List[OasisAgentProfile], file_path: str):
        """
        Twitter 프로필CSV형식으로저장（OASIS공식요구사항에부합）
        
        OASIS Twitter요구사항의CSV필드：
        - user_id: 사용자ID（CSV순서에따라0부터시작）
        - name: 사용자 실제 이름
        - username: 시스템의 사용자명
        - user_char: 상세 페르소나 설명（주입 LLM 시스템 안내 중, 안내 Agent 행동)
        - description: 간단한 공개 소개（표시에서 사용자 프로필 페이지)
        
        user_char vs description 영역 구분:
        - user_char: 내부 사용, LLM 시스템 안내, Agent가 어떻게 생각하고 행동할지 결정
        - description: 외부 표시, 기타 사용자 의견의 소개
        """
        import csv
        
        # 보장 파일 확장명 .csv
        if not file_path.endswith('.csv'):
            file_path = file_path.replace('.json', '.csv')
        
        with open(file_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            
            # OASIS 요구사항의 표제 작성
            headers = ['user_id', 'name', 'username', 'user_char', 'description']
            writer.writerow(headers)
            
            # 데이터 행 작성
            for idx, profile in enumerate(profiles):
                # user_char: 완전한 페르소나（bio + persona), LLM 시스템 안내 용
                user_char = profile.bio
                if profile.persona and profile.persona != profile.bio:
                    user_char = f"{profile.bio} {profile.persona}"
                # 처리 개행 문자（CSV 중 공백으로 대체)
                user_char = user_char.replace('\n', ' ').replace('\r', ' ')
                
                # description: 간단한 소개, 외부 표시 용
                description = profile.bio.replace('\n', ' ').replace('\r', ' ')
                
                row = [
                    idx,                    # user_id: 0부터 시작하는 순서 ID
                    profile.name,           # name: 실제 이름
                    profile.user_name,      # username: 사용자명
                    user_char,              # user_char: 완전한 페르소나（내부 LLM 사용)
                    description             # description: 간단한 소개（외부 표시)
                ]
                writer.writerow(row)
        
        logger.info(f"이미저장 {len(profiles)} 개Twitter Profile {file_path} (OASIS CSV형식)")
    
    def _normalize_gender(self, gender: Optional[str]) -> str:
        """
        표준화 gender 필드로 OASIS 요구사항의 영어 형식
        
        OASIS요구사항: male, female, other
        """
        if not gender:
            return "other"
        
        gender_lower = gender.lower().strip()
        
        # 중문 매핑
        gender_map = {
            "남성": "male",
            "여성": "female",
            "기관": "other",
            "기타": "other",
            # 영어 이미지
            "male": "male",
            "female": "female",
            "other": "other",
        }
        
        return gender_map.get(gender_lower, "other")
    
    def _save_reddit_json(self, profiles: List[OasisAgentProfile], file_path: str):
        """
        저장Reddit Profile로JSON형식
        
        사용과 to_reddit_format() 일치의 형식, 보장 OASIS 정확히 읽기.
        반드시 포함 user_id 필드, 이 OASIS agent_graph.get_agent() 매칭의 핵심!
        
        필수 필드:
        - user_id: 사용자 ID（정수, 초기_posts의 poster_agent_id와 매칭 용)
        - username: 사용자명
        - name: 표시이름
        - bio: 소개
        - persona: 상세 페르소나
        - age: 나이（정수)
        - gender: "male", "female", 또는 "other"
        - mbti: MBTI유형
        - country: 국가
        """
        data = []
        for idx, profile in enumerate(profiles):
            # 사용과 to_reddit_format() 일치의 형식
            item = {
                "user_id": profile.user_id if profile.user_id is not None else idx,  # 핵심: 반드시 포함 user_id
                "username": profile.user_name,
                "name": profile.name,
                "bio": profile.bio[:150] if profile.bio else f"{profile.name}",
                "persona": profile.persona or f"{profile.name} is a participant in social discussions.",
                "karma": profile.karma if profile.karma else 1000,
                "created_at": profile.created_at,
                # OASIS 필수 필드 - 보장 모두 기본값
                "age": profile.age if profile.age else 30,
                "gender": self._normalize_gender(profile.gender),
                "mbti": profile.mbti if profile.mbti else "ISTJ",
                "country": profile.country if profile.country else "중국",
            }
            
            # 선택 필드
            if profile.profession:
                item["profession"] = profile.profession
            if profile.interested_topics:
                item["interested_topics"] = profile.interested_topics
            
            data.append(item)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"이미 저장 {len(profiles)} 개 Reddit 프로필 {file_path} (JSON 형식, 포함 user_id 필드)")
    
    # 기존 메서드 이름을 별칭으로 보존, 후방 호환성 유지
    def save_profiles_to_json(
        self,
        profiles: List[OasisAgentProfile],
        file_path: str,
        platform: str = "reddit"
    ):
        """[이미 폐기됨] 사용 save_profiles() 메서드"""
        logger.warning("save_profiles_to_json 이미 폐기됨, 사용 save_profiles 메서드")
        self.save_profiles(profiles, file_path, platform)

