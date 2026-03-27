"""
MiroFish Backend 시작진입점
"""

import os
import sys

# 해결 Windows 콘솔 중문 깨짐 문제: 모든 가져오기 전에 설정 UTF-8 인코딩  
if sys.platform == 'win32':
    # 설정 환경변수 보장 Python 사용 UTF-8  
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
    # 재설정표준출력 스트림을 UTF-8
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# 추가 프로젝트 루트 디렉토리 경로  
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from app.config import Config


def main():
    """메인함수"""
    # 검증설정
    errors = Config.validate()
    if errors:
        print("설정오류:")
        for err in errors:
            print(f"  - {err}")
        print("\n.env 파일의 설정을 확인하세요")
        sys.exit(1)
    
    # 생성앱
    app = create_app()
    
    # 가져오기실행설정
    host = os.environ.get('FLASK_HOST', '0.0.0.0')
    port = int(os.environ.get('FLASK_PORT', 5001))
    debug = Config.DEBUG
    
    # 시작서비스
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == '__main__':
    main()

