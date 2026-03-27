"""
Zep그래프기억업데이트서비스
시뮬레이션의Agent활동동적으로업데이트Zep그래프중
"""

import os
import time
import threading
import json
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass
from datetime import datetime
from queue import Queue, Empty

from zep_cloud.client import Zep

from ..config import Config
from ..utils.logger import get_logger

logger = get_logger('mirofish.zep_graph_memory_updater')


@dataclass
class AgentActivity:
    """Agent활동기록"""
    platform: str           # twitter / reddit
    agent_id: int
    agent_name: str
    action_type: str        # CREATE_POST, LIKE_POST, etc.
    action_args: Dict[str, Any]
    round_num: int
    timestamp: str
    
    def to_episode_text(self) -> str:
        """
        활동전송 가능한 형태로 변환Zep의텍스트설명
        
        채택자연어설명형식, Zep로부터추출개체와관계
        # 안추가시뮬레이션 관련의 접두사, 避免误导 그래프 업데이트
        """
        # 에 따라안동의액션유형생성안동의설명
        action_descriptions = {
            "CREATE_POST": self._describe_create_post,
            "LIKE_POST": self._describe_like_post,
            "DISLIKE_POST": self._describe_dislike_post,
            "REPOST": self._describe_repost,
            "QUOTE_POST": self._describe_quote_post,
            "FOLLOW": self._describe_follow,
            "CREATE_COMMENT": self._describe_create_comment,
            "LIKE_COMMENT": self._describe_like_comment,
            "DISLIKE_COMMENT": self._describe_dislike_comment,
            "SEARCH_POSTS": self._describe_search,
            "SEARCH_USER": self._describe_search_user,
            "MUTE": self._describe_mute,
        }
        
        describe_func = action_descriptions.get(self.action_type, self._describe_generic)
        description = describe_func()
        
        # 직접 돌아가기 "agent 이름: 활동 설명" 형식, 안추가시뮬레이션 접두사
        return f"{self.agent_name}: {description}"
    
    def _describe_create_post(self) -> str:
        content = self.action_args.get("content", "")
        if content:
            return f"게시물发布：「{content}」"
        return "게시물发布"
    
    def _describe_like_post(self) -> str:
        """좋아요게시물 - 포함게시물 원문과 저자 정보"""
        post_content = self.action_args.get("post_content", "")
        post_author = self.action_args.get("post_author_name", "")
        
        if post_content and post_author:
            return f"좋아요 {post_author}의게시물：「{post_content}」"
        elif post_content:
            return f"좋아요 한 개게시물：「{post_content}」"
        elif post_author:
            return f"좋아요 {post_author}의 한 개게시물"
        return "좋아요 한 개게시물"
    
    def _describe_dislike_post(self) -> str:
        """싫어요게시물 - 포함게시물 원문과 저자 정보"""
        post_content = self.action_args.get("post_content", "")
        post_author = self.action_args.get("post_author_name", "")
        
        if post_content and post_author:
            return f"싫어요 {post_author}의게시물：「{post_content}」"
        elif post_content:
            return f"싫어요 한 개게시물：「{post_content}」"
        elif post_author:
            return f"싫어요 {post_author}의 한 개게시물"
        return "싫어요 한 개게시물"
    
    def _describe_repost(self) -> str:
        """게시물 리포스트 - 포함 원帖 내용과 저자 정보"""
        original_content = self.action_args.get("original_content", "")
        original_author = self.action_args.get("original_author_name", "")
        
        if original_content and original_author:
            return f"转发 {original_author}의게시물：「{original_content}」"
        elif original_content:
            return f"转发 한 개게시물：「{original_content}」"
        elif original_author:
            return f"转发 {original_author}의 한 개게시물"
        return "转发 한 개게시물"
    
    def _describe_quote_post(self) -> str:
        """게시물 인용 - 포함 원帖 내용、저자 정보와 인용 댓글"""
        original_content = self.action_args.get("original_content", "")
        original_author = self.action_args.get("original_author_name", "")
        quote_content = self.action_args.get("quote_content", "") or self.action_args.get("content", "")
        
        base = ""
        if original_content and original_author:
            base = f"引用 {original_author}의게시물「{original_content}」"
        elif original_content:
            base = f"引用 한 개게시물「{original_content}」"
        elif original_author:
            base = f"引用 {original_author}의 한 개게시물"
        else:
            base = "引用 한 개게시물"
        
        if quote_content:
            base += f", 그리고댓글道：「{quote_content}」"
        return base
    
    def _describe_follow(self) -> str:
        """关注 사용자 - 포함关注 사용자의 이름"""
        target_user_name = self.action_args.get("target_user_name", "")
        
        if target_user_name:
            return f"关注 사용자「{target_user_name}」"
        return "关注 한 개 사용자"
    
    def _describe_create_comment(self) -> str:
        """发表댓글 - 포함댓글 내용과所댓글의게시물 정보"""
        content = self.action_args.get("content", "")
        post_content = self.action_args.get("post_content", "")
        post_author = self.action_args.get("post_author_name", "")
        
        if content:
            if post_content and post_author:
                return f"에서 {post_author}의게시물「{post_content}」아래댓글道：「{content}」"
            elif post_content:
                return f"에서게시물「{post_content}」아래댓글道：「{content}」"
            elif post_author:
                return f"에서 {post_author}의게시물아래댓글道：「{content}」"
            return f"댓글道：「{content}」"
        return "发表댓글"
    
    def _describe_like_comment(self) -> str:
        """좋아요댓글 - 포함댓글 내용과 저자 정보"""
        comment_content = self.action_args.get("comment_content", "")
        comment_author = self.action_args.get("comment_author_name", "")
        
        if comment_content and comment_author:
            return f"좋아요 {comment_author}의댓글：「{comment_content}」"
        elif comment_content:
            return f"좋아요 한 개댓글：「{comment_content}」"
        elif comment_author:
            return f"좋아요 {comment_author}의 한 개댓글"
        return "좋아요 한 개댓글"
    
    def _describe_dislike_comment(self) -> str:
        """댓글 신고 - 포함 댓글내용과 작성자정보"""
        comment_content = self.action_args.get("comment_content", "")
        comment_author = self.action_args.get("comment_author_name", "")
        
        if comment_content and comment_author:
            return f"신고{comment_author}의 댓글：「{comment_content}」"
        elif comment_content:
            return f"신고한 개 댓글：「{comment_content}」"
        elif comment_author:
            return f"신고{comment_author}의 한 개 댓글"
        return "신고한 개 댓글"
    
    def _describe_search(self) -> str:
        """게시물 검색 - 포함 검색 키워드"""
        query = self.action_args.get("query", "") or self.action_args.get("keyword", "")
        return f"검색「{query}」" if query else "진행검색"
    
    def _describe_search_user(self) -> str:
        """사용자 검색 - 포함 검색 키워드"""
        query = self.action_args.get("query", "") or self.action_args.get("username", "")
        return f"검색사용자「{query}」" if query else "검색사용자"
    
    def _describe_mute(self) -> str:
        """사용자 차단 - 포함 차단 사용자의 이름"""
        target_user_name = self.action_args.get("target_user_name", "")
        
        if target_user_name:
            return f"차단 사용자「{target_user_name}」"
        return "차단한 개 사용자"
    
    def _describe_generic(self) -> str:
        # 알 수 없는 액션 유형에 대한 일반 설명 생성
        return f"실행 {self.action_type} 작업"


