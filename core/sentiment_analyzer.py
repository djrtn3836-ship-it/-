"""
core/sentiment_analyzer.py - v1.1 (지연 로딩 적용)
"""

from core.logger import setup_logger

logger = setup_logger("sentiment")

POSITIVE_KEYWORDS = [
    "상승",
    "급등",
    "강세",
    "호재",
    "돌파",
    "신고가",
    "목표가 상향",
    "매수",
    "수익",
    "성장",
    "기대",
    "호조",
    "개선",
    "확대",
    "증가",
    "선전",
    "희망",
    "긍정",
    "협력",
    "계약",
    "수주",
    "실적호조",
    "어닝서프라이즈",
]
NEGATIVE_KEYWORDS = [
    "하락",
    "급락",
    "약세",
    "악재",
    "이탈",
    "신저가",
    "목표가 하향",
    "매도",
    "손실",
    "둔화",
    "우려",
    "부진",
    "악화",
    "축소",
    "감소",
    "부정",
    "불확실",
    "리스크",
    "경고",
    "조정",
    "하향",
    "적자",
    "어닝쇼크",
]


class SentimentAnalyzer:
    def __init__(self):
        self._model = None
        self._tokenizer = None
        self._use_transformers = False
        self._model_loaded = False  # 🔥 R-03 해결: 지연 로드 플래그

    def _ensure_model_loaded(self):
        """최초 호출 시에만 모델 로드 (시작 지연 방지)"""
        if self._model_loaded:
            return
        try:
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            model_name = "nlptown/bert-base-multilingual-uncased-sentiment"
            logger.info("🧠 감성 분석 모델 로딩 중 (최초 1회, 이후 캐시됨)...")
            self._tokenizer = AutoTokenizer.from_pretrained(model_name)
            self._model = AutoModelForSequenceClassification.from_pretrained(model_name)
            self._use_transformers = True
            logger.info("✅ 감성 분석 모델 로드 완료")
        except ImportError:
            logger.warning("⚠️ transformers/torch 미설치 → 키워드 기반 모드")
            self._use_transformers = False
        except Exception as e:
            logger.warning(f"⚠️ 모델 로드 실패 ({e}) → 키워드 기반 모드")
            self._use_transformers = False
        finally:
            self._model_loaded = True

    async def analyze(self, texts: list[str]) -> float:
        if not texts:
            return 0.0
        self._ensure_model_loaded()  # 🔥 최초 호출 시점에 로드

        if self._use_transformers:
            return await self._analyze_transformers(texts)
        else:
            return self._analyze_keyword(texts)

    async def _analyze_transformers(self, texts: list[str]) -> float:
        try:
            import torch

            inputs = self._tokenizer(
                [t[:500] for t in texts], return_tensors="pt", padding=True, truncation=True, max_length=512
            )
            with torch.no_grad():
                outputs = self._model(**inputs)
                probs = torch.softmax(outputs.logits, dim=-1)
                weighted_sum = torch.sum(probs * torch.tensor([1, 2, 3, 4, 5]), dim=1)
                scores = (weighted_sum - 3) / 2
                return max(-1.0, min(1.0, scores.mean().item()))
        except Exception as e:
            logger.warning(f"⚠️ Transformers 분석 실패: {e}")
            return self._analyze_keyword(texts)

    def _analyze_keyword(self, texts: list[str]) -> float:
        total_score = 0.0
        count = 0
        for text in texts:
            text_lower = text.lower()
            pos = sum(1 for kw in POSITIVE_KEYWORDS if kw in text_lower)
            neg = sum(1 for kw in NEGATIVE_KEYWORDS if kw in text_lower)
            if pos > 0 or neg > 0:
                total_score += (pos - neg) / (pos + neg + 1)
                count += 1
        return total_score / count if count > 0 else 0.0

    async def analyze_single(self, text: str) -> float:
        return await self.analyze([text])


# 전역 인스턴스 (이제 시작 시 블로킹 없음)
sentiment_analyzer = SentimentAnalyzer()
