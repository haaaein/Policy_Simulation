"""
로그설정모듈
통합된로그관리，동시에출력콘솔과파일
"""

import os
import sys
import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler


def _ensure_utf8_stdout():
    """
    보장 stdout/stderr 사용 UTF-8 인코딩
    해결 Windows 콘솔 중문 깨짐 문제
    """
    if sys.platform == 'win32':
        # Windows 하 재설정 표준 출력으로 UTF-8
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        if hasattr(sys.stderr, 'reconfigure'):
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')


# 로그디렉토리
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'logs')


def setup_logger(name: str = 'mirofish', level: int = logging.DEBUG) -> logging.Logger:
    """
    설정 로그기
    
    Args:
        name: 로그기 이름
        level: 로그 수준
        
    Returns:
        설정 완료 로그기
    """
    # 보장 로그 디렉토리 저장에서
    os.makedirs(LOG_DIR, exist_ok=True)
    
    # 생성 로그기
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # 방지 로그 업로드 방송 logger, 피하기 중복 출력
    logger.propagate = False
    
    # 만약 이미 처리기, 안 중복 추가
    if logger.handlers:
        return logger
    
    # 로그형식
    detailed_formatter = logging.Formatter(
        '[%(asctime)s] %(levelname)s [%(name)s.%(funcName)s:%(lineno)d] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    simple_formatter = logging.Formatter(
        '[%(asctime)s] %(levelname)s: %(message)s',
        datefmt='%H:%M:%S'
    )
    
    # 1. 파일 처리기 - 상세 로그 (날짜로 명명, 포함 라운드 전환)
    log_filename = datetime.now().strftime('%Y-%m-%d') + '.log'
    file_handler = RotatingFileHandler(
        os.path.join(LOG_DIR, log_filename),
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(detailed_formatter)
    
    # 2. 콘솔 처리기 - 간결 로그 (INFO 및 그 이상)
    # 보장 Windows 하 사용 UTF-8 인코딩, 피하기 중문 난코드
    _ensure_utf8_stdout()
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(simple_formatter)
    
    # 추가 처리기
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger


def get_logger(name: str = 'mirofish') -> logging.Logger:
    """
    import log (if not exists then create)
    
    Args:
        name: 로그기 이름
        
    Returns:
        로그기 인스턴스
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        return setup_logger(name)
    return logger


# 생성 기본값 로그기
logger = setup_logger()


# 편리 메서드
def debug(msg, *args, **kwargs):
    logger.debug(msg, *args, **kwargs)

def info(msg, *args, **kwargs):
    logger.info(msg, *args, **kwargs)

def warning(msg, *args, **kwargs):
    logger.warning(msg, *args, **kwargs)

def error(msg, *args, **kwargs):
    logger.error(msg, *args, **kwargs)

def critical(msg, *args, **kwargs):
    logger.critical(msg, *args, **kwargs)