class ZepGraphMemoryUpdater:
    """
    Zep 그래프 기억 업데이트기
    
    모니터링 시뮬레이션의 actions 로그 파일, 새로운 agent 활동 실시간 업데이트 Zep 그래프 중.
    플랫폼별로 그룹화하여, BATCH_SIZE 개 활동 후 일괄 전송 Zep.
    
    모든 의미의 행로를 업데이트 Zep, action_args 중 포함된 전체 컨텍스트 정보:
    - 좋아요/싫어요의 게시물 원문
    - 리트윗/인용의 게시물 원문
    - 팔로우/차단의 사용자명
    - 좋아요/싫어요의 댓글 원문
    """
    
    # 일괄 전송 크기 (각 플랫폼별로 몇 개 누적 후 전송)
    BATCH_SIZE = 5
    
    # 플랫폼 이름 매핑 (용 콘솔 표시)
    PLATFORM_DISPLAY_NAMES = {
        'twitter': '월드1',
        'reddit': '월드2',
    }
    
    # 전송 간격 (초), 요청 속도 피하기
    SEND_INTERVAL = 0.5
    
    # 재시도설정
    MAX_RETRIES = 3
    RETRY_DELAY = 2  # 초
    
    def __init__(self, graph_id: str, api_key: Optional[str] = None):
        """
        초기화 업데이트기
        
        Args:
            graph_id: Zep그래프ID
            api_key: Zep API Key (선택, 기본값부터 설정 읽기)
        """
        self.graph_id = graph_id
        self.api_key = api_key or Config.ZEP_API_KEY
        
        if not self.api_key:
            raise ValueError("ZEP_API_KEY미설정")
        
        self.client = Zep(api_key=self.api_key)
        
        # 활동큐
        self._activity_queue: Queue = Queue()
        
        # 플랫폼별 활동 버퍼 영역 (각 플랫폼별로 BATCH_SIZE 후 일괄 전송)
        self._platform_buffers: Dict[str, List[AgentActivity]] = {
            'twitter': [],
            'reddit': [],
        }
        self._buffer_lock = threading.Lock()
        
        # 제어 플래그
        self._running = False
        self._worker_thread: Optional[threading.Thread] = None
        
        # 통계
        self._total_activities = 0  # 실제 추가 큐의 활동 수
        self._total_sent = 0        # 성공적으로 전송된 Zep의 배치 횟수
        self._total_items_sent = 0  # 성공전송Zep의활동개수
        self._failed_count = 0      # 전송 실패의 배치 횟수
        self._skipped_count = 0     # 필터링된 활동 수 (DO_NOTHING)
        
        logger.info(f"ZepGraphMemoryUpdater 초기화완료: graph_id={graph_id}, batch_size={self.BATCH_SIZE}")
    
    def _get_platform_display_name(self, platform: str) -> str:
        """가져오기플랫폼의표시이름"""
        return self.PLATFORM_DISPLAY_NAMES.get(platform.lower(), platform)
    
    def start(self):
        """시작 후 작업 스레드"""
        if self._running:
            return
        
        self._running = True
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name=f"ZepMemoryUpdater-{self.graph_id[:8]}"
        )
        self._worker_thread.start()
        logger.info(f"ZepGraphMemoryUpdater 이미시작: graph_id={self.graph_id}")
    
    def stop(self):
        """중지 후 작업 스레드"""
        self._running = False
        
        # 남은 활동 전송
        self._flush_remaining()
        
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=10)
        
        logger.info(f"ZepGraphMemoryUpdater 이미중지: graph_id={self.graph_id}, "
                   f"total_activities={self._total_activities}, "
                   f"batches_sent={self._total_sent}, "
                   f"items_sent={self._total_items_sent}, "
                   f"failed={self._failed_count}, "
                   f"skipped={self._skipped_count}")
    
    def add_activity(self, activity: AgentActivity):
        """
        추가 한 개 agent 활동 큐
        
        모든 의미의 행로를 추가 큐에 포함, 다음과 같은 내용:
        - CREATE_POST (게시물 작성)
        - CREATE_COMMENT (댓글 작성)
        - QUOTE_POST（게시물 인용）
        - SEARCH_POSTS (게시물 검색)
        - SEARCH_USER（검색사용자）
        - LIKE_POST/DISLIKE_POST (게시물 좋아요/싫어요)
        - REPOST（재전송）
        - FOLLOW（팔로우）
        - MUTE（음소거）
        - LIKE_COMMENT/DISLIKE_COMMENT（좋아요/싫어요 댓글）
        
        action_args중포함완전한컨텍스트정보（예: 게시물 원문, 사용자명 등）。
        
        Args:
            activity: Agent활동기록
        """
        # DO_NOTHING유형의활동을 건너뜁니다
        if activity.action_type == "DO_NOTHING":
            self._skipped_count += 1
            return
        
        self._activity_queue.put(activity)
        self._total_activities += 1
        logger.debug(f"추가활동Zep큐: {activity.agent_name} - {activity.action_type}")
    
    def add_activity_from_dict(self, data: Dict[str, Any], platform: str):
        """
        부터딕셔너리데이터추가활동
        
        Args:
            data: 부터actions.jsonl파싱의딕셔너리데이터
            platform: 플랫폼이름 (twitter/reddit)
        """
        # 이벤트유형의개목을 건너뜁니다
        if "event_type" in data:
            return
        
        activity = AgentActivity(
            platform=platform,
            agent_id=data.get("agent_id", 0),
            agent_name=data.get("agent_name", ""),
            action_type=data.get("action_type", ""),
            action_args=data.get("action_args", {}),
            round_num=data.get("round", 0),
            timestamp=data.get("timestamp", datetime.now().isoformat()),
        )
        
        self.add_activity(activity)
    
    def _worker_loop(self):
        """후방 작업 루프 - 플랫폼별로 활동 Zep를 일괄 전송"""
        while self._running or not self._activity_queue.empty():
            try:
                # 시도부터큐가져오기활동（시간 초과1초）
                try:
                    activity = self._activity_queue.get(timeout=1)
                    
                    # 활동 추가는 플랫폼의 버퍼 영역에
                    platform = activity.platform.lower()
                    with self._buffer_lock:
                        if platform not in self._platform_buffers:
                            self._platform_buffers[platform] = []
                        self._platform_buffers[platform].append(activity)
                        
                        # 해당 플랫폼이 일괄 크기에 도달했는지 확인
                        if len(self._platform_buffers[platform]) >= self.BATCH_SIZE:
                            batch = self._platform_buffers[platform][:self.BATCH_SIZE]
                            self._platform_buffers[platform] = self._platform_buffers[platform][self.BATCH_SIZE:]
                            # 잠금을 해제한 후 다시 전송
                            self._send_batch_activities(batch, platform)
                            # 전송 간격, 요청이 너무 빠르지 않도록
                            time.sleep(self.SEND_INTERVAL)
                    
                except Empty:
                    pass
                    
            except Exception as e:
                logger.error(f"작업 루프 예외: {e}")
                time.sleep(1)
    
    def _send_batch_activities(self, activities: List[AgentActivity], platform: str):
        """
        일괄 전송 활동 Zep 그래프（합쳐서 하나의 텍스트）
        
        Args:
            activities: Agent활동목록
            platform: 플랫폼이름
        """
        if not activities:
            return
        
        # 여러 활동을 합쳐서 하나의 텍스트로, 줄 바꿈으로 구분
        episode_texts = [activity.to_episode_text() for activity in activities]
        combined_text = "\n".join(episode_texts)
        
        # 포함재시도의전송
        for attempt in range(self.MAX_RETRIES):
            try:
                self.client.graph.add(
                    graph_id=self.graph_id,
                    type="text",
                    data=combined_text
                )
                
                self._total_sent += 1
                self._total_items_sent += len(activities)
                display_name = self._get_platform_display_name(platform)
                logger.info(f"성공적으로 일괄 전송 {len(activities)} 개 {display_name} 활동 그래프 {self.graph_id}")
                logger.debug(f"일괄 내용 미리보기: {combined_text[:200]}...")
                return
                
            except Exception as e:
                if attempt < self.MAX_RETRIES - 1:
                    logger.warning(f"일괄 전송 Zep 실패 (시도 {attempt + 1}/{self.MAX_RETRIES}): {e}")
                    time.sleep(self.RETRY_DELAY * (attempt + 1))
                else:
                    logger.error(f"일괄 전송 Zep 실패, 이미 재시도 {self.MAX_RETRIES} 회: {e}")
                    self._failed_count += 1
    
    def _flush_remaining(self):
        """전송 큐와 버퍼 영역 중 남은 활동"""
        # 먼저 큐 중 남은 활동을 처리하고 추가 버퍼 영역
        while not self._activity_queue.empty():
            try:
                activity = self._activity_queue.get_nowait()
                platform = activity.platform.lower()
                with self._buffer_lock:
                    if platform not in self._platform_buffers:
                        self._platform_buffers[platform] = []
                    self._platform_buffers[platform].append(activity)
            except Empty:
                break
        
        # 그 다음 각 플랫폼 버퍼 영역 중 남은 활동을 전송합니다（BATCH_SIZE 개수에 미치지 않더라도）
        with self._buffer_lock:
            for platform, buffer in self._platform_buffers.items():
                if buffer:
                    display_name = self._get_platform_display_name(platform)
                    logger.info(f"{display_name} 플랫폼에 남은 {len(buffer)} 개 활동 전송")
                    self._send_batch_activities(buffer, platform)
            # 버퍼 영역을 비웁니다
            for platform in self._platform_buffers:
                self._platform_buffers[platform] = []
    
    def get_stats(self) -> Dict[str, Any]:
        """가져오기통계정보"""
        with self._buffer_lock:
            buffer_sizes = {p: len(b) for p, b in self._platform_buffers.items()}
        
        return {
            "graph_id": self.graph_id,
            "batch_size": self.BATCH_SIZE,
            "total_activities": self._total_activities,  # 추가큐의활동총 수
            "batches_sent": self._total_sent,            # 성공적으로 전송된 배치 횟수
            "items_sent": self._total_items_sent,        # 성공전송의활동개수
            "failed_count": self._failed_count,          # 전송 실패의 배치 횟수
            "skipped_count": self._skipped_count,        # 필터링된 활동 수（DO_NOTHING）
            "queue_size": self._activity_queue.qsize(),
            "buffer_sizes": buffer_sizes,                # 각 플랫폼 버퍼 영역 크기
            "running": self._running,
        }


