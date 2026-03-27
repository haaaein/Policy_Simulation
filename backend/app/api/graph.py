"""
그래프관련API라우팅
채택프로젝트컨텍스트메커니즘, 서비스端지속상태  
"""

import os
import traceback
import threading
from flask import request, jsonify

from . import graph_bp
from ..config import Config
from ..services.ontology_generator import OntologyGenerator
from ..services.graph_builder import GraphBuilderService
from ..services.text_processor import TextProcessor
from ..utils.file_parser import FileParser
from ..utils.logger import get_logger
from ..models.task import TaskManager, TaskStatus
from ..models.project import ProjectManager, ProjectStatus

# 가져오기로그기록기  
logger = get_logger('mirofish.api')


def allowed_file(filename: str) -> bool:
    """파일 확장명이 허용되는지 확인"""
    if not filename or '.' not in filename:
        return False
    ext = os.path.splitext(filename)[1].lower().lstrip('.')
    return ext in Config.ALLOWED_EXTENSIONS


# ============== 프로젝트관리인터페이스 ==============

@graph_bp.route('/project/<project_id>', methods=['GET'])
def get_project(project_id: str):
    """
    가져오기프로젝트상세
    """
    project = ProjectManager.get_project(project_id)
    
    if not project:
        return jsonify({
            "success": False,
            "error": f"프로젝트존재하지 않음: {project_id}"
        }), 404
    
    return jsonify({
        "success": True,
        "data": project.to_dict()
    })


@graph_bp.route('/project/list', methods=['GET'])
def list_projects():
    """
    프로젝트 목록
    """
    limit = request.args.get('limit', 50, type=int)
    projects = ProjectManager.list_projects(limit=limit)
    
    return jsonify({
        "success": True,
        "data": [p.to_dict() for p in projects],
        "count": len(projects)
    })


@graph_bp.route('/project/<project_id>', methods=['DELETE'])
def delete_project(project_id: str):
    """
    삭제프로젝트
    """
    success = ProjectManager.delete_project(project_id)
    
    if not success:
        return jsonify({
            "success": False,
            "error": f"프로젝트존재하지 않음또는삭제 실패: {project_id}"
        }), 404
    
    return jsonify({
        "success": True,
        "message": f"프로젝트이미삭제: {project_id}"
    })


@graph_bp.route('/project/<project_id>/reset', methods=['POST'])
def reset_project(project_id: str):
    """
    프로젝트 상태 초기화(용 재구축 그래프)
    """
    project = ProjectManager.get_project(project_id)
    
    if not project:
        return jsonify({
            "success": False,
            "error": f"프로젝트존재하지 않음: {project_id}"
        }), 404
    
    # 초기화온톨로지이미생성상태
    if project.ontology:
        project.status = ProjectStatus.ONTOLOGY_GENERATED
    else:
        project.status = ProjectStatus.CREATED
    
    project.graph_id = None
    project.graph_build_task_id = None
    project.error = None
    ProjectManager.save_project(project)
    
    return jsonify({
        "success": True,
        "message": f"프로젝트이미초기화: {project_id}",
        "data": project.to_dict()
    })


# ============== 인터페이스1：파일 업로드 및 생성 온톨로지 ==============

