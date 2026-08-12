"""
data/stock_universe.py - v5.6.8 FINAL (Gemini 검증 완료)
- 하드코딩 FALLBACK_UNIVERSE 완전 삭제
- 동적 JSON 캐시(latest_universe_cache.json) 저장/복구
- csv.DictReader 기반 컬럼명 유연 파싱
- 2단계 검증 체인 (코드 6자리 + HTML 태그 없음 + 최소 100개)
- 모든 실패 시 RuntimeError 발생 (Fail-Fast)
"""

import csv
import json
import re
import requests
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass

from core.logger import setup_logger

logger = setup_logger("universe")

CSV_PATH = Path(__file__).parent / "krx_universe.csv"
CACHE_PATH = Path(__file__).parent / "latest_universe_cache.json"


@dataclass
class StockInfo:
    code: str
    name: str
    market: str
    listed_date: str
    delisted_date: Optional[str] = None
    is_active: bool = True


# ============================================================
# 1. 2단계 검증 체인
# ============================================================
def validate_universe(stock_dict: Dict[str, str]) -> Dict[str, str]:
    if not stock_dict:
        raise ValueError("❌ 검증 실패: 입력 데이터가 비어있습니다.")
    
    valid_universe = {}
    
    for code, name in stock_dict.items():
        if not (isinstance(code, str) and len(code) == 6 and code.isdigit()):
            logger.debug(f"⏭️ 코드 검증 실패: {code}")
            continue
        
        if '<' in name or '>' in name:
            logger.debug(f"⏭️ HTML 태그 발견: {name[:30]}...")
            continue
        
        if not name or not name.strip():
            logger.debug(f"⏭️ 빈 종목명: {code}")
            continue
        
        valid_universe[code] = name.strip()
    
    if len(valid_universe) < 100:
        raise ValueError(
            f"❌ 유니버스 데이터 검증 실패: 유효 종목 수 부족 ({len(valid_universe)}개, 최소 100개 필요)"
        )
    
    logger.info(f"✅ 검증 완료: {len(valid_universe)}개 종목 통과")
    return valid_universe


