"""
Report Agent서비스
사용LangChain + Zep实现ReACT모드의시뮬레이션보고서 생성

기능:
1. 에 따라시뮬레이션 요구사항와Zep그래프정보생성보고서
2. 먼저 계획디렉토리구조, 그 다음단계별생성
3. 각 단계채택ReACT다회차 사고및 반성 모식
4. 지원과사용자대화, 에서대화중자기메인호출검색도구
"""

import os
import json
import time
import re
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..config import Config
from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger
from .zep_tools import (
    ZepToolsService, 
    SearchResult, 
    InsightForgeResult, 
    PanoramaResult,
    InterviewResult
)

logger = get_logger('mirofish.report_agent')


class ReportLogger:
    """
    Report Agent 상세로그기록기
    
    에서보고서파일夹중생성 agent_log.jsonl 파일, 기록매 단계 상세액션。
    매행 하나의 완전한 JSON 객체, 포함시간스탬프、액션유형、상세내용등。
    """
    
    def __init__(self, report_id: str):
        """
        초기화로그기록기
        
        Args:
            report_id: 보고서ID, 용확인로그파일경로
        """
        self.report_id = report_id
        self.log_file_path = os.path.join(
            Config.UPLOAD_FOLDER, 'reports', report_id, 'agent_log.jsonl'
        )
        self.start_time = datetime.now()
        self._ensure_log_file()
    
    def _ensure_log_file(self):
        """보장로그파일이 저장될 디렉토리"""
        log_dir = os.path.dirname(self.log_file_path)
        os.makedirs(log_dir, exist_ok=True)
    
    def _get_elapsed_time(self) -> float:
        """가져오기부터 시작하는 현재의 소요시간(초)"""
        return (datetime.now() - self.start_time).total_seconds()
    
    def log(
        self, 
        action: str, 
        stage: str,
        details: Dict[str, Any],
        section_title: str = None,
        section_index: int = None
    ):
        """
        기록하나의 로그
        
        Args:
            action: 액션유형, 예: 'start', 'tool_call', 'llm_response', 'section_complete' 등
            stage: 현재 단계, 예: 'planning', 'generating', 'completed'
            details: 상세내용 사전, 안잘림
            section_title: 현재 장 제목(선택)
            section_index: 현재 장 인덱스(선택)
        """
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "elapsed_seconds": round(self._get_elapsed_time(), 2),
            "report_id": self.report_id,
            "action": action,
            "stage": stage,
            "section_title": section_title,
            "section_index": section_index,
            "details": details
        }
        
        # 추가로 JSONL 파일에 쓰기
        with open(self.log_file_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
    
    def log_start(self, simulation_id: str, graph_id: str, simulation_requirement: str):
        """기록보고서 생성시작"""
        self.log(
            action="report_start",
            stage="pending",
            details={
                "simulation_id": simulation_id,
                "graph_id": graph_id,
                "simulation_requirement": simulation_requirement,
                "message": "보고서 생성작업시작"
            }
        )
    
    def log_planning_start(self):
        """기록 대강 계획 시작"""
        self.log(
            action="planning_start",
            stage="planning",
            details={"message": "시작 계획 보고서 대강"}
        )
    
    def log_planning_context(self, context: Dict[str, Any]):
        """기록 계획 시 가져오기 컨텍스트 정보"""
        self.log(
            action="planning_context",
            stage="planning",
            details={
                "message": "가져오기시뮬레이션컨텍스트정보",
                "context": context
            }
        )
    
    def log_planning_complete(self, outline_dict: Dict[str, Any]):
        """기록 대강 계획 완료"""
        self.log(
            action="planning_complete",
            stage="planning",
            details={
                "message": "대강 계획 완료",
                "outline": outline_dict
            }
        )
    
    def log_section_start(self, section_title: str, section_index: int):
        """기록 장 생성 시작"""
        self.log(
            action="section_start",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={"message": f"시작 생성 장: {section_title}"}
        )
    
    def log_react_thought(self, section_title: str, section_index: int, iteration: int, thought: str):
        """기록 ReACT 사고 과정"""
        self.log(
            action="react_thought",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "iteration": iteration,
                "thought": thought,
                "message": f"ReACT 제{iteration}라운드 사고"
            }
        )
    
    def log_tool_call(
        self, 
        section_title: str, 
        section_index: int,
        tool_name: str, 
        parameters: Dict[str, Any],
        iteration: int
    ):
        """기록도구호출"""
        self.log(
            action="tool_call",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "iteration": iteration,
                "tool_name": tool_name,
                "parameters": parameters,
                "message": f"호출도구: {tool_name}"
            }
        )
    
    def log_tool_result(
        self,
        section_title: str,
        section_index: int,
        tool_name: str,
        result: str,
        iteration: int
    ):
        """기록 도구 호출 결과(완전 내용, 안잘림)"""
        self.log(
            action="tool_result",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "iteration": iteration,
                "tool_name": tool_name,
                "result": result,  # 완전 결과, 안잘림
                "result_length": len(result),
                "message": f"도구 {tool_name} 돌아가기결과"
            }
        )
    
    def log_llm_response(
        self,
        section_title: str,
        section_index: int,
        response: str,
        iteration: int,
        has_tool_calls: bool,
        has_final_answer: bool
    ):
        """기록 LLM 응답(완전 내용, 안잘림)"""
        self.log(
            action="llm_response",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "iteration": iteration,
                "response": response,  # 완전 응답, 안잘림
                "response_length": len(response),
                "has_tool_calls": has_tool_calls,
                "has_final_answer": has_final_answer,
                "message": f"LLM 응답 (도구 호출: {has_tool_calls}, 최종 답변: {has_final_answer})"
            }
        )
    
    def log_section_content(
        self,
        section_title: str,
        section_index: int,
        content: str,
        tool_calls_count: int
    ):
        """기록 장 내용 생성 완료(단지 기록 내용, 안 대표 전체 장 완료)"""
        self.log(
            action="section_content",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "content": content,  # 완전 내용, 안잘림
                "content_length": len(content),
                "tool_calls_count": tool_calls_count,
                "message": f"장 {section_title} 내용 생성 완료"
            }
        )
    
    def log_section_full_complete(
        self,
        section_title: str,
        section_index: int,
        full_content: str
    ):
        """
        기록 장 생성 완료

        프론트엔드는 이 로그를 듣고 하나의 장이 진짜 완료되었는지 판단하며, 완전 내용을 가져오기
        """
        self.log(
            action="section_complete",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "content": full_content,
                "content_length": len(full_content),
                "message": f"장 {section_title} 생성 완료"
            }
        )
    
    def log_report_complete(self, total_sections: int, total_time_seconds: float):
        """기록보고서 생성완료"""
        self.log(
            action="report_complete",
            stage="completed",
            details={
                "total_sections": total_sections,
                "total_time_seconds": round(total_time_seconds, 2),
                "message": "보고서 생성완료"
            }
        )
    
    def log_error(self, error_message: str, stage: str, section_title: str = None):
        """기록오류"""
        self.log(
            action="error",
            stage=stage,
            section_title=section_title,
            section_index=None,
            details={
                "error": error_message,
                "message": f"오류 발생: {error_message}"
            }
        )


class ReportConsoleLogger:
    """
    Report Agent 콘솔 로그 기록기
    
    콘솔 스타일의 로그(INFO、WARNING 등)를 보고서 파일夹의 console_log.txt 파일에 씁니다.
    이 로그와 agent_log.jsonl은 다르며, 순수 텍스트 형식의 콘솔 출력입니다.
    """
    
    def __init__(self, report_id: str):
        """
        초기화 콘솔 로그 기록기
        
        Args:
            report_id: 보고서ID, 용확인로그파일경로
        """
        self.report_id = report_id
        self.log_file_path = os.path.join(
            Config.UPLOAD_FOLDER, 'reports', report_id, 'console_log.txt'
        )
        self._ensure_log_file()
        self._file_handler = None
        self._setup_file_handler()
    
    def _ensure_log_file(self):
        """보장 로그 파일이 저장될 디렉토리"""
        log_dir = os.path.dirname(self.log_file_path)
        os.makedirs(log_dir, exist_ok=True)
    
    def _setup_file_handler(self):
        """파일 처리기를 설정하고, 로그를 동시에 파일에 씁니다"""
        import logging
        
        # 생성 파일 처리기
        self._file_handler = logging.FileHandler(
            self.log_file_path,
            mode='a',
            encoding='utf-8'
        )
        self._file_handler.setLevel(logging.INFO)
        
        # 사용과 콘솔 동일한 간결 형식
        formatter = logging.Formatter(
            '[%(asctime)s] %(levelname)s: %(message)s',
            datefmt='%H:%M:%S'
        )
        self._file_handler.setFormatter(formatter)
        
        # 추가 report_agent 관련의 logger
        loggers_to_attach = [
            'mirofish.report_agent',
            'mirofish.zep_tools',
        ]
        
        for logger_name in loggers_to_attach:
            target_logger = logging.getLogger(logger_name)
            # 중복 추가 방지
            if self._file_handler not in target_logger.handlers:
                target_logger.addHandler(self._file_handler)
    
    def close(self):
        """파일 처리기를 닫고 logger에서 제거"""
        import logging
        
        if self._file_handler:
            loggers_to_detach = [
                'mirofish.report_agent',
                'mirofish.zep_tools',
            ]
            
            for logger_name in loggers_to_detach:
                target_logger = logging.getLogger(logger_name)
                if self._file_handler in target_logger.handlers:
                    target_logger.removeHandler(self._file_handler)
            
            self._file_handler.close()
            self._file_handler = None
    
    def __del__(self):
        """소멸 시 보장 닫기 파일 처리기"""
        self.close()


class ReportStatus(str, Enum):
    """보고서상태"""
    PENDING = "pending"
    PLANNING = "planning"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ReportSection:
    """보고서 장"""
    title: str
    content: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "content": self.content
        }

    def to_markdown(self, level: int = 2) -> str:
        """Markdown 형식으로 변환"""
        md = f"{'#' * level} {self.title}\n\n"
        if self.content:
            md += f"{self.content}\n\n"
        return md


@dataclass
class ReportOutline:
    """보고서 대강"""
    title: str
    summary: str
    sections: List[ReportSection]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "summary": self.summary,
            "sections": [s.to_dict() for s in self.sections]
        }
    
    def to_markdown(self) -> str:
        """Markdown 형식으로 변환"""
        md = f"# {self.title}\n\n"
        md += f"> {self.summary}\n\n"
        for section in self.sections:
            md += section.to_markdown()
        return md


@dataclass
class Report:
    """완전한 보고서"""
    report_id: str
    simulation_id: str
    graph_id: str
    simulation_requirement: str
    status: ReportStatus
    outline: Optional[ReportOutline] = None
    markdown_content: str = ""
    created_at: str = ""
    completed_at: str = ""
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "report_id": self.report_id,
            "simulation_id": self.simulation_id,
            "graph_id": self.graph_id,
            "simulation_requirement": self.simulation_requirement,
            "status": self.status.value,
            "outline": self.outline.to_dict() if self.outline else None,
            "markdown_content": self.markdown_content,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "error": self.error
        }