class ZepGraphMemoryManager:
    """
    관리 다수 시뮬레이션의 Zep 그래프 기억 업데이트기
    
    각 시뮬레이션마다 자신의 업데이트기 인스턴스
    """
    
    _updaters: Dict[str, ZepGraphMemoryUpdater] = {}
    _lock = threading.Lock()
    
    @classmethod
    def create_updater(cls, simulation_id: str, graph_id: str) -> ZepGraphMemoryUpdater:
        """
        로 시뮬레이션 생성 그래프 기억 업데이트기
        
        Args:
            simulation_id: 시뮬레이션ID
            graph_id: Zep그래프ID
            
        Returns:
            ZepGraphMemoryUpdater인스턴스
        """
        with cls._lock:
            # 만약 이미 존재한다면, 먼저 기존 것을 중지
            if simulation_id in cls._updaters:
                cls._updaters[simulation_id].stop()
            
            updater = ZepGraphMemoryUpdater(graph_id)
            updater.start()
            cls._updaters[simulation_id] = updater
            
            logger.info(f"그래프 기억 업데이트기 생성: simulation_id={simulation_id}, graph_id={graph_id}")
            return updater
    
    @classmethod
    def get_updater(cls, simulation_id: str) -> Optional[ZepGraphMemoryUpdater]:
        """가져오기 시뮬레이션의 업데이트기"""
        return cls._updaters.get(simulation_id)
    
    @classmethod
    def stop_updater(cls, simulation_id: str):
        """중지하고 제거 시뮬레이션의 업데이트기"""
        with cls._lock:
            if simulation_id in cls._updaters:
                cls._updaters[simulation_id].stop()
                del cls._updaters[simulation_id]
                logger.info(f"이미 중지된 그래프 기억 업데이트기: simulation_id={simulation_id}")
    
    # stop_all 중복 호출 방지를 위한 플래그
    _stop_all_done = False
    
    @classmethod
    def stop_all(cls):
        """모든 업데이트기를 중지합니다"""
        # 중복 호출 방지
        if cls._stop_all_done:
            return
        cls._stop_all_done = True
        
        with cls._lock:
            if cls._updaters:
                for simulation_id, updater in list(cls._updaters.items()):
                    try:
                        updater.stop()
                    except Exception as e:
                        logger.error(f"업데이트 중지 실패: simulation_id={simulation_id}, error={e}")
                cls._updaters.clear()
            logger.info("이미 중지된 그래프 기억 업데이트기")
    
    @classmethod
    def get_all_stats(cls) -> Dict[str, Dict[str, Any]]:
        """업데이트기의 통계 정보를 가져오기"""
        return {
            sim_id: updater.get_stats() 
            for sim_id, updater in cls._updaters.items()
        }
