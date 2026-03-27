"""
설정 관리
통합부터 프로젝트 루트 디렉토리의 .env 파일 로드 설정
"""

import os
from dotenv import load_dotenv

# 프로젝트 루트 로드디렉토리의 .env 파일
# 경로: MiroFish/.env (기준으로 backend/app/config.py)
project_root_env = os.path.join(os.path.dirname(__file__), '../../.env')

if os.path.exists(project_root_env):
    load_dotenv(project_root_env, override=True)
else:
    # 루트에디렉토리없으면 .env，시도로드환경변수（용프로덕션환경）
    load_dotenv(override=True)


class Config:
    """Flask설정클래스"""
    
    # Flask설정
    SECRET_KEY = os.environ.get('SECRET_KEY', 'mirofish-secret-key')
    DEBUG = os.environ.get('FLASK_DEBUG', 'True').lower() == 'true'
    
    # JSON설정 - ASCII 이스케이프 비활성화，한국어가 직접표시（대신 \uXXXX 형식）
    JSON_AS_ASCII = False
    
    # LLM설정（통합사용OpenAI형식）
    LLM_API_KEY = os.environ.get('LLM_API_KEY')
    LLM_BASE_URL = os.environ.get('LLM_BASE_URL', 'https://api.openai.com/v1')
    LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME', 'gpt-4o-mini')
    
    # Zep설정
    ZEP_API_KEY = os.environ.get('ZEP_API_KEY')
    
    # 파일 업로드설정
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), '../uploads')
    ALLOWED_EXTENSIONS = {'pdf', 'md', 'txt', 'markdown'}
    
    # 텍스트처리설정
    DEFAULT_CHUNK_SIZE = 500  # 기본값청크크기
    DEFAULT_CHUNK_OVERLAP = 50  # 기본값오버랩크기
    
    # OASIS시뮬레이션설정
    OASIS_DEFAULT_MAX_ROUNDS = int(os.environ.get('OASIS_DEFAULT_MAX_ROUNDS', '10'))
    OASIS_SIMULATION_DATA_DIR = os.path.join(os.path.dirname(__file__), '../uploads/simulations')
    
    # OASIS플랫폼사용 가능한 액션설정
    OASIS_TWITTER_ACTIONS = [
        'CREATE_POST', 'LIKE_POST', 'REPOST', 'FOLLOW', 'DO_NOTHING', 'QUOTE_POST'
    ]
    OASIS_REDDIT_ACTIONS = [
        'LIKE_POST', 'DISLIKE_POST', 'CREATE_POST', 'CREATE_COMMENT',
        'LIKE_COMMENT', 'DISLIKE_COMMENT', 'SEARCH_POSTS', 'SEARCH_USER',
        'TREND', 'REFRESH', 'DO_NOTHING', 'FOLLOW', 'MUTE'
    ]
    
    # Report Agent설정
    REPORT_AGENT_MAX_TOOL_CALLS = int(os.environ.get('REPORT_AGENT_MAX_TOOL_CALLS', '5'))
    REPORT_AGENT_MAX_REFLECTION_ROUNDS = int(os.environ.get('REPORT_AGENT_MAX_REFLECTION_ROUNDS', '2'))
    REPORT_AGENT_TEMPERATURE = float(os.environ.get('REPORT_AGENT_TEMPERATURE', '0.5'))
    
    @classmethod
    def validate(cls):
        """필수 항목 검증설정"""
        errors = []
        if not cls.LLM_API_KEY:
            errors.append("LLM_API_KEY 미설정")
        if not cls.ZEP_API_KEY:
            errors.append("ZEP_API_KEY 미설정")
        return errors