# ═══════════════════════════════════════════════════════════════
# Prompt 템플릿 상수
# ═══════════════════════════════════════════════════════════════

# ── 도구설명 ──

TOOL_DESC_INSIGHT_FORGE = """\
【심층 통찰 검색 - 강력한 검색 도구】
이것은 우리가 강력한 검색 함수로, 전문적으로 심층 분석 설계합니다. 그것은:
1. 자동으로 당신의 문제를 분해하여 여러 개의 하위 질문
2. 여러 개의 차원에서 검색 시뮬레이션 그래프의 정보
3. 의미 검색, 개체 분석, 관계 체인 추적의 결과 통합
4. 가장 포괄적이고, 가장 심층적인 검색 내용으로 돌아가기

【사용 장면】
- 특정 주제를 심층 분석해야 할 때
- 이벤트의 여러 측면을 해석해야 할 때
- 지원 보고서 장의 풍부한 자료를 가져와야 할 때

【돌아가기내용】
- 관련 사실 원문(직접 인용)
- 핵심 개체 통찰
- 관계 체인 분석"""

TOOL_DESC_PANORAMA_SEARCH = """\
【넓이 검색 - 전체 보기 가져오기】
이 도구는 시뮬레이션 결과의 완전한 전체 보기를 가져오며, 특히 이벤트의 진화 과정을 해석하는 데 적합합니다. 그것은:
1. 관련 노드와 관계를 가져오기
2. 현재 유효한 사실과 역사/만료된 사실의 영역
3. 정책 파급효과가 어떻게 전파되는지를 해석하는 데 도움

【사용 장면】
- 이벤트의 완전한 발전 맥락을 해석해야 할 때
- 서로 다른 시나리오의 정책 파급효과를 비교해야 할 때
- 전체 개체와 관계 정보를 가져와야 할 때

【돌아가기내용】
- 현재 유효한 사실(시뮬레이션 최신 결과)
- 역사/만료된 사실(진화 기록)
- 관련된 개체"""

TOOL_DESC_QUICK_SEARCH = """\
【간단 검색 - 빠른 검색】
경량급의 빠른 검색 도구로, 간단하고 직접적인 정보 조회에 적합합니다.

【사용 장면】
- 특정 정보를 빠르게 찾아야 할 때
- 특정 사실을 검증해야 할 때
- 간단한 정보 검색

【돌아가기내용】
- 조회와 가장 관련된 사실 목록"""

TOOL_DESC_INTERVIEW_AGENTS = """\
【심층 인터뷰 - 실제 Agent 인터뷰(듀얼 플랫폼)】
호출 OASIS 시뮬레이션 환경의 인터뷰 API, 진행 중: 실행의 시뮬레이션 Agent가 실제 인터뷰를 진행합니다!
이것은 LLM 시뮬레이션이며, 호출된 실제 인터뷰 인터페이스에서 시뮬레이션 Agent의 원시 답변을 가져옵니다.
기본값에서 Twitter와 Reddit 두 개 플랫폼에서 동시에 인터뷰를 진행하여, 더 포괄적인 관점을 가져옵니다.

기능 프로세스:
1. 자동으로 페르소나 파일을 읽고, 시뮬레이션 Agent를 해석합니다.
2. 지능형 선택과 인터뷰의 주요 주제와 가장 관련된 Agent(예: 학생, 미디어, 공식 등)
3. 자동으로 인터뷰 질문 생성
4. 호출 /api/simulation/interview/batch 인터페이스에서 듀얼 플랫폼으로 실제 인터뷰 진행
5. 인터뷰 결과를 통합하여 다각적 분석을 제공합니다.

【사용 장면】
- 서로 다른 역할의 관점에서 이벤트를 해석해야 할 때(학생은 어떻게 생각하나요? 미디어는 어떻게 생각하나요? 공식은 뭐라고 하나요?)
- 여러 의견과 입장을 수집해야 할 때
- 시뮬레이션 Agent의 실제 답변을 가져와야 할 때(자 OASIS 시뮬레이션 환경)
- 보고서를 더 생동감 있게 만들고, "인터뷰 실록"을 포함하고 싶을 때

【돌아가기내용】
- 인터뷰 Agent의 신원 정보
- 각 Agent에서 Twitter와 Reddit 두 개 플랫폼의 인터뷰 답변
- 주요 인용(직접 인용)
- 인터뷰 요약과 관점 비교

【중요】OASIS 시뮬레이션 환경 진행 중: 실행 시에만 이 기능을 사용하세요!"""

# ── 개요 계획 prompt ──

PLAN_SYSTEM_PROMPT = """\
당신은 하나의「예측되지 않은 보고서」의 작성 전문가로, 시뮬레이션 세계의「신의 시각」을 통해 시뮬레이션 중 각 Agent의 행로, 발언과 상호작용을 통찰합니다.

【핵심 이념】
우리는 하나의 시뮬레이션 세계를 구축하고, 그 안에 특정의「시뮬레이션 요구사항」을 변수로 주입합니다. 시뮬레이션 세계의 연화 결과, 발생하지 않은 상황의 예측. 당신은 진행 중: 관찰의 안 "실험 데이터", 그리고 "미리의 예행".

【당신의 작업】
하나의「예측되지 않은 보고서」를 작성하고, 다음에 답하십시오:
1. 우리가 설정한 개체 하에서, 발생하지 않은 것은 무엇인가?
2. 각 클래스 Agent(군중)은 어떻게 반응하고 행동하는가?
3. 이 시뮬레이션은 어떤 가치의 관심 있는 미지의 추세와 위험을 드러내는가?

【보고서 위치】
- ✅ 이 기반으로 시뮬레이션의 예측되지 않은 보고서, "만약 이렇게, 그러면 어떻게"를 드러냅니다.
- ✅ 예측 결과에 집중: 이벤트 진행, 군중 반응, 출현 현상, 잠재적 위험
- ✅ 시뮬레이션 세계의 Agent의 언행이 군중 행로의 예측
- ❌ 현실 세계의 현상 분석
- ❌ 일반적인 정책 개요

【장 수량 제한】
- 최소 2개 장, 최대 5개 장
- 자식 장 필요 없음, 각 장은 직접 작성된 완전한 내용
- 내용 간결, 핵심 예측 발견에 집중
- 장 구조는 당신에 따라 예측 결과를 기반으로 설계

JSON 형식의 보고서 개요를 출력해 주세요, 형식은 다음과 같습니다:
{
    "title": "보고서 제목",
    "summary": "보고서 요약(한 문장으로 핵심 예측 발견 요약)",
    "sections": [
        {
            "title": "장 제목",
            "description": "장 내용 설명"
        }
    ]
}

주의: sections 배열은 최소 2개, 최대 5개 요소여야 합니다!"""

PLAN_USER_PROMPT_TEMPLATE = """\
【예측 장면 설정】
우리는 시뮬레이션 세계에 변수를 주입합니다(시뮬레이션 요구사항): {simulation_requirement}

【시뮬레이션 세계 규모】
- 시뮬레이션의 개체 수량: {total_nodes}
- 개체 간 발생의 관계 수량: {total_edges}
- 개체 유형 분포: {entity_types}
- 활성 Agent 수량: {total_entities}

【시뮬레이션 예측의 일부 미사실 샘플】
{related_facts_json}

「신의 시각」으로 이 미리 예행을 살펴보세요:
1. 우리가 설정한 개체 하에서, 어떤 상태가 나타나지 않았는가?
2. 각 클래스 군중(Agent)은 어떻게 반응하고 행동하는가?
3. 이 시뮬레이션은 어떤 가치의 관심 있는 미지의 추세를 드러내는가?

예측 결과에 따라 가장 적합한 보고서 장 구조를 설계합니다.

【다시 알림】보고서 장 수량: 최소 2개, 최대 5개, 내용 간결하게 핵심 예측 발견에 집중하세요. """

# ── 장 생성 prompt ──

