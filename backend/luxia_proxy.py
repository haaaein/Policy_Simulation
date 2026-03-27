"""
LuxiaCloud Bridge 프록시 서버
표준 OpenAI SDK 요청 → LuxiaCloud 특수 URL 형식으로 변환

사용법: python3 luxia_proxy.py
프록시 주소: http://localhost:8788/v1
"""

import json
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
import threading

# .env 로드
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '../.env'), override=True)

LUXIA_BASE = os.environ.get('LLM_BASE_URL', 'https://bridge.luxiacloud.com/llm/openai')
LUXIA_API_KEY = os.environ.get('LLM_API_KEY', '')
PROXY_PORT = 8788


class LuxiaProxyHandler(BaseHTTPRequestHandler):
    """표준 OpenAI 요청을 LuxiaCloud 형식으로 변환"""

    def do_POST(self):
        # /v1/chat/completions → LuxiaCloud /chat/completions/{model}/create
        if '/chat/completions' not in self.path:
            self.send_error(404, f"Not found: {self.path}")
            return

        # 요청 본문 읽기
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        data = json.loads(body)

        model = data.get('model', 'gpt-4o-mini')
        is_stream = data.get('stream', False)

        # LuxiaCloud URL 구성
        luxia_url = f"{LUXIA_BASE.rstrip('/')}/chat/completions/{model}/create"

        import httpx
        try:
            if is_stream:
                # 스트리밍 응답
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Cache-Control', 'no-cache')
                self.send_header('Connection', 'keep-alive')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()

                with httpx.stream(
                    'POST', luxia_url,
                    headers={'apikey': LUXIA_API_KEY, 'Content-Type': 'application/json'},
                    json=data,
                    timeout=120.0
                ) as resp:
                    for line in resp.iter_lines():
                        if line:
                            self.wfile.write((line + '\n').encode())
                            self.wfile.flush()
            else:
                # 일반 응답
                resp = httpx.post(
                    luxia_url,
                    headers={'apikey': LUXIA_API_KEY, 'Content-Type': 'application/json'},
                    json=data,
                    timeout=120.0
                )

                self.send_response(resp.status_code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(resp.content)

        except Exception as e:
            error_body = json.dumps({"error": {"message": str(e), "type": "proxy_error"}})
            self.send_response(502)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(error_body.encode())

    def do_GET(self):
        # /v1/models 엔드포인트 (camel-ai가 호출할 수 있음)
        if '/models' in self.path:
            models_response = {
                "object": "list",
                "data": [
                    {"id": "gpt-4o-mini", "object": "model", "owned_by": "luxiacloud"},
                    {"id": "gpt-4o", "object": "model", "owned_by": "luxiacloud"},
                    {"id": "gpt-4o-mini-2024-07-18", "object": "model", "owned_by": "luxiacloud"},
                ]
            }
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(models_response).encode())
        else:
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "LuxiaCloud Proxy OK"}).encode())

    def do_OPTIONS(self):
        # CORS preflight
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        self.end_headers()

    def log_message(self, format, *args):
        model = "?"
        try:
            if hasattr(self, '_body_cache'):
                model = json.loads(self._body_cache).get('model', '?')
        except:
            pass
        sys.stderr.write(f"[Proxy] {args[0]}\n")


def main():
    server = HTTPServer(('0.0.0.0', PROXY_PORT), LuxiaProxyHandler)
    print(f"""
╔══════════════════════════════════════════════════╗
║  LuxiaCloud OpenAI 프록시 서버                    ║
║                                                  ║
║  프록시 주소: http://localhost:{PROXY_PORT}/v1       ║
║  LuxiaCloud:  {LUXIA_BASE[:40]}...  ║
║                                                  ║
║  OASIS 환경변수 설정:                              ║
║  OPENAI_API_KEY=proxy-key                        ║
║  OPENAI_API_BASE_URL=http://localhost:{PROXY_PORT}/v1║
╚══════════════════════════════════════════════════╝
""")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n프록시 서버 종료")
        server.server_close()


if __name__ == '__main__':
    main()
