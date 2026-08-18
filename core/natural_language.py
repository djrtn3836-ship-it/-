"""
core/natural_language.py - 경량 자연어 이해 엔진 (오프라인, 무료)
- scikit-learn 기반 의도 분류기 (Intent Classifier)
- 개체명 인식 (티커/종목명 추출)
- "삼전 지금 몇이야?" → intent: "analyze", ticker: "005930"
- "오늘 장 어떻게 돼?" → intent: "status"
- 모델 캐싱 (config/nlp_model.pkl)로 재시작 시 빠르게 로드
"""

import os
import re
import pickle
from pathlib import Path
from typing import Optional, List, Dict
from dataclasses import dataclass

from core.logger import setup_logger

logger = setup_logger("nlp")

# ============================================================
# 데이터 클래스 (🔥 수정: 기본값 추가)
# ============================================================
@dataclass
class NLUResult:
    intent: str = "unknown"      # 기본값 추가 (오류 방지)
    ticker: Optional[str] = None
    stock_name: Optional[str] = None
    confidence: float = 0.0
    raw_text: str = ""

# ============================================================
# 종목명 → 티커 매핑 (확장 가능)
# ============================================================
STOCK_NAME_MAP = {
    "삼전": "005930", "삼성전자": "005930",
    "하닉": "000660", "하이닉스": "000660", "SK하이닉스": "000660", "에스케이하이닉스": "000660",
    "현차": "005380", "현대차": "005380",
    "네이버": "035420", "Naver": "035420",
    "엘지": "066570", "LG": "066570", "엘지전자": "066570",
    "카카오": "035720",
    "셀트리온": "068270",
    "포스코": "005490",
    "SK": "034730",
    "KT": "030200",
}


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
    # 1. 훈련 데이터
    # ============================================================
    TRAINING_DATA = [
        ("status", ["현황", "오늘 장", "장 상황", "시장 상태", "국면", "시스템 상태", "상태 알려줘", "지금 상황", "장은 어떻게",
                    "시장 분위기", "오늘 시장"]),
        ("signal", ["신호", "매수 신호", "매도 신호", "최근 신호", "오늘 신호", "신호 있어?", "매수 추천", "매도 추천"]),
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
            logger.warning("⚠️ scikit-learn 미설치 → 의도 분류기 비활성화 (pip install scikit-learn)")
            return

        texts, labels = self._build_training_data()
        self._vectorizer = TfidfVectorizer(analyzer='char', ngram_range=(1, 3))
        X = self._vectorizer.fit_transform(texts)
        self._intent_classifier = LogisticRegression(max_iter=1000, random_state=42)
        self._intent_classifier.fit(X, labels)
        self._trained = True
        logger.info(f"✅ NLU 의도 분류기 학습 완료 (샘플: {len(texts)}개)")

        # 모델 저장
        try:
            import joblib
            self._model_path.parent.mkdir(parents=True, exist_ok=True)
            joblib.dump({"vectorizer": self._vectorizer, "classifier": self._intent_classifier}, self._model_path)
            logger.debug("✅ NLU 모델 저장 완료")
        except:
            logger.warning("⚠️ NLU 모델 저장 실패 (joblib 미설치?)")

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

    def _extract_ticker(self, text: str) -> Optional[str]:
        """텍스트에서 티커 또는 종목명 추출"""
        # 1. 6자리 숫자 → 티커
        ticker_match = re.search(r'\b(\d{6})\b', text)
        if ticker_match:
            return ticker_match.group(1)

        # 2. 종목명 매핑 (전체 텍스트에서)
        for name, code in STOCK_NAME_MAP.items():
            if name in text:
                return code

        # 3. 마지막 6자리 숫자 (주식 코드로 의심)
        numbers = re.findall(r'\d{6}', text)
        if numbers:
            return numbers[-1]

        return None

    def parse(self, text: str) -> NLUResult:
        """자연어 텍스트 분석"""
        text = text.strip()
        # 🔥 수정: 기본 intent="unknown"으로 객체 생성
        result = NLUResult(intent="unknown", raw_text=text)

        # 1. 티커 추출 (항상 우선)
        result.ticker = self._extract_ticker(text)
        if result.ticker:
            # 종목명 찾기 (역매핑)
            for name, code in STOCK_NAME_MAP.items():
                if code == result.ticker:
                    result.stock_name = name
                    break

        # 2. 의도 분류 (ML)
        if self._trained and self._intent_classifier and self._vectorizer:
            try:
                X = self._vectorizer.transform([text])
                probs = self._intent_classifier.predict_proba(X)[0]
                pred = self._intent_classifier.predict(X)[0]
                result.intent = pred
                result.confidence = max(probs)
                logger.debug(f"🧠 의도 분류: {pred} ({result.confidence:.2f})")

                # 티커가 있고 의도가 "analyze" 아니면 강제로 analyze로 변경
                if result.ticker and result.intent != "analyze":
                    if result.confidence < 0.7:
                        result.intent = "analyze"
                        result.confidence = 0.6
                return result
            except Exception as e:
                logger.debug(f"⚠️ 의도 분류 오류: {e}")

        # 3. Fallback: 규칙 기반
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


# 전역 인스턴스
nlp_engine = NaturalLanguageEngine()