SECTION_SYSTEM_PROMPT_TEMPLATE = """\
당신은 하나의「예측되지 않은 보고서」의 작성 전문가로, 진행 중: 보고서의 하나의 장을 작성합니다.

보고서 제목: {report_title}
보고서요약: {report_summary}
예측 장면(시뮬레이션 요구사항): {simulation_requirement}

현재 작성 중인 장: {section_title}

═══════════════════════════════════════════════════════════════
【핵심理念】  
═══════════════════════════════════════════════════════════════

시뮬레이션월드의 예측. 우리는 시뮬레이션월드에 특정 개체(시뮬레이션 요구사항)를 주입하고,  
시뮬레이션 중 Agent의 행동과 상호작용, 인구 행동의 예측.  

너의 작업:  
- 설정된 개체 하에서 발생한 것을 밝혀내기  
- 각 클래스 인구(Agent)가 어떻게 반응하고 행동할지를 예측하기  
- 주목할 가치가 있는 미래의 트렌드, 위험과 기회를 발견하기  

❌ 하지 마세요 현실 세계 현황의 분석으로 작성  
✅ 집중하라 "미래가 어떻게 될지"——시뮬레이션 결과 예측의 미래  

═══════════════════════════════════════════════════════════════
【가장 중요한 규칙 - 반드시 준수】  
═══════════════════════════════════════════════════════════════

1. 【반드시 도구를 호출하여 시뮬레이션 월드를 관찰】  
   - 너 진행 중: 하여 「신의 시점」으로 미래의 예측을 관찰  
   - 모든 내용은 반드시 자 시뮬레이션 월드 중 발생한 이벤트와 Agent의 언행  
   - 사용하지 마세요 너 자신의 지식으로 보고서 내용을 작성  
   - 각 개 챕터에서 최소 3회 도구를 호출하여(최대 5회) 시뮬레이션의 월드를 관찰해야 하며, 이는 미래를 대표한다  

2. 【반드시 Agent의 원래 언행을 인용】  
   - Agent의 발언과 행동은 인구 행동의 예측  
   - 보고서 중 사용 시 인용 형식으로 이러한 예측을 표시, 예를 들어:  
     > "어떤 클래스 인구가 말했습니다: 원문 내용..."  
   - 이러한 인용은 시뮬레이션 예측의 핵심 증거  

3. 【언어 일관성 - 인용 내용은 반드시 번역하여 보고서 언어로】  
   - 도구 돌아가기의 내용은 반드시 영어 또는 중영 혼합의 표현 포함  
   - 만약 시뮬레이션 요구사항과 자료 원문 중 문서가 있다면, 보고서는 반드시 전부 사용하여 중문으로 작성  
   - 너가 도구 돌아가기의 영어 또는 중영 혼합 내용을 인용할 때, 반드시 그 번역이 매끄럽게 중문으로 후에 보고서에 작성  
   - 번역 시 원의를 변하지 않게 유지하고, 표현이 자연스럽게 통순하게 보장  
   - 이 규칙은 동시에 본문과 인용 블록(> 형식)의 내용에 적용된다  

4. 【충실히 예측 결과를 제시】  
   - 보고서 내용은 반드시 시뮬레이션 월드의 대표 미래의 시뮬레이션 결과를 반영해야 한다  
   - 하지 마세요추가시뮬레이션중존재하지 않음의정보
   - 만약 어떤 정보가 부족하다면, 사실대로 설명  

═══════════════════════════════════════════════════════════════
【⚠️ 형식 규범 - 극히 중요！】  
═══════════════════════════════════════════════════════════════

【한 개 챕터 = 최소 내용 단위】  
- 각 챕터는 보고서의 최소 분할 단위  
- ❌ 여기서 챕터 내에서 어떤 Markdown 제목(#、##、###、#### 등)을 사용하지 마세요  
- ❌ 여기서 내용 시작에 추가 챕터 메인 제목을 넣지 마세요  
- ✅ 챕터 제목은 시스템이 자동으로 추가하며, 너는 순수 본문 내용만 작성하면 된다  
- ✅ 사용 **굵은 글씨**, 단락 구분, 인용, 목록으로 내용을 조직하되, 제목을 사용하지 마세요  

【올바른 예시】  
```
본 챕터는 정책의 파급효과 전파 경로를 분석한다. 시뮬레이션 데이터의 심층 분석을 통해, 우리는 발견했다...  

**첫 발화 폭발 단계**  

이해관계자 간 정보 공유 채널이 정책 반응의 첫 번째 접점 역할을 한다:  

> "주요 이해관계자들이 초기 정책 반응의 68%를 주도했다..."  

**감정 확대 단계**  

틱톡 플랫폼은 이벤트 영향력을 더욱 확대한다:  

- 시각적 충격력이 강하다  
- 감정 공명도가 높다  
```

【오류 예시】  
```
## 실행 요약          ← 오류！여기서 어떤 제목도 추가하지 마세요  
### 일, 첫 발화 단계     ← 오류！여기서 ###로 소절을 나누지 마세요  
#### 1.1 상세 분석   ← 오류！여기서 ####로 세분화하지 마세요  

본 챕터는 분석...
```

═══════════════════════════════════════════════════════════════
【검색도구】(각 챕터 호출 3-5회)
═══════════════════════════════════════════════════════════════

{tools_description}

【도구사용제안 - 다른 도구를 혼합 사용하지 마세요, 한 가지만 사용하지 마세요】
- insight_forge: 심층 통찰 분석, 자동으로 문제를 분해하고 다차원적으로 사실과 관계를 검색
- panorama_search: 광각 전경 검색, 이벤트 전체 보기, 시간선과 변화를 해석
- quick_search: 특정 정보 포인트를 빠르게 검증
- interview_agents: 인터뷰 시뮬레이션 에이전트, 동일 역할의 1인칭 관점과 실제 반응을 가져오기

═══════════════════════════════════════════════════════════════
【작업 흐름】
═══════════════════════════════════════════════════════════════

매번 응답 시 아래 두 가지 중 하나만 수행하세요(동시에):

옵션 A - 도구 호출:
당신의 생각을 출력한 다음 아래 형식으로 하나의 도구를 호출하세요:
<tool_call>
{{"name": "도구이름", "parameters": {{"파라미터명": "파라미터값"}}}}
</tool_call>
시스템이 도구를 실행하고 결과를 당신에게 반환합니다. 당신은 도구의 결과를 직접 작성할 필요가 없습니다.

옵션 B - 최종 내용 출력:
이미 도구를 통해 충분한 정보를 가져온 경우, "Final Answer:"로 시작하여 챕터 내용을 출력하세요.

⚠️ 엄격히 금지:
- 한 번의 응답에서 도구 호출과 Final Answer를 동시에 포함하는 것
- 스스로 도구의 결과를 조작하는 것(Observation), 모든 도구 결과는 시스템에 의해 주입됩니다
- 매번 최대 하나의 도구만 호출

═══════════════════════════════════════════════════════════════
【챕터 내용 요구사항】
═══════════════════════════════════════════════════════════════

1. 내용반드시기반으로도구검색의시뮬레이션데이터
2. 대량 인용 원문 표시 시뮬레이션 효과
3. Markdown 형식 사용(단 제목 사용은 금지):
   - **굵은 글씨**로 강조 표시 사용(부제목 대신)
   - 목록 사용(- 또는 1.2.3.)으로 포인트 조직
   - 빈 줄로 단락 구분
   - ❌ #、##、###、#### 등 어떤 제목 문법도 사용 금지
4. 【인용 형식 규범 - 반드시 독립된 단락】
   인용은 반드시 독립된 단락으로, 앞뒤에 각각 빈 줄을 두고, 단락 중에서 혼합하지 마세요:

   ✅ 올바른 형식:
   ```
   교사의 응답은 실질적인 내용이 부족하다.

   > "교사의 응답 방식은 빠르게 변화하는 정책 환경에서 대응의 경직성과 지연을 드러낸다."

   이 평가는 대중의 일반적인 불만을 반영한다.
   ```

   ❌ 오류형식:
   ```
   교사의 응답은 실질적인 내용이 부족하다.> "교사의 응답 방식..." 이 평가는 반영한다...
   ```
5. 다른 챕터의 논리적 일관성 유지
6. 【중복 피하기】아래 완료된 챕터 내용을 주의 깊게 읽고, 동일한 정보를 반복하지 마세요
7. 【다시 강조】어떤 제목도 추가하지 마세요! **굵은 글씨**로 소제목 대신 사용하세요"""

SECTION_USER_PROMPT_TEMPLATE = """\
완료된 챕터 내용(주의 깊게 읽고, 중복 피하기):
{previous_content}

═══════════════════════════════════════════════════════════════
【현재 작업】챕터 작성: {section_title}
═══════════════════════════════════════════════════════════════

【중요 알림】
1. 위의 완료된 챕터를 주의 깊게 읽고, 동일한 내용을 반복하지 마세요!
2. 시작하기 전에 반드시 도구를 호출하여 시뮬레이션 데이터를 가져오세요
3. 다른 도구를 혼합 사용하지 마세요, 한 가지만 사용하지 마세요
4. 보고서 내용은 반드시 자가 검색 결과여야 하며, 자신의 지식을 사용하지 마세요

【⚠️ 형식 경고 - 반드시 준수】
- ❌ 어떤 제목도 작성하지 마세요(#、##、###、#### 모두 안 됩니다)
- ❌ "{section_title}"로 시작하지 마세요
- ✅ 챕터 제목은 시스템에 의해 자동으로 추가됩니다
- ✅ 본문을 직접 작성하고, 소제목 대신 **굵은 글씨**를 사용하세요

시작하세요:
1. 먼저 이 챕터에 필요한 정보에 대해 생각하세요
2. 그 다음호출도구(Action)가져오기시뮬레이션데이터
3. 충분한 정보를 수집한 후 Final Answer를 출력하세요(순수 본문, 어떤 제목도 없음)
"""

# ── ReACT 루프 내 메시지 템플릿 ──

REACT_OBSERVATION_TEMPLATE = """\
Observation(검색결과):

═══ 도구 {tool_name} 돌아가기 ═══
{result}

═══════════════════════════════════════════════════════════════
이미 호출 도구 {tool_calls_count}/{max_tool_calls} 회(이미 사용: {used_tools_str}){unused_hint}
- 만약 정보 충분:하여 "Final Answer:" 시작 출력 장 내용(반드시 인용 위의 원문)
- 만약 더 많은 정보 필요:호출 한 개 도구 계속 검색
═══════════════════════════════════════════════════════════════"""

REACT_INSUFFICIENT_TOOLS_MSG = (
    "【주의】당신은 단지 호출 {tool_calls_count} 회 도구, 최소 {min_tool_calls} 회 필요합니다."
    "다시 호출 도구 가져오기 더 많은 시뮬레이션 데이터, 그 다음 다시 출력 Final Answer。{unused_hint}"
)

REACT_INSUFFICIENT_TOOLS_MSG_ALT = (
    "현재 단지 호출 {tool_calls_count} 회 도구, 최소 {min_tool_calls} 회 필요합니다."
    "도구 가져오기 시뮬레이션 데이터。{unused_hint}"
)

REACT_TOOL_LIMIT_MSG = (
    "도구 호출 횟수 이미 상한에 도달({tool_calls_count}/{max_tool_calls}), 더 이상 호출 도구."
    '즉시 기반으로 이미 가져온 정보, 하여 "Final Answer:" 시작 출력 장 내용.'
)

REACT_UNUSED_TOOLS_HINT = "\n💡 당신은 아직 사용하지 않으면: {unused_list}, 추천 시도 안 같은 도구 가져오기 다각도 정보"

REACT_FORCE_FINAL_MSG = "이미 도구 호출 제한에 도달, 직접 출력 Final Answer: 그리고 생성 장 내용."

# ── Chat prompt ──

CHAT_SYSTEM_PROMPT_TEMPLATE = """\
당신은 한 개 간결하고 효율적인 시뮬레이션 예측 도우미입니다.

【배경】
예측 개체: {simulation_requirement}

【이미생성의분석 보고서】
{report_content}

【규칙】
1. 우선적으로 기반으로 위의 보고서 내용 질문에 답변
2. 직접 질문에 답변, 피하십시오 장황한 사고 논술
3. 만약 보고서 내용이 부족하여 답변할 때, 비로소 호출 도구 검색 더 많은 데이터
4. 답변 간결하고, 명확하며, 개리

【도구 사용】(만약 필요 시 사용, 최대 호출 1-2 회)
{tools_description}

【도구호출형식】
<tool_call>
{{"name": "도구 이름", "parameters": {{"파라미터 명": "파라미터 값"}}}}
</tool_call>

【답변 스타일】
- 간결하고 직접, 하지 마세요 장편 대론
- 사용 > 형식 인용 핵심 내용
- 우선 결론을 내고, 그 다음 이유를 설명"""

CHAT_OBSERVATION_SUFFIX = "\n\n간결하게 질문에 답변해 주세요."


# ═══════════════════════════════════════════════════════════════
# ReportAgent 메인클래스
# ═══════════════════════════════════════════════════════════════


