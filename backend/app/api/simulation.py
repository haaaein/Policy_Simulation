"""
시뮬레이션관련API라우팅
Step2: Zep 개체 읽기와 필터, OASIS 시뮬레이션 준비와 실행（전 과정 자동화）
"""

import os
import traceback
from flask import request, jsonify, send_file

from . import simulation_bp
from ..config import Config
from ..services.zep_entity_reader import ZepEntityReader
from ..services.oasis_profile_generator import OasisProfileGenerator
from ..services.simulation_manager import SimulationManager, SimulationStatus
from ..services.simulation_runner import SimulationRunner, RunnerStatus
from ..utils.logger import get_logger
from ..models.project import ProjectManager

logger = get_logger('mirofish.api.simulation')


# Interview prompt 최적화 전 접두사
# 추가하여 Agent 호출 도구를 피하고, 직접 텍스트로 응답
INTERVIEW_PROMPT_PREFIX = "당신의 페르소나, 소의 기억과 행동을 결합하여, 어떤 도구도 호출하지 않고 직접 텍스트로 나에게 응답하세요："


def optimize_interview_prompt(prompt: str) -> str:
    """
    Interview 질문 최적화, 추가 접두사로 Agent 호출 도구를 피함
    
    Args:
        prompt: 원본 질문
        
    Returns:
        최적화 후의 질문
    """
    if not prompt:
        return prompt
    # 반복 추가 접두사 피하기
    if prompt.startswith(INTERVIEW_PROMPT_PREFIX):
        return prompt
    return f"{INTERVIEW_PROMPT_PREFIX}{prompt}"


# ============== 개체 읽기 인터페이스 ==============

