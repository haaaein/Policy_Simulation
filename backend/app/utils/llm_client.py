"""
LLM클라이언트래핑
통합사용OpenAI형식호출
지원 LuxiaCloud Bridge API (URL경로에 모델명 포함 + apikey 헤더 방식)  
"""

import json
import re
import httpx
from typing import Optional, Dict, Any, List
from openai import OpenAI

from ..config import Config


class LLMClient:
    """LLM클라이언트 — OpenAI 표준 및 LuxiaCloud Bridge 호환"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None
    ):
        self.api_key = api_key or Config.LLM_API_KEY
        self.base_url = base_url or Config.LLM_BASE_URL
        self.model = model or Config.LLM_MODEL_NAME

        if not self.api_key:
            raise ValueError("LLM_API_KEY 미설정")

        # LuxiaCloud Bridge 감지: base_url에 'luxiacloud' 또는 'bridge' 포함
        self._is_luxia = 'luxiacloud' in (self.base_url or '')

        if self._is_luxia:
            # LuxiaCloud: apikey 헤더 방식 + URL 경로에 모델 포함
            # base_url 예: https://bridge.luxiacloud.com/llm/openai
            # 실제 호출: {base_url}/chat/completions/{model}/create
            http_client = httpx.Client(
                headers={"apikey": self.api_key},
                timeout=120.0
            )
            self.client = OpenAI(
                api_key="luxia-placeholder",  # OpenAI SDK가 요구하지만 실제로는 헤더 사용
                base_url=self.base_url,
                http_client=http_client
            )
        else:
            # 표준 OpenAI 호환 API
            self.client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url
            )

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: Optional[Dict] = None
    ) -> str:
        """
        전송채팅요청

        Args:
            messages: 메시지목록
            temperature: 온도파라미터
            max_tokens: 최대token수
            response_format: 응답형식  

        Returns:
            모델응답텍스트  
        """
        if self._is_luxia:
            return self._chat_luxia(messages, temperature, max_tokens, response_format)

        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if response_format:
            kwargs["response_format"] = response_format

        response = self.client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content
        # 일부 모델(예: MiniMax M2.5)이 content에 포함하는 <think> 사고 내용, 제거 필요  
        content = re.sub(r'<think>[\s\S]*?</think>', '', content).strip()
        return content

    def _chat_luxia(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: Optional[Dict] = None
    ) -> str:
        """LuxiaCloud Bridge API 호출 — URL 경로에 모델명 포함"""
        import httpx as _httpx

        # URL 구성: {base_url}/chat/completions/{model}/create
        url = f"{self.base_url.rstrip('/')}/chat/completions/{self.model}/create"

        body = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if response_format:
            body["response_format"] = response_format

        resp = _httpx.post(
            url,
            headers={
                "apikey": self.api_key,
                "Content-Type": "application/json",
            },
            json=body,
            timeout=120.0,
        )
        resp.raise_for_status()
        data = resp.json()

        content = data["choices"][0]["message"]["content"]
        content = re.sub(r'<think>[\s\S]*?</think>', '', content).strip()
        return content
    
    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 4096
    ) -> Dict[str, Any]:
        """
        전송 채팅 요청 및 돌아가기 JSON  
        
        Args:
            messages: 메시지목록
            temperature: 온도파라미터
            max_tokens: 최대token수
            
        Returns:
            파싱후의JSON형  
        """
        response = self.chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"}
        )
        # 정리 markdown 대 코드 블록 마크업
        cleaned_response = response.strip()
        cleaned_response = re.sub(r'^```(?:json)?\s*\n?', '', cleaned_response, flags=re.IGNORECASE)
        cleaned_response = re.sub(r'\n?```\s*$', '', cleaned_response)
        cleaned_response = cleaned_response.strip()

        try:
            return json.loads(cleaned_response)
        except json.JSONDecodeError:
            raise ValueError(f"LLM돌아가기의JSON형식유효하지 않음: {cleaned_response}")

