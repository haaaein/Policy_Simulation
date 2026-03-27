"""
온톨로지 생성서비스
정책 문서 내용을 분석하고, 적합한 정책 파급효과 시뮬레이션의 개체와 관계 유형을 정의합니다.
"""

import json
from typing import Dict, Any, List, Optional
from ..utils.llm_client import LLMClient


# 온톨로지 생성의 시스템 안내문
ONTOLOGY_SYSTEM_PROMPT = """당신은 전문적인 지식 그래프 온톨로지 설계 전문가입니다. 당신의 작업은 주어진 정책 문서 내용과 시뮬레이션 요구 사항을 분석하고, 적합한 **정책 파급효과 시뮬레이션**의 개체 유형과 관계 유형을 설계하는 것입니다.

**중요: 당신은 반드시 유효한 JSON 형식 데이터를 출력해야 하며, 기타 내용을 출력하지 마십시오.**

## 핵심 작업 배경

우리는 **정책 파급효과 시뮬레이션 시스템**을 구축하고 있습니다. 이 시스템에서:
- 각 개체는 정책의 영향을 받거나 정책을 시행하는 **이해관계자(stakeholder)**입니다.
- 개체 간에는 예산 배분, 평가, 규제, 협력, 경쟁 등의 관계가 존재합니다.
- 우리는 정책 시행 시 각 이해관계자의 반응과 파급효과 전파 경로를 시뮬레이션합니다.

따라서, **개체는 반드시 정책 생태계에서 실제로 행동하고 반응하는 주체여야 합니다**:

**적합한 개체 유형**:
- 정부 부처, 규제 기관 (교육부, 과기정통부, 국무조정실 등)
- 대학, 연구기관 (서울대, 포항공대, 한국연구재단 등)
- 기업, 산업체 (대기업, 중소기업, 스타트업 등)
- 수혜자 집단 (대학원생, 신진연구자, 교수, 학부생 등)
- 평가 기관, 위원회 (사업관리위원회, 평가위원회 등)
- 지자체, 지역 거버넌스
- 특정 프로그램/사업 (BK21, 지방대학 활성화 등)

**부적합한 개체 유형**:
- 추상 개념 (예: "교육 품질", "연구 역량", "국제 경쟁력")
- 지표/수치 (예: "취업률", "논문 수")
- 의견/태도 (예: "찬성 측", "반대 측")

## 출력형식

JSON 형식으로 출력해 주십시오. 아래 구조를 포함해야 합니다:

```json
{
    "entity_types": [
        {
            "name": "개체 유형 이름(영어, PascalCase)",
            "description": "간단한 설명(영어, 100자를 넘지 않음)",
            "attributes": [
                {
                    "name": "속성명(영어, snake_case)",
                    "type": "text",
                    "description": "속성 설명"
                }
            ],
            "examples": ["예시 개체1", "예시 개체2"]
        }
    ],
    "edge_types": [
        {
            "name": "관계 유형 이름(영어, UPPER_SNAKE_CASE)",
            "description": "간단한 설명(영어, 100자를 넘지 않음)",
            "source_targets": [
                {"source": "출발 개체 유형", "target": "목표 개체 유형"}
            ],
            "attributes": []
        }
    ],
    "analysis_summary": "정책 문서 내용의 간단한 분석 설명(한국어)"
}
```

## 설계 가이드(매우 중요!)

### 1. 개체 유형 설계 - 반드시 엄격히 준수

**수량 요구 사항: 반드시 정확히 10개 개체 유형**

**계층 구조 요구 사항(반드시 동시에 구체 유형과 보편 유형을 포함)**:

당신의 10개 개체 유형은 반드시 아래 계층을 포함해야 합니다:

A. **보편 유형(반드시 포함, 목록의 마지막 2개에 배치)**:
   - `Person`: 자연인 개체의 보편 유형입니다. 만약 한 사람이 다른 더 구체적인 인물 유형에 속할 경우, 이 클래스에 포함됩니다.
   - `Organization`: 조직 기관의 보편 유형입니다. 만약 한 조직이 다른 더 구체적인 조직 유형에 속할 경우, 이 클래스에 포함됩니다.

B. **구체 유형(8개, 텍스트 내용에 따라 설계)**:
   - 텍스트 중 등장하는 주요 역할에 따라 더 구체적인 유형을 설계합니다.
   - 예를 들어: 만약 텍스트가 학술 이벤트와 관련이 있다면, `Student`, `Professor`, `University`와 같이 설계합니다.
   - 예를 들어: 만약 텍스트가 상업적 이벤트와 관련이 있다면, `Company`, `CEO`, `Employee`

**로 무엇이 필요할지 유형**：
- 텍스트 중에 다양한 인물이 등장하는 경우, 예를 들어 "중학교 교사", "행인甲", "어떤 네티즌"
- 만약 없으면 전문의 유형 매칭, 그들은 `Person`에 포함되어야 합니다.
- 마찬가지로, 소규모 조직, 임시 단체 등은 `Organization`에 포함되어야 합니다.

**구체적 유형의 설계 원칙**：
- 텍스트 중에서 고빈도로 등장하거나 중요한 역할 유형을 식별합니다.
- 각 구체적 유형은 명확한 경계를 가져야 하며, 중복을 피해야 합니다.
- description은 반드시 이 유형과 기본 유형의 영역을 명확히 설명해야 합니다.

### 2. 관계유형설계

- 수량：6-10개
- 관계는 정책 생태계에서의 실제 관계(예산 배분, 평가, 규제, 협력 등)를 반영해야 합니다.
- 관계의 source_targets는 당신이 정의한 개체 유형을 포함해야 합니다.

### 3. 속성 설계

- 각 개체 유형은 1-3개의 주요 속성을 가집니다.
- **주의**: 속성명으로 `name`, `uuid`, `group_id`, `created_at`, `summary`(이들은 시스템 예약어입니다)를 사용하지 마십시오.
- 추천 사용: `full_name`, `title`, `role`, `position`, `location`, `description` 등

## 개체 유형 참고

**개인 클래스（구체적）**：
- Student: 학생
- Professor: 교수/학자
- Journalist: 기자
- Celebrity: 스타/인플루언서
- Executive: 고위 경영자
- Official: 정부 관료
- Lawyer: 변호사
- Doctor: 의사

**개인 클래스（기본）**：
- Person: 자연인(위의 구체적 유형에 속하지 않을 때 사용)

**조직 클래스（구체적）**：
- University: 대학
- Company: 회사
- GovernmentAgency: 정부 기관
- MediaOutlet: 미디어 기관
- Hospital: 병원
- School: 중학교/초등학교
- NGO: 비정부 조직

**조직 클래스（기본）**：
- Organization: 모든 조직 기관(위의 구체적 유형에 속하지 않을 때 사용)

## 관계 유형 참고

- WORKS_FOR: 근무하다
- STUDIES_AT: 학습하다
- AFFILIATED_WITH: 소속
- REPRESENTS: 대표
- REGULATES: 규제
- REPORTS_ON: 보도
- COMMENTS_ON: 댓글
- RESPONDS_TO: 응답
- SUPPORTS: 지원
- OPPOSES: 반대
- COLLABORATES_WITH: 협력
- COMPETES_WITH: 경쟁
"""