class ReportAgent:
    """
    Report Agent - 시뮬레이션보고서 생성Agent

    채택 ReACT(Reasoning + Acting) 모드:
    1. 계획 단계:분석 시뮬레이션 요구 사항, 계획 보고서 디렉토리 구조
    2. 생성 단계:각 장 생성 내용, 매 장 여러 번 호출 도구 가져오기 정보
    3. 반성 단계:내용 완전성과 정확성 점검
    """
    
    # 최대 도구 호출 횟수(각 장마다)
    MAX_TOOL_CALLS_PER_SECTION = 5
    
    # 최대 반성 라운드 수
    MAX_REFLECTION_ROUNDS = 3
    
    # 대화의최대도구호출횟수
    MAX_TOOL_CALLS_PER_CHAT = 2
    
    def __init__(
        self, 
        graph_id: str,
        simulation_id: str,
        simulation_requirement: str,
        llm_client: Optional[LLMClient] = None,
        zep_tools: Optional[ZepToolsService] = None
    ):
        """
        초기화Report Agent
        
        Args:
            graph_id: 그래프ID
            simulation_id: 시뮬레이션ID
            simulation_requirement: 시뮬레이션 요구사항설명
            llm_client: LLM클라이언트(선택)
            zep_tools: Zep도구서비스(선택)
        """
        self.graph_id = graph_id
        self.simulation_id = simulation_id
        self.simulation_requirement = simulation_requirement
        
        self.llm = llm_client or LLMClient()
        self.zep_tools = zep_tools or ZepToolsService()
        
        # 도구 정의
        self.tools = self._define_tools()
        
        # 로그 기록기(에서 generate_report 중 초기화)
        self.report_logger: Optional[ReportLogger] = None
        # 콘솔 로그 기록기(에서 generate_report 중 초기화)
        self.console_logger: Optional[ReportConsoleLogger] = None
        
        logger.info(f"ReportAgent 초기화완료: graph_id={graph_id}, simulation_id={simulation_id}")
    
    def _define_tools(self) -> Dict[str, Dict[str, Any]]:
        """정의용 도구"""
        return {
            "insight_forge": {
                "name": "insight_forge",
                "description": TOOL_DESC_INSIGHT_FORGE,
                "parameters": {
                    "query": "당신은 심층 분석의 질문 또는 주제를 원하십니까",
                    "report_context": "현재 보고서 장의 컨텍스트(선택, 도움이 되어 생성 더 정확한 하위 질문)"
                }
            },
            "panorama_search": {
                "name": "panorama_search",
                "description": TOOL_DESC_PANORAMA_SEARCH,
                "parameters": {
                    "query": "검색 조회, 용 관련성 정렬",
                    "include_expired": "아니요 포함 만료된/역사 내용(기본값 True)"
                }
            },
            "quick_search": {
                "name": "quick_search",
                "description": TOOL_DESC_QUICK_SEARCH,
                "parameters": {
                    "query": "검색 조회 문자열",
                    "limit": "돌아가기결과수량(선택, 기본값10)"
                }
            },
            "interview_agents": {
                "name": "interview_agents",
                "description": TOOL_DESC_INTERVIEW_AGENTS,
                "parameters": {
                    "interview_topic": "인터뷰 메인 주제 또는 요구 설명(예: '학생 기숙사 포름알데히드 사건에 대한 의견')",
                    "max_agents": "최대 인터뷰의 Agent 수량(선택, 기본값 5, 최대 10)"
                }
            }
        }
    
    def _execute_tool(self, tool_name: str, parameters: Dict[str, Any], report_context: str = "") -> str:
        """
        실행도구호출
        
        Args:
            tool_name: 도구이름
            parameters: 도구파라미터
            report_context: 보고서컨텍스트(용InsightForge)
            
        Returns:
            도구실행결과(텍스트형식)
        """
        logger.info(f"실행도구: {tool_name}, 파라미터: {parameters}")
        
        try:
            if tool_name == "insight_forge":
                query = parameters.get("query", "")
                ctx = parameters.get("report_context", "") or report_context
                result = self.zep_tools.insight_forge(
                    graph_id=self.graph_id,
                    query=query,
                    simulation_requirement=self.simulation_requirement,
                    report_context=ctx
                )
                return result.to_text()
            
            elif tool_name == "panorama_search":
                # 넓이검색 - 가져오기전체 보기
                query = parameters.get("query", "")
                include_expired = parameters.get("include_expired", True)
                if isinstance(include_expired, str):
                    include_expired = include_expired.lower() in ['true', '1', 'yes']
                result = self.zep_tools.panorama_search(
                    graph_id=self.graph_id,
                    query=query,
                    include_expired=include_expired
                )
                return result.to_text()
            
            elif tool_name == "quick_search":
                # 간단 검색 - 빠른 검색
                query = parameters.get("query", "")
                limit = parameters.get("limit", 10)
                if isinstance(limit, str):
                    limit = int(limit)
                result = self.zep_tools.quick_search(
                    graph_id=self.graph_id,
                    query=query,
                    limit=limit
                )
                return result.to_text()
            
            elif tool_name == "interview_agents":
                # 심층 인터뷰 - 호출 실제의 OASIS 인터뷰 API 가져오기 시뮬레이션 Agent의 답변(듀얼 플랫폼)
                interview_topic = parameters.get("interview_topic", parameters.get("query", ""))
                max_agents = parameters.get("max_agents", 5)
                if isinstance(max_agents, str):
                    max_agents = int(max_agents)
                max_agents = min(max_agents, 10)
                result = self.zep_tools.interview_agents(
                    simulation_id=self.simulation_id,
                    interview_requirement=interview_topic,
                    simulation_requirement=self.simulation_requirement,
                    max_agents=max_agents
                )
                return result.to_text()
            
            # ========== 향후 호환의 구 도구(내부 리디렉션 신 도구) ==========
            
            elif tool_name == "search_graph":
                # 리디렉션 quick_search
                logger.info("search_graph 이미 리디렉션 quick_search")
                return self._execute_tool("quick_search", parameters, report_context)
            
            elif tool_name == "get_graph_statistics":
                result = self.zep_tools.get_graph_statistics(self.graph_id)
                return json.dumps(result, ensure_ascii=False, indent=2)
            
            elif tool_name == "get_entity_summary":
                entity_name = parameters.get("entity_name", "")
                result = self.zep_tools.get_entity_summary(
                    graph_id=self.graph_id,
                    entity_name=entity_name
                )
                return json.dumps(result, ensure_ascii=False, indent=2)
            
            elif tool_name == "get_simulation_context":
                # 리디렉션 insight_forge, 그 이유로 그것이 더 강력하기 때문
                logger.info("get_simulation_context 이미 리디렉션 insight_forge")
                query = parameters.get("query", self.simulation_requirement)
                return self._execute_tool("insight_forge", {"query": query}, report_context)
            
            elif tool_name == "get_entities_by_type":
                entity_type = parameters.get("entity_type", "")
                nodes = self.zep_tools.get_entities_by_type(
                    graph_id=self.graph_id,
                    entity_type=entity_type
                )
                result = [n.to_dict() for n in nodes]
                return json.dumps(result, ensure_ascii=False, indent=2)
            
            else:
                return f"알 수 없음도구: {tool_name}。사용하여아래도구의一: insight_forge, panorama_search, quick_search"
                
        except Exception as e:
            logger.error(f"도구실행실패: {tool_name}, 오류: {str(e)}")
            return f"도구실행실패: {str(e)}"
    
    # 합법의도구이름집합合, 용裸 JSON 兜底파싱时校验
    VALID_TOOL_NAMES = {"insight_forge", "panorama_search", "quick_search", "interview_agents"}

    def _parse_tool_calls(self, response: str) -> List[Dict[str, Any]]:
        """
        부터LLM응답중파싱도구호출

        지원의형식(기준으로优先级):
        1. <tool_call>{"name": "tool_name", "parameters": {...}}</tool_call>
        2. 裸 JSON(응답전체또는단행一개도구호출 JSON)
        """
        tool_calls = []

        # 형식1: XML스타일(표준형식)
        xml_pattern = r'<tool_call>\s*(\{.*?\})\s*</tool_call>'
        for match in re.finditer(xml_pattern, response, re.DOTALL):
            try:
                call_data = json.loads(match.group(1))
                tool_calls.append(call_data)
            except json.JSONDecodeError:
                pass

        if tool_calls:
            return tool_calls

        # 형식2: 兜底 - LLM 직접출력裸 JSON(没包 <tool_call> 标签)
        # 만에서형식1미匹配时시도, 避免误匹配正文의 JSON
        stripped = response.strip()
        if stripped.startswith('{') and stripped.endswith('}'):
            try:
                call_data = json.loads(stripped)
                if self._is_valid_tool_call(call_data):
                    tool_calls.append(call_data)
                    return tool_calls
            except json.JSONDecodeError:
                pass

        # 응답포함思考文字 + 裸 JSON, 시도추출가장후一개 JSON 象
        json_pattern = r'(\{"(?:name|tool)"\s*:.*?\})\s*$'
        match = re.search(json_pattern, stripped, re.DOTALL)
        if match:
            try:
                call_data = json.loads(match.group(1))
                if self._is_valid_tool_call(call_data):
                    tool_calls.append(call_data)
            except json.JSONDecodeError:
                pass

        return tool_calls

    def _is_valid_tool_call(self, data: dict) -> bool:
        """校验파싱출력의 JSON 否合法의도구호출"""
        # 지원 {"name": ..., "parameters": ...} 와 {"tool": ..., "params": ...} 두종키名
        tool_name = data.get("name") or data.get("tool")
        if tool_name and tool_name in self.VALID_TOOL_NAMES:
            # 통합키명로 name / parameters
            if "tool" in data:
                data["name"] = data.pop("tool")
            if "params" in data and "parameters" not in data:
                data["parameters"] = data.pop("params")
            return True
        return False
    
    def _get_tools_description(self) -> str:
        """생성도구설명텍스트"""
        desc_parts = ["용도구:"]
        for name, tool in self.tools.items():
            params_desc = ", ".join([f"{k}: {v}" for k, v in tool["parameters"].items()])
            desc_parts.append(f"- {name}: {tool['description']}")
            if params_desc:
                desc_parts.append(f"  파라미터: {params_desc}")
        return "\n".join(desc_parts)
    
    def plan_outline(
        self, 
        progress_callback: Optional[Callable] = None
    ) -> ReportOutline:
        """
        계획보고서대강
        
        사용LLM분석시뮬레이션 요구사항, 계획보고서의디렉토리구조
        
        Args:
            progress_callback: 진행률콜백함수
            
        Returns:
            ReportOutline: 보고서대강
        """
        logger.info("시작계획보고서대강...")
        
        if progress_callback:
            progress_callback("planning", 0, "진행 중: 분석시뮬레이션 요구사항...")
        
        # 먼저가져오기시뮬레이션컨텍스트
        context = self.zep_tools.get_simulation_context(
            graph_id=self.graph_id,
            simulation_requirement=self.simulation_requirement
        )
        
        if progress_callback:
            progress_callback("planning", 30, "진행 중: 생성보고서대강...")
        
        system_prompt = PLAN_SYSTEM_PROMPT
        user_prompt = PLAN_USER_PROMPT_TEMPLATE.format(
            simulation_requirement=self.simulation_requirement,
            total_nodes=context.get('graph_statistics', {}).get('total_nodes', 0),
            total_edges=context.get('graph_statistics', {}).get('total_edges', 0),
            entity_types=list(context.get('graph_statistics', {}).get('entity_types', {}).keys()),
            total_entities=context.get('total_entities', 0),
            related_facts_json=json.dumps(context.get('related_facts', [])[:10], ensure_ascii=False, indent=2),
        )

        try:
            response = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3
            )
            
            if progress_callback:
                progress_callback("planning", 80, "진행 중: 파싱대강구조...")
            
            # 파싱대강
            sections = []
            for section_data in response.get("sections", []):
                sections.append(ReportSection(
                    title=section_data.get("title", ""),
                    content=""
                ))
            
            outline = ReportOutline(
                title=response.get("title", "시뮬레이션분석 보고서"),
                summary=response.get("summary", ""),
                sections=sections
            )
            
            if progress_callback:
                progress_callback("planning", 100, "대강계획완료")
            
            logger.info(f"대강계획완료: {len(sections)} 개장")
            return outline
            
        except Exception as e:
            logger.error(f"대강계획실패: {str(e)}")
            # 돌아가기기본값대강(3개장, 作로fallback)
            return ReportOutline(
                title="예측보고서",
                summary="기반으로시뮬레이션예측의예트렌드와위험분석",
                sections=[
                    ReportSection(title="예측장면와핵심발견"),
                    ReportSection(title="인구행로예측분석"),
                    ReportSection(title="트렌드전망와위험안내")
                ]
            )
    
    def _generate_section_react(
        self, 
        section: ReportSection,
        outline: ReportOutline,
        previous_sections: List[str],
        progress_callback: Optional[Callable] = None,
        section_index: int = 0
    ) -> str:
        """
        사용ReACT모드생성단일장내용
        
        ReACT루프:
        1. Thought(思考)- 분석필요什么정보
        2. Action(행动)- 호출도구가져오기정보
        3. Observation(观察)- 분석도구돌아가기결과
        4. 重复直정보足够또는达최대횟수
        5. Final Answer(가장终되돌리기答)- 생성장내용
        
        Args:
            section: 생성의장
            outline: 완전대강
            previous_sections: 전에장의내용(용保持连贯性)
            progress_callback: 진행률콜백
            section_index: 장인덱스(용로그기록)
            
        Returns:
            장내용(Markdown형식)
        """
        logger.info(f"ReACT생성장: {section.title}")
        
        # 기록장시작로그
        if self.report_logger:
            self.report_logger.log_section_start(section.title, section_index)
        
        system_prompt = SECTION_SYSTEM_PROMPT_TEMPLATE.format(
            report_title=outline.title,
            report_summary=outline.summary,
            simulation_requirement=self.simulation_requirement,
            section_title=section.title,
            tools_description=self._get_tools_description(),
        )

        # 구축사용자prompt - 각완료장각전달최대4000字
        if previous_sections:
            previous_parts = []
            for sec in previous_sections:
                # 각장최대4000字
                truncated = sec[:4000] + "..." if len(sec) > 4000 else sec
                previous_parts.append(truncated)
            previous_content = "\n\n---\n\n".join(previous_parts)
        else:
            previous_content = "(这第一개장)"
        
        user_prompt = SECTION_USER_PROMPT_TEMPLATE.format(
            previous_content=previous_content,
            section_title=section.title,
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        # ReACT루프
        tool_calls_count = 0
        max_iterations = 5  # 최대반복라운드 수
        min_tool_calls = 3  # 최소도구호출횟수
        conflict_retries = 0  # 도구호출와Final Answer동시에출력现의连续冲突횟수
        used_tools = set()  # 기록이미호출의도구名
        all_tools = {"insight_forge", "panorama_search", "quick_search", "interview_agents"}

        # 보고서컨텍스트, 용InsightForge의하위 질문생성
        report_context = f"장 제목: {section.title}\n시뮬레이션 요구사항: {self.simulation_requirement}"
        
        for iteration in range(max_iterations):
            if progress_callback:
                progress_callback(
                    "generating", 
                    int((iteration / max_iterations) * 100),
                    f"심층 검색과 작성 중 ({tool_calls_count}/{self.MAX_TOOL_CALLS_PER_SECTION})"
                )
            
            # 호출LLM
            response = self.llm.chat(
                messages=messages,
                temperature=0.5,
                max_tokens=4096
            )

            # LLM이 None으로 돌아가는지 확인 (API 예외 또는 내용이 비어있음)
            if response is None:
                logger.warning(f"장 {section.title} 제 {iteration + 1} 회차: LLM이 None으로 돌아감")
                # 만약 아직 반복 횟수가 남았다면, 추가 메시지를 보내고 재시도
                if iteration < max_iterations - 1:
                    messages.append({"role": "assistant", "content": "(응답이 비어있음)"})
                    messages.append({"role": "user", "content": "계속해서 내용을 생성해 주세요."})
                    continue
                # 마지막 반복에서도 돌아가기가 None이라면, 강제로 루프를 탈출
                break

            logger.debug(f"LLM응답: {response[:200]}...")

            # 한 번 파싱하고 결과 재사용
            tool_calls = self._parse_tool_calls(response)
            has_tool_calls = bool(tool_calls)
            has_final_answer = "Final Answer:" in response

            # ── 충돌 처리: LLM이 동시에 도구 호출과 최종 답변을 출력 ──
            if has_tool_calls and has_final_answer:
                conflict_retries += 1
                logger.warning(
                    f"장 {section.title} 제 {iteration+1} 라운드: "
                    f"LLM이 동시에 도구 호출과 최종 답변을 출력함 (제 {conflict_retries} 회 충돌)"
                )

                if conflict_retries <= 2:
                    # 처음 두 번: 이번 응답을 버리고, LLM에게 요구사항을 다시 응답하게 함
                    messages.append({"role": "assistant", "content": response})
                    messages.append({
                        "role": "user",
                        "content": (
                            "【형식 오류】당신의 한 번의 응답 중 동시에 도구 호출과 최종 답변이 포함되어 있습니다. 이는 허용되지 않습니다.\n"
                            "각 응답은 아래 두 가지 중 하나만 수행해야 합니다:\n"
                            "- 하나의 도구 호출 (하나의 <tool_call> 블록을 출력하고, 최종 답변을 작성하지 마세요)\n"
                            "- 최종 내용을 출력 ( '최종 답변:'으로 시작하고, <tool_call>을 포함하지 마세요)\n"
                            "다시 응답해 주세요, 단 하나의 일만 수행해 주세요."
                        ),
                    })
                    continue
                else:
                    # 세 번째: 다운그레이드 처리, 첫 번째 도구 호출을 잘라내고 강제로 실행
                    logger.warning(
                        f"장 {section.title}: 연속 {conflict_retries} 회 충돌, "
                        "다운그레이드로 첫 번째 도구 호출을 잘라내어 실행"
                    )
                    first_tool_end = response.find('</tool_call>')
                    if first_tool_end != -1:
                        response = response[:first_tool_end + len('</tool_call>')]
                        tool_calls = self._parse_tool_calls(response)
                        has_tool_calls = bool(tool_calls)
                    has_final_answer = False
                    conflict_retries = 0

            # 기록 LLM 응답로그
            if self.report_logger:
                self.report_logger.log_llm_response(
                    section_title=section.title,
                    section_index=section_index,
                    response=response,
                    iteration=iteration + 1,
                    has_tool_calls=has_tool_calls,
                    has_final_answer=has_final_answer
                )

            # ── 상황 1: LLM이 최종 답변을 출력 ──
            if has_final_answer:
                # 도구 호출 횟수가 부족하여 거부하고 요구사항에 따라 도구를 계속 호출
                if tool_calls_count < min_tool_calls:
                    messages.append({"role": "assistant", "content": response})
                    unused_tools = all_tools - used_tools
                    unused_hint = f"(이 도구들은 아직 사용되지 않았습니다, 사용해 보세요: {', '.join(unused_tools)})" if unused_tools else ""
                    messages.append({
                        "role": "user",
                        "content": REACT_INSUFFICIENT_TOOLS_MSG.format(
                            tool_calls_count=tool_calls_count,
                            min_tool_calls=min_tool_calls,
                            unused_hint=unused_hint,
                        ),
                    })
                    continue

                # 정상 종료
                final_answer = response.split("Final Answer:")[-1].strip()
                logger.info(f"장 {section.title} 생성 완료(도구 호출: {tool_calls_count}회)")

                if self.report_logger:
                    self.report_logger.log_section_content(
                        section_title=section.title,
                        section_index=section_index,
                        content=final_answer,
                        tool_calls_count=tool_calls_count
                    )
                return final_answer

            # ── 상황 2: LLM이 도구 호출을 시도 ──
            if has_tool_calls:
                # 도구 할당량이 이미 소진됨 → 명확히 알리고, 요구사항에 따라 최종 답변을 출력
                if tool_calls_count >= self.MAX_TOOL_CALLS_PER_SECTION:
                    messages.append({"role": "assistant", "content": response})
                    messages.append({
                        "role": "user",
                        "content": REACT_TOOL_LIMIT_MSG.format(
                            tool_calls_count=tool_calls_count,
                            max_tool_calls=self.MAX_TOOL_CALLS_PER_SECTION,
                        ),
                    })
                    continue

                # 첫 번째 도구 호출만 실행
                call = tool_calls[0]
                if len(tool_calls) > 1:
                    logger.info(f"LLM이 {len(tool_calls)} 개 도구를 호출 시도, 첫 번째만 실행: {call['name']}")

                if self.report_logger:
                    self.report_logger.log_tool_call(
                        section_title=section.title,
                        section_index=section_index,
                        tool_name=call["name"],
                        parameters=call.get("parameters", {}),
                        iteration=iteration + 1
                    )

                result = self._execute_tool(
                    call["name"],
                    call.get("parameters", {}),
                    report_context=report_context
                )

                if self.report_logger:
                    self.report_logger.log_tool_result(
                        section_title=section.title,
                        section_index=section_index,
                        tool_name=call["name"],
                        result=result,
                        iteration=iteration + 1
                    )

                tool_calls_count += 1
                used_tools.add(call['name'])

                # 사용되지 않은 도구 안내
                unused_tools = all_tools - used_tools
                unused_hint = ""
                if unused_tools and tool_calls_count < self.MAX_TOOL_CALLS_PER_SECTION:
                    unused_hint = REACT_UNUSED_TOOLS_HINT.format(unused_list="、".join(unused_tools))

                messages.append({"role": "assistant", "content": response})
                messages.append({
                    "role": "user",
                    "content": REACT_OBSERVATION_TEMPLATE.format(
                        tool_name=call["name"],
                        result=result,
                        tool_calls_count=tool_calls_count,
                        max_tool_calls=self.MAX_TOOL_CALLS_PER_SECTION,
                        used_tools_str=", ".join(used_tools),
                        unused_hint=unused_hint,
                    ),
                })
                continue

            # ── 상황 3: 도구 호출도 없고, 최종 답변도 없음 ──
            messages.append({"role": "assistant", "content": response})

            if tool_calls_count < min_tool_calls:
                # 도구 호출 횟수가 부족하여 사용되지 않은 도구를 추천
                unused_tools = all_tools - used_tools
                unused_hint = f"(이 도구들은 아직 사용되지 않았습니다, 사용해 보세요: {', '.join(unused_tools)})" if unused_tools else ""

                messages.append({
                    "role": "user",
                    "content": REACT_INSUFFICIENT_TOOLS_MSG_ALT.format(
                        tool_calls_count=tool_calls_count,
                        min_tool_calls=min_tool_calls,
                        unused_hint=unused_hint,
                    ),
                })
                continue

            # 도구 호출이 이미 충분하여, LLM이 출력한 내용이지만 "최종 답변:" 접두사가 없음
            # 직접 이 내용을 최종 답변으로 사용하고, 더 이상 공회전하지 않음
            logger.info(f"장 {section.title}에서 '최종 답변:' 접두사를 감지하지 못했습니다, LLM 출력 내용을 최종 내용으로 직접 채택합니다(도구 호출: {tool_calls_count}회)")
            final_answer = response.strip()

            if self.report_logger:
                self.report_logger.log_section_content(
                    section_title=section.title,
                    section_index=section_index,
                    content=final_answer,
                    tool_calls_count=tool_calls_count
                )
            return final_answer
        
        # 최대 반복 횟수에 도달하여 강제로 내용을 생성
        logger.warning(f"장 {section.title} 최대 반복 횟수에 도달하여 강제로 생성")
        messages.append({"role": "user", "content": REACT_FORCE_FINAL_MSG})
        
        response = self.llm.chat(
            messages=messages,
            temperature=0.5,
            max_tokens=4096
        )

        # 강제로 종료할 때 LLM이 None으로 돌아가는지 확인
        if response is None:
            logger.error(f"장 절 {section.title} 강제수尾 시 LLM 돌아가기 None, 사용기본값오류안내")
            final_answer = f"(본 장 절생성 실패:LLM 돌아가기공응답, 잠시 후재시도)"
        elif "Final Answer:" in response:
            final_answer = response.split("Final Answer:")[-1].strip()
        else:
            final_answer = response
        
        # 기록 장 절내용생성완료로그
        if self.report_logger:
            self.report_logger.log_section_content(
                section_title=section.title,
                section_index=section_index,
                content=final_answer,
                tool_calls_count=tool_calls_count
            )
        
        return final_answer
    
    def generate_report(
        self, 
        progress_callback: Optional[Callable[[str, int, str], None]] = None,
        report_id: Optional[str] = None
    ) -> Report:
        """
        생성 완전보고서(분 장 절실시간출력)
        
        매개 장 절생성완료즉시저장파일夹, 안필요대기전체보고서완료。
        파일구조:
        reports/{report_id}/
            meta.json       - 보고서 메타정보
            outline.json    - 보고서 대강
            progress.json   - 생성진행률
            section_01.md   - 제1장
            section_02.md   - 제2장
            ...
            full_report.md  - 완전보고서
        
        Args:
            progress_callback: 진행률 콜백함수 (stage, progress, message)
            report_id: 보고서 ID(선택, 만약안传자동생성)
            
        Returns:
            Report: 완전보고서
        """
        import uuid
        
        # 만약없으면传입력 report_id, 자동생성
        if not report_id:
            report_id = f"report_{uuid.uuid4().hex[:12]}"
        start_time = datetime.now()
        
        report = Report(
            report_id=report_id,
            simulation_id=self.simulation_id,
            graph_id=self.graph_id,
            simulation_requirement=self.simulation_requirement,
            status=ReportStatus.PENDING,
            created_at=datetime.now().isoformat()
        )
        
        # 완료의 장 절 제목목록(용 진행률 추적)
        completed_section_titles = []
        
        try:
            # 초기화:생성보고서파일夹그리고저장초기상태
            ReportManager._ensure_report_folder(report_id)
            
            # 초기화로그기록기(구조화로그 agent_log.jsonl)
            self.report_logger = ReportLogger(report_id)
            self.report_logger.log_start(
                simulation_id=self.simulation_id,
                graph_id=self.graph_id,
                simulation_requirement=self.simulation_requirement
            )
            
            # 초기화콘솔로그기록기(console_log.txt)
            self.console_logger = ReportConsoleLogger(report_id)
            
            ReportManager.update_progress(
                report_id, "pending", 0, "초기화보고서...",
                completed_sections=[]
            )
            ReportManager.save_report(report)
            
            # 단계1: 계획 대강
            report.status = ReportStatus.PLANNING
            ReportManager.update_progress(
                report_id, "planning", 5, "시작 계획보고서 대강...",
                completed_sections=[]
            )
            
            # 기록 계획시작로그
            self.report_logger.log_planning_start()
            
            if progress_callback:
                progress_callback("planning", 0, "시작 계획보고서 대강...")
            
            outline = self.plan_outline(
                progress_callback=lambda stage, prog, msg: 
                    progress_callback(stage, prog // 5, msg) if progress_callback else None
            )
            report.outline = outline
            
            # 기록 계획완료로그
            self.report_logger.log_planning_complete(outline.to_dict())
            
            # 저장 대강파일
            ReportManager.save_outline(report_id, outline)
            ReportManager.update_progress(
                report_id, "planning", 15, f"대강 계획완료, 共{len(outline.sections)}개 장 절",
                completed_sections=[]
            )
            ReportManager.save_report(report)
            
            logger.info(f"대강이미저장파일: {report_id}/outline.json")
            
            # 단계2: 장 절별 생성(분 장 절 저장)
            report.status = ReportStatus.GENERATING
            
            total_sections = len(outline.sections)
            generated_sections = []  # 저장내용용컨텍스트
            
            for i, section in enumerate(outline.sections):
                section_num = i + 1
                base_progress = 20 + int((i / total_sections) * 70)
                
                # 업데이트진행률
                ReportManager.update_progress(
                    report_id, "generating", base_progress,
                    f"진행 중: 생성 장 절: {section.title} ({section_num}/{total_sections})",
                    current_section=section.title,
                    completed_sections=completed_section_titles
                )
                
                if progress_callback:
                    progress_callback(
                        "generating", 
                        base_progress, 
                        f"진행 중: 생성 장 절: {section.title} ({section_num}/{total_sections})"
                    )
                
                # 생성 메인 장 절 내용
                section_content = self._generate_section_react(
                    section=section,
                    outline=outline,
                    previous_sections=generated_sections,
                    progress_callback=lambda stage, prog, msg:
                        progress_callback(
                            stage, 
                            base_progress + int(prog * 0.7 / total_sections),
                            msg
                        ) if progress_callback else None,
                    section_index=section_num
                )
                
                section.content = section_content
                generated_sections.append(f"## {section.title}\n\n{section_content}")

                # 저장 장 절
                ReportManager.save_section(report_id, section_num, section)
                completed_section_titles.append(section.title)

                # 기록 장 절완료로그
                full_section_content = f"## {section.title}\n\n{section_content}"

                if self.report_logger:
                    self.report_logger.log_section_full_complete(
                        section_title=section.title,
                        section_index=section_num,
                        full_content=full_section_content.strip()
                    )

                logger.info(f"장 절이미저장: {report_id}/section_{section_num:02d}.md")
                
                # 업데이트진행률
                ReportManager.update_progress(
                    report_id, "generating", 
                    base_progress + int(70 / total_sections),
                    f"장 절 {section.title} 완료",
                    current_section=None,
                    completed_sections=completed_section_titles
                )
            
            # 단계3: 조립 완전보고서
            if progress_callback:
                progress_callback("generating", 95, "진행 중: 조립 완전보고서...")
            
            ReportManager.update_progress(
                report_id, "generating", 95, "진행 중: 조립 완전보고서...",
                completed_sections=completed_section_titles
            )
            
            # 사용 ReportManager 조립 완전보고서
            report.markdown_content = ReportManager.assemble_full_report(report_id, outline)
            report.status = ReportStatus.COMPLETED
            report.completed_at = datetime.now().isoformat()
            
            # 계산 총 소요시간
            total_time_seconds = (datetime.now() - start_time).total_seconds()
            
            # 기록보고서완료로그
            if self.report_logger:
                self.report_logger.log_report_complete(
                    total_sections=total_sections,
                    total_time_seconds=total_time_seconds
                )
            
            # 저장 최종보고서
            ReportManager.save_report(report)
            ReportManager.update_progress(
                report_id, "completed", 100, "보고서 생성완료",
                completed_sections=completed_section_titles
            )
            
            if progress_callback:
                progress_callback("completed", 100, "보고서 생성완료")
            
            logger.info(f"보고서 생성완료: {report_id}")
            
            # 닫기 콘솔 로그 기록기
            if self.console_logger:
                self.console_logger.close()
                self.console_logger = None
            
            return report
            
        except Exception as e:
            logger.error(f"보고서 생성실패: {str(e)}")
            report.status = ReportStatus.FAILED
            report.error = str(e)
            
            # 기록오류로그
            if self.report_logger:
                self.report_logger.log_error(str(e), "failed")
            
            # 저장 실패상태
            try:
                ReportManager.save_report(report)
                ReportManager.update_progress(
                    report_id, "failed", -1, f"보고서 생성실패: {str(e)}",
                    completed_sections=completed_section_titles
                )
            except Exception:
                pass  # 저장 실패의 오류 무시
            
            # 닫기 콘솔 로그 기록기
            if self.console_logger:
                self.console_logger.close()
                self.console_logger = None
            
            return report
    
    def chat(
        self, 
        message: str,
        chat_history: List[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        와Report Agent대화
        
        에서 대화 중 Agent 하여 자 메인 호출 검색 도구 답변 질문
        
        Args:
            message: 사용자메시지
            chat_history: 대화 역사
            
        Returns:
            {
                "response": "Agent 응답",
                "tool_calls": [호출의도구목록],
                "sources": [정보원]
            }
        """
        logger.info(f"Report Agent대화: {message[:50]}...")
        
        chat_history = chat_history or []
        
        # 가져오기이미생성의보고서내용
        report_content = ""
        try:
            report = ReportManager.get_report_by_simulation(self.simulation_id)
            if report and report.markdown_content:
                # 보고서 길이 제한, 컨텍스트 너무 길어지지 않도록
                report_content = report.markdown_content[:15000]
                if len(report.markdown_content) > 15000:
                    report_content += "\n\n... [보고서 내용 이미잘림] ..."
        except Exception as e:
            logger.warning(f"가져오기보고서내용실패: {e}")
        
        system_prompt = CHAT_SYSTEM_PROMPT_TEMPLATE.format(
            simulation_requirement=self.simulation_requirement,
            report_content=report_content if report_content else "(暂없음 보고서)",
            tools_description=self._get_tools_description(),
        )

        # 구축메시지
        messages = [{"role": "system", "content": system_prompt}]
        
        # 추가 역사 대화
        for h in chat_history[-10:]:  # 역사 길이 제한
            messages.append(h)
        
        # 추가사용자메시지
        messages.append({
            "role": "user", 
            "content": message
        })
        
        # ReACT 루프 (간소화 버전)
        tool_calls_made = []
        max_iterations = 2  # 반복 라운드 수 줄이기
        
        for iteration in range(max_iterations):
            response = self.llm.chat(
                messages=messages,
                temperature=0.5
            )
            
            # 파싱도구호출
            tool_calls = self._parse_tool_calls(response)
            
            if not tool_calls:
                # 없으면 도구 호출, 직접 돌아가기 응답
                clean_response = re.sub(r'<tool_call>.*?</tool_call>', '', response, flags=re.DOTALL)
                clean_response = re.sub(r'\[TOOL_CALL\].*?\)', '', clean_response)
                
                return {
                    "response": clean_response.strip(),
                    "tool_calls": tool_calls_made,
                    "sources": [tc.get("parameters", {}).get("query", "") for tc in tool_calls_made]
                }
            
            # 실행 도구 호출 (수량 제한)
            tool_results = []
            for call in tool_calls[:1]:  # 매 라운드 최대 실행 1회 도구 호출
                if len(tool_calls_made) >= self.MAX_TOOL_CALLS_PER_CHAT:
                    break
                result = self._execute_tool(call["name"], call.get("parameters", {}))
                tool_results.append({
                    "tool": call["name"],
                    "result": result[:1500]  # 결과 길이 제한
                })
                tool_calls_made.append(call)
            
            # 결과추가메시지
            messages.append({"role": "assistant", "content": response})
            observation = "\n".join([f"[{r['tool']}결과]\n{r['result']}" for r in tool_results])
            messages.append({
                "role": "user",
                "content": observation + CHAT_OBSERVATION_SUFFIX
            })
        
        # 최대 반복 도달, 최종 응답 가져오기
        final_response = self.llm.chat(
            messages=messages,
            temperature=0.5
        )
        
        # 정리응답
        clean_response = re.sub(r'<tool_call>.*?</tool_call>', '', final_response, flags=re.DOTALL)
        clean_response = re.sub(r'\[TOOL_CALL\].*?\)', '', clean_response)
        
        return {
            "response": clean_response.strip(),
            "tool_calls": tool_calls_made,
            "sources": [tc.get("parameters", {}).get("query", "") for tc in tool_calls_made]
        }


class ReportManager:
    """
    보고서 관리자
    
    보고서의 지속화 저장과 검색 담당
    
    파일 구조 (분 장 출력):
    reports/
      {report_id}/
        meta.json          - 보고서 메타 정보와 상태
        outline.json       - 보고서 개요
        progress.json      - 생성진행률
        section_01.md      - 제 1 장
        section_02.md      - 제 2 장
        ...
        full_report.md     - 전체 보고서
    """
    
    # 보고서 저장 디렉토리
    REPORTS_DIR = os.path.join(Config.UPLOAD_FOLDER, 'reports')
    
    @classmethod
    def _ensure_reports_dir(cls):
        """보장 보고서 루트 디렉토리 저장에서"""
        os.makedirs(cls.REPORTS_DIR, exist_ok=True)
    
    @classmethod
    def _get_report_folder(cls, report_id: str) -> str:
        """가져오기 보고서 파일夹 경로"""
        return os.path.join(cls.REPORTS_DIR, report_id)
    
    @classmethod
    def _ensure_report_folder(cls, report_id: str) -> str:
        """보장 보고서 파일夹 저장에서 및 돌아가기 경로"""
        folder = cls._get_report_folder(report_id)
        os.makedirs(folder, exist_ok=True)
        return folder
    
    @classmethod
    def _get_report_path(cls, report_id: str) -> str:
        """가져오기 보고서 메타 정보 파일 경로"""
        return os.path.join(cls._get_report_folder(report_id), "meta.json")
    
    @classmethod
    def _get_report_markdown_path(cls, report_id: str) -> str:
        """가져오기 전체 보고서 Markdown 파일 경로"""
        return os.path.join(cls._get_report_folder(report_id), "full_report.md")
    
    @classmethod
    def _get_outline_path(cls, report_id: str) -> str:
        """가져오기 개요 파일 경로"""
        return os.path.join(cls._get_report_folder(report_id), "outline.json")
    
    @classmethod
    def _get_progress_path(cls, report_id: str) -> str:
        """가져오기진행률파일경로"""
        return os.path.join(cls._get_report_folder(report_id), "progress.json")
    
    @classmethod
    def _get_section_path(cls, report_id: str, section_index: int) -> str:
        """가져오기 장 Markdown 파일 경로"""
        return os.path.join(cls._get_report_folder(report_id), f"section_{section_index:02d}.md")
    
    @classmethod
    def _get_agent_log_path(cls, report_id: str) -> str:
        """가져오기 Agent 로그파일경로"""
        return os.path.join(cls._get_report_folder(report_id), "agent_log.jsonl")
    
    @classmethod
    def _get_console_log_path(cls, report_id: str) -> str:
        """가져오기콘솔로그파일경로"""
        return os.path.join(cls._get_report_folder(report_id), "console_log.txt")
    
    @classmethod
    def get_console_log(cls, report_id: str, from_line: int = 0) -> Dict[str, Any]:
        """
        가져오기콘솔로그내용
        
        이 보고서 생성 과정의 콘솔 출력 로그 (INFO, WARNING 등),
        와 agent_log.jsonl 의 구조화 로그와 동일.
        
        Args:
            report_id: 보고서ID
            from_line: 몇 번째 줄부터 시작 읽기 (증분 가져오기, 0은 처음부터 시작)
            
        Returns:
            {
                "logs": [로그 행 목록],
                "total_lines": 총 행 수,
                "from_line": 시작행번호,
                "has_more": 더 많은 로그가 있습니까,
            }
        """
        log_path = cls._get_console_log_path(report_id)
        
        if not os.path.exists(log_path):
            return {
                "logs": [],
                "total_lines": 0,
                "from_line": 0,
                "has_more": False
            }
        
        logs = []
        total_lines = 0
        
        with open(log_path, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f):
                total_lines = i + 1
                if i >= from_line:
                    # 원본 로그 행을 보존하고 끝의 줄 바꿈 기호를 제거합니다
                    logs.append(line.rstrip('\n\r'))
        
        return {
            "logs": logs,
            "total_lines": total_lines,
            "from_line": from_line,
            "has_more": False  # 이미 끝까지 읽었습니다
        }
    
    @classmethod
    def get_console_log_stream(cls, report_id: str) -> List[str]:
        """
        가져오기 전체 콘솔 로그 (한 번에 모두 가져오기)
        
        Args:
            report_id: 보고서ID
            
        Returns:
            로그 행 목록
        """
        result = cls.get_console_log(report_id, from_line=0)
        return result["logs"]
    
    @classmethod
    def get_agent_log(cls, report_id: str, from_line: int = 0) -> Dict[str, Any]:
        """
        가져오기 Agent 로그내용
        
        Args:
            report_id: 보고서ID
            from_line: 몇 번째 줄부터 시작 읽기 (증분 가져오기, 0은 처음부터 시작)
            
        Returns:
            {
                "logs": [로그 항목 목록],
                "total_lines": 총 행 수,
                "from_line": 시작행번호,
                "has_more": 더 많은 로그가 있습니까
            }
        """
        log_path = cls._get_agent_log_path(report_id)
        
        if not os.path.exists(log_path):
            return {
                "logs": [],
                "total_lines": 0,
                "from_line": 0,
                "has_more": False
            }
        
        logs = []
        total_lines = 0
        
        with open(log_path, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f):
                total_lines = i + 1
                if i >= from_line:
                    try:
                        log_entry = json.loads(line.strip())
                        logs.append(log_entry)
                    except json.JSONDecodeError:
                        # 파싱 실패의 행 건너뛰기
                        continue
        
        return {
            "logs": logs,
            "total_lines": total_lines,
            "from_line": from_line,
            "has_more": False  # 이미 끝까지 읽었습니다
        }
    
    @classmethod
    def get_agent_log_stream(cls, report_id: str) -> List[Dict[str, Any]]:
        """
        가져오기 전체 Agent 로그 (한 번에 모두 가져오기)
        
        Args:
            report_id: 보고서ID
            
        Returns:
            로그 항목 목록
        """
        result = cls.get_agent_log(report_id, from_line=0)
        return result["logs"]
    
    @classmethod
    def save_outline(cls, report_id: str, outline: ReportOutline) -> None:
        """
        저장 보고서 개요
        
        계획 단계 완료 즉시 호출
        """
        cls._ensure_report_folder(report_id)
        
        with open(cls._get_outline_path(report_id), 'w', encoding='utf-8') as f:
            json.dump(outline.to_dict(), f, ensure_ascii=False, indent=2)
        
        logger.info(f"개요가 이미 저장되었습니다: {report_id}")
    
    @classmethod
    def save_section(
        cls,
        report_id: str,
        section_index: int,
        section: ReportSection
    ) -> str:
        """
        저장 단일 챕터

        각 챕터 생성 완료 즉시 호출, 챕터별 출력 구현

        Args:
            report_id: 보고서ID
            section_index: 챕터 인덱스 (1부터 시작)
            section: 챕터 객체

        Returns:
            저장의파일경로
        """
        cls._ensure_report_folder(report_id)

        # 챕터 Markdown 내용 구축 - 정리 저장에서의 중복 제목
        cleaned_content = cls._clean_section_content(section.content, section.title)
        md_content = f"## {section.title}\n\n"
        if cleaned_content:
            md_content += f"{cleaned_content}\n\n"

        # 저장파일
        file_suffix = f"section_{section_index:02d}.md"
        file_path = os.path.join(cls._get_report_folder(report_id), file_suffix)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(md_content)

        logger.info(f"챕터가 이미 저장되었습니다: {report_id}/{file_suffix}")
        return file_path
    
    @classmethod
    def _clean_section_content(cls, content: str, section_title: str) -> str:
        """
        정리 챕터 내용
        
        1. 내용의 시작과 챕터 제목이 중복된 Markdown 제목 행 제거
        2. 모든 ### 및 하위 제목을 굵은 텍스트로 변환
        
        Args:
            content: 원본 내용
            section_title: 챕터 제목
            
        Returns:
            정리후의내용
        """
        import re
        
        if not content:
            return content
        
        content = content.strip()
        lines = content.split('\n')
        cleaned_lines = []
        skip_next_empty = False
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            
            # Markdown 제목 행인지 확인
            heading_match = re.match(r'^(#{1,6})\s+(.+)$', stripped)
            
            if heading_match:
                level = len(heading_match.group(1))
                title_text = heading_match.group(2).strip()
                
                # 챕터 제목과 중복된 제목인지 확인 (이전 5행 내의 중복)
                if i < 5:
                    if title_text == section_title or title_text.replace(' ', '') == section_title.replace(' ', ''):
                        skip_next_empty = True
                        continue
                
                # 모든 수준의 제목 (#, ##, ###, #### 등)을 굵게 변환
                # 챕터 제목은 시스템에 의해 추가되므로 내용 중 어떤 제목도 포함될 수 있습니다
                cleaned_lines.append(f"**{title_text}**")
                cleaned_lines.append("")  # 빈 행 추가
                continue
            
            # 만약 이전 행이 제목이고 현재 행이 비어 있다면, 건너뜁니다
            if skip_next_empty and stripped == '':
                skip_next_empty = False
                continue
            
            skip_next_empty = False
            cleaned_lines.append(line)
        
        # 시작의 빈 행 제거
        while cleaned_lines and cleaned_lines[0].strip() == '':
            cleaned_lines.pop(0)
        
        # 시작의 구분선 제거
        while cleaned_lines and cleaned_lines[0].strip() in ['---', '***', '___']:
            cleaned_lines.pop(0)
            # 동시에 구분선 이후의 빈 행 제거
            while cleaned_lines and cleaned_lines[0].strip() == '':
                cleaned_lines.pop(0)
        
        return '\n'.join(cleaned_lines)
    
    @classmethod
    def update_progress(
        cls, 
        report_id: str, 
        status: str, 
        progress: int, 
        message: str,
        current_section: str = None,
        completed_sections: List[str] = None
    ) -> None:
        """
        업데이트보고서 생성진행률
        
        프론트엔드에서 progress.json을 읽어 실시간 진행률 가져오기
        """
        cls._ensure_report_folder(report_id)
        
        progress_data = {
            "status": status,
            "progress": progress,
            "message": message,
            "current_section": current_section,
            "completed_sections": completed_sections or [],
            "updated_at": datetime.now().isoformat()
        }
        
        with open(cls._get_progress_path(report_id), 'w', encoding='utf-8') as f:
            json.dump(progress_data, f, ensure_ascii=False, indent=2)
    
    @classmethod
    def get_progress(cls, report_id: str) -> Optional[Dict[str, Any]]:
        """가져오기보고서 생성진행률"""
        path = cls._get_progress_path(report_id)
        
        if not os.path.exists(path):
            return None
        
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    @classmethod
    def get_generated_sections(cls, report_id: str) -> List[Dict[str, Any]]:
        """
        가져오기 이미 생성된 챕터 목록
        
        돌아가기所이미저장의챕터파일정보
        """
        folder = cls._get_report_folder(report_id)
        
        if not os.path.exists(folder):
            return []
        
        sections = []
        for filename in sorted(os.listdir(folder)):
            if filename.startswith('section_') and filename.endswith('.md'):
                file_path = os.path.join(folder, filename)
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                # 부터파일명파싱챕터인덱스
                parts = filename.replace('.md', '').split('_')
                section_index = int(parts[1])

                sections.append({
                    "filename": filename,
                    "section_index": section_index,
                    "content": content
                })

        return sections
    
    @classmethod
    def assemble_full_report(cls, report_id: str, outline: ReportOutline) -> str:
        """
        조립완전보고서
        
        부터이미저장의챕터파일조립완전보고서, 그리고진행제목정리
        """
        folder = cls._get_report_folder(report_id)
        
        # 구축보고서머리
        md_content = f"# {outline.title}\n\n"
        md_content += f"> {outline.summary}\n\n"
        md_content += f"---\n\n"
        
        # 순서대로읽기所챕터파일
        sections = cls.get_generated_sections(report_id)
        for section_info in sections:
            md_content += section_info["content"]
        
        # 후처리:정리전체보고서의제목문제
        md_content = cls._post_process_report(md_content, outline)
        
        # 저장완전보고서
        full_path = cls._get_report_markdown_path(report_id)
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(md_content)
        
        logger.info(f"완전보고서이미조립: {report_id}")
        return md_content
    
    @classmethod
    def _post_process_report(cls, content: str, outline: ReportOutline) -> str:
        """
        후처리보고서내용
        
        1. 제거중복의제목
        2. 보존보고서메인제목(#)와챕터제목(##), 제거기타수준의제목(###, ####등)
        3. 정리여분의빈행와구분선
        
        Args:
            content: 원본보고서내용
            outline: 보고서개요
            
        Returns:
            처리후의내용
        """
        import re
        
        lines = content.split('\n')
        processed_lines = []
        prev_was_heading = False
        
        # 수집개요의所챕터제목
        section_titles = set()
        for section in outline.sections:
            section_titles.add(section.title)
        
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            
            # 확인제목행인지
            heading_match = re.match(r'^(#{1,6})\s+(.+)$', stripped)
            
            if heading_match:
                level = len(heading_match.group(1))
                title = heading_match.group(2).strip()
                
                # 확인중복제목인지(에서연속5행내출현상동내용의제목)
                is_duplicate = False
                for j in range(max(0, len(processed_lines) - 5), len(processed_lines)):
                    prev_line = processed_lines[j].strip()
                    prev_match = re.match(r'^(#{1,6})\s+(.+)$', prev_line)
                    if prev_match:
                        prev_title = prev_match.group(2).strip()
                        if prev_title == title:
                            is_duplicate = True
                            break
                
                if is_duplicate:
                    # 건너뛰기중복제목및그후의빈행
                    i += 1
                    while i < len(lines) and lines[i].strip() == '':
                        i += 1
                    continue
                
                # 제목수준처리:
                # - # (level=1) 오직보존보고서메인제목
                # - ## (level=2) 보존챕터제목
                # - ### 및이하 (level>=3) 변환으로굵은텍스트
                
                if level == 1:
                    if title == outline.title:
                        # 보존보고서메인제목
                        processed_lines.append(line)
                        prev_was_heading = True
                    elif title in section_titles:
                        # 챕터제목오류사용#, 수정으로##
                        processed_lines.append(f"## {title}")
                        prev_was_heading = True
                    else:
                        # 기타일급제목변환으로굵은텍스트
                        processed_lines.append(f"**{title}**")
                        processed_lines.append("")
                        prev_was_heading = False
                elif level == 2:
                    if title in section_titles or title == outline.title:
                        # 보존챕터제목
                        processed_lines.append(line)
                        prev_was_heading = True
                    else:
                        # 비챕터의이급제목변환으로굵은텍스트
                        processed_lines.append(f"**{title}**")
                        processed_lines.append("")
                        prev_was_heading = False
                else:
                    # ### 및이하수준의제목변환으로굵은텍스트
                    processed_lines.append(f"**{title}**")
                    processed_lines.append("")
                    prev_was_heading = False
                
                i += 1
                continue
            
            elif stripped == '---' and prev_was_heading:
                # 건너뛰기제목후긴접의구분선
                i += 1
                continue
            
            elif stripped == '' and prev_was_heading:
                # 제목후오직보존한개빈행
                if processed_lines and processed_lines[-1].strip() != '':
                    processed_lines.append(line)
                prev_was_heading = False
            
            else:
                processed_lines.append(line)
                prev_was_heading = False
            
            i += 1
        
        # 정리연속의여러빈행(보존최대2개)
        result_lines = []
        empty_count = 0
        for line in processed_lines:
            if line.strip() == '':
                empty_count += 1
                if empty_count <= 2:
                    result_lines.append(line)
            else:
                empty_count = 0
                result_lines.append(line)
        
        return '\n'.join(result_lines)
    
    @classmethod
    def save_report(cls, report: Report) -> None:
        """저장보고서메타정보와완전보고서"""
        cls._ensure_report_folder(report.report_id)
        
        # 저장메타정보JSON
        with open(cls._get_report_path(report.report_id), 'w', encoding='utf-8') as f:
            json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
        
        # 저장개요
        if report.outline:
            cls.save_outline(report.report_id, report.outline)
        
        # 저장완전Markdown보고서
        if report.markdown_content:
            with open(cls._get_report_markdown_path(report.report_id), 'w', encoding='utf-8') as f:
                f.write(report.markdown_content)
        
        logger.info(f"보고서이미저장: {report.report_id}")
    
    @classmethod
    def get_report(cls, report_id: str) -> Optional[Report]:
        """가져오기보고서"""
        path = cls._get_report_path(report_id)
        
        if not os.path.exists(path):
            # 호환구형식:확인직접저장에서reports디렉토리하의파일
            old_path = os.path.join(cls.REPORTS_DIR, f"{report_id}.json")
            if os.path.exists(old_path):
                path = old_path
            else:
                return None
        
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 재구성Report형상
        outline = None
        if data.get('outline'):
            outline_data = data['outline']
            sections = []
            for s in outline_data.get('sections', []):
                sections.append(ReportSection(
                    title=s['title'],
                    content=s.get('content', '')
                ))
            outline = ReportOutline(
                title=outline_data['title'],
                summary=outline_data['summary'],
                sections=sections
            )
        
        # 만약markdown_content로빈, 시도부터full_report.md읽기
        markdown_content = data.get('markdown_content', '')
        if not markdown_content:
            full_report_path = cls._get_report_markdown_path(report_id)
            if os.path.exists(full_report_path):
                with open(full_report_path, 'r', encoding='utf-8') as f:
                    markdown_content = f.read()
        
        return Report(
            report_id=data['report_id'],
            simulation_id=data['simulation_id'],
            graph_id=data['graph_id'],
            simulation_requirement=data['simulation_requirement'],
            status=ReportStatus(data['status']),
            outline=outline,
            markdown_content=markdown_content,
            created_at=data.get('created_at', ''),
            completed_at=data.get('completed_at', ''),
            error=data.get('error')
        )
    
    @classmethod
    def get_report_by_simulation(cls, simulation_id: str) -> Optional[Report]:
        """에 따라시뮬레이션ID가져오기보고서"""
        cls._ensure_reports_dir()
        
        for item in os.listdir(cls.REPORTS_DIR):
            item_path = os.path.join(cls.REPORTS_DIR, item)
            # 신형식:파일폴더
            if os.path.isdir(item_path):
                report = cls.get_report(item)
                if report and report.simulation_id == simulation_id:
                    return report
            # 호환구형식:JSON파일
            elif item.endswith('.json'):
                report_id = item[:-5]
                report = cls.get_report(report_id)
                if report and report.simulation_id == simulation_id:
                    return report
        
        return None
    
    @classmethod
    def list_reports(cls, simulation_id: Optional[str] = None, limit: int = 50) -> List[Report]:
        """보고서 목록"""
        cls._ensure_reports_dir()
        
        reports = []
        for item in os.listdir(cls.REPORTS_DIR):
            item_path = os.path.join(cls.REPORTS_DIR, item)
            # 새로운 형식:파일夹
            if os.path.isdir(item_path):
                report = cls.get_report(item)
                if report:
                    if simulation_id is None or report.simulation_id == simulation_id:
                        reports.append(report)
            # 구형식 호환:JSON파일
            elif item.endswith('.json'):
                report_id = item[:-5]
                report = cls.get_report(report_id)
                if report:
                    if simulation_id is None or report.simulation_id == simulation_id:
                        reports.append(report)
        
        # 생성시간 역순
        reports.sort(key=lambda r: r.created_at, reverse=True)
        
        return reports[:limit]
    
    @classmethod
    def delete_report(cls, report_id: str) -> bool:
        """삭제 보고서(전체 파일夹)"""
        import shutil
        
        folder_path = cls._get_report_folder(report_id)
        
        # 새로운 형식:전체 파일夹 삭제
        if os.path.exists(folder_path) and os.path.isdir(folder_path):
            shutil.rmtree(folder_path)
            logger.info(f"보고서 파일夹가 이미 삭제됨: {report_id}")
            return True
        
        # 구형식 호환:개별 파일 삭제
        deleted = False
        old_json_path = os.path.join(cls.REPORTS_DIR, f"{report_id}.json")
        old_md_path = os.path.join(cls.REPORTS_DIR, f"{report_id}.md")
        
        if os.path.exists(old_json_path):
            os.remove(old_json_path)
            deleted = True
        if os.path.exists(old_md_path):
            os.remove(old_md_path)
            deleted = True
        
        return deleted