@graph_bp.route('/ontology/generate', methods=['POST'])
def generate_ontology():
    """
    인터페이스1：파일 업로드, 분석 생성 온톨로지 정의
    
    요청 방식：multipart/form-data
    
    파라미터：
        files: 업로드할 파일（PDF/MD/TXT）, 다수
        simulation_requirement: 시뮬레이션 요구사항설명（필수）
        project_name: 프로젝트 이름（선택）
        additional_context: 추가 설명（선택）
        
    돌아가기：
        {
            "success": true,
            "data": {
                "project_id": "proj_xxxx",
                "ontology": {
                    "entity_types": [...],
                    "edge_types": [...],
                    "analysis_summary": "..."
                },
                "files": [...],
                "total_text_length": 12345
            }
        }
    """
    try:
        logger.info("=== 온톨로지 정의 생성을 시작합니다 ===")
        
        # 가져오기파라미터
        simulation_requirement = request.form.get('simulation_requirement', '')
        project_name = request.form.get('project_name', 'Unnamed Project')
        additional_context = request.form.get('additional_context', '')
        
        logger.debug(f"프로젝트 이름: {project_name}")
        logger.debug(f"시뮬레이션 요구사항: {simulation_requirement[:100]}...")
        
        if not simulation_requirement:
            return jsonify({
                "success": False,
                "error": "시뮬레이션 요구사항 설명을 제공해 주세요 (simulation_requirement)"
            }), 400
        
        # 가져오기업로드의파일
        uploaded_files = request.files.getlist('files')
        if not uploaded_files or all(not f.filename for f in uploaded_files):
            return jsonify({
                "success": False,
                "error": "최소한 하나의 문서 파일을 업로드해 주세요"
            }), 400
        
        # 생성프로젝트
        project = ProjectManager.create_project(name=project_name)
        project.simulation_requirement = simulation_requirement
        logger.info(f"생성프로젝트: {project.project_id}")
        
        # 파일 저장 및 텍스트 추출
        document_texts = []
        all_text = ""
        
        for file in uploaded_files:
            if file and file.filename and allowed_file(file.filename):
                # 저장파일프로젝트디렉토리
                file_info = ProjectManager.save_file_to_project(
                    project.project_id, 
                    file, 
                    file.filename
                )
                project.files.append({
                    "filename": file_info["original_filename"],
                    "size": file_info["size"]
                })
                
                # 추출텍스트
                text = FileParser.extract_text(file_info["path"])
                text = TextProcessor.preprocess_text(text)
                document_texts.append(text)
                all_text += f"\n\n=== {file_info['original_filename']} ===\n{text}"
        
        if not document_texts:
            ProjectManager.delete_project(project.project_id)
            return jsonify({
                "success": False,
                "error": "문서가 없으면 성공 처리할 수 없습니다. 파일 형식을 확인해 주세요"
            }), 400
        
        # 저장추출의텍스트
        project.total_text_length = len(all_text)
        ProjectManager.save_extracted_text(project.project_id, all_text)
        logger.info(f"텍스트 추출 완료, 총 {len(all_text)} 문자")
        
        # 생성온톨로지
        logger.info("LLM 호출하여 온톨로지 정의 생성 중...")
        generator = OntologyGenerator()
        ontology = generator.generate(
            document_texts=document_texts,
            simulation_requirement=simulation_requirement,
            additional_context=additional_context if additional_context else None
        )
        
        # 저장온톨로지프로젝트
        entity_count = len(ontology.get("entity_types", []))
        edge_count = len(ontology.get("edge_types", []))
        logger.info(f"온톨로지 생성완료: {entity_count} 개개체 유형, {edge_count} 개관계유형")
        
        project.ontology = {
            "entity_types": ontology.get("entity_types", []),
            "edge_types": ontology.get("edge_types", [])
        }
        project.analysis_summary = ontology.get("analysis_summary", "")
        project.status = ProjectStatus.ONTOLOGY_GENERATED
        ProjectManager.save_project(project)
        logger.info(f"=== 온톨로지 생성완료 === 프로젝트 ID: {project.project_id}")
        
        return jsonify({
            "success": True,
            "data": {
                "project_id": project.project_id,
                "project_name": project.name,
                "ontology": project.ontology,
                "analysis_summary": project.analysis_summary,
                "files": project.files,
                "total_text_length": project.total_text_length
            }
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 인터페이스2：구축그래프 ==============

@graph_bp.route('/build', methods=['POST'])
def build_graph():
    """
    인터페이스2：에 따라project_id구축그래프
    
    요청（JSON）：
        {
            "project_id": "proj_xxxx",  // 필수, 自 인터페이스1
            "graph_name": "그래프이름",    // 선택
            "chunk_size": 500,          // 선택, 기본값500
            "chunk_overlap": 50         // 선택, 기본값50
        }
        
    돌아가기：
        {
            "success": true,
            "data": {
                "project_id": "proj_xxxx",
                "task_id": "task_xxxx",
                "message": "그래프 구축작업이 시작되었습니다"
            }
        }
    """
    try:
        logger.info("=== 시작구축그래프 ===")
        
        # 설정 확인
        errors = []
        if not Config.ZEP_API_KEY:
            errors.append("ZEP_API_KEY미설정")
        if errors:
            logger.error(f"설정오류: {errors}")
            return jsonify({
                "success": False,
                "error": "설정오류: " + "; ".join(errors)
            }), 500
        
        # 파싱요청
        data = request.get_json() or {}
        project_id = data.get('project_id')
        logger.debug(f"요청 파라미터: project_id={project_id}")
        
        if not project_id:
            return jsonify({
                "success": False,
                "error": "project_id를 제공해 주세요"
            }), 400
        
        # 가져오기프로젝트
        project = ProjectManager.get_project(project_id)
        if not project:
            return jsonify({
                "success": False,
                "error": f"프로젝트존재하지 않음: {project_id}"
            }), 404
        
        # 프로젝트 상태 확인
        force = data.get('force', False)  # 강제로 재구축
        
        if project.status == ProjectStatus.CREATED:
            return jsonify({
                "success": False,
                "error": "프로젝트가 아직 온톨로지를 생성하지 않았습니다. 먼저 /ontology/generate를 호출해 주세요"
            }), 400
        
        if project.status == ProjectStatus.GRAPH_BUILDING and not force:
            return jsonify({
                "success": False,
                "error": "그래프 진행 중: 구축 중, 반복 제출하지 마세요. 강제로 재구축이 필요하면 force: true를 추가해 주세요",
                "task_id": project.graph_build_task_id
            }), 400
        
        # 만약 강제로 재구축하면, 상태 초기화
        if force and project.status in [ProjectStatus.GRAPH_BUILDING, ProjectStatus.FAILED, ProjectStatus.GRAPH_COMPLETED]:
            project.status = ProjectStatus.ONTOLOGY_GENERATED
            project.graph_id = None
            project.graph_build_task_id = None
            project.error = None
        
        # 가져오기설정
        graph_name = data.get('graph_name', project.name or 'MiroFish Graph')
        chunk_size = data.get('chunk_size', project.chunk_size or Config.DEFAULT_CHUNK_SIZE)
        chunk_overlap = data.get('chunk_overlap', project.chunk_overlap or Config.DEFAULT_CHUNK_OVERLAP)
        
        # 업데이트프로젝트설정
        project.chunk_size = chunk_size
        project.chunk_overlap = chunk_overlap
        
        # 가져오기추출의텍스트
        text = ProjectManager.get_extracted_text(project_id)
        if not text:
            return jsonify({
                "success": False,
                "error": "찾을 수 없음추출의텍스트내용"
            }), 400
        
        # 가져오기온톨로지
        ontology = project.ontology
        if not ontology:
            return jsonify({
                "success": False,
                "error": "온톨로지 정의를 찾을 수 없습니다"
            }), 400
        
        # 생성비동기작업
        task_manager = TaskManager()
        task_id = task_manager.create_task(f"구축그래프: {graph_name}")
        logger.info(f"생성그래프 구축작업: task_id={task_id}, project_id={project_id}")
        
        # 업데이트프로젝트상태
        project.status = ProjectStatus.GRAPH_BUILDING
        project.graph_build_task_id = task_id
        ProjectManager.save_project(project)
        
        # 백그라운드 작업 시작
        def build_task():
            build_logger = get_logger('mirofish.build')
            try:
                build_logger.info(f"[{task_id}] 시작구축그래프...")
                task_manager.update_task(
                    task_id, 
                    status=TaskStatus.PROCESSING,
                    message="초기화그래프 구축서비스..."
                )
                
                # 생성그래프 구축서비스
                builder = GraphBuilderService(api_key=Config.ZEP_API_KEY)
                
                # 분할
                task_manager.update_task(
                    task_id,
                    message="텍스트 분할 중...",
                    progress=5
                )
                chunks = TextProcessor.split_text(
                    text, 
                    chunk_size=chunk_size, 
                    overlap=chunk_overlap
                )
                total_chunks = len(chunks)
                
                # 생성그래프
                task_manager.update_task(
                    task_id,
                    message="생성Zep그래프...",
                    progress=10
                )
                graph_id = builder.create_graph(name=graph_name)
                
                # 업데이트프로젝트의graph_id
                project.graph_id = graph_id
                ProjectManager.save_project(project)
                
                # 온톨로지 설정
                task_manager.update_task(
                    task_id,
                    message="온톨로지 정의 설정 중...",
                    progress=15
                )
                builder.set_ontology(graph_id, ontology)
                
                # 추가 텍스트（progress_callback 서명 (msg, progress_ratio)）
                def add_progress_callback(msg, progress_ratio):
                    progress = 15 + int(progress_ratio * 40)  # 15% - 55%
                    task_manager.update_task(
                        task_id,
                        message=msg,
                        progress=progress
                    )
                
                task_manager.update_task(
                    task_id,
                    message=f"추가 {total_chunks} 개 텍스트 블록 시작...",
                    progress=15
                )
                
                episode_uuids = builder.add_text_batches(
                    graph_id, 
                    chunks,
                    batch_size=3,
                    progress_callback=add_progress_callback
                )
                
                # Zep 처리 완료 대기（각 episode의 processed 상태 조회）
                task_manager.update_task(
                    task_id,
                    message="대기Zep처리데이터...",
                    progress=55
                )
                
                def wait_progress_callback(msg, progress_ratio):
                    progress = 55 + int(progress_ratio * 35)  # 55% - 90%
                    task_manager.update_task(
                        task_id,
                        message=msg,
                        progress=progress
                    )
                
                builder._wait_for_episodes(episode_uuids, wait_progress_callback)
                
                # 가져오기그래프데이터
                task_manager.update_task(
                    task_id,
                    message="가져오기그래프데이터...",
                    progress=95
                )
                graph_data = builder.get_graph_data(graph_id)
                
                # 업데이트프로젝트상태
                project.status = ProjectStatus.GRAPH_COMPLETED
                ProjectManager.save_project(project)
                
                node_count = graph_data.get("node_count", 0)
                edge_count = graph_data.get("edge_count", 0)
                build_logger.info(f"[{task_id}] 그래프 구축 완료: graph_id={graph_id}, 노드={node_count}, 엣지={edge_count}")
                
                # 완료
                task_manager.update_task(
                    task_id,
                    status=TaskStatus.COMPLETED,
                    message="그래프 구축완료",
                    progress=100,
                    result={
                        "project_id": project_id,
                        "graph_id": graph_id,
                        "node_count": node_count,
                        "edge_count": edge_count,
                        "chunk_count": total_chunks
                    }
                )
                
            except Exception as e:
                # 업데이트프로젝트상태로실패
                build_logger.error(f"[{task_id}] 그래프 구축실패: {str(e)}")
                build_logger.debug(traceback.format_exc())
                
                project.status = ProjectStatus.FAILED
                project.error = str(e)
                ProjectManager.save_project(project)
                
                task_manager.update_task(
                    task_id,
                    status=TaskStatus.FAILED,
                    message=f"구축 실패: {str(e)}",
                    error=traceback.format_exc()
                )
        
        # 백그라운드 스레드 시작
        thread = threading.Thread(target=build_task, daemon=True)
        thread.start()
        
        return jsonify({
            "success": True,
            "data": {
                "project_id": project_id,
                "task_id": task_id,
                "message": "그래프 구축 작업이 시작되었습니다. /task/{task_id}로 진행률을 조회해 주세요"
            }
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 작업조회인터페이스 ==============

@graph_bp.route('/task/<task_id>', methods=['GET'])
def get_task(task_id: str):
    """
    조회작업상태
    """
    task = TaskManager().get_task(task_id)
    
    if not task:
        return jsonify({
            "success": False,
            "error": f"작업존재하지 않음: {task_id}"
        }), 404
    
    return jsonify({
        "success": True,
        "data": task.to_dict()
    })


@graph_bp.route('/tasks', methods=['GET'])
def list_tasks():
    """
    작업 목록
    """
    tasks = TaskManager().list_tasks()
    
    return jsonify({
        "success": True,
        "data": [t.to_dict() for t in tasks],
        "count": len(tasks)
    })


# ============== 그래프데이터인터페이스 ==============

@graph_bp.route('/data/<graph_id>', methods=['GET'])
def get_graph_data(graph_id: str):
    """
    그래프 데이터 가져오기（노드와 엣지）
    """
    try:
        if not Config.ZEP_API_KEY:
            return jsonify({
                "success": False,
                "error": "ZEP_API_KEY미설정"
            }), 500
        
        builder = GraphBuilderService(api_key=Config.ZEP_API_KEY)
        graph_data = builder.get_graph_data(graph_id)
        
        return jsonify({
            "success": True,
            "data": graph_data
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@graph_bp.route('/delete/<graph_id>', methods=['DELETE'])
def delete_graph(graph_id: str):
    """
    삭제Zep그래프
    """
    try:
        if not Config.ZEP_API_KEY:
            return jsonify({
                "success": False,
                "error": "ZEP_API_KEY미설정"
            }), 500
        
        builder = GraphBuilderService(api_key=Config.ZEP_API_KEY)
        builder.delete_graph(graph_id)
        
        return jsonify({
            "success": True,
            "message": f"그래프이미삭제: {graph_id}"
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500
