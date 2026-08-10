"""
DART Connector v5.2.0 — DART API 연동 및 Risk Score 계산 + 재무제표 조회 통합

변경사항:
1. DART Open API 연동 (기존)
2. Risk Score 0~100 계산 및 레벨 분류 (기존)
3. 정규표현식 기반 공시 분류 (기존)
4. [신규] 재무제표(매출/영업이익) 동기 조회 (PDF 보고서 연동용)
5. [신규] 기업 기본 정보 조회
6. [신규] 간편 공시 검색
"""

import re
import asyncio
import aiohttp
import requests  # 🔥 신규 추가: 동기식 재무제표 조회용
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class RiskLevel(Enum):
    """위험 수준"""
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass
class DisclosureAnalysis:
    """공시 분석 결과"""
    corp_code: str
    title: str
    report_type: str
    date: str
    url: str
    content: str = ""
    
    # Risk Score (0~100)
    risk_score: int = 0
    risk_level: RiskLevel = RiskLevel.NORMAL
    
    # 상세 정보
    matched_patterns: List[str] = field(default_factory=list)
    discount_rate: Optional[float] = None
    funding_purpose: Optional[str] = None
    is_third_party: bool = False
    is_related_party: bool = False
    issue_amount: Optional[float] = None
    
    # 추천 액션
    recommended_action: str = "매수"


