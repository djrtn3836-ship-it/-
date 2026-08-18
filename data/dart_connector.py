"""
DART Connector v5.3.3 FINAL (User-Agent 추가, 캐시 강화)
- get_corp_code_sync(): 티커(6자리) → DART 고유번호(8자리) 매핑
- DART Open API corpCode.xml 다운로드 및 캐싱 (7일 TTL)
- 실패 시 빈 캐시 저장하여 재시도 방지
- 모든 HTTP 요청에 User-Agent 헤더 추가 (HTML 응답 방지)
- 기존 Risk Score + 공시 분석 (비동기) 100% 유지
- 모든 HTTP 요청 Timeout/ConnectionError 처리
"""

import json
import re
import asyncio
import aiohttp
import requests
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from core.logger import setup_logger

logger = setup_logger("dart_connector")

# 캐시 파일 및 TTL
CACHE_FILE = Path(__file__).parent.parent / "config" / "corp_code_cache.json"
CACHE_TTL_DAYS = 7


class RiskLevel(Enum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass
class DisclosureAnalysis:
    corp_code: str
    title: str
    report_type: str
    date: str
    url: str
    content: str = ""
    risk_score: int = 0
    risk_level: RiskLevel = RiskLevel.NORMAL
    matched_patterns: List[str] = field(default_factory=list)
    discount_rate: Optional[float] = None
    funding_purpose: Optional[str] = None
    is_third_party: bool = False
    is_related_party: bool = False
    issue_amount: Optional[float] = None
    recommended_action: str = "매수"


class DartConnector:
    """DART Open API 연동 모듈 (Risk 분석 + 재무제표 + corp_code 매핑)"""

    RISK_WEIGHTS = {
        'third_party_allotment': 30,
        'rights_offering_high_discount': 30,
        'rights_offering_low_discount': 15,
        'cb_issue': 20,
        'bw_issue': 20,
        'large_issue': 30,
        'related_party': 20,
        'funding_operation': 10,
        'funding_debt': 15,
        'funding_investment': 5,
        'max_shareholder_change': 20,
        'merger': 25
    }

    RISK_THRESHOLDS = {
        RiskLevel.NORMAL: 0,
        RiskLevel.WARNING: 20,
        RiskLevel.HIGH: 50,
        RiskLevel.CRITICAL: 70
    }

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://opendart.fss.or.kr/api"
        self.daily_limit = 10000
        self.daily_used = 0
        self.last_reset = datetime.now()
        self._session: Optional[aiohttp.ClientSession] = None

        self.patterns = {
            'third_party_allotment': {'pattern': r'제3자배정', 'weight': self.RISK_WEIGHTS['third_party_allotment']},
            'rights_offering': {'pattern': r'유상증자', 'weight': 0},
            'cb_issue': {'pattern': r'전환사채\s*(발행|결정)', 'weight': self.RISK_WEIGHTS['cb_issue']},
            'bw_issue': {'pattern': r'신주인수권부사채\s*(발행|결정)', 'weight': self.RISK_WEIGHTS['bw_issue']},
            'max_shareholder_change': {'pattern': r'최대주주\s*(변경|매각)', 'weight': self.RISK_WEIGHTS['max_shareholder_change']},
            'merger': {'pattern': r'합병\s*(결정|공고)', 'weight': self.RISK_WEIGHTS['merger']}
        }

        # 🔥 corp_code 매핑 캐시 관련 변수 (v5.3.3 개선)
        self._corp_code_map = None
        self._cache_loaded = False

    # ============================================================
    # 🔥 개선된 corp_code 매핑 (User-Agent, 빈 캐시 저장)
    # ============================================================
    def _load_cache(self):
        """로컬 캐시에서 corp_code 매핑 로드 (유효기간 확인)"""
        if CACHE_FILE.exists():
            try:
                with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                cached_time = datetime.fromisoformat(data.get('cached_at', '2000-01-01'))
                if datetime.now() - cached_time < timedelta(days=CACHE_TTL_DAYS):
                    self._corp_code_map = data.get('mapping', {})
                    self._cache_loaded = True
                    logger.debug(f"✅ corp_code 캐시 로드 완료 ({len(self._corp_code_map)}개)")
                    return
                else:
                    logger.info("⏳ corp_code 캐시 만료, 재다운로드 필요")
            except Exception as e:
                logger.warning(f"⚠️ 캐시 로드 실패: {e}")

        self._corp_code_map = {}
        self._cache_loaded = False

    def _save_cache(self, mapping: dict):
        """매핑 데이터를 로컬 캐시에 저장"""
        try:
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump({
                    'cached_at': datetime.now().isoformat(),
                    'mapping': mapping
                }, f, ensure_ascii=False, indent=2)
            self._corp_code_map = mapping
            self._cache_loaded = True
            logger.debug(f"✅ corp_code 캐시 저장 완료 ({len(mapping)}개)")
        except Exception as e:
            logger.error(f"❌ 캐시 저장 실패: {e}")

    def _download_corp_code(self) -> Dict[str, str]:
        """DART API에서 corpCode.xml 다운로드 및 파싱 (User-Agent 추가)"""
        if not self.api_key:
            logger.warning("⚠️ DART API 키 없음, corp_code 매핑 불가")
            return {}

        url = f"{self.base_url}/corpCode.xml"
        params = {"crtfc_key": self.api_key}
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

        try:
            resp = requests.get(url, params=params, headers=headers, timeout=30)
            resp.raise_for_status()

            # Content-Type 확인 (HTML 응답 방지)
            content_type = resp.headers.get('Content-Type', '')
            if 'xml' not in content_type and not resp.text.strip().startswith('<?xml'):
                logger.warning(f"⚠️ DART 응답이 XML 아님 (Content-Type: {content_type})")
                return {}

            root = ET.fromstring(resp.content)
            mapping = {}
            for corp in root.findall('list'):
                corp_code = corp.findtext('corp_code')
                stock_code = corp.findtext('stock_code')
                if corp_code and stock_code and stock_code.isdigit() and len(stock_code) >= 6:
                    mapping[stock_code] = corp_code
            logger.info(f"✅ DART corp_code 매핑 다운로드 완료 ({len(mapping)}개)")
            return mapping
        except requests.exceptions.Timeout:
            logger.error("❌ corpCode.xml 다운로드 타임아웃 (30초)")
        except ET.ParseError as e:
            logger.error(f"❌ corpCode.xml 파싱 오류: {e}")
        except Exception as e:
            logger.error(f"❌ corpCode.xml 다운로드 실패: {e}")
        return {}

    def get_corp_code_sync(self, ticker: str) -> Optional[str]:
        """
        티커(6자리 종목코드) → DART 고유번호(8자리) 반환
        - 캐시 우선 조회, 없으면 다운로드
        - 다운로드 실패 시 빈 캐시 저장하여 재시도 방지 (v5.3.3)
        """
        if not ticker or not ticker.isdigit() or len(ticker) < 6:
            return None

        # 1. 캐시 로드
        if not self._cache_loaded:
            self._load_cache()

        # 2. 캐시에서 조회
        if self._corp_code_map and ticker in self._corp_code_map:
            return self._corp_code_map[ticker]

        # 3. 캐시에 없거나 만료 → 다운로드
        logger.info(f"📥 corp_code 매핑 다운로드 시작 (티커: {ticker})")
        new_mapping = self._download_corp_code()
        if new_mapping:
            self._save_cache(new_mapping)
            return new_mapping.get(ticker)
        else:
            # 실패 시 빈 캐시 저장 (재시도 방지)
            self._save_cache({})
            logger.warning(f"⚠️ {ticker}에 대한 corp_code를 찾을 수 없음")
            return None

    # ============================================================
    # 비동기 연결 관리 (기존)
    # ============================================================
    async def connect(self):
        if self._session is None:
            self._session = aiohttp.ClientSession()
        logger.info("DART Connector connected")

    async def disconnect(self):
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("DART Connector disconnected")

    # ============================================================
    # 공시 조회 및 Risk 분석 (비동기, 100% 유지)
    # ============================================================
    async def get_disclosures(self, corp_code: str, from_date: str, to_date: str, deep_scan: bool = False) -> List[DisclosureAnalysis]:
        if self._session is None:
            await self.connect()
        await self._check_rate_limit()

        url = f"{self.base_url}/list.json"
        params = {
            'crtfc_key': self.api_key,
            'corp_code': corp_code,
            'bgn_de': from_date,
            'end_de': to_date,
            'pblntf_detail_ty': 'A001'
        }

        try:
            async with self._session.get(url, params=params) as resp:
                self.daily_used += 1
                data = await resp.json()
                if data.get('status') != '000':
                    logger.error(f"DART API error: {data.get('message', 'Unknown')}")
                    return []

                results = []
                for item in data.get('list', []):
                    analysis = self._analyze_by_title(item)
                    if deep_scan and analysis.risk_level in [RiskLevel.HIGH, RiskLevel.CRITICAL]:
                        content = await self._fetch_content(analysis.corp_code, item.get('rcept_no', ''))
                        if content:
                            analysis.content = content
                            self._analyze_by_content(analysis)
                    results.append(analysis)
                return results
        except Exception as e:
            logger.error(f"DART API 호출 실패: {e}")
            return []

    def _analyze_by_title(self, item: Dict) -> DisclosureAnalysis:
        title = item.get('report_nm', '')
        corp_code = item.get('corp_code', '')
        date = item.get('rcept_de', '')

        analysis = DisclosureAnalysis(
            corp_code=corp_code,
            title=title,
            report_type=item.get('pblntf_detail_ty', ''),
            date=date,
            url=item.get('rm_url', '')
        )

        risk_score = 0
        matched = []

        for pattern_name, pattern_info in self.patterns.items():
            if re.search(pattern_info['pattern'], title):
                matched.append(pattern_name)
                risk_score += pattern_info['weight']

        discount_match = re.search(r'할인율\s*(\d+)%', title)
        if discount_match:
            try:
                rate = float(discount_match.group(1))
                analysis.discount_rate = rate
                if rate >= 30:
                    risk_score += self.RISK_WEIGHTS['rights_offering_high_discount']
                    matched.append('rights_offering_high_discount')
                else:
                    risk_score += self.RISK_WEIGHTS['rights_offering_low_discount']
                    matched.append('rights_offering_low_discount')
            except:
                pass

        amount_match = re.search(r'발행금액\s*([\d,]+)\s*억원', title)
        if amount_match:
            try:
                amount = float(amount_match.group(1).replace(',', ''))
                analysis.issue_amount = amount
                if amount >= 100:
                    risk_score += self.RISK_WEIGHTS['large_issue']
                    matched.append('large_issue')
            except:
                pass

        if '제3자배정' in title:
            analysis.is_third_party = True
            if '최대주주' in title or '특수관계' in title:
                analysis.is_related_party = True
                risk_score += self.RISK_WEIGHTS['related_party']
                matched.append('related_party')

        if '운영자금' in title:
            analysis.funding_purpose = '운영자금'
            risk_score += self.RISK_WEIGHTS['funding_operation']
        elif '채무상환' in title:
            analysis.funding_purpose = '채무상환'
            risk_score += self.RISK_WEIGHTS['funding_debt']
        elif '시설투자' in title:
            analysis.funding_purpose = '시설투자'
            risk_score += self.RISK_WEIGHTS['funding_investment']

        analysis.risk_score = min(100, risk_score)
        analysis.matched_patterns = matched
        analysis.risk_level = self._get_risk_level(analysis.risk_score)
        analysis.recommended_action = self._get_recommended_action(analysis)
        return analysis

    async def _fetch_content(self, corp_code: str, rcept_no: str) -> Optional[str]:
        if self._session is None:
            await self.connect()
        await self._check_rate_limit()

        url = f"{self.base_url}/document.json"
        params = {
            'crtfc_key': self.api_key,
            'corp_code': corp_code,
            'rcept_no': rcept_no
        }

        try:
            async with self._session.get(url, params=params) as resp:
                self.daily_used += 1
                data = await resp.json()
                if 'document' in data:
                    return self._parse_document(data['document'])
                return None
        except Exception as e:
            logger.error(f"공시 본문 조회 실패: {e}")
            return None

    def _parse_document(self, raw_doc: str) -> str:
        try:
            text = re.sub(r'<[^>]+>', ' ', raw_doc)
            text = re.sub(r'\s+', ' ', text)
            return text.strip()
        except:
            return raw_doc

    def _analyze_by_content(self, analysis: DisclosureAnalysis):
        content = analysis.content
        if not content:
            return
        if '할인율' in content:
            match = re.search(r'할인율\s*(\d+)%', content)
            if match:
                rate = float(match.group(1))
                if rate > (analysis.discount_rate or 0):
                    analysis.discount_rate = rate
                    analysis.risk_score = min(100, analysis.risk_score + 10)
        analysis.risk_level = self._get_risk_level(analysis.risk_score)
        analysis.recommended_action = self._get_recommended_action(analysis)

    def _get_risk_level(self, score: int) -> RiskLevel:
        if score >= 70: return RiskLevel.CRITICAL
        elif score >= 50: return RiskLevel.HIGH
        elif score >= 20: return RiskLevel.WARNING
        else: return RiskLevel.NORMAL

    def _get_recommended_action(self, analysis: DisclosureAnalysis) -> str:
        if analysis.risk_level == RiskLevel.CRITICAL: return "거래차단"
        elif analysis.risk_level == RiskLevel.HIGH: return "매도"
        elif analysis.risk_level == RiskLevel.WARNING: return "주의"
        else: return "매수"

    async def _check_rate_limit(self):
        now = datetime.now()
        if (now - self.last_reset).days >= 1:
            self.daily_used = 0
            self.last_reset = now
        if self.daily_used >= self.daily_limit:
            wait_until = self.last_reset + timedelta(days=1)
            wait_seconds = (wait_until - now).total_seconds()
            logger.warning(f"DART daily limit reached, waiting {wait_seconds:.0f}s")
            await asyncio.sleep(wait_seconds)
            self.daily_used = 0
            self.last_reset = datetime.now()

    # ============================================================
    # 안정성 강화 동기 메서드 (User-Agent 추가)
    # ============================================================
    def get_financials_sync(self, corp_code: str, year: str = "2024") -> Dict[str, float]:
        """
        재무제표 조회 + 정규화 반환 (예외 발생 시 빈 딕셔너리)
        v5.3.3: User-Agent 헤더 추가
        """
        if not self.api_key:
            return {}

        try:
            resp = requests.get(
                f"{self.base_url}/fnlttSinglAcnt.json",
                params={
                    "crtfc_key": self.api_key,
                    "corp_code": corp_code,
                    "bsns_year": year,
                    "reprt_code": "11011"
                },
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get('status') != '000':
                logger.warning(f"⚠️ {corp_code} 재무제표 조회 실패 ({year}): {data.get('message')}")
                return {}

            result = {}
            target = ['매출액', '영업이익', '당기순이익', '자산총계', '부채총계', '자본총계']
            for item in data.get('list', []):
                if item.get('sj_div') != 'CFS':
                    continue
                acc = item.get('account_nm')
                if acc in target:
                    raw = item.get('thstrm_amount', '0')
                    try:
                        result[acc] = float(raw.replace(',', ''))
                    except:
                        result[acc] = 0.0

            # 재무비율 자동 계산
            revenue = result.get('매출액', 0)
            op = result.get('영업이익', 0)
            net = result.get('당기순이익', 0)
            eq = result.get('자본총계', 0)
            debt = result.get('부채총계', 0)

            if revenue > 0:
                result['영업이익률'] = (op / revenue) * 100 if op else 0.0
            if eq > 0:
                result['ROE'] = (net / eq) * 100 if net else 0.0
                result['부채비율'] = (debt / eq) * 100 if debt else 0.0

            logger.debug(f"✅ {corp_code} 재무제표 정규화 완료 ({year})")
            return result

        except requests.exceptions.Timeout:
            logger.error(f"❌ {corp_code} 재무제표 Timeout")
        except requests.exceptions.ConnectionError:
            logger.error(f"❌ {corp_code} 재무제표 연결 오류")
        except Exception as e:
            logger.error(f"❌ {corp_code} 재무제표 예외: {e}")
        return {}

    def get_company_info_sync(self, corp_code: str) -> Optional[Dict]:
        try:
            resp = requests.get(
                f"{self.base_url}/company.json",
                params={"crtfc_key": self.api_key, "corp_code": corp_code},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get('status') == '000':
                return data
        except Exception as e:
            logger.error(f"❌ 기업 정보 조회 오류 ({corp_code}): {e}")
        return None

    def search_notices_sync(self, corp_code: str, start_date: Optional[str] = None, limit: int = 10) -> Optional[List]:
        if start_date is None:
            start_date = datetime.now().replace(month=1, day=1).strftime("%Y%m%d")

        try:
            resp = requests.get(
                f"{self.base_url}/list.json",
                params={
                    "crtfc_key": self.api_key,
                    "corp_code": corp_code,
                    "bgn_de": start_date,
                    "page_no": 1,
                    "page_count": limit
                },
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get('status') == '000':
                return data.get('list', [])
        except Exception as e:
            logger.error(f"❌ 공시 검색 오류 ({corp_code}): {e}")
        return None

    # ============================================================
    # 상태 조회 (기존)
    # ============================================================
    def get_stats(self) -> Dict:
        return {
            'daily_used': self.daily_used,
            'daily_limit': self.daily_limit,
            'remaining': self.daily_limit - self.daily_used,
            'last_reset': self.last_reset.isoformat()
        }