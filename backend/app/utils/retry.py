"""
API호출재시도메커니즘
용처리LLM등외부API호출의재시도로직
"""

import time
import random
import functools
from typing import Callable, Any, Optional, Type, Tuple
from ..utils.logger import get_logger

logger = get_logger('mirofish.retry')


def retry_with_backoff(
    max_retries: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    on_retry: Optional[Callable[[Exception, int], None]] = None
):
    """
    포함지수 백오프의재시도데코레이터
    
    Args:
        max_retries: 최대재시도횟수
        initial_delay: 초기 지연（초）
        max_delay: 최대 지연(초)
        backoff_factor: 대기 인자
        jitter: 추가 랜덤 지터
        exceptions: 재시도의 예외 유형
        on_retry: 재시도 시의 콜백 함수 (exception, retry_count)
    
    Usage:
        @retry_with_backoff(max_retries=3)
        def call_llm_api():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exception = None
            delay = initial_delay
            
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                    
                except exceptions as e:
                    last_exception = e
                    
                    if attempt == max_retries:
                        logger.error(f"함수 {func.__name__} 에서 {max_retries} 회 재시도 후 여전히 실패: {str(e)}")
                        raise
                    
                    # 지연 계산
                    current_delay = min(delay, max_delay)
                    if jitter:
                        current_delay = current_delay * (0.5 + random.random())
                    
                    logger.warning(
                        f"함수 {func.__name__} {attempt + 1} 회 시도 실패: {str(e)}, "
                        f"{current_delay:.1f}초후재시도..."
                    )
                    
                    if on_retry:
                        on_retry(e, attempt + 1)
                    
                    time.sleep(current_delay)
                    delay *= backoff_factor
            
            raise last_exception
        
        return wrapper
    return decorator


def retry_with_backoff_async(
    max_retries: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    on_retry: Optional[Callable[[Exception, int], None]] = None
):
    """
    비동기버전의재시도데코레이터
    """
    import asyncio
    
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            last_exception = None
            delay = initial_delay
            
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                    
                except exceptions as e:
                    last_exception = e
                    
                    if attempt == max_retries:
                        logger.error(f"비동기 함수 {func.__name__} 에서 {max_retries} 회 재시도 후 여전히 실패: {str(e)}")
                        raise
                    
                    current_delay = min(delay, max_delay)
                    if jitter:
                        current_delay = current_delay * (0.5 + random.random())
                    
                    logger.warning(
                        f"비동기 함수 {func.__name__} {attempt + 1} 회 시도 실패: {str(e)}, "
                        f"{current_delay:.1f}초후재시도..."
                    )
                    
                    if on_retry:
                        on_retry(e, attempt + 1)
                    
                    await asyncio.sleep(current_delay)
                    delay *= backoff_factor
            
            raise last_exception
        
        return wrapper
    return decorator


class RetryableAPIClient:
    """
    재시도의API클라이언트래핑
    """
    
    def __init__(
        self,
        max_retries: int = 3,
        initial_delay: float = 1.0,
        max_delay: float = 30.0,
        backoff_factor: float = 2.0
    ):
        self.max_retries = max_retries
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.backoff_factor = backoff_factor
    
    def call_with_retry(
        self,
        func: Callable,
        *args,
        exceptions: Tuple[Type[Exception], ...] = (Exception,),
        **kwargs
    ) -> Any:
        """
        실행 함수 호출 및 실패 시 재시도
        
        Args:
            func: 호출의함수
            *args: 함수파라미터
            exceptions: 재시도의 예외 유형
            **kwargs: 함수 키워드 파라미터
            
        Returns:
            함수 반환 값
        """
        last_exception = None
        delay = self.initial_delay
        
        for attempt in range(self.max_retries + 1):
            try:
                return func(*args, **kwargs)
                
            except exceptions as e:
                last_exception = e
                
                if attempt == self.max_retries:
                    logger.error(f"API 호출에서 {self.max_retries} 회 재시도 후 여전히 실패: {str(e)}")
                    raise
                
                current_delay = min(delay, self.max_delay)
                current_delay = current_delay * (0.5 + random.random())
                
                logger.warning(
                    f"API 호출 {attempt + 1} 회 시도 실패: {str(e)}, "
                    f"{current_delay:.1f}초후재시도..."
                )
                
                time.sleep(current_delay)
                delay *= self.backoff_factor
        
        raise last_exception
    
    def call_batch_with_retry(
        self,
        items: list,
        process_func: Callable,
        exceptions: Tuple[Type[Exception], ...] = (Exception,),
        continue_on_failure: bool = True
    ) -> Tuple[list, list]:
        """
        배치 호출 및 각 실패 항목 개별 재시도
        
        Args:
            items: 처리의프로젝트목록
            process_func: 처리 함수, 수신 단일 item을 파라미터로 사용
            exceptions: 재시도의 예외 유형
            continue_on_failure: 단일 항목 실패 후 다른 항목 처리 여부
            
        Returns:
            (성공 결과 목록, 실패 항목 목록)
        """
        results = []
        failures = []
        
        for idx, item in enumerate(items):
            try:
                result = self.call_with_retry(
                    process_func,
                    item,
                    exceptions=exceptions
                )
                results.append(result)
                
            except Exception as e:
                logger.error(f"처리 {idx + 1} 항목 실패: {str(e)}")
                failures.append({
                    "index": idx,
                    "item": item,
                    "error": str(e)
                })
                
                if not continue_on_failure:
                    raise
        
        return results, failures

