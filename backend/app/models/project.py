"""
프로젝트컨텍스트관리
서비스 측에서 프로젝트 상태 영속화, 프론트엔드에서 인터페이스 간 대량 데이터 전달 방지
"""

import os
import json
import uuid
import shutil
from datetime import datetime
from typing import Dict, Any, List, Optional
from enum import Enum
from dataclasses import dataclass, field, asdict
from ..config import Config


class ProjectStatus(str, Enum):
    """프로젝트상태"""
    CREATED = "created"              # 방금생성, 파일이미업로드
    ONTOLOGY_GENERATED = "ontology_generated"  # 온톨로지이미생성
    GRAPH_BUILDING = "graph_building"    # 그래프 구축 중
    GRAPH_COMPLETED = "graph_completed"  # 그래프 구축완료
    FAILED = "failed"                # 실패


@dataclass
class Project:
    """프로젝트데이터모델"""
    project_id: str
    name: str
    status: ProjectStatus
    created_at: str
    updated_at: str
    
    # 파일정보
    files: List[Dict[str, str]] = field(default_factory=list)  # [{filename, path, size}]
    total_text_length: int = 0
    
    # 온톨로지정보（인터페이스1생성후채우기）
    ontology: Optional[Dict[str, Any]] = None
    analysis_summary: Optional[str] = None
    
    # 그래프정보（인터페이스2완료후채우기）
    graph_id: Optional[str] = None
    graph_build_task_id: Optional[str] = None
    
    # 설정
    simulation_requirement: Optional[str] = None
    chunk_size: int = 500
    chunk_overlap: int = 50
    
    # 오류정보
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """변환로사전"""
        return {
            "project_id": self.project_id,
            "name": self.name,
            "status": self.status.value if isinstance(self.status, ProjectStatus) else self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "files": self.files,
            "total_text_length": self.total_text_length,
            "ontology": self.ontology,
            "analysis_summary": self.analysis_summary,
            "graph_id": self.graph_id,
            "graph_build_task_id": self.graph_build_task_id,
            "simulation_requirement": self.simulation_requirement,
            "chunk_size": self.chunk_size,
            "chunk_overlap": self.chunk_overlap,
            "error": self.error
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Project':
        """부터사전생성"""
        status = data.get('status', 'created')
        if isinstance(status, str):
            status = ProjectStatus(status)
        
        return cls(
            project_id=data['project_id'],
            name=data.get('name', 'Unnamed Project'),
            status=status,
            created_at=data.get('created_at', ''),
            updated_at=data.get('updated_at', ''),
            files=data.get('files', []),
            total_text_length=data.get('total_text_length', 0),
            ontology=data.get('ontology'),
            analysis_summary=data.get('analysis_summary'),
            graph_id=data.get('graph_id'),
            graph_build_task_id=data.get('graph_build_task_id'),
            simulation_requirement=data.get('simulation_requirement'),
            chunk_size=data.get('chunk_size', 500),
            chunk_overlap=data.get('chunk_overlap', 50),
            error=data.get('error')
        )


class ProjectManager:
    """프로젝트관리기 - 프로젝트의지속성저장과검색을책임진다"""
    
    # 프로젝트저장루트디렉토리
    PROJECTS_DIR = os.path.join(Config.UPLOAD_FOLDER, 'projects')
    
    @classmethod
    def _ensure_projects_dir(cls):
        """프로젝트디렉토리저장보장"""
        os.makedirs(cls.PROJECTS_DIR, exist_ok=True)
    
    @classmethod
    def _get_project_dir(cls, project_id: str) -> str:
        """가져오기프로젝트디렉토리경로"""
        return os.path.join(cls.PROJECTS_DIR, project_id)
    
    @classmethod
    def _get_project_meta_path(cls, project_id: str) -> str:
        """프로젝트메타데이터파일경로가져오기"""
        return os.path.join(cls._get_project_dir(project_id), 'project.json')
    
    @classmethod
    def _get_project_files_dir(cls, project_id: str) -> str:
        """프로젝트파일저장디렉토리가져오기"""
        return os.path.join(cls._get_project_dir(project_id), 'files')
    
    @classmethod
    def _get_project_text_path(cls, project_id: str) -> str:
        """프로젝트추출텍스트저장경로가져오기"""
        return os.path.join(cls._get_project_dir(project_id), 'extracted_text.txt')
    
    @classmethod
    def create_project(cls, name: str = "Unnamed Project") -> Project:
        """
        생성새프로젝트
        
        Args:
            name: 프로젝트 이름
            
        Returns:
            새생성의Project상
        """
        cls._ensure_projects_dir()
        
        project_id = f"proj_{uuid.uuid4().hex[:12]}"
        now = datetime.now().isoformat()
        
        project = Project(
            project_id=project_id,
            name=name,
            status=ProjectStatus.CREATED,
            created_at=now,
            updated_at=now
        )
        
        # 생성프로젝트디렉토리구조
        project_dir = cls._get_project_dir(project_id)
        files_dir = cls._get_project_files_dir(project_id)
        os.makedirs(project_dir, exist_ok=True)
        os.makedirs(files_dir, exist_ok=True)
        
        # 저장프로젝트메타데이터
        cls.save_project(project)
        
        return project
    
    @classmethod
    def save_project(cls, project: Project) -> None:
        """저장프로젝트메타데이터"""
        project.updated_at = datetime.now().isoformat()
        meta_path = cls._get_project_meta_path(project.project_id)
        
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump(project.to_dict(), f, ensure_ascii=False, indent=2)
    
    @classmethod
    def get_project(cls, project_id: str) -> Optional[Project]:
        """
        가져오기프로젝트
        
        Args:
            project_id: 프로젝트 ID
            
        Returns:
            Project상, 만약존재하지않으면None으로돌아가기
        """
        meta_path = cls._get_project_meta_path(project_id)
        
        if not os.path.exists(meta_path):
            return None
        
        with open(meta_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        return Project.from_dict(data)
    
    @classmethod
    def list_projects(cls, limit: int = 50) -> List[Project]:
        """
        목록에있는프로젝트나열
        
        Args:
            limit: 돌아가기수량제한
            
        Returns:
            프로젝트목록, 생성시간역순으로
        """
        cls._ensure_projects_dir()
        
        projects = []
        for project_id in os.listdir(cls.PROJECTS_DIR):
            project = cls.get_project(project_id)
            if project:
                projects.append(project)
        
        # 생성시간역순으로정렬
        projects.sort(key=lambda p: p.created_at, reverse=True)
        
        return projects[:limit]
    
    @classmethod
    def delete_project(cls, project_id: str) -> bool:
        """
        삭제프로젝트및그소속파일
        
        Args:
            project_id: 프로젝트 ID
            
        Returns:
            아니오삭제성공
        """
        project_dir = cls._get_project_dir(project_id)
        
        if not os.path.exists(project_dir):
            return False
        
        shutil.rmtree(project_dir)
        return True
    
    @classmethod
    def save_file_to_project(cls, project_id: str, file_storage, original_filename: str) -> Dict[str, str]:
        """
        저장업로드의파일프로젝트디렉토리
        
        Args:
            project_id: 프로젝트 ID
            file_storage: Flask의FileStorage상
            original_filename: 원본파일명
            
        Returns:
            파일정보사전 {filename, path, size}
        """
        files_dir = cls._get_project_files_dir(project_id)
        os.makedirs(files_dir, exist_ok=True)
        
        # 생성보안의파일명
        ext = os.path.splitext(original_filename)[1].lower()
        safe_filename = f"{uuid.uuid4().hex[:8]}{ext}"
        file_path = os.path.join(files_dir, safe_filename)
        
        # 저장파일
        file_storage.save(file_path)
        
        # 가져오기파일크기
        file_size = os.path.getsize(file_path)
        
        return {
            "original_filename": original_filename,
            "saved_filename": safe_filename,
            "path": file_path,
            "size": file_size
        }
    
    @classmethod
    def save_extracted_text(cls, project_id: str, text: str) -> None:
        """저장추출의텍스트"""
        text_path = cls._get_project_text_path(project_id)
        with open(text_path, 'w', encoding='utf-8') as f:
            f.write(text)
    
    @classmethod
    def get_extracted_text(cls, project_id: str) -> Optional[str]:
        """가져오기추출의텍스트"""
        text_path = cls._get_project_text_path(project_id)
        
        if not os.path.exists(text_path):
            return None
        
        with open(text_path, 'r', encoding='utf-8') as f:
            return f.read()
    
    @classmethod
    def get_project_files(cls, project_id: str) -> List[str]:
        """프로젝트의소속파일경로가져오기"""
        files_dir = cls._get_project_files_dir(project_id)
        
        if not os.path.exists(files_dir):
            return []
        
        return [
            os.path.join(files_dir, f) 
            for f in os.listdir(files_dir) 
            if os.path.isfile(os.path.join(files_dir, f))
        ]

