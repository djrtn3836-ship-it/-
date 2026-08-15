"""
core/sentiment_analyzer.py - v1.0 (한국어 뉴스 감성 분석기)
- HuggingFace Transformers 기반 (KoBERT/KcELECTRA 호환)
- 긍정/부정 점수를 -1 ~ 1 사이로 반환
- 네트워크/모델 로드 실패 시 키워드 기반 Fallback
"""

import asyncio
import re
from typing import List, Dict, Optional, Tuple
from core.logger import setup_logger

logger = setup_logger("sentiment")

# 긍정/부정 키워드 사전 (Fallback용)
POSITIVE_KEYWORDS = [
    '상승', '급등', '강세', '호재', '돌파', '신고가', '목표가 상향', '매수', 
    '수익', '성장', '기대', '호조', '개선', '확대', '증가', '선전', '희망',
    '긍정', '협력', '계약', '수주', '실적호조', '어닝서프라이즈'
]
NEGATIVE_KEYWORDS = [
    '하락', '급락', '약세', '악재', '이탈', '신저가', '목표가 하향', '매도',
    '손실', '둔화', '우려', '부진', '악화', '축소', '감소', '부정',
    '불확실', '리스크', '경고', '조정', '하향', '적자', '어닝쇼크'
]

class SentimentAnalyzer:
    def __init__(self):
        self._model = None
        self._tokenizer = None
        self._use_transformers = False
        self._load_model()

    def _load_model(self):
        """Transformers 모델 로드 (비동기 아님, 초기화 시 한 번 실행)"""
        try:
            from transformers import AutoTokenizer, AutoModelForSequenceClassification
            import torch
            
            # 다국어 감성 분석 모델 (한국어 지원)
            model_name = "nlptown/bert-base-multilingual-uncased-sentiment"
            
            logger.info(f"🧠 감성 분석 모델 로딩 중: {model_name} (첫 실행 시 다운로드 필요)")
            self._tokenizer = AutoTokenizer.from_pretrained(model_name)
            self._model = AutoModelForSequenceClassification.from_pretrained(model_name)
            self._use_transformers = True
            logger.info("✅ 감성 분석 모델 로드 완료")
        except ImportError:
            logger.warning("⚠️ transformers 또는 torch 미설치 → 키워드 기반 Fallback 모드로 전환")
            self._use_transformers = False
        except Exception as e:
            logger.warning(f"⚠️ 모델 로드 실패 ({e}) → 키워드 기반 Fallback 모드로 전환")
            self._use_transformers = False

    async def analyze(self, texts: List[str]) -> float:
        """
        뉴스 텍스트 리스트를 받아 평균 감성 점수 반환 (-1 ~ 1)
        """
        if not texts:
            return 0.0

        if self._use_transformers:
            return await self._analyze_transformers(texts)
        else:
            return self._analyze_keyword(texts)

    async def _analyze_transformers(self, texts: List[str]) -> float:
        """HuggingFace Transformers 기반 분석"""
        try:
            import torch
            # 512 토큰 제한을 위해 텍스트 자르기
            truncated = [t[:500] for t in texts]
            
            # 배치 추론
            inputs = self._tokenizer(
                truncated, 
                return_tensors="pt", 
                padding=True, 
                truncation=True, 
                max_length=512
            )
            
            with torch.no_grad():
                outputs = self._model(**inputs)
                logits = outputs.logits
                # sentiment: 1 ~ 5 (별점), 점수 변환: (rating - 3) / 2 → -1 ~ 1
                probabilities = torch.softmax(logits, dim=-1)
                # 가중 평균: 1*prob1 + 2*prob2 + ... + 5*prob5
                weighted_sum = torch.sum(probabilities * torch.tensor([1, 2, 3, 4, 5]), dim=1)
                scores = (weighted_sum - 3) / 2  # -1 ~ 1
                avg_score = scores.mean().item()
                return max(-1.0, min(1.0, avg_score))
        except Exception as e:
            logger.warning(f"⚠️ Transformers 분석 실패 ({e}) → Fallback")
            return self._analyze_keyword(texts)

    def _analyze_keyword(self, texts: List[str]) -> float:
        """키워드 기반 Fallback 분석 (속도 빠름)"""
        total_score = 0.0
        count = 0
        for text in texts:
            # 대소문자 무시, 한글/영문 모두 처리
            text_lower = text.lower()
            pos_score = sum(1 for kw in POSITIVE_KEYWORDS if kw in text_lower)
            neg_score = sum(1 for kw in NEGATIVE_KEYWORDS if kw in text_lower)
            if pos_score > 0 or neg_score > 0:
                score = (pos_score - neg_score) / (pos_score + neg_score + 1)  # -1 ~ 1 정규화
                total_score += score
                count += 1
        return total_score / count if count > 0 else 0.0

    async def analyze_single(self, text: str) -> float:
        """단일 텍스트 감성 분석"""
        return await self.analyze([text])

# 전역 싱글톤
sentiment_analyzer = SentimentAnalyzer()