@simulation_bp.route('/entities/<graph_id>', methods=['GET'])
def get_graph_entities(graph_id: str):
    """
    가져오기 그래프의 소 개체（이미 필터됨）
    
    조건에 맞는 미리 정의된 개체 유형의 노드만 반환（Labels 안의 Entity 노드만）
    
    Query파라미터：
        entity_types: 쉼표로 구분된 개체 유형 목록（선택, 추가 필터링에 사용）
        enrich: 관련 엣지 정보를 가져올지 여부（기본값 true）
    """
    try:
        if not Config.ZEP_API_KEY:
            return jsonify({
                "success": False,
                "error": "ZEP_API_KEY미설정"
            }), 500
        
        entity_types_str = request.args.get('entity_types', '')
        entity_types = [t.strip() for t in entity_types_str.split(',') if t.strip()] if entity_types_str else None
        enrich = request.args.get('enrich', 'true').lower() == 'true'
        
        logger.info(f"가져오기그래프개체: graph_id={graph_id}, entity_types={entity_types}, enrich={enrich}")
        
        reader = ZepEntityReader()
        result = reader.filter_defined_entities(
            graph_id=graph_id,
            defined_entity_types=entity_types,
            enrich_with_edges=enrich
        )
        
        return jsonify({
            "success": True,
            "data": result.to_dict()
        })
        
    except Exception as e:
        logger.error(f"가져오기그래프개체실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/entities/<graph_id>/<entity_uuid>', methods=['GET'])
def get_entity_detail(graph_id: str, entity_uuid: str):
    """단일 개체의 상세 정보를 가져오기"""
    try:
        if not Config.ZEP_API_KEY:
            return jsonify({
                "success": False,
                "error": "ZEP_API_KEY미설정"
            }), 500
        
        reader = ZepEntityReader()
        entity = reader.get_entity_with_context(graph_id, entity_uuid)
        
        if not entity:
            return jsonify({
                "success": False,
                "error": f"개체존재하지 않음: {entity_uuid}"
            }), 404
        
        return jsonify({
            "success": True,
            "data": entity.to_dict()
        })
        
    except Exception as e:
        logger.error(f"가져오기개체상세실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/entities/<graph_id>/by-type/<entity_type>', methods=['GET'])
def get_entities_by_type(graph_id: str, entity_type: str):
    """지정된 유형의 소 개체를 가져오기"""
    try:
        if not Config.ZEP_API_KEY:
            return jsonify({
                "success": False,
                "error": "ZEP_API_KEY미설정"
            }), 500
        
        enrich = request.args.get('enrich', 'true').lower() == 'true'
        
        reader = ZepEntityReader()
        entities = reader.get_entities_by_type(
            graph_id=graph_id,
            entity_type=entity_type,
            enrich_with_edges=enrich
        )
        
        return jsonify({
            "success": True,
            "data": {
                "entity_type": entity_type,
                "count": len(entities),
                "entities": [e.to_dict() for e in entities]
            }
        })
        
    except Exception as e:
        logger.error(f"가져오기개체실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 시뮬레이션관리인터페이스 ==============

@simulation_bp.route('/create', methods=['POST'])
def create_simulation():
    """
    새로운 시뮬레이션 생성
    
    주의: max_rounds 등의 파라미터는 LLM 지능형 생성에 의해 자동 설정되며, 수동으로 설정할 필요 없음
    
    요청（JSON）：
        {
            "project_id": "proj_xxxx",      // 필수
            "graph_id": "mirofish_xxxx",    // 선택 사항, 제공되지 않으면 프로젝트에서 가져오기
            "enable_twitter": true,          // 선택, 기본값true
            "enable_reddit": true            // 선택, 기본값true
        }
    
    돌아가기：
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "project_id": "proj_xxxx",
                "graph_id": "mirofish_xxxx",
                "status": "created",
                "enable_twitter": true,
                "enable_reddit": true,
                "created_at": "2025-12-01T10:00:00"
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        project_id = data.get('project_id')
        if not project_id:
            return jsonify({
                "success": False,
                "error": "프로젝트 ID를 제공해 주세요"
            }), 400
        
        project = ProjectManager.get_project(project_id)
        if not project:
            return jsonify({
                "success": False,
                "error": f"프로젝트존재하지 않음: {project_id}"
            }), 404
        
        graph_id = data.get('graph_id') or project.graph_id
        if not graph_id:
            return jsonify({
                "success": False,
                "error": "프로젝트가 아직 그래프를 구축하지 않았습니다, 먼저 /api/graph/build를 호출하세요"
            }), 400
        
        simulation_mode = data.get('simulation_mode', 'social_media')

        manager = SimulationManager()
        state = manager.create_simulation(
            project_id=project_id,
            graph_id=graph_id,
            enable_twitter=data.get('enable_twitter', True),
            enable_reddit=data.get('enable_reddit', True),
            simulation_mode=simulation_mode,
        )
        
        return jsonify({
            "success": True,
            "data": state.to_dict()
        })
        
    except Exception as e:
        logger.error(f"생성시뮬레이션실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


def _check_simulation_prepared(simulation_id: str) -> tuple:
    """
    시뮬레이션이 이미 준비 완료인지 확인
    
    확인할 항목:
    1. state.json이 저장되어 있고 상태가 "ready"
    2. 필수 파일이 저장되어 있음: reddit_profiles.json, twitter_profiles.csv, simulation_config.json
    
    주의: 실행 스크립트(run_*.py)는 backend/scripts/ 디렉토리에 보관하고, 시뮬레이션 디렉토리에 복사하지 마세요
    
    Args:
        simulation_id: 시뮬레이션ID
        
    Returns:
        (is_prepared: bool, info: dict)
    """
    import os
    from ..config import Config
    
    simulation_dir = os.path.join(Config.OASIS_SIMULATION_DATA_DIR, simulation_id)
    
    # 디렉토리에 파일이 저장되어 있는지 확인
    if not os.path.exists(simulation_dir):
        return False, {"reason": "시뮬레이션디렉토리존재하지 않음"}
    
    # 필수 파일 목록（스크립트는 포함하지 않음, 스크립트는 backend/scripts/에 위치）
    required_files = [
        "state.json",
        "simulation_config.json",
        "reddit_profiles.json",
        "twitter_profiles.csv"
    ]
    
    # 파일이 저장되어 있는지 확인
    existing_files = []
    missing_files = []
    for f in required_files:
        file_path = os.path.join(simulation_dir, f)
        if os.path.exists(file_path):
            existing_files.append(f)
        else:
            missing_files.append(f)
    
    if missing_files:
        return False, {
            "reason": "필수 파일이 누락됨",
            "missing_files": missing_files,
            "existing_files": existing_files
        }
    
    # state.json의 상태 확인
    state_file = os.path.join(simulation_dir, "state.json")
    try:
        import json
        with open(state_file, 'r', encoding='utf-8') as f:
            state_data = json.load(f)
        
        status = state_data.get("status", "")
        config_generated = state_data.get("config_generated", False)
        
        # 상세 로그
        logger.debug(f"감지시뮬레이션준비상태: {simulation_id}, status={status}, config_generated={config_generated}")
        
        # 만약 config_generated=True 이고 파일이 저장되어 있다면, 준비 완료로 인식
        # 따라서 아래 상태는 모두 준비 작업 완료를 의미:
        # - ready: 준비 완료, 하여실행
        # - preparing: 만약 config_generated=True 이면 완료
        # - running: 진행 중: 실행 중, 준비가 이미 완료됨을 의미
        # - completed: 실행 완료, 준비가 이미 완료됨을 의미
        # - stopped: 이미 중지됨, 준비가 이미 완료됨을 의미
        # - failed: 실행 실패（하지만 준비는 완료됨）
        prepared_statuses = ["ready", "preparing", "running", "completed", "stopped", "failed"]
        if status in prepared_statuses and config_generated:
            # 가져오기파일통계정보
            profiles_file = os.path.join(simulation_dir, "reddit_profiles.json")
            config_file = os.path.join(simulation_dir, "simulation_config.json")
            
            profiles_count = 0
            if os.path.exists(profiles_file):
                with open(profiles_file, 'r', encoding='utf-8') as f:
                    profiles_data = json.load(f)
                    profiles_count = len(profiles_data) if isinstance(profiles_data, list) else 0
            
            # 만약 상태가 preparing 이지만 파일이 완료되었다면, 자동으로 상태를 ready로 업데이트
            if status == "preparing":
                try:
                    state_data["status"] = "ready"
                    from datetime import datetime
                    state_data["updated_at"] = datetime.now().isoformat()
                    with open(state_file, 'w', encoding='utf-8') as f:
                        json.dump(state_data, f, ensure_ascii=False, indent=2)
                    logger.info(f"자동업데이트시뮬레이션상태: {simulation_id} preparing -> ready")
                    status = "ready"
                except Exception as e:
                    logger.warning(f"자동업데이트상태실패: {e}")
            
            logger.info(f"시뮬레이션 {simulation_id} 감지결과: 이미준비 완료 (status={status}, config_generated={config_generated})")
            return True, {
                "status": status,
                "entities_count": state_data.get("entities_count", 0),
                "profiles_count": profiles_count,
                "entity_types": state_data.get("entity_types", []),
                "config_generated": config_generated,
                "created_at": state_data.get("created_at"),
                "updated_at": state_data.get("updated_at"),
                "existing_files": existing_files
            }
        else:
            logger.warning(f"시뮬레이션 {simulation_id} 감지 결과: 준비 완료되지 않음 (status={status}, config_generated={config_generated})")
            return False, {
                "reason": f"상태안에서이미준비목록중또는config_generated로false: status={status}, config_generated={config_generated}",
                "status": status,
                "config_generated": config_generated
            }
            
    except Exception as e:
        return False, {"reason": f"상태 파일 읽기 실패: {str(e)}"}


@simulation_bp.route('/prepare', methods=['POST'])
def prepare_simulation():
    """
    준비 시뮬레이션 환경(비동기 작업, LLM 지능형 생성 소 파라미터)
    
    이 작업은 시간이 소요되며, 인터페이스는 즉시 task_id로 돌아갑니다.
    사용 GET /api/simulation/prepare/status 조회진행률
    
    특성：
    - 자동 감지 완료의 준비 작업, 반복 생성을 피합니다.
    - 만약 이미 준비 완료라면, 직접 돌아가서 이미 결과를 반환합니다.
    - 강제로 재생성을 지원합니다 (force_regenerate=true).
    
    단계:
    1. 이미 완료된 준비 작업인지 확인합니다.
    2. 부터Zep그래프읽고필터개체
    3. 각 개체에 대해 OASIS Agent Profile을 생성합니다 (재시도 메커니즘 포함).
    4. LLM지능형생성시뮬레이션설정（포함재시도메커니즘）
    5. 저장설정파일와프리셋 스크립트
    
    요청（JSON）：
        {
            "simulation_id": "sim_xxxx",                   // 필수, 시뮬레이션ID
            "entity_types": ["Student", "PublicFigure"],  // 선택, 지정 개체 유형
            "use_llm_for_profiles": true,                 // 선택, LLM을 사용하여 페르소나 생성
            "parallel_profile_count": 5,                  // 선택, 병렬생성페르소나수량, 기본값5
            "force_regenerate": false                     // 선택, 강제로 재생성, 기본값 false
        }
    
    돌아가기：
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "task_id": "task_xxxx",           // 새 작업 시 돌아가기
                "status": "preparing|ready",
                "message": "준비 작업이 시작되었습니다|이미 완료된 준비 작업",
                "already_prepared": true|false    // 이미 준비 완료인지 여부
            }
        }
    """
    import threading
    import os
    from ..models.task import TaskManager, TaskStatus
    from ..config import Config
    
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "시뮬레이션 ID를 제공해 주세요"
            }), 400
        
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        
        if not state:
            return jsonify({
                "success": False,
                "error": f"시뮬레이션존재하지 않음: {simulation_id}"
            }), 404
        
        # 강제로 재생성 여부 확인
        force_regenerate = data.get('force_regenerate', False)
        logger.info(f"시작처리 /prepare 요청: simulation_id={simulation_id}, force_regenerate={force_regenerate}")
        
        # 이미 준비 완료인지 확인 (반복 생성을 피하기 위해)
        if not force_regenerate:
            logger.debug(f"시뮬레이션 {simulation_id} 이미 준비 완료인지 확인 중...")
            is_prepared, prepare_info = _check_simulation_prepared(simulation_id)
            logger.debug(f"확인 결과: is_prepared={is_prepared}, prepare_info={prepare_info}")
            if is_prepared:
                logger.info(f"시뮬레이션 {simulation_id} 이미 준비 완료, 반복 생성을 건너뜁니다.")
                return jsonify({
                    "success": True,
                    "data": {
                        "simulation_id": simulation_id,
                        "status": "ready",
                        "message": "이미 완료된 준비 작업, 반복 생성을 할 필요가 없습니다.",
                        "already_prepared": True,
                        "prepare_info": prepare_info
                    }
                })
            else:
                logger.info(f"시뮬레이션 {simulation_id} 준비 완료되지 않음, 준비 작업 시작합니다.")
        
        # 프로젝트에서 가져오기 필수 정보
        project = ProjectManager.get_project(state.project_id)
        if not project:
            return jsonify({
                "success": False,
                "error": f"프로젝트존재하지 않음: {state.project_id}"
            }), 404
        
        # 가져오기시뮬레이션 요구사항
        simulation_requirement = project.simulation_requirement or ""
        if not simulation_requirement:
            return jsonify({
                "success": False,
                "error": "프로젝트에 시뮬레이션 요구 사항 설명이 부족합니다 (simulation_requirement)"
            }), 400
        
        # 가져오기문서텍스트
        document_text = ProjectManager.get_extracted_text(state.project_id) or ""
        
        entity_types_list = data.get('entity_types')
        use_llm_for_profiles = data.get('use_llm_for_profiles', True)
        parallel_profile_count = data.get('parallel_profile_count', 5)
        
        # ========== 동기 가져오기 개체 수량 (백그라운드에서 작업 시작 전) ==========
        # 이렇게 하면 프론트엔드에서 호출 시 prepare 즉시 예상 Agent 총 수를 가져옵니다.
        try:
            logger.info(f"동기 가져오기 개체 수량: graph_id={state.graph_id}")
            reader = ZepEntityReader()
            # 빠르게 개체를 읽기 (엣지 정보는 필요 없으며, 수량만 통계)
            filtered_preview = reader.filter_defined_entities(
                graph_id=state.graph_id,
                defined_entity_types=entity_types_list,
                enrich_with_edges=False  # 엣지 정보를 가져오지 않음, 속도 향상
            )
            # 개체 수량 상태 저장 (프론트엔드에서 즉시 가져오기 위해)
            state.entities_count = filtered_preview.filtered_count
            state.entity_types = list(filtered_preview.entity_types)
            logger.info(f"예상 개체 수량: {filtered_preview.filtered_count}, 유형: {filtered_preview.entity_types}")
        except Exception as e:
            logger.warning(f"동기 가져오기 개체 수량 실패 (백그라운드에서 작업 중 재시도): {e}")
            # 실패는 후속 프로세스에 영향을 미치지 않으며, 백그라운드 작업을 다시 가져옵니다.
        
        # 생성비동기작업
        task_manager = TaskManager()
        task_id = task_manager.create_task(
            task_type="simulation_prepare",
            metadata={
                "simulation_id": simulation_id,
                "project_id": state.project_id
            }
        )
        
        # 시뮬레이션 상태 업데이트 (예상 가져오기 개체 수량 포함)
        state.status = SimulationStatus.PREPARING
        manager._save_simulation_state(state)
        
        # 백그라운드 작업 정의
        def run_prepare():
            try:
                task_manager.update_task(
                    task_id,
                    status=TaskStatus.PROCESSING,
                    progress=0,
                    message="시작준비시뮬레이션환경..."
                )
                
                # 준비 시뮬레이션 (진행률 콜백 포함)
                # 단계 진행률 세부 정보 저장
                stage_details = {}
                
                def progress_callback(stage, progress, message, **kwargs):
                    # 총 진행률 계산
                    stage_weights = {
                        "reading": (0, 20),           # 0-20%
                        "generating_profiles": (20, 70),  # 20-70%
                        "generating_config": (70, 90),    # 70-90%
                        "copying_scripts": (90, 100)       # 90-100%
                    }
                    
                    start, end = stage_weights.get(stage, (0, 100))
                    current_progress = int(start + (end - start) * progress / 100)
                    
                    # 상세 진행률 정보 구축
                    stage_names = {
                        "reading": "그래프 개체 읽기",
                        "generating_profiles": "생성Agent페르소나",
                        "generating_config": "생성시뮬레이션설정",
                        "copying_scripts": "시뮬레이션 스크립트 준비"
                    }
                    
                    stage_index = list(stage_weights.keys()).index(stage) + 1 if stage in stage_weights else 1
                    total_stages = len(stage_weights)
                    
                    # 업데이트 단계 상세
                    stage_details[stage] = {
                        "stage_name": stage_names.get(stage, stage),
                        "stage_progress": progress,
                        "current": kwargs.get("current", 0),
                        "total": kwargs.get("total", 0),
                        "item_name": kwargs.get("item_name", "")
                    }
                    
                    # 구축 상세 진행률 정보
                    detail = stage_details[stage]
                    progress_detail_data = {
                        "current_stage": stage,
                        "current_stage_name": stage_names.get(stage, stage),
                        "stage_index": stage_index,
                        "total_stages": total_stages,
                        "stage_progress": progress,
                        "current_item": detail["current"],
                        "total_items": detail["total"],
                        "item_description": message
                    }
                    
                    # 구축 간결 메시지
                    if detail["total"] > 0:
                        detailed_message = (
                            f"[{stage_index}/{total_stages}] {stage_names.get(stage, stage)}: "
                            f"{detail['current']}/{detail['total']} - {message}"
                        )
                    else:
                        detailed_message = f"[{stage_index}/{total_stages}] {stage_names.get(stage, stage)}: {message}"
                    
                    task_manager.update_task(
                        task_id,
                        progress=current_progress,
                        message=detailed_message,
                        progress_detail=progress_detail_data
                    )
                
                result_state = manager.prepare_simulation(
                    simulation_id=simulation_id,
                    simulation_requirement=simulation_requirement,
                    document_text=document_text,
                    defined_entity_types=entity_types_list,
                    use_llm_for_profiles=use_llm_for_profiles,
                    progress_callback=progress_callback,
                    parallel_profile_count=parallel_profile_count
                )
                
                # 작업완료
                task_manager.complete_task(
                    task_id,
                    result=result_state.to_simple_dict()
                )
                
            except Exception as e:
                logger.error(f"준비시뮬레이션실패: {str(e)}")
                task_manager.fail_task(task_id, str(e))
                
                # 업데이트시뮬레이션상태로실패
                state = manager.get_simulation(simulation_id)
                if state:
                    state.status = SimulationStatus.FAILED
                    state.error = str(e)
                    manager._save_simulation_state(state)
        
        # 시작 후 대기 스레드
        thread = threading.Thread(target=run_prepare, daemon=True)
        thread.start()
        
        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "task_id": task_id,
                "status": "preparing",
                "message": "준비 작업이 시작되었습니다, /api/simulation/prepare/status 조회 진행률을 확인하세요",
                "already_prepared": False,
                "expected_entities_count": state.entities_count,  # 예상 Agent 총 수
                "entity_types": state.entity_types  # 개체 유형목록
            }
        })
        
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 404
        
    except Exception as e:
        logger.error(f"시작준비작업실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/prepare/status', methods=['POST'])
def get_prepare_status():
    """
    조회준비작업진행률
    
    두 가지 조회 방식 지원:
    1. task_id로 진행 중 조회: 작업 진행률 확인
    2. simulation_id로 준비 작업이 완료되었는지 확인
    
    요청（JSON）：
        {
            "task_id": "task_xxxx",          // 선택, prepare돌아가기의task_id
            "simulation_id": "sim_xxxx"      // 선택, 시뮬레이션 ID (완료된 준비 확인용)
        }
    
    돌아가기：
        {
            "success": true,
            "data": {
                "task_id": "task_xxxx",
                "status": "processing|completed|ready",
                "progress": 45,
                "message": "...",
                "already_prepared": true|false,  // 이미 완료된 준비 여부
                "prepare_info": {...}            // 이미 준비 완료 시의 상세 정보
            }
        }
    """
    from ..models.task import TaskManager
    
    try:
        data = request.get_json() or {}
        
        task_id = data.get('task_id')
        simulation_id = data.get('simulation_id')
        
        # 만약 simulation_id를 제공하면, 먼저 준비 완료 여부 확인
        if simulation_id:
            is_prepared, prepare_info = _check_simulation_prepared(simulation_id)
            if is_prepared:
                return jsonify({
                    "success": True,
                    "data": {
                        "simulation_id": simulation_id,
                        "status": "ready",
                        "progress": 100,
                        "message": "이미 완료된 준비 작업입니다",
                        "already_prepared": True,
                        "prepare_info": prepare_info
                    }
                })
        
        # 만약없으면task_id, 돌아가기오류
        if not task_id:
            if simulation_id:
                # simulation_id가 있지만 준비가 완료되지 않음
                return jsonify({
                    "success": True,
                    "data": {
                        "simulation_id": simulation_id,
                        "status": "not_started",
                        "progress": 0,
                        "message": "아직 준비가 시작되지 않았습니다, /api/simulation/prepare 호출하여 시작하세요",
                        "already_prepared": False
                    }
                })
            return jsonify({
                "success": False,
                "error": "task_id 또는 simulation_id를 제공해 주세요"
            }), 400
        
        task_manager = TaskManager()
        task = task_manager.get_task(task_id)
        
        if not task:
            # 작업이 존재하지 않지만, simulation_id가 있다면 준비 완료 여부 확인
            if simulation_id:
                is_prepared, prepare_info = _check_simulation_prepared(simulation_id)
                if is_prepared:
                    return jsonify({
                        "success": True,
                        "data": {
                            "simulation_id": simulation_id,
                            "task_id": task_id,
                            "status": "ready",
                            "progress": 100,
                            "message": "작업 완료 (준비 작업이 이미 존재함)",
                            "already_prepared": True,
                            "prepare_info": prepare_info
                        }
                    })
            
            return jsonify({
                "success": False,
                "error": f"작업존재하지 않음: {task_id}"
            }), 404
        
        task_dict = task.to_dict()
        task_dict["already_prepared"] = False
        
        return jsonify({
            "success": True,
            "data": task_dict
        })
        
    except Exception as e:
        logger.error(f"조회작업상태실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@simulation_bp.route('/<simulation_id>', methods=['GET'])
def get_simulation(simulation_id: str):
    """가져오기시뮬레이션상태"""
    try:
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        
        if not state:
            return jsonify({
                "success": False,
                "error": f"시뮬레이션존재하지 않음: {simulation_id}"
            }), 404
        
        result = state.to_dict()
        
        # 만약 시뮬레이션이 이미 준비되었다면, 실행 설명 추가
        if state.status == SimulationStatus.READY:
            result["run_instructions"] = manager.get_run_instructions(simulation_id)
        
        return jsonify({
            "success": True,
            "data": result
        })
        
    except Exception as e:
        logger.error(f"가져오기시뮬레이션상태실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/list', methods=['GET'])
def list_simulations():
    """
    시뮬레이션 목록 나열
    
    Query파라미터：
        project_id: 프로젝트 ID로 필터링 (선택)
    """
    try:
        project_id = request.args.get('project_id')
        
        manager = SimulationManager()
        simulations = manager.list_simulations(project_id=project_id)
        
        return jsonify({
            "success": True,
            "data": [s.to_dict() for s in simulations],
            "count": len(simulations)
        })
        
    except Exception as e:
        logger.error(f"시뮬레이션 나열 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


def _get_report_id_for_simulation(simulation_id: str) -> str:
    """
    최신 report_id를 가져오기 위해 simulation 응답
    
    reports 디렉토리를 탐색하여 simulation_id와 일치하는 report를 찾습니다,
    만약 여러 개라면 최신의 것을 반환합니다 (created_at 기준 정렬)
    
    Args:
        simulation_id: 시뮬레이션ID
        
    Returns:
        report_id 또는 None
    """
    import json
    from datetime import datetime
    
    # reports 디렉토리경로：backend/uploads/reports
    # __file__  app/api/simulation.py, 두 단계 위로 backend/
    reports_dir = os.path.join(os.path.dirname(__file__), '../../uploads/reports')
    if not os.path.exists(reports_dir):
        return None
    
    matching_reports = []
    
    try:
        for report_folder in os.listdir(reports_dir):
            report_path = os.path.join(reports_dir, report_folder)
            if not os.path.isdir(report_path):
                continue
            
            meta_file = os.path.join(report_path, "meta.json")
            if not os.path.exists(meta_file):
                continue
            
            try:
                with open(meta_file, 'r', encoding='utf-8') as f:
                    meta = json.load(f)
                
                if meta.get("simulation_id") == simulation_id:
                    matching_reports.append({
                        "report_id": meta.get("report_id"),
                        "created_at": meta.get("created_at", ""),
                        "status": meta.get("status", "")
                    })
            except Exception:
                continue
        
        if not matching_reports:
            return None
        
        # 생성 시간 기준 내림차순 정렬, 최신의 것을 반환
        matching_reports.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return matching_reports[0].get("report_id")
        
    except Exception as e:
        logger.warning(f"simulation {simulation_id}의 report 찾기 실패: {e}")
        return None


@simulation_bp.route('/history', methods=['GET'])
def get_simulation_history():
    """
    역사 시뮬레이션 목록 가져오기 (프로젝트 상세 포함)
    
    홈페이지 역사 프로젝트 표시를 위해, 프로젝트 이름, 설명 등 풍부한 정보의 시뮬레이션 목록 반환
    
    Query파라미터：
        limit: 반환할 수량 제한 (기본값 20)
    
    돌아가기：
        {
            "success": true,
            "data": [
                {
                    "simulation_id": "sim_xxxx",
                    "project_id": "proj_xxxx",
                    "project_name": "무한대 여론 분석",
                    "simulation_requirement": "만약 우한대학교가 발표한다면...",
                    "status": "completed",
                    "entities_count": 68,
                    "profiles_count": 68,
                    "entity_types": ["Student", "Professor", ...],
                    "created_at": "2024-12-10",
                    "updated_at": "2024-12-10",
                    "total_rounds": 120,
                    "current_round": 120,
                    "report_id": "report_xxxx",
                    "version": "v1.0.2"
                },
                ...
            ],
            "count": 7
        }
    """
    try:
        limit = request.args.get('limit', 20, type=int)
        
        manager = SimulationManager()
        simulations = manager.list_simulations()[:limit]
        
        # 시뮬레이션 데이터를 강화하기 위해, Simulation 파일에서 읽기 시작
        enriched_simulations = []
        for sim in simulations:
            sim_dict = sim.to_dict()
            
            # 시뮬레이션 설정 정보를 가져오기 위해 (simulation_config.json에서 simulation_requirement 읽기)
            config = manager.get_simulation_config(sim.simulation_id)
            if config:
                sim_dict["simulation_requirement"] = config.get("simulation_requirement", "")
                time_config = config.get("time_config", {})
                sim_dict["total_simulation_hours"] = time_config.get("total_simulation_hours", 0)
                # 추천 라운드 수 (후备 값)
                recommended_rounds = int(
                    time_config.get("total_simulation_hours", 0) * 60 / 
                    max(time_config.get("minutes_per_round", 60), 1)
                )
            else:
                sim_dict["simulation_requirement"] = ""
                sim_dict["total_simulation_hours"] = 0
                recommended_rounds = 0
            
            # 실행 상태 가져오기 (run_state.json에서 사용자 설정의 실제 라운드 수 읽기)
            run_state = SimulationRunner.get_run_state(sim.simulation_id)
            if run_state:
                sim_dict["current_round"] = run_state.current_round
                sim_dict["runner_status"] = run_state.runner_status.value
                # 사용자 설정의 total_rounds, 없으면 추천 라운드 수 사용
                sim_dict["total_rounds"] = run_state.total_rounds if run_state.total_rounds > 0 else recommended_rounds
            else:
                sim_dict["current_round"] = 0
                sim_dict["runner_status"] = "idle"
                sim_dict["total_rounds"] = recommended_rounds
            
            # 가져오기 연관 프로젝트의 파일 목록（최대 3개）
            project = ProjectManager.get_project(sim.project_id)
            if project and hasattr(project, 'files') and project.files:
                sim_dict["files"] = [
                    {"filename": f.get("filename", "알 수 없음파일")} 
                    for f in project.files[:3]
                ]
            else:
                sim_dict["files"] = []
            
            # 가져오기 연관의 report_id（해당 시뮬레이션 최신의 report 찾기）
            sim_dict["report_id"] = _get_report_id_for_simulation(sim.simulation_id)
            
            # 추가 버전 번호
            sim_dict["version"] = "v1.0.2"
            
            # 형식화날짜
            try:
                created_date = sim_dict.get("created_at", "")[:10]
                sim_dict["created_date"] = created_date
            except:
                sim_dict["created_date"] = ""
            
            enriched_simulations.append(sim_dict)
        
        return jsonify({
            "success": True,
            "data": enriched_simulations,
            "count": len(enriched_simulations)
        })
        
    except Exception as e:
        logger.error(f"가져오기 역사 시뮬레이션 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/profiles', methods=['GET'])
def get_simulation_profiles(simulation_id: str):
    """
    가져오기시뮬레이션의Agent Profile
    
    Query파라미터：
        platform: 플랫폼유형（reddit/twitter, 기본값reddit）
    """
    try:
        platform = request.args.get('platform', 'reddit')
        
        manager = SimulationManager()
        profiles = manager.get_profiles(simulation_id, platform=platform)
        
        return jsonify({
            "success": True,
            "data": {
                "platform": platform,
                "count": len(profiles),
                "profiles": profiles
            }
        })
        
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 404
        
    except Exception as e:
        logger.error(f"가져오기Profile실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/profiles/realtime', methods=['GET'])
def get_simulation_profiles_realtime(simulation_id: str):
    """
    실시간 가져오기 시뮬레이션의 Agent Profile（용에서 생성 중 실시간 진행률 보기）
    
    와 /profiles 인터페이스의 영역별：
    - 직접 읽기 파일, SimulationManager를 경유하지 않음
    - 적용 생성 중의 실시간 보기
    - 돌아가기 추가의 원 데이터（예: 파일 수정 시간, 진행 중이 아닐 경우: 생성 등）
    
    Query파라미터：
        platform: 플랫폼유형（reddit/twitter, 기본값reddit）
    
    돌아가기：
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "platform": "reddit",
                "count": 15,
                "total_expected": 93,  // 예상 총 수（만약）
                "is_generating": true,  //진행 중이 아닐 경우: 생성
                "file_exists": true,
                "file_modified_at": "2025-12-04T18:20:00",
                "profiles": [...]
            }
        }
    """
    import json
    import csv
    from datetime import datetime
    
    try:
        platform = request.args.get('platform', 'reddit')
        
        # 가져오기시뮬레이션디렉토리
        sim_dir = os.path.join(Config.OASIS_SIMULATION_DATA_DIR, simulation_id)
        
        if not os.path.exists(sim_dir):
            return jsonify({
                "success": False,
                "error": f"시뮬레이션존재하지 않음: {simulation_id}"
            }), 404
        
        # 확인파일경로
        if platform == "reddit":
            profiles_file = os.path.join(sim_dir, "reddit_profiles.json")
        else:
            profiles_file = os.path.join(sim_dir, "twitter_profiles.csv")
        
        # 체크 파일이 저장되어 있는지
        file_exists = os.path.exists(profiles_file)
        profiles = []
        file_modified_at = None
        
        if file_exists:
            # 가져오기파일수정시간
            file_stat = os.stat(profiles_file)
            file_modified_at = datetime.fromtimestamp(file_stat.st_mtime).isoformat()
            
            try:
                if platform == "reddit":
                    with open(profiles_file, 'r', encoding='utf-8') as f:
                        profiles = json.load(f)
                else:
                    with open(profiles_file, 'r', encoding='utf-8') as f:
                        reader = csv.DictReader(f)
                        profiles = list(reader)
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"읽기 profiles 파일 실패（진행 중: 쓰기 중）: {e}")
                profiles = []
        
        # 체크 진행 중이 아닐 경우: 생성（state.json으로 판단）
        is_generating = False
        total_expected = None
        
        state_file = os.path.join(sim_dir, "state.json")
        if os.path.exists(state_file):
            try:
                with open(state_file, 'r', encoding='utf-8') as f:
                    state_data = json.load(f)
                    status = state_data.get("status", "")
                    is_generating = status == "preparing"
                    total_expected = state_data.get("entities_count")
            except Exception:
                pass
        
        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "platform": platform,
                "count": len(profiles),
                "total_expected": total_expected,
                "is_generating": is_generating,
                "file_exists": file_exists,
                "file_modified_at": file_modified_at,
                "profiles": profiles
            }
        })
        
    except Exception as e:
        logger.error(f"실시간가져오기Profile실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/config/realtime', methods=['GET'])
def get_simulation_config_realtime(simulation_id: str):
    """
    실시간 가져오기 시뮬레이션 설정（용에서 생성 중 실시간 진행률 보기）
    
    와 /config 인터페이스의 영역별：
    - 직접 읽기 파일, SimulationManager를 경유하지 않음
    - 적용 생성 중의 실시간 보기
    - 돌아가기 추가의 원 데이터（예: 파일 수정 시간, 진행 중이 아닐 경우: 생성 등）
    - 설정이 아직 생성되지 않았더라도 돌아가기 부분 정보
    
    돌아가기：
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "file_exists": true,
                "file_modified_at": "2025-12-04T18:20:00",
                "is_generating": true,  //진행 중이 아닐 경우: 생성
                "generation_stage": "generating_config",  // 현재 생성 단계
                "config": {...}  // 설정 내용（만약 저장되어 있다면）
            }
        }
    """
    import json
    from datetime import datetime
    
    try:
        # 가져오기시뮬레이션디렉토리
        sim_dir = os.path.join(Config.OASIS_SIMULATION_DATA_DIR, simulation_id)
        
        if not os.path.exists(sim_dir):
            return jsonify({
                "success": False,
                "error": f"시뮬레이션존재하지 않음: {simulation_id}"
            }), 404
        
        # 설정파일경로
        config_file = os.path.join(sim_dir, "simulation_config.json")
        
        # 체크 파일이 저장되어 있는지
        file_exists = os.path.exists(config_file)
        config = None
        file_modified_at = None
        
        if file_exists:
            # 가져오기파일수정시간
            file_stat = os.stat(config_file)
            file_modified_at = datetime.fromtimestamp(file_stat.st_mtime).isoformat()
            
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"읽기 config 파일 실패（진행 중: 쓰기 중）: {e}")
                config = None
        
        # 체크 진행 중이 아닐 경우: 생성（state.json으로 판단）
        is_generating = False
        generation_stage = None
        config_generated = False
        
        state_file = os.path.join(sim_dir, "state.json")
        if os.path.exists(state_file):
            try:
                with open(state_file, 'r', encoding='utf-8') as f:
                    state_data = json.load(f)
                    status = state_data.get("status", "")
                    is_generating = status == "preparing"
                    config_generated = state_data.get("config_generated", False)
                    
                    # 판단 현재 단계
                    if is_generating:
                        if state_data.get("profiles_generated", False):
                            generation_stage = "generating_config"
                        else:
                            generation_stage = "generating_profiles"
                    elif status == "ready":
                        generation_stage = "completed"
            except Exception:
                pass
        
        # 구축돌아가기데이터
        response_data = {
            "simulation_id": simulation_id,
            "file_exists": file_exists,
            "file_modified_at": file_modified_at,
            "is_generating": is_generating,
            "generation_stage": generation_stage,
            "config_generated": config_generated,
            "config": config
        }
        
        # 만약 설정이 저장되어 있다면, 일부 주요 통계 정보 추출
        if config:
            response_data["summary"] = {
                "total_agents": len(config.get("agent_configs", [])),
                "simulation_hours": config.get("time_config", {}).get("total_simulation_hours"),
                "initial_posts_count": len(config.get("event_config", {}).get("initial_posts", [])),
                "hot_topics_count": len(config.get("event_config", {}).get("hot_topics", [])),
                "has_twitter_config": "twitter_config" in config,
                "has_reddit_config": "reddit_config" in config,
                "generated_at": config.get("generated_at"),
                "llm_model": config.get("llm_model")
            }
        
        return jsonify({
            "success": True,
            "data": response_data
        })
        
    except Exception as e:
        logger.error(f"실시간가져오기Config실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/config', methods=['GET'])
def get_simulation_config(simulation_id: str):
    """
    가져오기 시뮬레이션 설정（LLM 지능형 생성의 완전 설정）
    
    돌아가기포함：
        - time_config: 시간 설정 (시뮬레이션 시각, 라운드 차수, 고峰/비활성 시간대)
        - agent_configs: 매개 Agent의 활동 설정（활성도、발언 빈도、입장 등）
        - event_config: 이벤트 설정（초기 게시물、핫토픽）
        - platform_configs: 플랫폼설정
        - generation_reasoning: LLM의 설정 추론 설명
    """
    try:
        manager = SimulationManager()
        config = manager.get_simulation_config(simulation_id)
        
        if not config:
            return jsonify({
                "success": False,
                "error": f"시뮬레이션 설정 존재하지 않음, 먼저 호출 /prepare 인터페이스"
            }), 404
        
        return jsonify({
            "success": True,
            "data": config
        })
        
    except Exception as e:
        logger.error(f"가져오기설정실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/config/download', methods=['GET'])
def download_simulation_config(simulation_id: str):
    """다운로드시뮬레이션설정파일"""
    try:
        manager = SimulationManager()
        sim_dir = manager._get_simulation_dir(simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")
        
        if not os.path.exists(config_path):
            return jsonify({
                "success": False,
                "error": "설정 파일이 존재하지 않음, 먼저 호출 /prepare 인터페이스"
            }), 404
        
        return send_file(
            config_path,
            as_attachment=True,
            download_name="simulation_config.json"
        )
        
    except Exception as e:
        logger.error(f"다운로드설정실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/script/<script_name>/download', methods=['GET'])
def download_simulation_script(script_name: str):
    """
    다운로드 시뮬레이션 실행 스크립트 파일（통용 스크립트, 위치 backend/scripts/）
    
    script_name 선택 값：
        - run_twitter_simulation.py
        - run_reddit_simulation.py
        - run_parallel_simulation.py
        - action_logger.py
    """
    try:
        # 스크립트 위치 backend/scripts/ 디렉토리
        scripts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../scripts'))
        
        # 검증 스크립트 이름
        allowed_scripts = [
            "run_twitter_simulation.py",
            "run_reddit_simulation.py", 
            "run_parallel_simulation.py",
            "action_logger.py"
        ]
        
        if script_name not in allowed_scripts:
            return jsonify({
                "success": False,
                "error": f"알 수 없는 스크립트: {script_name}, 선택: {allowed_scripts}"
            }), 400
        
        script_path = os.path.join(scripts_dir, script_name)
        
        if not os.path.exists(script_path):
            return jsonify({
                "success": False,
                "error": f"스크립트 파일이 존재하지 않음: {script_name}"
            }), 404
        
        return send_file(
            script_path,
            as_attachment=True,
            download_name=script_name
        )
        
    except Exception as e:
        logger.error(f"다운로드 스크립트 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 프로필 생성 인터페이스（독립 사용） ==============

@simulation_bp.route('/generate-profiles', methods=['POST'])
def generate_profiles():
    """
    직접부터 그래프 생성 OASIS Agent Profile（안 생성 시뮬레이션）
    
    요청（JSON）：
        {
            "graph_id": "mirofish_xxxx",     // 필수
            "entity_types": ["Student"],      // 선택
            "use_llm": true,                  // 선택
            "platform": "reddit"              // 선택
        }
    """
    try:
        data = request.get_json() or {}
        
        graph_id = data.get('graph_id')
        if not graph_id:
            return jsonify({
                "success": False,
                "error": "그래프_id를 제공해 주세요"
            }), 400
        
        entity_types = data.get('entity_types')
        use_llm = data.get('use_llm', True)
        platform = data.get('platform', 'reddit')
        
        reader = ZepEntityReader()
        filtered = reader.filter_defined_entities(
            graph_id=graph_id,
            defined_entity_types=entity_types,
            enrich_with_edges=True
        )
        
        if filtered.filtered_count == 0:
            return jsonify({
                "success": False,
                "error": "없으면 해당 개체를 찾을 수 없습니다"
            }), 400
        
        generator = OasisProfileGenerator()
        profiles = generator.generate_profiles_from_entities(
            entities=filtered.entities,
            use_llm=use_llm
        )
        
        if platform == "reddit":
            profiles_data = [p.to_reddit_format() for p in profiles]
        elif platform == "twitter":
            profiles_data = [p.to_twitter_format() for p in profiles]
        else:
            profiles_data = [p.to_dict() for p in profiles]
        
        return jsonify({
            "success": True,
            "data": {
                "platform": platform,
                "entity_types": list(filtered.entity_types),
                "count": len(profiles_data),
                "profiles": profiles_data
            }
        })
        
    except Exception as e:
        logger.error(f"생성Profile실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 시뮬레이션 실행 제어 인터페이스 ==============

@simulation_bp.route('/start', methods=['POST'])
def start_simulation():
    """
    시작실행시뮬레이션

    요청（JSON）：
        {
            "simulation_id": "sim_xxxx",          // 필수, 시뮬레이션ID
            "platform": "parallel",                // 선택: twitter / reddit / parallel (기본값)
            "max_rounds": 100,                     // 선택: 최대 시뮬레이션 라운드 수, 너무 긴 시뮬레이션을 잘라냄
            "enable_graph_memory_update": false,   // 선택: 에이전트 활동을 동적으로 업데이트하지 않음
            "force": false                         // 선택: 강제로 다시 시작（중지 실행의 시뮬레이션 및 로그 정리）
        }

    force 파라미터에 대하여：
        - 활성화 후, 만약 시뮬레이션 진행 중: 실행 또는 완료, 먼저 중지하고 실행 로그를 정리
        - 정리의 내용 포함: run_state.json, actions.jsonl, simulation.log 등
        - 안정리설정파일（simulation_config.json）와 profile 파일
        - 시뮬레이션을 다시 실행해야 하는 경우에 적용

    enable_graph_memory_update에 대하여：
        - 활성화 후, 시뮬레이션 중 에이전트의 활동（발행, 댓글, 좋아요 등）이 실시간으로 그래프에 업데이트됨
        - 이로 인해 그래프가 시뮬레이션 과정을 "기억"하게 되어 후속 분석 또는 AI 대화에 사용됨
        - 시뮬레이션과 관련된 프로젝트에 유효한 graph_id가 필요함
        - 배치 업데이트 메커니즘을 채택하여 API 호출 횟수를 줄임

    돌아가기：
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "runner_status": "running",
                "process_pid": 12345,
                "twitter_running": true,
                "reddit_running": true,
                "started_at": "2025-12-01T10:00:00",
                "graph_memory_update_enabled": true,  // 활성화된 그래프 기억 업데이트
                "force_restarted": true               // 강제로 다시 시작
            }
        }
    """
    try:
        data = request.get_json() or {}

        simulation_id = data.get('simulation_id')
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "시뮬레이션_id를 제공해 주세요"
            }), 400

        platform = data.get('platform', 'parallel')
        max_rounds = data.get('max_rounds')
        enable_graph_memory_update = data.get('enable_graph_memory_update', False)
        force = data.get('force', False)

        # 시뮬레이션 모드에 따라 platform 자동 결정
        manager = SimulationManager()
        sim_state = manager.get_simulation_state(simulation_id)
        if sim_state and sim_state.simulation_mode == "stakeholder_meeting":
            platform = "stakeholder_meeting"
            if max_rounds is None:
                max_rounds = 8  # 회의 기본 8라운드

        # 검증 max_rounds 파라미터
        if max_rounds is not None:
            try:
                max_rounds = int(max_rounds)
                if max_rounds <= 0:
                    return jsonify({
                        "success": False,
                        "error": "max_rounds는 반드시 양의 정수여야 합니다"
                    }), 400
            except (ValueError, TypeError):
                return jsonify({
                    "success": False,
                    "error": "max_rounds는 반드시 유효한 정수여야 합니다"
                }), 400

        if platform not in ['twitter', 'reddit', 'parallel', 'stakeholder_meeting']:
            return jsonify({
                "success": False,
                "error": f"유효하지 않은 플랫폼 유형: {platform}, 선택: twitter/reddit/parallel/stakeholder_meeting"
            }), 400

        # 시뮬레이션이 이미 준비되었는지 확인
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)

        if not state:
            return jsonify({
                "success": False,
                "error": f"시뮬레이션존재하지 않음: {simulation_id}"
            }), 404

        force_restarted = False
        
        # 지능형 처리 상태：만약 준비 작업이 완료되면, 재시작 허용
        if state.status != SimulationStatus.READY:
            # 준비 작업이 완료되었는지 확인
            is_prepared, prepare_info = _check_simulation_prepared(simulation_id)

            if is_prepared:
                # 준비 작업 완료, 진행 중인지 확인: 실행의 프로세스
                if state.status == SimulationStatus.RUNNING:
                    # 시뮬레이션 프로세스가 실제로 실행 중인지 확인
                    run_state = SimulationRunner.get_run_state(simulation_id)
                    if run_state and run_state.runner_status.value == "running":
                        # 프로세스가 실제로 실행 중임
                        if force:
                            # 강제 모드：중지 실행의 시뮬레이션
                            logger.info(f"강제 모드：중지 실행의 시뮬레이션 {simulation_id}")
                            try:
                                SimulationRunner.stop_simulation(simulation_id)
                            except Exception as e:
                                logger.warning(f"중지 시뮬레이션 시 경고 발생: {str(e)}")
                        else:
                            return jsonify({
                                "success": False,
                                "error": f"시뮬레이션 진행 중: 실행 중, 먼저 호출 /stop 인터페이스 중지, 또는 사용 force=true 강제로 다시 시작"
                            }), 400

                # 만약 강제 모드라면, 실행 로그 정리
                if force:
                    logger.info(f"강제 모드：정리 시뮬레이션 로그 {simulation_id}")
                    cleanup_result = SimulationRunner.cleanup_simulation_logs(simulation_id)
                    if not cleanup_result.get("success"):
                        logger.warning(f"정리 로그 시 발생 경고: {cleanup_result.get('errors')}")
                    force_restarted = True

                # 프로세스 존재하지 않음 또는 이미 종료, 초기화 상태로 ready
                logger.info(f"시뮬레이션 {simulation_id} 준비 작업 완료, 초기화 상태로 ready（원상태: {state.status.value}）")
                state.status = SimulationStatus.READY
                manager._save_simulation_state(state)
            else:
                # 준비 작업 미완료
                return jsonify({
                    "success": False,
                    "error": f"시뮬레이션 준비가 완료되지 않음, 현재 상태: {state.status.value}, 먼저 /prepare 인터페이스를 호출하십시오"
                }), 400
        
        # 가져오기그래프ID（용그래프기억업데이트）
        graph_id = None
        if enable_graph_memory_update:
            # 부터시뮬레이션상태또는프로젝트중가져오기 graph_id
            graph_id = state.graph_id
            if not graph_id:
                # 시도부터프로젝트중가져오기
                project = ProjectManager.get_project(state.project_id)
                if project:
                    graph_id = project.graph_id
            
            if not graph_id:
                return jsonify({
                    "success": False,
                    "error": "활성화 그래프 기억 업데이트에 유효한 graph_id가 필요합니다, 프로젝트가 이미 구축된 그래프를 보장하십시오"
                }), 400
            
            logger.info(f"활성화그래프기억업데이트: simulation_id={simulation_id}, graph_id={graph_id}")
        
        # 시작시뮬레이션
        run_state = SimulationRunner.start_simulation(
            simulation_id=simulation_id,
            platform=platform,
            max_rounds=max_rounds,
            enable_graph_memory_update=enable_graph_memory_update,
            graph_id=graph_id
        )
        
        # 업데이트시뮬레이션상태
        state.status = SimulationStatus.RUNNING
        manager._save_simulation_state(state)
        
        response_data = run_state.to_dict()
        if max_rounds:
            response_data['max_rounds_applied'] = max_rounds
        response_data['graph_memory_update_enabled'] = enable_graph_memory_update
        response_data['force_restarted'] = force_restarted
        if enable_graph_memory_update:
            response_data['graph_id'] = graph_id
        
        return jsonify({
            "success": True,
            "data": response_data
        })
        
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400
        
    except Exception as e:
        logger.error(f"시작시뮬레이션실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/stop', methods=['POST'])
def stop_simulation():
    """
    중지시뮬레이션
    
    요청（JSON）：
        {
            "simulation_id": "sim_xxxx"  // 필수, 시뮬레이션ID
        }
    
    돌아가기：
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "runner_status": "stopped",
                "completed_at": "2025-12-01T12:00:00"
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "simulation_id를 제공해 주십시오"
            }), 400
        
        run_state = SimulationRunner.stop_simulation(simulation_id)
        
        # 업데이트시뮬레이션상태
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        if state:
            state.status = SimulationStatus.PAUSED
            manager._save_simulation_state(state)
        
        return jsonify({
            "success": True,
            "data": run_state.to_dict()
        })
        
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400
        
    except Exception as e:
        logger.error(f"중지시뮬레이션실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 실시간 상태 모니터링 인터페이스 ==============

@simulation_bp.route('/<simulation_id>/run-status', methods=['GET'])
def get_run_status(simulation_id: str):
    """
    가져오기 시뮬레이션 실행 실시간 상태（용 프론트엔드 라운드 문의）
    
    돌아가기：
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "runner_status": "running",
                "current_round": 5,
                "total_rounds": 144,
                "progress_percent": 3.5,
                "simulated_hours": 2,
                "total_simulation_hours": 72,
                "twitter_running": true,
                "reddit_running": true,
                "twitter_actions_count": 150,
                "reddit_actions_count": 200,
                "total_actions_count": 350,
                "started_at": "2025-12-01T10:00:00",
                "updated_at": "2025-12-01T10:30:00"
            }
        }
    """
    try:
        run_state = SimulationRunner.get_run_state(simulation_id)
        
        if not run_state:
            return jsonify({
                "success": True,
                "data": {
                    "simulation_id": simulation_id,
                    "runner_status": "idle",
                    "current_round": 0,
                    "total_rounds": 0,
                    "progress_percent": 0,
                    "twitter_actions_count": 0,
                    "reddit_actions_count": 0,
                    "total_actions_count": 0,
                }
            })
        
        return jsonify({
            "success": True,
            "data": run_state.to_dict()
        })
        
    except Exception as e:
        logger.error(f"가져오기실행상태실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/run-status/detail', methods=['GET'])
def get_run_status_detail(simulation_id: str):
    """
    가져오기 시뮬레이션 실행 상세 상태（포함 소액션）
    
    용 프론트엔드 표시 실시간 동적으로
    
    Query파라미터：
        platform: 필터플랫폼（twitter/reddit, 선택）
    
    돌아가기：
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "runner_status": "running",
                "current_round": 5,
                ...
                "all_actions": [
                    {
                        "round_num": 5,
                        "timestamp": "2025-12-01T10:30:00",
                        "platform": "twitter",
                        "agent_id": 3,
                        "agent_name": "Agent Name",
                        "action_type": "CREATE_POST",
                        "action_args": {"content": "..."},
                        "result": null,
                        "success": true
                    },
                    ...
                ],
                "twitter_actions": [...],  # Twitter 플랫폼의 소액션
                "reddit_actions": [...]    # Reddit 플랫폼의 소액션
            }
        }
    """
    try:
        run_state = SimulationRunner.get_run_state(simulation_id)
        platform_filter = request.args.get('platform')
        
        if not run_state:
            return jsonify({
                "success": True,
                "data": {
                    "simulation_id": simulation_id,
                    "runner_status": "idle",
                    "all_actions": [],
                    "twitter_actions": [],
                    "reddit_actions": []
                }
            })
        
        # 가져오기 완전한 액션 목록
        all_actions = SimulationRunner.get_all_actions(
            simulation_id=simulation_id,
            platform=platform_filter
        )
        
        # 분 플랫폼 가져오기 액션
        twitter_actions = SimulationRunner.get_all_actions(
            simulation_id=simulation_id,
            platform="twitter"
        ) if not platform_filter or platform_filter == "twitter" else []
        
        reddit_actions = SimulationRunner.get_all_actions(
            simulation_id=simulation_id,
            platform="reddit"
        ) if not platform_filter or platform_filter == "reddit" else []
        
        # 가져오기 현재 라운드 차의 액션（recent_actions는 최신 한 라운드만 표시）
        current_round = run_state.current_round
        recent_actions = SimulationRunner.get_all_actions(
            simulation_id=simulation_id,
            platform=platform_filter,
            round_num=current_round
        ) if current_round > 0 else []
        
        # 가져오기 기본 상태 정보
        result = run_state.to_dict()
        result["all_actions"] = [a.to_dict() for a in all_actions]
        result["twitter_actions"] = [a.to_dict() for a in twitter_actions]
        result["reddit_actions"] = [a.to_dict() for a in reddit_actions]
        result["rounds_count"] = len(run_state.rounds)
        # recent_actions는 현재 최신 한 라운드 두 개 플랫폼의 내용을 표시
        result["recent_actions"] = [a.to_dict() for a in recent_actions]
        
        return jsonify({
            "success": True,
            "data": result
        })
        
    except Exception as e:
        logger.error(f"가져오기 상세 상태 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/actions', methods=['GET'])
def get_simulation_actions(simulation_id: str):
    """
    가져오기 시뮬레이션의 Agent 액션 역사
    
    Query파라미터：
        limit: 돌아가기수량（기본값100）
        offset: 오프셋（기본값 0）
        platform: 필터플랫폼（twitter/reddit）
        agent_id: 필터Agent ID
        round_num: 필터 라운드 차
    
    돌아가기：
        {
            "success": true,
            "data": {
                "count": 100,
                "actions": [...]
            }
        }
    """
    try:
        limit = request.args.get('limit', 100, type=int)
        offset = request.args.get('offset', 0, type=int)
        platform = request.args.get('platform')
        agent_id = request.args.get('agent_id', type=int)
        round_num = request.args.get('round_num', type=int)
        
        actions = SimulationRunner.get_actions(
            simulation_id=simulation_id,
            limit=limit,
            offset=offset,
            platform=platform,
            agent_id=agent_id,
            round_num=round_num
        )
        
        return jsonify({
            "success": True,
            "data": {
                "count": len(actions),
                "actions": [a.to_dict() for a in actions]
            }
        })
        
    except Exception as e:
        logger.error(f"가져오기 액션 역사 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/timeline', methods=['GET'])
def get_simulation_timeline(simulation_id: str):
    """
    가져오기 시뮬레이션 시간선 (라운드 차수에 따라 汇总)
    
    용 프론트엔드 표시 진행률 개와 시간선 뷰
    
    Query파라미터：
        start_round: 시작 라운드 차（기본값 0）
        end_round: 종료 라운드 차（기본값 전체）
    
    돌아가기 매 라운드의 汇总 정보
    """
    try:
        start_round = request.args.get('start_round', 0, type=int)
        end_round = request.args.get('end_round', type=int)
        
        timeline = SimulationRunner.get_timeline(
            simulation_id=simulation_id,
            start_round=start_round,
            end_round=end_round
        )
        
        return jsonify({
            "success": True,
            "data": {
                "rounds_count": len(timeline),
                "timeline": timeline
            }
        })
        
    except Exception as e:
        logger.error(f"가져오기 시간선 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/agent-stats', methods=['GET'])
def get_agent_stats(simulation_id: str):
    """
    가져오기 매개 Agent의 통계 정보
    
    용 프론트엔드 표시 Agent 활성도 랭킹, 액션 분포 등
    """
    try:
        stats = SimulationRunner.get_agent_stats(simulation_id)
        
        return jsonify({
            "success": True,
            "data": {
                "agents_count": len(stats),
                "stats": stats
            }
        })
        
    except Exception as e:
        logger.error(f"가져오기Agent통계실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 데이터베이스조회인터페이스 ==============

@simulation_bp.route('/<simulation_id>/posts', methods=['GET'])
def get_simulation_posts(simulation_id: str):
    """
    가져오기 시뮬레이션의 게시물
    
    Query파라미터：
        platform: 플랫폼유형（twitter/reddit）
        limit: 돌아가기수량（기본값50）
        offset: 오프셋
    
    돌아가기 게시물 목록（부터 SQLite 데이터베이스 읽기）
    """
    try:
        platform = request.args.get('platform', 'reddit')
        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)
        
        sim_dir = os.path.join(
            os.path.dirname(__file__),
            f'../../uploads/simulations/{simulation_id}'
        )
        
        db_file = f"{platform}_simulation.db"
        db_path = os.path.join(sim_dir, db_file)
        
        if not os.path.exists(db_path):
            return jsonify({
                "success": True,
                "data": {
                    "platform": platform,
                    "count": 0,
                    "posts": [],
                    "message": "데이터베이스 존재하지 않음, 시뮬레이션 아직 실행되지 않음"
                }
            })
        
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT * FROM post 
                ORDER BY created_at DESC 
                LIMIT ? OFFSET ?
            """, (limit, offset))
            
            posts = [dict(row) for row in cursor.fetchall()]
            
            cursor.execute("SELECT COUNT(*) FROM post")
            total = cursor.fetchone()[0]
            
        except sqlite3.OperationalError:
            posts = []
            total = 0
        
        conn.close()
        
        return jsonify({
            "success": True,
            "data": {
                "platform": platform,
                "total": total,
                "count": len(posts),
                "posts": posts
            }
        })
        
    except Exception as e:
        logger.error(f"가져오기 게시물 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/comments', methods=['GET'])
def get_simulation_comments(simulation_id: str):
    """
    가져오기 시뮬레이션의 댓글（만 Reddit）
    
    Query파라미터：
        post_id: 필터 게시물 ID（선택）
        limit: 돌아가기수량
        offset: 오프셋
    """
    try:
        post_id = request.args.get('post_id')
        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)
        
        sim_dir = os.path.join(
            os.path.dirname(__file__),
            f'../../uploads/simulations/{simulation_id}'
        )
        
        db_path = os.path.join(sim_dir, "reddit_simulation.db")
        
        if not os.path.exists(db_path):
            return jsonify({
                "success": True,
                "data": {
                    "count": 0,
                    "comments": []
                }
            })
        
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        try:
            if post_id:
                cursor.execute("""
                    SELECT * FROM comment 
                    WHERE post_id = ?
                    ORDER BY created_at DESC 
                    LIMIT ? OFFSET ?
                """, (post_id, limit, offset))
            else:
                cursor.execute("""
                    SELECT * FROM comment 
                    ORDER BY created_at DESC 
                    LIMIT ? OFFSET ?
                """, (limit, offset))
            
            comments = [dict(row) for row in cursor.fetchall()]
            
        except sqlite3.OperationalError:
            comments = []
        
        conn.close()
        
        return jsonify({
            "success": True,
            "data": {
                "count": len(comments),
                "comments": comments
            }
        })
        
    except Exception as e:
        logger.error(f"가져오기 댓글 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Interview 인터뷰 인터페이스 ==============

@simulation_bp.route('/interview', methods=['POST'])
def interview_agent():
    """
    인터뷰 단일 Agent

    주의: 이 기능은 시뮬레이션 환경이 실행 상태여야 합니다 (완료 후 시뮬레이션 루프 진입 대기 명령 모드)

    요청（JSON）：
        {
            "simulation_id": "sim_xxxx",       // 필수, 시뮬레이션ID
            "agent_id": 0,                     // 필수, Agent ID
            "prompt": "이 일에 대한 당신의 의견은 무엇인가요?",  // 필수, 인터뷰 질문
            "platform": "twitter",             // 선택, 지정 플랫폼 (twitter/reddit)
                                               // 지정하지 않을 경우: 듀얼 플랫폼 시뮬레이션으로 두 개 플랫폼에서 동시에 인터뷰
            "timeout": 60                      // 선택, 시간 초과시간（초）, 기본값60
        }

    돌아가기 (지정하지 않은 platform, 듀얼 플랫폼 모드):
        {
            "success": true,
            "data": {
                "agent_id": 0,
                "prompt": "이 일에 대한 당신의 의견은 무엇인가요?",
                "result": {
                    "agent_id": 0,
                    "prompt": "...",
                    "platforms": {
                        "twitter": {"agent_id": 0, "response": "...", "platform": "twitter"},
                        "reddit": {"agent_id": 0, "response": "...", "platform": "reddit"}
                    }
                },
                "timestamp": "2025-12-08T10:00:01"
            }
        }

    돌아가기 (지정한 platform):
        {
            "success": true,
            "data": {
                "agent_id": 0,
                "prompt": "이 일에 대한 당신의 의견은 무엇인가요?",
                "result": {
                    "agent_id": 0,
                    "response": "나는 로...",
                    "platform": "twitter",
                    "timestamp": "2025-12-08T10:00:00"
                },
                "timestamp": "2025-12-08T10:00:01"
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        agent_id = data.get('agent_id')
        prompt = data.get('prompt')
        platform = data.get('platform')  # 선택：twitter/reddit/None
        timeout = data.get('timeout', 60)
        
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "simulation_id를 제공해 주세요"
            }), 400
        
        if agent_id is None:
            return jsonify({
                "success": False,
                "error": "agent_id를 제공해 주세요"
            }), 400
        
        if not prompt:
            return jsonify({
                "success": False,
                "error": "prompt (인터뷰 질문)를 제공해 주세요"
            }), 400
        
        # 검증platform파라미터
        if platform and platform not in ("twitter", "reddit"):
            return jsonify({
                "success": False,
                "error": "platform 파라미터는 'twitter' 또는 'reddit'만 가능합니다"
            }), 400
        
        # 환경 상태 확인
        if not SimulationRunner.check_env_alive(simulation_id):
            return jsonify({
                "success": False,
                "error": "시뮬레이션 환경이 실행되지 않았거나 이미 닫혔습니다. 시뮬레이션이 완료되고 대기 명령 모드에 진입했는지 확인해 주세요."
            }), 400
        
        # 프롬프트 최적화, 추가 접두어로 Agent 호출 도구 회피
        optimized_prompt = optimize_interview_prompt(prompt)
        
        result = SimulationRunner.interview_agent(
            simulation_id=simulation_id,
            agent_id=agent_id,
            prompt=optimized_prompt,
            platform=platform,
            timeout=timeout
        )

        return jsonify({
            "success": result.get("success", False),
            "data": result
        })
        
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400
        
    except TimeoutError as e:
        return jsonify({
            "success": False,
            "error": f"대기Interview응답시간 초과: {str(e)}"
        }), 504
        
    except Exception as e:
        logger.error(f"Interview실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/interview/batch', methods=['POST'])
def interview_agents_batch():
    """
    배치 인터뷰 다수 Agent

    주의: 이 기능은 시뮬레이션 환경이 실행 상태여야 합니다

    요청（JSON）：
        {
            "simulation_id": "sim_xxxx",       // 필수, 시뮬레이션ID
            "interviews": [                    // 필수, 인터뷰 목록
                {
                    "agent_id": 0,
                    "prompt": "A에 대한 당신의 의견은 무엇인가요?",
                    "platform": "twitter"      // 선택, 해당 Agent의 인터뷰 플랫폼 지정
                },
                {
                    "agent_id": 1,
                    "prompt": "B에 대한 당신의 의견은 무엇인가요?"  // platform을 지정하지 않으면 기본값 사용
                }
            ],
            "platform": "reddit",              // 선택, 기본값 플랫폼 (각 항목의 platform이 덮어씀)
                                               // 지정하지 않을 경우: 듀얼 플랫폼 시뮬레이션으로 각 Agent에서 동시에 두 개 플랫폼 인터뷰
            "timeout": 120                     // 선택, 시간 초과시간（초）, 기본값120
        }

    돌아가기：
        {
            "success": true,
            "data": {
                "interviews_count": 2,
                "result": {
                    "interviews_count": 4,
                    "results": {
                        "twitter_0": {"agent_id": 0, "response": "...", "platform": "twitter"},
                        "reddit_0": {"agent_id": 0, "response": "...", "platform": "reddit"},
                        "twitter_1": {"agent_id": 1, "response": "...", "platform": "twitter"},
                        "reddit_1": {"agent_id": 1, "response": "...", "platform": "reddit"}
                    }
                },
                "timestamp": "2025-12-08T10:00:01"
            }
        }
    """
    try:
        data = request.get_json() or {}

        simulation_id = data.get('simulation_id')
        interviews = data.get('interviews')
        platform = data.get('platform')  # 선택：twitter/reddit/None
        timeout = data.get('timeout', 120)

        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "simulation_id를 제공해 주세요"
            }), 400

        if not interviews or not isinstance(interviews, list):
            return jsonify({
                "success": False,
                "error": "interviews (인터뷰 목록)를 제공해 주세요"
            }), 400

        # 검증platform파라미터
        if platform and platform not in ("twitter", "reddit"):
            return jsonify({
                "success": False,
                "error": "platform 파라미터는 'twitter' 또는 'reddit'만 가능합니다"
            }), 400

        # 각 인터뷰 항목 검증
        for i, interview in enumerate(interviews):
            if 'agent_id' not in interview:
                return jsonify({
                    "success": False,
                    "error": f"인터뷰 목록 {i+1} 항목에 agent_id가 누락되었습니다"
                }), 400
            if 'prompt' not in interview:
                return jsonify({
                    "success": False,
                    "error": f"인터뷰 목록 {i+1} 항목에 prompt가 누락되었습니다"
                }), 400
            # 각 항목의 platform 검증 (만약)
            item_platform = interview.get('platform')
            if item_platform and item_platform not in ("twitter", "reddit"):
                return jsonify({
                    "success": False,
                    "error": f"인터뷰 목록 {i+1} 항목의 platform은 'twitter' 또는 'reddit'만 가능합니다"
                }), 400

        # 환경 상태 확인
        if not SimulationRunner.check_env_alive(simulation_id):
            return jsonify({
                "success": False,
                "error": "시뮬레이션 환경이 실행되지 않았거나 이미 닫혔습니다. 시뮬레이션이 완료되고 대기 명령 모드에 진입했는지 확인해 주세요."
            }), 400

        # 각 인터뷰 항목의 프롬프트 최적화, 추가 접두어로 Agent 호출 도구 회피
        optimized_interviews = []
        for interview in interviews:
            optimized_interview = interview.copy()
            optimized_interview['prompt'] = optimize_interview_prompt(interview.get('prompt', ''))
            optimized_interviews.append(optimized_interview)

        result = SimulationRunner.interview_agents_batch(
            simulation_id=simulation_id,
            interviews=optimized_interviews,
            platform=platform,
            timeout=timeout
        )

        return jsonify({
            "success": result.get("success", False),
            "data": result
        })

    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400

    except TimeoutError as e:
        return jsonify({
            "success": False,
            "error": f"대기 배치 Interview 응답 시간 초과: {str(e)}"
        }), 504

    except Exception as e:
        logger.error(f"배치 Interview 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/interview/all', methods=['POST'])
def interview_all_agents():
    """
    전역 인터뷰 - 동일한 질문으로 Agent를 인터뷰

    주의：이 기능은 시뮬레이션 환경이 실행 상태여야 합니다.

    요청（JSON）：
        {
            "simulation_id": "sim_xxxx",            // 필수, 시뮬레이션ID
            "prompt": "이 일에 대한 전반적인 의견은 무엇인가요?",  // 필수, 인터뷰 질문（소Agent사용 동일 질문）
            "platform": "reddit",                   // 선택, 지정 플랫폼（twitter/reddit）
                                                    // 안 지정 시：듀얼 플랫폼 시뮬레이션에서 각 Agent가 동시에 두 개 플랫폼을 인터뷰
            "timeout": 180                          // 선택, 시간 초과시간（초）, 기본값180
        }

    돌아가기：
        {
            "success": true,
            "data": {
                "interviews_count": 50,
                "result": {
                    "interviews_count": 100,
                    "results": {
                        "twitter_0": {"agent_id": 0, "response": "...", "platform": "twitter"},
                        "reddit_0": {"agent_id": 0, "response": "...", "platform": "reddit"},
                        ...
                    }
                },
                "timestamp": "2025-12-08T10:00:01"
            }
        }
    """
    try:
        data = request.get_json() or {}

        simulation_id = data.get('simulation_id')
        prompt = data.get('prompt')
        platform = data.get('platform')  # 선택：twitter/reddit/None
        timeout = data.get('timeout', 180)

        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "simulation_id를 제공해 주세요"
            }), 400

        if not prompt:
            return jsonify({
                "success": False,
                "error": "prompt（인터뷰 질문）을 제공해 주세요"
            }), 400

        # 검증platform파라미터
        if platform and platform not in ("twitter", "reddit"):
            return jsonify({
                "success": False,
                "error": "platform 파라미터는 'twitter' 또는 'reddit'만 가능합니다"
            }), 400

        # 환경 상태 확인
        if not SimulationRunner.check_env_alive(simulation_id):
            return jsonify({
                "success": False,
                "error": "시뮬레이션 환경이 실행 중이 아니거나 이미 닫혔습니다. 시뮬레이션이 완료되었는지 확인하고 대기 명령 모드로 진입하세요."
            }), 400

        # prompt 최적화, 추가 접두어로 Agent 호출 도구 회피
        optimized_prompt = optimize_interview_prompt(prompt)

        result = SimulationRunner.interview_all_agents(
            simulation_id=simulation_id,
            prompt=optimized_prompt,
            platform=platform,
            timeout=timeout
        )

        return jsonify({
            "success": result.get("success", False),
            "data": result
        })

    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400

    except TimeoutError as e:
        return jsonify({
            "success": False,
            "error": f"대기 전역 Interview 응답 시간 초과: {str(e)}"
        }), 504

    except Exception as e:
        logger.error(f"전역 Interview 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/interview/history', methods=['POST'])
def get_interview_history():
    """
    인터뷰 역사 기록 가져오기

    시뮬레이션 데이터베이스에서 인터뷰 기록 읽기

    요청（JSON）：
        {
            "simulation_id": "sim_xxxx",  // 필수, 시뮬레이션ID
            "platform": "reddit",          // 선택, 플랫폼유형（reddit/twitter）
                                           // 안 지정 시 두 개 플랫폼의 역사로 돌아감
            "agent_id": 0,                 // 선택, 해당 Agent의 인터뷰 역사만 가져오기
            "limit": 100                   // 선택, 돌아가기수량, 기본값100
        }

    돌아가기：
        {
            "success": true,
            "data": {
                "count": 10,
                "history": [
                    {
                        "agent_id": 0,
                        "response": "나는 로...",
                        "prompt": "이 일에 대한 의견은 무엇인가요?",
                        "timestamp": "2025-12-08T10:00:00",
                        "platform": "reddit"
                    },
                    ...
                ]
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        platform = data.get('platform')  # 안 지정 시 두 개 플랫폼의 역사로 돌아감
        agent_id = data.get('agent_id')
        limit = data.get('limit', 100)
        
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "simulation_id를 제공해 주세요"
            }), 400

        history = SimulationRunner.get_interview_history(
            simulation_id=simulation_id,
            platform=platform,
            agent_id=agent_id,
            limit=limit
        )

        return jsonify({
            "success": True,
            "data": {
                "count": len(history),
                "history": history
            }
        })

    except Exception as e:
        logger.error(f"인터뷰 역사 가져오기 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/env-status', methods=['POST'])
def get_env_status():
    """
    가져오기시뮬레이션환경상태

    시뮬레이션 환경이 살아 있는지 확인 (인터뷰 명령 수신을 위해)

    요청（JSON）：
        {
            "simulation_id": "sim_xxxx"  // 필수, 시뮬레이션ID
        }

    돌아가기：
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "env_alive": true,
                "twitter_available": true,
                "reddit_available": true,
                "message": "환경진행 중: 실행, 하여수신Interview명령"
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "simulation_id를 제공해 주세요"
            }), 400

        env_alive = SimulationRunner.check_env_alive(simulation_id)
        
        # 더 자세한 상태 정보 가져오기
        env_status = SimulationRunner.get_env_status_detail(simulation_id)

        if env_alive:
            message = "환경진행 중: 실행, 하여수신Interview명령"
        else:
            message = "환경이 실행 중이 아니거나 이미 닫혔습니다"

        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "env_alive": env_alive,
                "twitter_available": env_status.get("twitter_available", False),
                "reddit_available": env_status.get("reddit_available", False),
                "message": message
            }
        })

    except Exception as e:
        logger.error(f"가져오기환경상태실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/close-env', methods=['POST'])
def close_simulation_env():
    """
    닫기시뮬레이션환경
    
    시뮬레이션에 닫기 환경 명령을 전송하여 우아하게 대기 명령 모드에서 종료합니다.
    
    주의：이것은 /stop 인터페이스와 다릅니다. /stop은 프로세스를 강제로 종료합니다,
    반면 이 인터페이스는 시뮬레이션을 우아하게 닫고 환경을 종료합니다.
    
    요청（JSON）：
        {
            "simulation_id": "sim_xxxx",  // 필수, 시뮬레이션ID
            "timeout": 30                  // 선택, 시간 초과시간（초）, 기본값30
        }
    
    돌아가기：
        {
            "success": true,
            "data": {
                "message": "환경닫기명령이미전송",
                "result": {...},
                "timestamp": "2025-12-08T10:00:01"
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        timeout = data.get('timeout', 30)
        
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "simulation_id를 제공해 주세요"
            }), 400
        
        result = SimulationRunner.close_simulation_env(
            simulation_id=simulation_id,
            timeout=timeout
        )
        
        # 업데이트시뮬레이션상태
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        if state:
            state.status = SimulationStatus.COMPLETED
            manager._save_simulation_state(state)
        
        return jsonify({
            "success": result.get("success", False),
            "data": result
        })
        
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400
        
    except Exception as e:
        logger.error(f"닫기환경실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500
