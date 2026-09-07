# -*- coding: utf-8 -*-
"""
infrastructure/dart/client.py - DART Connector v5.4.2 (Session 39: mypy strict 적용)
- 모든 메서드 반환 타입/제네릭 타입 명시, 로직 100% 무변경
- V10: data/dart_connector.py에서 이동 (CACHE_FILE 경로만 다름)
- data/dart_connector.py와 동일한 두 가지 방어 기법 적용
  (len(x or {}) Optional 가드, dict()/list() 래핑을 통한 warn_return_any 방지)
"""

import asyncio
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp
import requests

from collector.collector_status import collector_status
from core.logger import setup_logger

logger = setup_logger("dart_connector")

# 🔧 data/dart_connector.py와 유일한 실질적 차이: .parent 한 단계 더 위로
CACHE_FILE = Path(__file__).parent.parent.parent / "config" / "corp_code_cache.json"
CACHE_TTL_DAYS = 7
RETRY_INTERVAL_HOURS = 1


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
    RISK_WEIGHTS: Dict[str, int] = {
        "third_party_allotment": 30,
        "rights_offering_high_discount": 30,
        "rights_offering_low_discount": 15,
        "cb_issue": 20,
        "bw_issue": 20,
        "large_issue": 30,
        "related_party": 20,
        "funding_operation": 10,
        "funding_debt": 15,
        "funding_investment": 5,
        "max_shareholder_change": 20,
        "merger": 25,
    }

    RISK_THRESHOLDS: Dict[RiskLevel, int] = {
        RiskLevel.NORMAL: 0, RiskLevel.WARNING: 20, RiskLevel.HIGH: 50, RiskLevel.CRITICAL: 70
    }

    def __init__(self, api_key: str) -> None:
        self.api_key: str = api_key
        self.base_url: str = "https://opendart.fss.or.kr/api"
        self.daily_limit: int = 10000
        self.daily_used: int = 0
        self.last_reset: datetime = datetime.now()
        self._session: Optional[aiohttp.ClientSession] = None

        self.patterns: Dict[str, Dict[str, Any]] = {
            "third_party_allotment": {"pattern": r"제3자배정", "weight": self.RISK_WEIGHTS["third_party_allotment"]},
            "rights_offering": {"pattern": r"유상증자", "weight": 0},
            "cb_issue": {"pattern": r"전환사채\s*(발행|결정)", "weight": self.RISK_WEIGHTS["cb_issue"]},
            "bw_issue": {"pattern": r"신주인수권부사채\s*(발행|결정)", "weight": self.RISK_WEIGHTS["bw_issue"]},
            "max_shareholder_change": {
                "pattern": r"최대주주\s*(변경|매각)",
                "weight": self.RISK_WEIGHTS["max_shareholder_change"],
            },
            "merger": {"pattern": r"합병\s*(결정|공고)", "weight": self.RISK_WEIGHTS["merger"]},
        }

        self._corp_code_map: Optional[Dict[str, str]] = None
        self._cache_loaded: bool = False
        self._last_retry_time: Dict[str, float] = {}

        self._load_cache()
        collector_status.register("dart_connector", freshness_seconds=86400)

    def _load_cache(self) -> None:
        if CACHE_FILE.exists():
            try:
                with open(CACHE_FILE, encoding="utf-8") as f:
                    data: Dict[str, Any] = json.load(f)
                cached_time = datetime.fromisoformat(data.get("cached_at", "2000-01-01"))
                if datetime.now() - cached_time < timedelta(days=CACHE_TTL_DAYS):
                    self._corp_code_map = data.get("mapping", {})
                    self._cache_loaded = True
                    logger.debug(f"✅ corp_code 캐시 로드 완료 ({len(self._corp_code_map or {})}개)")
                    collector_status.record_success(
                        "dart_connector", {"cache_size": len(self._corp_code_map or {})}
                    )
                    return
                else:
                    logger.info("⏳ corp_code 캐시 만료, 재다운로드 필요")
            except Exception as e:
                logger.warning(f"⚠️ 캐시 로드 실패: {e}")

        self._corp_code_map = {}
        self._cache_loaded = False

    def _save_cache(self, mapping: Dict[str, str]) -> None:
        try:
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(
                    {"cached_at": datetime.now().isoformat(), "mapping": mapping}, f, ensure_ascii=False, indent=2
                )
            self._corp_code_map = mapping
            self._cache_loaded = True
            logger.debug(f"✅ corp_code 캐시 저장 완료 ({len(mapping)}개)")
        except Exception as e:
            logger.error(f"❌ 캐시 저장 실패: {e}")

    def _download_corp_code(self) -> Dict[str, str]:
        if not self.api_key:
            logger.warning("⚠️ DART API 키 없음, corp_code 매핑 불가")
            return {}

        url = f"{self.base_url}/corpCode.xml"
        params = {"crtfc_key": self.api_key}
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

        try:
            resp = requests.get(url, params=params, headers=headers, timeout=30)
            resp.raise_for_status()

            content_type = resp.headers.get("Content-Type", "")
            if "xml" not in content_type and not resp.text.strip().startswith("<?xml"):
                logger.warning(f"⚠️ DART 응답이 XML 아님 (Content-Type: {content_type})")
                return {}

            root = ET.fromstring(resp.content)
            mapping: Dict[str, str] = {}
            for corp in root.findall("list"):
                corp_code = corp.findtext("corp_code")
                stock_code = corp.findtext("stock_code")
                if corp_code and stock_code and stock_code.isdigit() and len(stock_code) >= 6:
                    mapping[stock_code] = corp_code
            logger.info(f"✅ DART corp_code 매핑 다운로드 완료 ({len(mapping)}개)")
            collector_status.record_success("dart_connector", {"mapping_size": len(mapping)})
            return mapping
        except requests.exceptions.Timeout:
            logger.error("❌ corpCode.xml 다운로드 타임아웃 (30초)")
        except ET.ParseError as e:
            logger.error(f"❌ corpCode.xml 파싱 오류: {e}")
        except Exception as e:
            logger.error(f"❌ corpCode.xml 다운로드 실패: {e}")
            collector_status.record_failure("dart_connector", str(e))
        return {}

    def get_corp_code_sync(self, ticker: str) -> Optional[str]:
        if not ticker or not ticker.isdigit() or len(ticker) < 6:
            return None

        if not self._cache_loaded:
            self._load_cache()

        if self._corp_code_map and ticker in self._corp_code_map:
            return self._corp_code_map[ticker]

        now = datetime.now().timestamp()
        last_retry = self._last_retry_time.get(ticker, 0.0)
        if self._cache_loaded and (now - last_retry) < RETRY_INTERVAL_HOURS * 3600:
            remaining = (RETRY_INTERVAL_HOURS * 3600 - (now - last_retry)) / 60
            logger.debug(f"⏳ {ticker} 재시도 대기 중 (남은 시간: {remaining:.0f}분)")
            return None

        logger.info(f"📥 corp_code 매핑 다운로드 시작 (티커: {ticker})")
        new_mapping = self._download_corp_code()
        if new_mapping:
            self._corp_code_map = new_mapping
            self._cache_loaded = True
            self._save_cache(new_mapping)
            return new_mapping.get(ticker)
        else:
            self._last_retry_time[ticker] = now
            logger.warning(f"⚠️ {ticker} corp_code 매핑 실패 → {RETRY_INTERVAL_HOURS}시간 후 재시도")
            return None

    async def connect(self) -> None:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        logger.info("DART Connector connected")

    async def disconnect(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("DART Connector disconnected")

    async def get_disclosures(
        self, corp_code: str, from_date: str, to_date: str, deep_scan: bool = False
    ) -> List[DisclosureAnalysis]:
        if self._session is None:
            await self.connect()
        await self._check_rate_limit()

        url = f"{self.base_url}/list.json"
        params: Dict[str, Any] = {
            "crtfc_key": self.api_key,
            "corp_code": corp_code,
            "bgn_de": from_date,
            "end_de": to_date,
            "pblntf_detail_ty": "A001",
        }

        try:
            if self._session is None:
                return []
            async with self._session.get(url, params=params) as resp:
                self.daily_used += 1
                data: Dict[str, Any] = await resp.json()
                if data.get("status") != "000":
                    logger.error(f"DART API error: {data.get('message', 'Unknown')}")
                    return []

                results: List[DisclosureAnalysis] = []
                for item in data.get("list", []):
                    analysis = self._analyze_by_title(item)
                    if deep_scan and analysis.risk_level in [RiskLevel.HIGH, RiskLevel.CRITICAL]:
                        content = await self._fetch_content(analysis.corp_code, str(item.get("rcept_no", "")))
                        if content:
                            analysis.content = content
                            self._analyze_by_content(analysis)
                    results.append(analysis)
                return results
        except Exception as e:
            logger.error(f"DART API 호출 실패: {e}")
            return []

    def _analyze_by_title(self, item: Dict[str, Any]) -> DisclosureAnalysis:
        title: str = str(item.get("report_nm", ""))
        corp_code: str = str(item.get("corp_code", ""))
        date: str = str(item.get("rcept_de", ""))

        analysis = DisclosureAnalysis(
            corp_code=corp_code,
            title=title,
            report_type=str(item.get("pblntf_detail_ty", "")),
            date=date,
            url=str(item.get("rm_url", "")),
        )

        risk_score: int = 0
        matched: List[str] = []

        for pattern_name, pattern_info in self.patterns.items():
            if re.search(str(pattern_info["pattern"]), title):
                matched.append(pattern_name)
                risk_score += int(pattern_info["weight"])

        discount_match = re.search(r"할인율\s*(\d+)%", title)
        if discount_match:
            try:
                rate = float(discount_match.group(1))
                analysis.discount_rate = rate
                if rate >= 30:
                    risk_score += self.RISK_WEIGHTS["rights_offering_high_discount"]
                    matched.append("rights_offering_high_discount")
                else:
                    risk_score += self.RISK_WEIGHTS["rights_offering_low_discount"]
                    matched.append("rights_offering_low_discount")
            except Exception:
                pass

        amount_match = re.search(r"발행금액\s*([\d,]+)\s*억원", title)
        if amount_match:
            try:
                amount = float(amount_match.group(1).replace(",", ""))
                analysis.issue_amount = amount
                if amount >= 100:
                    risk_score += self.RISK_WEIGHTS["large_issue"]
                    matched.append("large_issue")
            except Exception:
                pass

        if "제3자배정" in title:
            analysis.is_third_party = True
            if "최대주주" in title or "특수관계" in title:
                analysis.is_related_party = True
                risk_score += self.RISK_WEIGHTS["related_party"]
                matched.append("related_party")

        if "운영자금" in title:
            analysis.funding_purpose = "운영자금"
            risk_score += self.RISK_WEIGHTS["funding_operation"]
        elif "채무상환" in title:
            analysis.funding_purpose = "채무상환"
            risk_score += self.RISK_WEIGHTS["funding_debt"]
        elif "시설투자" in title:
            analysis.funding_purpose = "시설투자"
            risk_score += self.RISK_WEIGHTS["funding_investment"]

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
        params: Dict[str, str] = {"crtfc_key": self.api_key, "corp_code": corp_code, "rcept_no": rcept_no}

        try:
            if self._session is None:
                return None
            async with self._session.get(url, params=params) as resp:
                self.daily_used += 1
                data: Dict[str, Any] = await resp.json()
                if "document" in data:
                    return self._parse_document(str(data["document"]))
                return None
        except Exception as e:
            logger.error(f"공시 본문 조회 실패: {e}")
            return None

    def _parse_document(self, raw_doc: str) -> str:
        try:
            text = re.sub(r"<[^>]+>", " ", raw_doc)
            text = re.sub(r"\s+", " ", text)
            return text.strip()
        except Exception:
            return raw_doc

    def _analyze_by_content(self, analysis: DisclosureAnalysis) -> None:
        content = analysis.content
        if not content:
            return
        if "할인율" in content:
            match = re.search(r"할인율\s*(\d+)%", content)
            if match:
                rate = float(match.group(1))
                if rate > (analysis.discount_rate or 0.0):
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

    async def _check_rate_limit(self) -> None:
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

    def get_financials_sync(self, corp_code: str, year: Optional[str] = None) -> Dict[str, float]:
        if not self.api_key:
            return {}

        years_to_try: List[str] = [year] if year else ["2024", "2023", "2022"]

        for try_year in years_to_try:
            try:
                resp = requests.get(
                    f"{self.base_url}/fnlttSinglAcnt.json",
                    params={
                        "crtfc_key": self.api_key,
                        "corp_code": corp_code,
                        "bsns_year": try_year,
                        "reprt_code": "11011",
                    },
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=10,
                )
                resp.raise_for_status()
                data: Dict[str, Any] = resp.json()

                if data.get("status") == "000":
                    result: Dict[str, float] = {}
                    target = ["매출액", "영업이익", "당기순이익", "자산총계", "부채총계", "자본총계"]
                    for item in data.get("list", []):
                        if item.get("sj_div") != "CFS":
                            continue
                        acc = item.get("account_nm")
                        if acc in target:
                            raw = str(item.get("thstrm_amount", "0"))
                            try:
                                result[acc] = float(raw.replace(",", ""))
                            except Exception:
                                result[acc] = 0.0

                    revenue = result.get("매출액", 0.0)
                    op = result.get("영업이익", 0.0)
                    net = result.get("당기순이익", 0.0)
                    eq = result.get("자본총계", 0.0)
                    debt = result.get("부채총계", 0.0)

                    if revenue > 0:
                        result["영업이익률"] = (op / revenue) * 100 if op else 0.0
                    if eq > 0:
                        result["ROE"] = (net / eq) * 100 if net else 0.0
                        result["부채비율"] = (debt / eq) * 100 if debt else 0.0

                    logger.debug(f"✅ {corp_code} 재무제표 정규화 완료 ({try_year})")
                    collector_status.record_success("dart_connector", {"year": try_year})
                    return result
                else:
                    logger.debug(f"ℹ️ {corp_code} {try_year}년 데이터 없음, 다음 연도 시도")
            except requests.exceptions.Timeout:
                logger.warning(f"⚠️ {corp_code} {try_year}년 재무 Timeout")
            except requests.exceptions.ConnectionError:
                logger.warning(f"⚠️ {corp_code} {try_year}년 연결 오류")
            except Exception as e:
                logger.warning(f"⚠️ {corp_code} {try_year}년 재무 조회 실패: {e}")

        logger.warning(f"⚠️ {corp_code} 재무제표 데이터 없음 (모든 연도 실패)")
        collector_status.record_failure("dart_connector", f"재무제표 없음 ({corp_code})")
        return {}

    def get_company_info_sync(self, corp_code: str) -> Optional[Dict[str, Any]]:
        try:
            resp = requests.get(
                f"{self.base_url}/company.json",
                params={"crtfc_key": self.api_key, "corp_code": corp_code},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") == "000":
                return dict(data)
        except Exception as e:
            logger.error(f"❌ 기업 정보 조회 오류 ({corp_code}): {e}")
        return None

    def search_notices_sync(
        self, corp_code: str, start_date: Optional[str] = None, limit: int = 10
    ) -> Optional[List[Dict[str, Any]]]:
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
                    "page_count": limit,
                },
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") == "000":
                return list(data.get("list", []))
        except Exception as e:
            logger.error(f"❌ 공시 검색 오류 ({corp_code}): {e}")
        return None

    async def get_financials_async(self, corp_code: str, year: Optional[str] = None) -> Dict[str, float]:
        """비동기 재무제표 조회 (이벤트 루프 블로킹 없음)"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.get_financials_sync, corp_code, year)

    async def get_company_info_async(self, corp_code: str) -> Optional[Dict[str, Any]]:
        """비동기 기업 정보 조회 (이벤트 루프 블로킹 없음)"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.get_company_info_sync, corp_code)

    async def search_notices_async(
        self, corp_code: str, start_date: Optional[str] = None, limit: int = 10
    ) -> Optional[List[Dict[str, Any]]]:
        """비동기 공시 검색 (이벤트 루프 블로킹 없음)"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.search_notices_sync, corp_code, start_date, limit)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "daily_used": self.daily_used,
            "daily_limit": self.daily_limit,
            "remaining": self.daily_limit - self.daily_used,
            "last_reset": self.last_reset.isoformat(),
        }
