"""
core/natural_language.py - v1.1 FINAL (감정 분석 + 유사도 검색)
- 사용자 발화에서 감정(긍정/부정/중립) 추출 (경량 키워드 기반)
- 종목명 매핑에 fuzzy match(Levenshtein 거리) 도입 (오타/약어 인식률 향상)
- 기존 의도 분류기(Intent Classifier)는 그대로 유지
"""

import re
from dataclasses import dataclass
from pathlib import Path

from core.logger import setup_logger

logger = setup_logger("nlp")


# ============================================================
# 데이터 클래스 (확장)
# ============================================================
@dataclass
class NLUResult:
    intent: str = "unknown"
    ticker: str | None = None
    stock_name: str | None = None
    confidence: float = 0.0
    raw_text: str = ""
    sentiment: str = "neutral"  # 🔥 v1.1: 긍정/부정/중립
    sentiment_score: float = 0.0  # -1.0 ~ 1.0


# ============================================================
# 종목명 → 티커 매핑 (확장 가능)
# ============================================================
STOCK_NAME_MAP = {
    "삼전": "005930",
    "삼성전자": "005930",
    "삼성": "005930",
    "하닉": "000660",
    "하이닉스": "000660",
    "SK하이닉스": "000660",
    "에스케이하이닉스": "000660",
    "현차": "005380",
    "현대차": "005380",
    "현대": "005380",
    "네이버": "035420",
    "Naver": "035420",
    "엘지": "066570",
    "LG": "066570",
    "엘지전자": "066570",
    "카카오": "035720",
    "셀트리온": "068270",
    "포스코": "005490",
    "SK": "034730",
    "KT": "030200",
    "기아": "000270",
    "기아차": "000270",
    "신한지주": "055550",
    "신한": "055550",
    "KB금융": "105560",
    "KB": "105560",
    "하나금융": "086790",
    "하나": "086790",
    "우리금융": "316140",
    "우리": "316140",
    "삼성바이오": "207940",
    "삼바": "207940",
    "LG화학": "051910",
    "엘지화학": "051910",
    "삼성SDI": "006400",
    "SK이노": "096770",
    "SK이노베이션": "096770",
}

# ============================================================
# 감정 분석 키워드 (v1.1)
# ============================================================
POSITIVE_KEYWORDS = [
    "좋아",
    "기대",
    "상승",
    "오르",
    "잘",
    "굳",
    "최고",
    "대박",
    "감사",
    "고마워",
    "신나",
    "즐거",
    "행복",
    "만족",
    "괜찮",
]
NEGATIVE_KEYWORDS = [
    "나빠",
    "하락",
    "내리",
    "떨어",
    "걱정",
    "불안",
    "짜증",
    "답답",
    "실망",
    "아쉽",
    "슬프",
    "화나",
    "스트레스",
    "힘들",
]
NEUTRAL_KEYWORDS = ["궁금", "알려줘", "뭐야", "어때", "어떻게"]