class DartConnector:
    """DART Open API 연동 모듈 (Risk 분석 + 재무제표 통합)"""
    
    # Risk Score 가중치 (0~100)
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
    
    # 위험 수준 기준
    RISK_THRESHOLDS = {
        RiskLevel.NORMAL: 0,
        RiskLevel.WARNING: 20,
        RiskLevel.HIGH: 50,
        RiskLevel.CRITICAL: 70
    }
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://opendart.fss.or.kr/api"
        
        # Rate Limit
        self.daily_limit = 10000
        self.daily_used = 0
        self.last_reset = datetime.now()
        
        # 세션 (비동기용)
        self._session: Optional[aiohttp.ClientSession] = None
        
        # 정규표현식 패턴 (Risk 분석용)
        self.patterns = {
            'third_party_allotment': {
                'pattern': r'제3자배정',
                'weight': self.RISK_WEIGHTS['third_party_allotment']
            },
            'rights_offering': {
                'pattern': r'유상증자',
                'weight': 0
            },
            'cb_issue': {
                'pattern': r'전환사채\s*(발행|결정)',
                'weight': self.RISK_WEIGHTS['cb_issue']
            },
            'bw_issue': {
                'pattern': r'신주인수권부사채\s*(발행|결정)',
                'weight': self.RISK_WEIGHTS['bw_issue']
            },
            'max_shareholder_change': {
                'pattern': r'최대주주\s*(변경|매각)',
                'weight': self.RISK_WEIGHTS['max_shareholder_change']
            },
            'merger': {
                'pattern': r'합병\s*(결정|공고)',
                'weight': self.RISK_WEIGHTS['merger']
            }
        }

    # ============================================================
    # 1. 비동기 연결 관리 (기존)
    # ============================================================
    async def connect(self):
        """세션 연결"""
        if self._session is None:
            self._session = aiohttp.ClientSession()
        logger.info("DART Connector connected")
    
    async def disconnect(self):
        """세션 종료"""
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("DART Connector disconnected")

    # ============================================================
    # 2. 공시 조회 및 Risk 분석 (기존, 비동기)
    # ============================================================
    async def get_disclosures(self, corp_code: str, 
                              from_date: str, to_date: str,
                              deep_scan: bool = False) -> List[DisclosureAnalysis]:
        """공시 조회 및 분석"""
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
                        content = await self._fetch_content(
                            analysis.corp_code, 
                            item.get('rcept_no', '')
                        )
                        if content:
                            analysis.content = content
                            self._analyze_by_content(analysis)
                    
                    results.append(analysis)
                
                return results
                
        except Exception as e:
            logger.error(f"DART API 호출 실패: {e}")
            return []
    
    def _analyze_by_title(self, item: Dict) -> DisclosureAnalysis:
        """공시 제목 분석 (기존)"""
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
        
        # 할인율 확인
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
        
        # 발행금액 확인
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
        
        # 제3자배정
        if '제3자배정' in title:
            analysis.is_third_party = True
            if '최대주주' in title or '특수관계' in title:
                analysis.is_related_party = True
                risk_score += self.RISK_WEIGHTS['related_party']
                matched.append('related_party')
        
        # 자금조달 목적
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
        """공시 본문 조회 (기존)"""
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
        """HTML/XML 문서 파싱 (기존)"""
        try:
            text = re.sub(r'<[^>]+>', ' ', raw_doc)
            text = re.sub(r'\s+', ' ', text)
            return text.strip()
        except:
            return raw_doc
    
    def _analyze_by_content(self, analysis: DisclosureAnalysis):
        """본문 내용 추가 분석 (기존)"""
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
        if score >= 70:
            return RiskLevel.CRITICAL
        elif score >= 50:
            return RiskLevel.HIGH
        elif score >= 20:
            return RiskLevel.WARNING
        else:
            return RiskLevel.NORMAL
    
    def _get_recommended_action(self, analysis: DisclosureAnalysis) -> str:
        if analysis.risk_level == RiskLevel.CRITICAL:
            return "거래차단"
        elif analysis.risk_level == RiskLevel.HIGH:
            return "매도"
        elif analysis.risk_level == RiskLevel.WARNING:
            return "주의"
        else:
            return "매수"
    
    async def _check_rate_limit(self):
        """일일 Rate Limit 체크 (기존)"""
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
    # 3. 🔥 신규 추가: 재무제표 및 기본 정보 조회 (동기, PDF 보고서용)
    # ============================================================
    
    def get_financials_sync(self, corp_code: str, year: str = "2024") -> Optional[Dict]:
        """
        단일회사 재무제표 조회 (동기 방식)
        - PDF 주간 보고서에서 매출, 영업이익 등 재무 데이터 수집용
        - 사용 예: dart.get_financials_sync("00126380", "2024")
        """
        if not self.api_key:
            logger.error("❌ DART_API_KEY 없음")
            return None
            
        url = f"{self.base_url}/fnlttSinglAcnt.json"
        params = {
            "crtfc_key": self.api_key,
            "corp_code": corp_code,
            "bsns_year": year,
            "reprt_code": "11011"  # 11011=사업보고서(연간)
        }
        
        try:
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data.get('status') == '000':
                    return data
                else:
                    logger.warning(f"⚠️ 재무제표 조회 실패 ({corp_code}): {data.get('message')}")
                    return None
            else:
                logger.error(f"❌ HTTP 오류: {resp.status_code}")
                return None
        except Exception as e:
            logger.error(f"❌ 재무제표 요청 오류: {e}")
            return None
    
    def get_company_info_sync(self, corp_code: str) -> Optional[Dict]:
        """
        기업 기본 정보 조회 (동기 방식)
        - 회사명, 종목코드, 업종 등 조회
        """
        url = f"{self.base_url}/company.json"
        params = {
            "crtfc_key": self.api_key,
            "corp_code": corp_code
        }
        try:
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data.get('status') == '000':
                    return data
            return None
        except Exception as e:
            logger.error(f"❌ 기업 정보 조회 오류: {e}")
            return None
    
    def search_notices_sync(self, corp_code: str, start_date: str = "20250101", limit: int = 10) -> Optional[List]:
        """
        최근 공시 검색 (동기 방식, 간편 버전)
        - PDF 보고서에 최근 공시 내역 포함용
        """
        url = f"{self.base_url}/list.json"
        params = {
            "crtfc_key": self.api_key,
            "corp_code": corp_code,
            "bgn_de": start_date,
            "page_no": 1,
            "page_count": limit
        }
        try:
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data.get('status') == '000':
                    return data.get('list', [])
            return None
        except Exception as e:
            logger.error(f"❌ 공시 검색 오류: {e}")
            return None

    # ============================================================
    # 4. 상태 조회 (기존)
    # ============================================================
    def get_stats(self) -> Dict:
        return {
            'daily_used': self.daily_used,
            'daily_limit': self.daily_limit,
            'remaining': self.daily_limit - self.daily_used,
            'last_reset': self.last_reset.isoformat()
        }