# ============================================================
# 2. 동적 JSON 캐시 저장/복구
# ============================================================
def save_cache(universe: Dict[str, str]) -> None:
    try:
        with open(CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(universe, f, ensure_ascii=False, indent=2)
        logger.info(f"✅ 캐시 저장 완료: {CACHE_PATH}")
    except Exception as e:
        logger.warning(f"⚠️ 캐시 저장 실패: {e}")


def load_cache() -> Optional[Dict[str, str]]:
    if not CACHE_PATH.exists():
        logger.info("ℹ️ 캐시 파일 없음")
        return None
    try:
        with open(CACHE_PATH, 'r', encoding='utf-8') as f:
            universe = json.load(f)
        logger.info(f"✅ 캐시 복구 완료: {len(universe)}개 종목")
        return universe
    except json.JSONDecodeError as e:
        logger.warning(f"⚠️ 캐시 JSON 파싱 오류: {e}")
        return None
    except Exception as e:
        logger.warning(f"⚠️ 캐시 복구 실패: {e}")
        return None


# ============================================================
# 3. CSV 파싱 (들여쓰기 오류 수정 완료)
# ============================================================
def parse_krx_csv(content: str) -> Dict[str, str]:
    # 🔥 BOM 제거 (들여쓰기 오류 수정)
    if content.startswith('\ufeff'):
        content = content[1:]
    
    # 🔥 reader는 BOM 여부와 관계없이 항상 정의됨 (수정 완료)
    reader = csv.DictReader(content.splitlines(), delimiter=',', quotechar='"')
    
    fieldnames = reader.fieldnames or []
    code_col = None
    name_col = None
    
    for col in fieldnames:
        col_clean = col.strip()
        if '종목코드' in col_clean or 'code' in col_clean.lower():
            code_col = col
        if '회사명' in col_clean or 'name' in col_clean.lower() or '종목명' in col_clean:
            name_col = col
    
    if code_col is None or name_col is None:
        raise ValueError(f"❌ 필수 컬럼을 찾을 수 없음: {fieldnames}")
    
    logger.info(f"📋 컬럼 매핑: code='{code_col}', name='{name_col}'")
    
    universe = {}
    for row in reader:
        code = row.get(code_col, '').strip()
        name = row.get(name_col, '').strip()
        if code and name:
            universe[code] = name
    
    return universe


# ============================================================
# 4. 메인 함수 (4단계 우선순위)
# ============================================================
def get_universe() -> Dict[str, str]:
    universe = {}
    
    # ---------- 1순위: CSV 파일 ----------
    if CSV_PATH.exists():
        try:
            with open(CSV_PATH, 'r', encoding='utf-8-sig') as f:
                content = f.read()
            universe = parse_krx_csv(content)
            if universe:
                universe = validate_universe(universe)
                logger.info(f"✅ CSV에서 {len(universe)}개 종목 로드 완료")
                save_cache(universe)
                return universe
        except Exception as e:
            logger.warning(f"⚠️ CSV 로드 실패: {e}")

    # ---------- 2순위: 인터넷 다운로드 ----------
    try:
        logger.info("📡 인터넷에서 KRX 종목 CSV 다운로드 중...")
        url = "https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13"
        response = requests.get(url, timeout=15)
        response.encoding = 'cp949'
        
        universe = parse_krx_csv(response.text)
        if universe:
            universe = validate_universe(universe)
            logger.info(f"✅ 인터넷에서 {len(universe)}개 종목 로드 완료")
            
            import pandas as pd
            df = pd.DataFrame(list(universe.items()), columns=['code', 'name'])
            df.to_csv(CSV_PATH, index=False, encoding='utf-8-sig')
            save_cache(universe)
            return universe
        else:
            raise ValueError("인터넷 다운로드 결과가 비어있음")
            
    except requests.exceptions.Timeout:
        logger.error("❌ 인터넷 다운로드 타임아웃")
    except requests.exceptions.ConnectionError:
        logger.error("❌ 인터넷 연결 오류")
    except Exception as e:
        logger.error(f"❌ 인터넷 다운로드 실패: {e}")

    # ---------- 3순위: 동적 로컬 캐시 ----------
    cache = load_cache()
    if cache:
        try:
            validated = validate_universe(cache)
            logger.info(f"📦 캐시에서 {len(validated)}개 종목 복구 완료 (Warning: 오래된 데이터일 수 있음)")
            return validated
        except Exception as e:
            logger.error(f"❌ 캐시 검증 실패: {e}")

    # ---------- 4순위: Fail-Fast ----------
    error_msg = (
        "❌ 종목 리스트를 로드할 수 없습니다.\n"
        "   - CSV 파일 확인 (data/krx_universe.csv)\n"
        "   - 인터넷 연결 확인\n"
        "   - 캐시 파일 확인 (data/latest_universe_cache.json)\n"
        "   시스템을 종료합니다."
    )
    logger.error(error_msg)
    raise RuntimeError(error_msg)


# ============================================================
# 5. StockUniverse 클래스
# ============================================================
class StockUniverse:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance
    
    def _init(self):
        self._stocks: Dict[str, StockInfo] = {}
        universe = get_universe()
        for code, name in universe.items():
            self._stocks[code] = StockInfo(code, name, "KOSPI", "2000-01-01")
        logger.info(f"StockUniverse 로드 완료: {len(self._stocks)}개 종목")
    
    def get_all(self) -> List[StockInfo]:
        return list(self._stocks.values())
    
    def get_active(self) -> List[StockInfo]:
        return [s for s in self._stocks.values() if s.is_active]
    
    def get_tier1(self, count: int = 500) -> List[StockInfo]:
        return self.get_active()[:count]
    
    def get_tier2(self, count: int = 400) -> List[StockInfo]:
        return self.get_active()[500:500+count]
    
    def get_tier3(self) -> List[StockInfo]:
        return self.get_active()[900:]


if __name__ == "__main__":
    try:
        universe = get_universe()
        print(f"📊 Universe 크기: {len(universe)}개 종목")
        for code, name in list(universe.items())[:10]:
            print(f"  • {code}: {name}")
    except RuntimeError as e:
        print(f"❌ 시스템 종료: {e}")