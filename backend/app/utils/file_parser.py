"""
파일파싱도구
지원 PDF, Markdown, TXT 파일의 텍스트 추출
"""

import os
from pathlib import Path
from typing import List, Optional


def _read_text_with_fallback(file_path: str) -> str:
    """
    텍스트 파일을 읽고, UTF-8 실패 시 자동 인코딩 탐지.
    
    채택다단계 폴백 전략：
    1. 먼저 시도 UTF-8 디코딩
    2. 사용 charset_normalizer 감지인코딩
    3. 회귀 chardet 감지 인코딩
    4. 최종 사용 UTF-8 + errors='replace' 보완
    
    Args:
        file_path: 파일경로
        
    Returns:
        디코딩후의텍스트내용
    """
    data = Path(file_path).read_bytes()
    
    # 먼저 시도 UTF-8
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        pass
    
    # 시도사용 charset_normalizer 감지인코딩
    encoding = None
    try:
        from charset_normalizer import from_bytes
        best = from_bytes(data).best()
        if best and best.encoding:
            encoding = best.encoding
    except Exception:
        pass
    
    # 회귀 chardet
    if not encoding:
        try:
            import chardet
            result = chardet.detect(data)
            encoding = result.get('encoding') if result else None
        except Exception:
            pass
    
    # 최종 보완: 사용 UTF-8 + replace
    if not encoding:
        encoding = 'utf-8'
    
    return data.decode(encoding, errors='replace')


class FileParser:
    """파일 파서"""
    
    SUPPORTED_EXTENSIONS = {'.pdf', '.md', '.markdown', '.txt'}
    
    @classmethod
    def extract_text(cls, file_path: str) -> str:
        """
        부터파일중추출텍스트
        
        Args:
            file_path: 파일경로
            
        Returns:
            추출의텍스트내용
        """
        path = Path(file_path)
        
        if not path.exists():
            raise FileNotFoundError(f"파일이 존재하지 않음: {file_path}")
        
        suffix = path.suffix.lower()
        
        if suffix not in cls.SUPPORTED_EXTENSIONS:
            raise ValueError(f"지원하지 않음의파일 형식: {suffix}")
        
        if suffix == '.pdf':
            return cls._extract_from_pdf(file_path)
        elif suffix in {'.md', '.markdown'}:
            return cls._extract_from_md(file_path)
        elif suffix == '.txt':
            return cls._extract_from_txt(file_path)
        
        raise ValueError(f"처리할 수 없는 파일 형식: {suffix}")
    
    @staticmethod
    def _extract_from_pdf(file_path: str) -> str:
        """부터PDF추출텍스트"""
        try:
            import fitz  # PyMuPDF
        except ImportError:
            raise ImportError("PyMuPDF를 설치해야 합니다: pip install PyMuPDF")
        
        text_parts = []
        with fitz.open(file_path) as doc:
            for page in doc:
                text = page.get_text()
                if text.strip():
                    text_parts.append(text)
        
        return "\n\n".join(text_parts)
    
    @staticmethod
    def _extract_from_md(file_path: str) -> str:
        """부터 Markdown 추출 텍스트, 지원 자동 인코딩 감지"""
        return _read_text_with_fallback(file_path)
    
    @staticmethod
    def _extract_from_txt(file_path: str) -> str:
        """부터 TXT 추출 텍스트, 지원 자동 인코딩 감지"""
        return _read_text_with_fallback(file_path)
    
    @classmethod
    def extract_from_multiple(cls, file_paths: List[str]) -> str:
        """
        여러 개 파일에서 텍스트 추출 및 병합
        
        Args:
            file_paths: 파일경로목록
            
        Returns:
            병합 후의 텍스트
        """
        all_texts = []
        
        for i, file_path in enumerate(file_paths, 1):
            try:
                text = cls.extract_text(file_path)
                filename = Path(file_path).name
                all_texts.append(f"=== 문서 {i}: {filename} ===\n{text}")
            except Exception as e:
                all_texts.append(f"=== 문서 {i}: {file_path} (추출실패: {str(e)}) ===")
        
        return "\n\n".join(all_texts)


def split_text_into_chunks(
    text: str, 
    chunk_size: int = 500, 
    overlap: int = 50
) -> List[str]:
    """
    텍스트를 작은 블록으로 분할
    
    Args:
        text: 원본 텍스트
        chunk_size: 각 블록의 문자 수
        overlap: 오버랩 문자 수
        
    Returns:
        텍스트 블록 목록
    """
    if len(text) <= chunk_size:
        return [text] if text.strip() else []
    
    chunks = []
    start = 0
    
    while start < len(text):
        end = start + chunk_size
        
        # 시도에서 문장 경계에서 분할
        if end < len(text):
            # 최근의 문장 종료 기호 찾기
            for sep in ['。', '！', '？', '.\n', '!\n', '?\n', '\n\n', '. ', '! ', '? ']:
                last_sep = text[start:end].rfind(sep)
                if last_sep != -1 and last_sep > chunk_size * 0.3:
                    end = start + last_sep + len(sep)
                    break
        
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        
        # 다음 블록부터 오버랩 위치 시작
        start = end - overlap if end < len(text) else len(text)
    
    return chunks

