"""
Report API라우팅
보고서 생성, 가져오기, 대화 등 인터페이스
"""

import os
import traceback
import threading
from flask import request, jsonify, send_file

from . import report_bp
from ..config import Config
from ..services.report_agent import ReportAgent, ReportManager, ReportStatus
from ..services.simulation_manager import SimulationManager
from ..models.project import ProjectManager
from ..models.task import TaskManager, TaskStatus
from ..utils.logger import get_logger

logger = get_logger('mirofish.api.report')


# ============== 보고서 생성인터페이스 ==============

@report_bp.route('/generate', methods=['POST'])
def generate_report():
    """
    생성시뮬레이션분석 보고서（비동기작업）
    
    이 작업은 시간이 소요되며, 인터페이스는 즉시 돌아가기 task_id,
    사용 GET /api/report/generate/status 조회진행률
    
    요청（JSON）：
        {
            "simulation_id": "sim_xxxx",    // 필수，시뮬레이션ID
            "force_regenerate": false        // 선택, 강제로 재생성
        }
    
    돌아가기：
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "task_id": "task_xxxx",
                "status": "generating",
                "message": "보고서 생성작업이 시작되었습니다"
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "시뮬레이션 ID를 제공해 주세요"
            }), 400
        
        force_regenerate = data.get('force_regenerate', False)
        
        # 가져오기시뮬레이션정보
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        
        if not state:
            return jsonify({
                "success": False,
                "error": f"시뮬레이션존재하지 않음: {simulation_id}"
            }), 404
        
        # 이미 보고서가 있는지 확인
        if not force_regenerate:
            existing_report = ReportManager.get_report_by_simulation(simulation_id)
            if existing_report and existing_report.status == ReportStatus.COMPLETED:
                return jsonify({
                    "success": True,
                    "data": {
                        "simulation_id": simulation_id,
                        "report_id": existing_report.report_id,
                        "status": "completed",
                        "message": "보고서이미 존재함",
                        "already_generated": True
                    }
                })
        
        # 가져오기프로젝트 정보
        project = ProjectManager.get_project(state.project_id)
        if not project:
            return jsonify({
                "success": False,
                "error": f"프로젝트존재하지 않음: {state.project_id}"
            }), 404
        
        graph_id = state.graph_id or project.graph_id
        if not graph_id:
            return jsonify({
                "success": False,
                "error": "그래프 ID가 부족합니다, 이미 구축된 그래프를 보장해 주세요"
            }), 400
        
        simulation_requirement = project.simulation_requirement
        if not simulation_requirement:
            return jsonify({
                "success": False,
                "error": "시뮬레이션 요구사항 설명이 부족합니다"
            }), 400
        
        # 미리 생성된 report_id를 사용하여 즉시 돌아가기
        import uuid
        report_id = f"report_{uuid.uuid4().hex[:12]}"
        
        # 생성비동기작업
        task_manager = TaskManager()
        task_id = task_manager.create_task(
            task_type="report_generate",
            metadata={
                "simulation_id": simulation_id,
                "graph_id": graph_id,
                "report_id": report_id
            }
        )
        
        # 백그라운드 작업 정의
        def run_generate():
            try:
                task_manager.update_task(
                    task_id,
                    status=TaskStatus.PROCESSING,
                    progress=0,
                    message="초기화Report Agent..."
                )
                
                # 생성Report Agent
                agent = ReportAgent(
                    graph_id=graph_id,
                    simulation_id=simulation_id,
                    simulation_requirement=simulation_requirement
                )
                
                # 진행률 콜백
                def progress_callback(stage, progress, message):
                    task_manager.update_task(
                        task_id,
                        progress=progress,
                        message=f"[{stage}] {message}"
                    )
                
                # 보고서 생성 (미리 생성된 report_id를 전달)
                report = agent.generate_report(
                    progress_callback=progress_callback,
                    report_id=report_id
                )
                
                # 저장보고서
                ReportManager.save_report(report)
                
                if report.status == ReportStatus.COMPLETED:
                    task_manager.complete_task(
                        task_id,
                        result={
                            "report_id": report.report_id,
                            "simulation_id": simulation_id,
                            "status": "completed"
                        }
                    )
                else:
                    task_manager.fail_task(task_id, report.error or "보고서 생성실패")
                
            except Exception as e:
                logger.error(f"보고서 생성실패: {str(e)}")
                task_manager.fail_task(task_id, str(e))
        
        # 백그라운드 스레드 시작
        thread = threading.Thread(target=run_generate, daemon=True)
        thread.start()
        
        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "report_id": report_id,
                "task_id": task_id,
                "status": "generating",
                "message": "보고서 생성 작업이 시작되었습니다, 진행률을 조회하려면 /api/report/generate/status를 통하세요",
                "already_generated": False
            }
        })
        
    except Exception as e:
        logger.error(f"시작보고서 생성작업실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/generate/status', methods=['POST'])
def get_generate_status():
    """
    조회보고서 생성작업진행률
    
    요청（JSON）：
        {
            "task_id": "task_xxxx",         // 선택，generate돌아가기의task_id
            "simulation_id": "sim_xxxx"     // 선택，시뮬레이션ID
        }
    
    돌아가기：
        {
            "success": true,
            "data": {
                "task_id": "task_xxxx",
                "status": "processing|completed|failed",
                "progress": 45,
                "message": "..."
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        task_id = data.get('task_id')
        simulation_id = data.get('simulation_id')
        
        # 만약 시뮬레이션 ID를 제공하면, 먼저 이미 완료된 보고서가 있는지 확인
        if simulation_id:
            existing_report = ReportManager.get_report_by_simulation(simulation_id)
            if existing_report and existing_report.status == ReportStatus.COMPLETED:
                return jsonify({
                    "success": True,
                    "data": {
                        "simulation_id": simulation_id,
                        "report_id": existing_report.report_id,
                        "status": "completed",
                        "progress": 100,
                        "message": "보고서이미생성",
                        "already_completed": True
                    }
                })
        
        if not task_id:
            return jsonify({
                "success": False,
                "error": "task_id 또는 simulation_id를 제공해 주세요"
            }), 400
        
        task_manager = TaskManager()
        task = task_manager.get_task(task_id)
        
        if not task:
            return jsonify({
                "success": False,
                "error": f"작업존재하지 않음: {task_id}"
            }), 404
        
        return jsonify({
            "success": True,
            "data": task.to_dict()
        })
        
    except Exception as e:
        logger.error(f"조회작업상태실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# ============== 보고서가져오기인터페이스 ==============

@report_bp.route('/<report_id>', methods=['GET'])
def get_report(report_id: str):
    """
    가져오기보고서상세
    
    돌아가기：
        {
            "success": true,
            "data": {
                "report_id": "report_xxxx",
                "simulation_id": "sim_xxxx",
                "status": "completed",
                "outline": {...},
                "markdown_content": "...",
                "created_at": "...",
                "completed_at": "..."
            }
        }
    """
    try:
        report = ReportManager.get_report(report_id)
        
        if not report:
            return jsonify({
                "success": False,
                "error": f"보고서존재하지 않음: {report_id}"
            }), 404
        
        return jsonify({
            "success": True,
            "data": report.to_dict()
        })
        
    except Exception as e:
        logger.error(f"가져오기보고서실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/by-simulation/<simulation_id>', methods=['GET'])
def get_report_by_simulation(simulation_id: str):
    """
    에 따라시뮬레이션ID가져오기보고서
    
    돌아가기：
        {
            "success": true,
            "data": {
                "report_id": "report_xxxx",
                ...
            }
        }
    """
    try:
        report = ReportManager.get_report_by_simulation(simulation_id)
        
        if not report:
            return jsonify({
                "success": False,
                "error": f"해당 시뮬레이션에 보고서가 없습니다: {simulation_id}",
                "has_report": False
            }), 404
        
        return jsonify({
            "success": True,
            "data": report.to_dict(),
            "has_report": True
        })
        
    except Exception as e:
        logger.error(f"가져오기보고서실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/list', methods=['GET'])
def list_reports():
    """
    보고서 목록 나열
    
    Query파라미터：
        simulation_id: 시뮬레이션 ID로 필터링 (선택)
        limit: 가져오기 수량 제한 (기본값 50)
    
    돌아가기：
        {
            "success": true,
            "data": [...],
            "count": 10
        }
    """
    try:
        simulation_id = request.args.get('simulation_id')
        limit = request.args.get('limit', 50, type=int)
        
        reports = ReportManager.list_reports(
            simulation_id=simulation_id,
            limit=limit
        )
        
        return jsonify({
            "success": True,
            "data": [r.to_dict() for r in reports],
            "count": len(reports)
        })
        
    except Exception as e:
        logger.error(f"보고서 목록 나열 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/<report_id>/download', methods=['GET'])
def download_report(report_id: str):
    """
    다운로드보고서（Markdown형식）
    
    돌아가기Markdown파일
    """
    try:
        report = ReportManager.get_report(report_id)
        
        if not report:
            return jsonify({
                "success": False,
                "error": f"보고서존재하지 않음: {report_id}"
            }), 404
        
        md_path = ReportManager._get_report_markdown_path(report_id)
        
        if not os.path.exists(md_path):
            # 만약 MD 파일이 존재하지 않으면, 임시 파일을 생성
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
                f.write(report.markdown_content)
                temp_path = f.name
            
            return send_file(
                temp_path,
                as_attachment=True,
                download_name=f"{report_id}.md"
            )
        
        return send_file(
            md_path,
            as_attachment=True,
            download_name=f"{report_id}.md"
        )
        
    except Exception as e:
        logger.error(f"다운로드보고서실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/<report_id>', methods=['DELETE'])
def delete_report(report_id: str):
    """삭제보고서"""
    try:
        success = ReportManager.delete_report(report_id)
        
        if not success:
            return jsonify({
                "success": False,
                "error": f"보고서존재하지 않음: {report_id}"
            }), 404
        
        return jsonify({
            "success": True,
            "message": f"보고서이미삭제: {report_id}"
        })
        
    except Exception as e:
        logger.error(f"삭제보고서실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Report Agent대화인터페이스 ==============

@report_bp.route('/chat', methods=['POST'])
def chat_with_report_agent():
    """
    와Report Agent대화
    
    Report Agent가 대화 중 메인 호출 검색 도구로 질문에 답변
    
    요청（JSON）：
        {
            "simulation_id": "sim_xxxx",        // 필수，시뮬레이션ID
            "message": "여론의 흐름에 대해 설명해 주세요",    // 필수, 사용자 메시지
            "chat_history": [                   // 선택, 대화 역사
                {"role": "user", "content": "..."},
                {"role": "assistant", "content": "..."}
            ]
        }
    
    돌아가기：
        {
            "success": true,
            "data": {
                "response": "Agent의 응답...",
                "tool_calls": [호출의도구목록],
                "sources": [정보원]
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        message = data.get('message')
        chat_history = data.get('chat_history', [])
        
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "시뮬레이션 ID를 제공해 주세요"
            }), 400
        
        if not message:
            return jsonify({
                "success": False,
                "error": "메시지를 제공해 주세요"
            }), 400
        
        # 가져오기시뮬레이션와프로젝트 정보
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        
        if not state:
            return jsonify({
                "success": False,
                "error": f"시뮬레이션존재하지 않음: {simulation_id}"
            }), 404
        
        project = ProjectManager.get_project(state.project_id)
        if not project:
            return jsonify({
                "success": False,
                "error": f"프로젝트존재하지 않음: {state.project_id}"
            }), 404
        
        graph_id = state.graph_id or project.graph_id
        if not graph_id:
            return jsonify({
                "success": False,
                "error": "그래프 ID가 부족합니다"
            }), 400
        
        simulation_requirement = project.simulation_requirement or ""
        
        # Agent 생성 및 대화 진행
        agent = ReportAgent(
            graph_id=graph_id,
            simulation_id=simulation_id,
            simulation_requirement=simulation_requirement
        )
        
        result = agent.chat(message=message, chat_history=chat_history)
        
        return jsonify({
            "success": True,
            "data": result
        })
        
    except Exception as e:
        logger.error(f"대화실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 보고서 진행률과 분 장 인터페이스 ==============

@report_bp.route('/<report_id>/progress', methods=['GET'])
def get_report_progress(report_id: str):
    """
    가져오기보고서 생성진행률（실시간）
    
    돌아가기：
        {
            "success": true,
            "data": {
                "status": "generating",
                "progress": 45,
                "message": "진행 중: 생성 장: 주요 발견",
                "current_section": "주요 발견",
                "completed_sections": ["실행요약", "시뮬레이션배경"],
                "updated_at": "2025-12-09T..."
            }
        }
    """
    try:
        progress = ReportManager.get_progress(report_id)
        
        if not progress:
            return jsonify({
                "success": False,
                "error": f"보고서가 존재하지 않거나 진행률 정보가 유효하지 않습니다: {report_id}"
            }), 404
        
        return jsonify({
            "success": True,
            "data": progress
        })
        
    except Exception as e:
        logger.error(f"가져오기보고서진행률실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/<report_id>/sections', methods=['GET'])
def get_report_sections(report_id: str):
    """
    이미 생성된 장 목록 가져오기 (분 장 출력)
    
    프론트엔드에서 이 인터페이스를 통해 이미 생성된 장 내용을 가져오며, 전체 보고서 완료를 기다릴 필요 없음
    
    돌아가기：
        {
            "success": true,
            "data": {
                "report_id": "report_xxxx",
                "sections": [
                    {
                        "filename": "section_01.md",
                        "section_index": 1,
                        "content": "## 실행요약\\n\\n..."
                    },
                    ...
                ],
                "total_sections": 3,
                "is_complete": false
            }
        }
    """
    try:
        sections = ReportManager.get_generated_sections(report_id)
        
        # 가져오기보고서상태
        report = ReportManager.get_report(report_id)
        is_complete = report is not None and report.status == ReportStatus.COMPLETED
        
        return jsonify({
            "success": True,
            "data": {
                "report_id": report_id,
                "sections": sections,
                "total_sections": len(sections),
                "is_complete": is_complete
            }
        })
        
    except Exception as e:
        logger.error(f"장 목록 가져오기 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/<report_id>/section/<int:section_index>', methods=['GET'])
def get_single_section(report_id: str, section_index: int):
    """
    단일 장 내용 가져오기
    
    돌아가기：
        {
            "success": true,
            "data": {
                "filename": "section_01.md",
                "content": "## 실행요약\\n\\n..."
            }
        }
    """
    try:
        section_path = ReportManager._get_section_path(report_id, section_index)
        
        if not os.path.exists(section_path):
            return jsonify({
                "success": False,
                "error": f"장 존재하지 않음: section_{section_index:02d}.md"
            }), 404
        
        with open(section_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        return jsonify({
            "success": True,
            "data": {
                "filename": f"section_{section_index:02d}.md",
                "section_index": section_index,
                "content": content
            }
        })
        
    except Exception as e:
        logger.error(f"장 내용 가져오기 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 보고서 상태 체크 인터페이스 ==============

@report_bp.route('/check/<simulation_id>', methods=['GET'])
def check_report_status(simulation_id: str):
    """
    체크 시뮬레이션 여부 보고서，하여 및 보고서 상태
    
    용 프론트엔드 판단 여부 해제 Interview 기능
    
    돌아가기：
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "has_report": true,
                "report_status": "completed",
                "report_id": "report_xxxx",
                "interview_unlocked": true
            }
        }
    """
    try:
        report = ReportManager.get_report_by_simulation(simulation_id)
        
        has_report = report is not None
        report_status = report.status.value if report else None
        report_id = report.report_id if report else None
        
        # 보고서 완료 후에만 해제 interview  
        interview_unlocked = has_report and report.status == ReportStatus.COMPLETED
        
        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "has_report": has_report,
                "report_status": report_status,
                "report_id": report_id,
                "interview_unlocked": interview_unlocked
            }
        })
        
    except Exception as e:
        logger.error(f"체크 보고서 상태 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Agent 로그인터페이스 ==============

@report_bp.route('/<report_id>/agent-log', methods=['GET'])
def get_agent_log(report_id: str):
    """
    가져오기 Report Agent 의 상세 실행 로그
    
    실시간 가져오기 보고서 생성 과정의 매 단계 액션, 포함：
    - 보고서 시작、계획 시작/완료
    - 매 개 챕터의 시작、도구 호출、LLM 응답、완료
    - 보고서완료또는실패
    
    Query파라미터：
        from_line: 몇 번째 줄부터 시작 읽기（선택，기본값 0，용 증분 가져오기）
    
    돌아가기：
        {
            "success": true,
            "data": {
                "logs": [
                    {
                        "timestamp": "2025-12-13T...",
                        "elapsed_seconds": 12.5,
                        "report_id": "report_xxxx",
                        "action": "tool_call",
                        "stage": "generating",
                        "section_title": "실행요약",
                        "section_index": 1,
                        "details": {
                            "tool_name": "insight_forge",
                            "parameters": {...},
                            ...
                        }
                    },
                    ...
                ],
                "total_lines": 25,
                "from_line": 0,
                "has_more": false
            }
        }
    """
    try:
        from_line = request.args.get('from_line', 0, type=int)
        
        log_data = ReportManager.get_agent_log(report_id, from_line=from_line)
        
        return jsonify({
            "success": True,
            "data": log_data
        })
        
    except Exception as e:
        logger.error(f"가져오기Agent로그실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/<report_id>/agent-log/stream', methods=['GET'])
def stream_agent_log(report_id: str):
    """
    가져오기 전체의 Agent 로그（한 번에 가져오기 전부）
    
    돌아가기：
        {
            "success": true,
            "data": {
                "logs": [...],
                "count": 25
            }
        }
    """
    try:
        logs = ReportManager.get_agent_log_stream(report_id)
        
        return jsonify({
            "success": True,
            "data": {
                "logs": logs,
                "count": len(logs)
            }
        })
        
    except Exception as e:
        logger.error(f"가져오기Agent로그실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 콘솔로그인터페이스 ==============

@report_bp.route('/<report_id>/console-log', methods=['GET'])
def get_console_log(report_id: str):
    """
    가져오기 Report Agent 의콘솔출력로그
    
    실시간 가져오기 보고서 생성 과정의 콘솔 출력（INFO、WARNING 등），
    이것과 agent-log 인터페이스 돌아가기의 구조화 JSON 로그와 동일，
    순수 텍스트 형식의 콘솔 스타일 로그。
    
    Query파라미터：
        from_line: 몇 번째 줄부터 시작 읽기（선택，기본값 0，용 증분 가져오기）
    
    돌아가기：
        {
            "success": true,
            "data": {
                "logs": [
                    "[19:46:14] INFO: 검색 완료: 찾음 15 개 관련 사실",
                    "[19:46:14] INFO: 그래프검색: graph_id=xxx, query=...",
                    ...
                ],
                "total_lines": 100,
                "from_line": 0,
                "has_more": false
            }
        }
    """
    try:
        from_line = request.args.get('from_line', 0, type=int)
        
        log_data = ReportManager.get_console_log(report_id, from_line=from_line)
        
        return jsonify({
            "success": True,
            "data": log_data
        })
        
    except Exception as e:
        logger.error(f"가져오기콘솔로그실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/<report_id>/console-log/stream', methods=['GET'])
def stream_console_log(report_id: str):
    """
    가져오기 전체의 콘솔 로그（한 번에 가져오기 전부）
    
    돌아가기：
        {
            "success": true,
            "data": {
                "logs": [...],
                "count": 100
            }
        }
    """
    try:
        logs = ReportManager.get_console_log_stream(report_id)
        
        return jsonify({
            "success": True,
            "data": {
                "logs": logs,
                "count": len(logs)
            }
        })
        
    except Exception as e:
        logger.error(f"가져오기콘솔로그실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 도구호출인터페이스（제공디버그사용）==============

@report_bp.route('/tools/search', methods=['POST'])
def search_graph_tool():
    """
    그래프검색도구인터페이스（제공디버그사용）
    
    요청（JSON）：
        {
            "graph_id": "mirofish_xxxx",
            "query": "검색조회",
            "limit": 10
        }
    """
    try:
        data = request.get_json() or {}
        
        graph_id = data.get('graph_id')
        query = data.get('query')
        limit = data.get('limit', 10)
        
        if not graph_id or not query:
            return jsonify({
                "success": False,
                "error": "제공해 주세요 graph_id 와 query"
            }), 400
        
        from ..services.zep_tools import ZepToolsService
        
        tools = ZepToolsService()
        result = tools.search_graph(
            graph_id=graph_id,
            query=query,
            limit=limit
        )
        
        return jsonify({
            "success": True,
            "data": result.to_dict()
        })
        
    except Exception as e:
        logger.error(f"그래프검색실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/tools/statistics', methods=['POST'])
def get_graph_statistics_tool():
    """
    그래프통계도구인터페이스（제공디버그사용）
    
    요청（JSON）：
        {
            "graph_id": "mirofish_xxxx"
        }
    """
    try:
        data = request.get_json() or {}
        
        graph_id = data.get('graph_id')
        
        if not graph_id:
            return jsonify({
                "success": False,
                "error": "제공해 주세요 graph_id"
            }), 400
        
        from ..services.zep_tools import ZepToolsService
        
        tools = ZepToolsService()
        result = tools.get_graph_statistics(graph_id)
        
        return jsonify({
            "success": True,
            "data": result
        })
        
    except Exception as e:
        logger.error(f"가져오기그래프통계실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500