# ============================================================
# NaturalLanguageEngine (v1.1)
# ============================================================
class NaturalLanguageEngine:
    def __init__(self):
        self._intent_classifier = None
        self._vectorizer = None
        self._trained = False
        self._model_path = Path(__file__).parent.parent / "config" / "nlp_model.pkl"

        # 최초 1회 자동 로드/학습
        if not self.load():
            self.train()

    # ============================================================
    # 1. 훈련 데이터 (기존 유지)
    # ============================================================
    TRAINING_DATA = [
        (
            "status",
            [
                "현황",
                "오늘 장",
                "장 상황",
                "시장 상태",
                "국면",
                "시스템 상태",
                "상태 알려줘",
                "지금 상황",
                "장은 어떻게",
                "시장 분위기",
                "오늘 시장",
            ],
        ),
        (
            "signal",
            ["신호", "매수 신호", "매도 신호", "최근 신호", "오늘 신호", "신호 있어?", "매수 추천", "매도 추천"],
        ),
        ("analyze", ["삼전", "005930", "현대차", "분석", "알려줘", "봐줘", "지금 가격", "현재가", "주가"]),
    ]

    def _build_training_data(self):
        texts, labels = [], []
        for intent, examples in self.TRAINING_DATA:
            for ex in examples:
                texts.append(ex)
                labels.append(intent)
        return texts, labels

    def train(self):
        """의도 분류기 학습 (최초 1회)"""
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.linear_model import LogisticRegression
        except ImportError:
            logger.warning("⚠️ scikit-learn 미설치 → 의도 분류기 비활성화")
            return

        texts, labels = self._build_training_data()
        self._vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(1, 3))
        X = self._vectorizer.fit_transform(texts)
        self._intent_classifier = LogisticRegression(max_iter=1000, random_state=42)
        self._intent_classifier.fit(X, labels)
        self._trained = True
        logger.info(f"✅ NLU 의도 분류기 학습 완료 (샘플: {len(texts)}개)")

        try:
            import joblib

            self._model_path.parent.mkdir(parents=True, exist_ok=True)
            joblib.dump({"vectorizer": self._vectorizer, "classifier": self._intent_classifier}, self._model_path)
            logger.debug("✅ NLU 모델 저장 완료")
        except:
            logger.warning("⚠️ NLU 모델 저장 실패")

    def load(self):
        """저장된 모델 로드"""
        if not self._model_path.exists():
            return False
        try:
            import joblib

            data = joblib.load(self._model_path)
            self._vectorizer = data["vectorizer"]
            self._intent_classifier = data["classifier"]
            self._trained = True
            logger.info("✅ NLU 모델 로드 완료")
            return True
        except Exception as e:
            logger.warning(f"⚠️ NLU 모델 로드 실패: {e}, 재학습 필요")
            return False

    # ============================================================
    # 🔥 v1.1: 감정 분석 (경량 키워드 기반)
    # ============================================================
    def _analyze_sentiment(self, text: str) -> tuple[str, float]:
        """텍스트에서 감정 추출 (긍정/부정/중립)"""
        text_lower = text.lower()
        pos_score = sum(1 for kw in POSITIVE_KEYWORDS if kw in text_lower)
        neg_score = sum(1 for kw in NEGATIVE_KEYWORDS if kw in text_lower)
        neu_score = sum(1 for kw in NEUTRAL_KEYWORDS if kw in text_lower)

        total = pos_score + neg_score + neu_score
        if total == 0:
            return "neutral", 0.0

        # 점수 계산 (-1 ~ 1)
        score = (pos_score - neg_score) / total
        if score > 0.3:
            return "positive", min(1.0, score)
        elif score < -0.3:
            return "negative", max(-1.0, score)
        else:
            return "neutral", score

    # ============================================================
    # 🔥 v1.1: 유사도 검색 (fuzzy match)
    # ============================================================
    def _fuzzy_match_ticker(self, text: str, threshold: int = 70) -> str | None:
        """
        텍스트에서 종목명을 유사도 검색으로 찾음 (Levenshtein 거리 기반)
        Args:
            text: 사용자 입력
            threshold: 유사도 임계값 (0~100, 기본 70)
        Returns:
            매칭된 티커 또는 None
        """
        # 빠른 경로: 정확 매칭 먼저 시도
        for name, code in STOCK_NAME_MAP.items():
            if name in text:
                return code

        # fuzzy match 시도 (rapidfuzz 또는 difflib 사용)
        try:
            from rapidfuzz import fuzz, process

            best_match = process.extractOne(text, STOCK_NAME_MAP.keys(), scorer=fuzz.partial_ratio)
            if best_match and best_match[1] >= threshold:
                return STOCK_NAME_MAP[best_match[0]]
        except ImportError:
            try:
                # difflib 폴백
                import difflib

                matches = difflib.get_close_matches(text, STOCK_NAME_MAP.keys(), n=1, cutoff=threshold / 100)
                if matches:
                    return STOCK_NAME_MAP[matches[0]]
            except:
                pass
        return None

    # ============================================================
    # 🔥 v1.1: 티커 추출 (정규식 + fuzzy match)
    # ============================================================
    def _extract_ticker(self, text: str) -> str | None:
        """텍스트에서 티커 추출 (정규식 우선, fuzzy match 후보)"""
        # 1. 6자리 숫자 → 티커
        ticker_match = re.search(r"\b(\d{6})\b", text)
        if ticker_match:
            return ticker_match.group(1)

        # 2. 종목명 매핑 (fuzzy match 포함)
        fuzzy_result = self._fuzzy_match_ticker(text)
        if fuzzy_result:
            return fuzzy_result

        # 3. 마지막 6자리 숫자 (주식 코드로 의심)
        numbers = re.findall(r"\d{6}", text)
        if numbers:
            return numbers[-1]

        return None

    # ============================================================
    # 메인 파싱 (v1.1)
    # ============================================================
    def parse(self, text: str) -> NLUResult:
        """자연어 텍스트 분석 (v1.1 - 감정 + 유사도)"""
        text = text.strip()
        result = NLUResult(intent="unknown", raw_text=text)

        # 1. 감정 분석 (v1.1)
        sentiment, score = self._analyze_sentiment(text)
        result.sentiment = sentiment
        result.sentiment_score = score

        # 2. 티커 추출 (v1.1 - fuzzy match 적용)
        result.ticker = self._extract_ticker(text)
        if result.ticker:
            for name, code in STOCK_NAME_MAP.items():
                if code == result.ticker:
                    result.stock_name = name
                    break

        # 3. 의도 분류 (ML)
        if self._trained and self._intent_classifier and self._vectorizer:
            try:
                X = self._vectorizer.transform([text])
                probs = self._intent_classifier.predict_proba(X)[0]
                pred = self._intent_classifier.predict(X)[0]
                result.intent = pred
                result.confidence = max(probs)
                logger.debug(f"🧠 의도 분류: {pred} ({result.confidence:.2f})")

                if result.ticker and result.intent != "analyze":
                    if result.confidence < 0.7:
                        result.intent = "analyze"
                        result.confidence = 0.6
                return result
            except Exception as e:
                logger.debug(f"⚠️ 의도 분류 오류: {e}")

        # 4. Fallback: 규칙 기반
        if result.ticker:
            result.intent = "analyze"
            result.confidence = 0.7
        elif "신호" in text:
            result.intent = "signal"
            result.confidence = 0.6
        elif "장" in text or "상태" in text or "현황" in text:
            result.intent = "status"
            result.confidence = 0.6
        else:
            result.intent = "unknown"
            result.confidence = 0.0

        return result

    # ============================================================
    # 응답 톤 조정 헬퍼 (v1.1)
    # ============================================================
    def get_response_tone(self, sentiment: str) -> str:
        """감정에 따른 응답 톤 반환"""
        tone_map = {
            "positive": "😊 즐거운 마음으로",
            "negative": "🤔 차분하게",
            "neutral": "📊 객관적으로",
        }
        return tone_map.get(sentiment, "📊 객관적으로")


# 전역 인스턴스
nlp_engine = NaturalLanguageEngine()