class OntologyGenerator:
    """
    온톨로지 생성기
    분석 텍스트 내용, 생성 개체와 관계 유형 정의
    """
    
    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or LLMClient()
    
    def generate(
        self,
        document_texts: List[str],
        simulation_requirement: str,
        additional_context: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        생성 온톨로지 정의
        
        Args:
            document_texts: 문서텍스트목록
            simulation_requirement: 시뮬레이션 요구사항설명
            additional_context: 추가컨텍스트
            
        Returns:
            온톨로지 정의 (entity_types, edge_types 등)
        """
        # 구축사용자메시지
        user_message = self._build_user_message(
            document_texts, 
            simulation_requirement,
            additional_context
        )
        
        messages = [
            {"role": "system", "content": ONTOLOGY_SYSTEM_PROMPT},
            {"role": "user", "content": user_message}
        ]
        
        # 호출LLM
        result = self.llm_client.chat_json(
            messages=messages,
            temperature=0.3,
            max_tokens=4096
        )
        
        # 검증와후처리
        result = self._validate_and_process(result)
        
        return result
    
    # LLM의 텍스트 최대 길이 (5만 자)
    MAX_TEXT_LENGTH_FOR_LLM = 50000
    
    def _build_user_message(
        self,
        document_texts: List[str],
        simulation_requirement: str,
        additional_context: Optional[str]
    ) -> str:
        """구축사용자메시지"""
        
        # 텍스트 병합
        combined_text = "\n\n---\n\n".join(document_texts)
        original_length = len(combined_text)
        
        # 만약 텍스트가 5만 자를 초과하면, 잘라냄 (LLM의 내용에만 영향을 미치고, 그래프 구축에는 영향을 미치지 않음)
        if len(combined_text) > self.MAX_TEXT_LENGTH_FOR_LLM:
            combined_text = combined_text[:self.MAX_TEXT_LENGTH_FOR_LLM]
            combined_text += f"\n\n...(원문 총 {original_length}자, 이미 잘라낸 앞 {self.MAX_TEXT_LENGTH_FOR_LLM}자 용 온톨로지 분석)..."
        
        message = f"""## 시뮬레이션 요구사항

{simulation_requirement}

## 문서내용

{combined_text}
"""
        
        if additional_context:
            message += f"""
## 추가 설명

{additional_context}
"""
        
        message += """
요청에 따라 위 내용을 바탕으로, 정책 파급효과 시뮬레이션에 적합한 개체 유형과 관계 유형을 정의합니다.

**반드시 준수해야 할 규칙**：
1. 반드시 정확히 10개 개체 유형을 출력해야 합니다.
2. 마지막 2개는 반드시 기본 유형: Person (개인 기본)과 Organization (조직 기본)이어야 합니다.
3. 앞의 8개는 텍스트 내용에 따라 설계된 구체적인 유형이어야 합니다.
4. 모든 개체 유형은 반드시 현실에서 발언하는 주체여야 하며, 추상 개념은 포함하지 않아야 합니다.
5. 속성명으로 name, uuid, group_id 등의 예약어를 사용하지 말고, full_name, org_name 등으로 대체해야 합니다.
"""
        
        return message
    
    def _validate_and_process(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """검증와후처리결과"""
        
        # 필수 필드 보장
        if "entity_types" not in result:
            result["entity_types"] = []
        if "edge_types" not in result:
            result["edge_types"] = []
        if "analysis_summary" not in result:
            result["analysis_summary"] = ""
        
        # 검증개체 유형
        for entity in result["entity_types"]:
            if "attributes" not in entity:
                entity["attributes"] = []
            if "examples" not in entity:
                entity["examples"] = []
            # description이 100자를 초과하지 않도록 보장
            if len(entity.get("description", "")) > 100:
                entity["description"] = entity["description"][:97] + "..."
        
        # 검증관계유형
        for edge in result["edge_types"]:
            if "source_targets" not in edge:
                edge["source_targets"] = []
            if "attributes" not in edge:
                edge["attributes"] = []
            if len(edge.get("description", "")) > 100:
                edge["description"] = edge["description"][:97] + "..."
        
        # Zep API 제한: 최대 10개 사용자 정의 개체 유형, 최대 10개 사용자 정의 엣지 유형
        MAX_ENTITY_TYPES = 10
        MAX_EDGE_TYPES = 10
        
        # 기본 유형 정의
        person_fallback = {
            "name": "Person",
            "description": "Any individual person not fitting other specific person types.",
            "attributes": [
                {"name": "full_name", "type": "text", "description": "Full name of the person"},
                {"name": "role", "type": "text", "description": "Role or occupation"}
            ],
            "examples": ["ordinary citizen", "anonymous netizen"]
        }
        
        organization_fallback = {
            "name": "Organization",
            "description": "Any organization not fitting other specific organization types.",
            "attributes": [
                {"name": "org_name", "type": "text", "description": "Name of the organization"},
                {"name": "org_type", "type": "text", "description": "Type of organization"}
            ],
            "examples": ["small business", "community group"]
        }
        
        # 이미 기본 유형인지 확인
        entity_names = {e["name"] for e in result["entity_types"]}
        has_person = "Person" in entity_names
        has_organization = "Organization" in entity_names
        
        # 추가해야 할 기본 유형
        fallbacks_to_add = []
        if not has_person:
            fallbacks_to_add.append(person_fallback)
        if not has_organization:
            fallbacks_to_add.append(organization_fallback)
        
        if fallbacks_to_add:
            current_count = len(result["entity_types"])
            needed_slots = len(fallbacks_to_add)
            
            # 만약 추가 후 10개를 초과하면, 일부 기존 유형을 제거해야 함
            if current_count + needed_slots > MAX_ENTITY_TYPES:
                # 제거해야 할 개수 계산
                to_remove = current_count + needed_slots - MAX_ENTITY_TYPES
                # 끝에서부터 제거 (앞쪽의 더 중요한 구체적인 유형을 보존)
                result["entity_types"] = result["entity_types"][:-to_remove]
            
            # 추가 기본 유형
            result["entity_types"].extend(fallbacks_to_add)
        
        # 최종적으로 제한을 초과하지 않도록 보장 (방어적 프로그래밍)
        if len(result["entity_types"]) > MAX_ENTITY_TYPES:
            result["entity_types"] = result["entity_types"][:MAX_ENTITY_TYPES]
        
        if len(result["edge_types"]) > MAX_EDGE_TYPES:
            result["edge_types"] = result["edge_types"][:MAX_EDGE_TYPES]
        
        return result
    
    def generate_python_code(self, ontology: Dict[str, Any]) -> str:
        """
        온톨로지 정의를 Python 코드로 변환 (클래스는 ontology.py와 유사)
        
        Args:
            ontology: 온톨로지 정의
            
        Returns:
            Python 코드 문자열
        """
        code_lines = [
            '"""',
            '사용자 정의 개체 유형 정의',
            'PolicySim에 의해 자동 생성된, 정책 파급효과 시뮬레이션',
            '"""',
            '',
            'from pydantic import Field',
            'from zep_cloud.external_clients.ontology import EntityModel, EntityText, EdgeModel',
            '',
            '',
            '# ============== 개체 유형 정의 ==============',
            '',
        ]
        
        # 생성개체 유형
        for entity in ontology.get("entity_types", []):
            name = entity["name"]
            desc = entity.get("description", f"A {name} entity.")
            
            code_lines.append(f'class {name}(EntityModel):')
            code_lines.append(f'    """{desc}"""')
            
            attrs = entity.get("attributes", [])
            if attrs:
                for attr in attrs:
                    attr_name = attr["name"]
                    attr_desc = attr.get("description", attr_name)
                    code_lines.append(f'    {attr_name}: EntityText = Field(')
                    code_lines.append(f'        description="{attr_desc}",')
                    code_lines.append(f'        default=None')
                    code_lines.append(f'    )')
            else:
                code_lines.append('    pass')
            
            code_lines.append('')
            code_lines.append('')
        
        code_lines.append('# ============== 관계 유형 정의 ==============')
        code_lines.append('')
        
        # 생성관계유형
        for edge in ontology.get("edge_types", []):
            name = edge["name"]
            # PascalCase 클래스명으로 변환
            class_name = ''.join(word.capitalize() for word in name.split('_'))
            desc = edge.get("description", f"A {name} relationship.")
            
            code_lines.append(f'class {class_name}(EdgeModel):')
            code_lines.append(f'    """{desc}"""')
            
            attrs = edge.get("attributes", [])
            if attrs:
                for attr in attrs:
                    attr_name = attr["name"]
                    attr_desc = attr.get("description", attr_name)
                    code_lines.append(f'    {attr_name}: EntityText = Field(')
                    code_lines.append(f'        description="{attr_desc}",')
                    code_lines.append(f'        default=None')
                    code_lines.append(f'    )')
            else:
                code_lines.append('    pass')
            
            code_lines.append('')
            code_lines.append('')
        
        # 생성 유형 딕셔너리
        code_lines.append('# ============== 유형설정 ==============')
        code_lines.append('')
        code_lines.append('ENTITY_TYPES = {')
        for entity in ontology.get("entity_types", []):
            name = entity["name"]
            code_lines.append(f'    "{name}": {name},')
        code_lines.append('}')
        code_lines.append('')
        code_lines.append('EDGE_TYPES = {')
        for edge in ontology.get("edge_types", []):
            name = edge["name"]
            class_name = ''.join(word.capitalize() for word in name.split('_'))
            code_lines.append(f'    "{name}": {class_name},')
        code_lines.append('}')
        code_lines.append('')
        
        # 생성 엣지의 source_targets 매핑
        code_lines.append('EDGE_SOURCE_TARGETS = {')
        for edge in ontology.get("edge_types", []):
            name = edge["name"]
            source_targets = edge.get("source_targets", [])
            if source_targets:
                st_list = ', '.join([
                    f'{{"source": "{st.get("source", "Entity")}", "target": "{st.get("target", "Entity")}"}}'
                    for st in source_targets
                ])
                code_lines.append(f'    "{name}": [{st_list}],')
        code_lines.append('}')
        
        return '\n'.join(code_lines)

