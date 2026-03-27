"""
작업상태관리
긴 실행 작업 추적용 (예: 그래프 구축)  # 긴 실행 작업 추적 (예: 그래프 구축)  
"""

import uuid
import threading
from datetime import datetime
from enum import Enum
from typing import Dict, Any, Optional
from dataclasses import dataclass, field


class TaskStatus(str, Enum):
    """작업상태열거형"""
    PENDING = "pending"          # 대기 중
    PROCESSING = "processing"    # 처리 중
    COMPLETED = "completed"      # 완료
    FAILED = "failed"            # 실패


@dataclass
class Task:
    """작업데이터클래스"""
    task_id: str
    task_type: str
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
    progress: int = 0              # 총진행률퍼센트 0-100
    message: str = ""              # 상태 메시지
    result: Optional[Dict] = None  # 작업결과
    error: Optional[str] = None    # 오류정보
    metadata: Dict = field(default_factory=dict)  # 추가 메타데이터  
    progress_detail: Dict = field(default_factory=dict)  # 상세 진행률 정보  
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환"""  # 딕셔너리로 변환  
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "progress": self.progress,
            "message": self.message,
            "progress_detail": self.progress_detail,
            "result": self.result,
            "error": self.error,
            "metadata": self.metadata,
        }


class TaskManager:
    """
    작업 관리자  # 작업 관리자  
    스레드보안의작업상태관리
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        """싱글톤 패턴"""  # 싱글톤 패턴  
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._tasks: Dict[str, Task] = {}
                    cls._instance._task_lock = threading.Lock()
        return cls._instance
    
    def create_task(self, task_type: str, metadata: Optional[Dict] = None) -> str:
        """
        새 작업 생성  # 새 작업 생성  
        
        Args:
            task_type: 작업유형
            metadata: 추가 메타데이터  # 메타데이터: 추가 메타데이터  
            
        Returns:
            작업ID
        """
        task_id = str(uuid.uuid4())
        now = datetime.now()
        
        task = Task(
            task_id=task_id,
            task_type=task_type,
            status=TaskStatus.PENDING,
            created_at=now,
            updated_at=now,
            metadata=metadata or {}
        )
        
        with self._task_lock:
            self._tasks[task_id] = task
        
        return task_id
    
    def get_task(self, task_id: str) -> Optional[Task]:
        """가져오기작업"""
        with self._task_lock:
            return self._tasks.get(task_id)
    
    def update_task(
        self,
        task_id: str,
        status: Optional[TaskStatus] = None,
        progress: Optional[int] = None,
        message: Optional[str] = None,
        result: Optional[Dict] = None,
        error: Optional[str] = None,
        progress_detail: Optional[Dict] = None
    ):
        """
        업데이트작업상태
        
        Args:
            task_id: 작업ID
            status: 새 상태  # 상태: 새 상태  
            progress: 진행률
            message: 메시지
            result: 결과
            error: 오류정보
            progress_detail: 상세 진행률 정보  # 진행 세부 정보: 상세 진행률 정보  
        """
        with self._task_lock:
            task = self._tasks.get(task_id)
            if task:
                task.updated_at = datetime.now()
                if status is not None:
                    task.status = status
                if progress is not None:
                    task.progress = progress
                if message is not None:
                    task.message = message
                if result is not None:
                    task.result = result
                if error is not None:
                    task.error = error
                if progress_detail is not None:
                    task.progress_detail = progress_detail
    
    def complete_task(self, task_id: str, result: Dict):
        """작업 완료 표시"""  # 작업 완료 표시  
        self.update_task(
            task_id,
            status=TaskStatus.COMPLETED,
            progress=100,
            message="작업완료",
            result=result
        )
    
    def fail_task(self, task_id: str, error: str):
        """작업 실패 표시"""  # 작업 실패 표시  
        self.update_task(
            task_id,
            status=TaskStatus.FAILED,
            message="작업실패",
            error=error
        )
    
    def list_tasks(self, task_type: Optional[str] = None) -> list:
        """작업 목록"""  # 작업 나열  
        with self._task_lock:
            tasks = list(self._tasks.values())
            if task_type:
                tasks = [t for t in tasks if t.task_type == task_type]
            return [t.to_dict() for t in sorted(tasks, key=lambda x: x.created_at, reverse=True)]
    
    def cleanup_old_tasks(self, max_age_hours: int = 24):
        """오래된 작업 정리"""  # 오래된 작업 정리
        from datetime import timedelta
        cutoff = datetime.now() - timedelta(hours=max_age_hours)
        
        with self._task_lock:
            old_ids = [
                tid for tid, task in self._tasks.items()
                if task.created_at < cutoff and task.status in [TaskStatus.COMPLETED, TaskStatus.FAILED]
            ]
            for tid in old_ids:
                del self._tasks[tid]

