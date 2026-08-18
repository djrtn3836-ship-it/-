"""
report/telegram_commands.py - v7.3.0 FINAL (완전 방어 및 사용자 경험 개선)
- 가격 조회 강화 (RealtimeMonitor → DB → 장마감 안내)
- DART/수급 오류 무시하고 리포트 전송
- 각 데이터 상태를 상세히 표시 (데이터 부족, 장마감 등)
- 한글 자연어 처리 통합
"""

import os
import re
import traceback
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any

from telegram.ext import Application, CommandHandler, MessageHandler, filters
from telegram import Update
from telegram.ext import ContextTypes

from core.logger import setup_logger
from core.debug_tower import debug_tower
from core.natural_language import nlp_engine, NLUResult

logger = setup_logger("telegram_cmd")


class TelegramCommandHandler:
    def __init__(self, token: str, chat_id: str, get_stats_callback):
        self.token = token
        self.chat_id = str(chat_id).strip()
        self.get_stats = get_stats_callback
        self.app = None
        self._running = False
        self._db_manager = None
        self._analyzer = None
        self._monitor = None
        self._dart = None
        self._news = None
        self._kiwoom = None

    def set_dependencies(self, db_manager=None, analyzer=None, monitor=None, dart=None, news=None, kiwoom=None):
        self._db_manager = db_manager
        self._analyzer = analyzer
        self._monitor = monitor
        self._dart = dart
        self._news = news
        self._kiwoom = kiwoom

    async def start(self):
        if self._running:
            return

        self.app = Application.builder().token(self.token).build()
        self.app.add_handler(CommandHandler("status", self._status_command))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._natural_language_handler))

        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling()

        self._running = True
        logger.info("✅ Telegram 봇 시작됨 (v7.3.0)")

    # ============================================================
    # 자연어 처리
    # ============================================================
    async def _natural_language_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            chat_id = update.effective_chat.id
            if str(chat_id) != self.chat_id:
                return

            text = update.message.text.strip()
            logger.info(f"💬 [자연어] {text}")

            if text.startswith("/"):
                if text == "/신호" or text == "/signal":
                    await self._signal_command(update, context)
                    return
                if text.startswith("/분석") or text.startswith("/analyze"):
                    args = text.split()
                    if len(args) >= 2:
                        await self._send_comprehensive_report(update, args[1].strip())
                    else:
                        await update.message.reply_text("⚠️ 티커를 입력하세요.\n예: /분석 005930")
                    return

            result = nlp_engine.parse(text)

            if result.intent == "status":
                await self._status_command(update, context)
            elif result.intent == "signal":
                await self._signal_command(update, context)
            elif result.intent == "analyze" and result.ticker:
                await self._send_comprehensive_report(update, result.ticker)
            elif result.intent == "analyze" and not result.ticker:
                await update.message.reply_text(
                    "📊 어떤 종목을 분석할까요? 종목명이나 코드를 알려주세요.\n"
                    "예: 삼전, 005930, 현대차"
                )
            else:
                await update.message.reply_text(
                    "🤔 잘 이해하지 못했어요.\n\n"
                    "💡 이렇게 물어보세요:\n"
                    "• '현황' → 시스템 상태\n"
                    "• '신호' → 최근 매수/매도 신호\n"
                    "• '삼전' → 종합 분석 리포트\n"
                    "• '005930' → 종목 코드 분석"
                )

        except Exception as e:
            logger.error(f"❌ 자연어 처리 오류: {e}")
            await update.message.reply_text("⚠️ 처리 중 오류가 발생했어요.")

    # ============================================================
    # 상태 명령어
    # ============================================================
    async def _status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            chat_id = update.effective_chat.id
            if str(chat_id) != self.chat_id:
                await update.message.reply_text("⛔ 접근 권한이 없습니다.")
                return

            stats = self.get_stats()
            uptime_seconds = stats.get('uptime_seconds', 0)
            hours = int(uptime_seconds // 3600)
            minutes = int((uptime_seconds % 3600) // 60)
            seconds = int(uptime_seconds % 60)

            msg = f"""
📊 <b>시스템 실시간 상태</b>
━━━━━━━━━━━━━━━━━━━━━
🟢 상태: <b>{stats.get('status', '알 수 없음')}</b>
⏱️ 가동 시간: {hours}시간 {minutes}분 {seconds}초
📡 구독 종목: <b>{stats.get('tickers', 0)}개</b>
📈 마지막 데이터: {stats.get('last_data_ago', 'N/A')}
🔌 키움 연결: {'✅' if stats.get('kiwoom_connected') else '❌'}
📊 큐 사용률: {stats.get('queue_usage', 0):.1f}%
📌 현재 국면: <b>{stats.get('regime', 'Sideways')}</b>
━━━━━━━━━━━━━━━━━━━━━
<i>🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>
"""
            await update.message.reply_text(msg, parse_mode='HTML')

        except Exception as e:
            logger.error(f"❌ 상태 명령어 오류: {e}")
            await update.message.reply_text(f"⚠️ 오류 발생: {e}")

    # ============================================================
    # 신호 목록
    # ============================================================
    async def _signal_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            chat_id = update.effective_chat.id
            if str(chat_id) != self.chat_id:
                await update.message.reply_text("⛔ 접근 권한이 없습니다.")
                return

            if not self._db_manager:
                await update.message.reply_text("⚠️ DB 연결이 초기화되지 않았습니다.")
                return

            signals = []
            for i in range(5):
                day = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
                day_signals = await self._db_manager.get_decisions_by_date(day)
                filtered = [s for s in day_signals if s.get('action') in ['BUY', 'SELL', 'SIGNAL_ENTRY']]
                signals.extend(filtered[:3])

            if not signals:
                await update.message.reply_text("📭 최근 5일간 신호가 없습니다.")
                return

            signals = signals[:10]
            lines = ["📈 <b>최근 신호 (최대 10개)</b>", "━━━━━━━━━━━━━━━━━━━━━"]

            for s in signals:
                ticker = s.get('ticker', 'N/A')
                action = s.get('action', 'UNKNOWN')
                price = s.get('price_at_decision', s.get('price', 0))
                score = s.get('score', 0)
                created = s.get('created_at', '')[:16]
                emoji = "🟢" if action in ['BUY', 'SIGNAL_ENTRY'] else "🔴"
                label = "매수" if action in ['BUY', 'SIGNAL_ENTRY'] else "매도"
                lines.append(f"{emoji} <b>{ticker}</b> {label} @ {price:,.0f}원 (확신도 {score:.0%})")
                lines.append(f"   🕒 {created}")

            lines.append("━━━━━━━━━━━━━━━━━━━━━")
            lines.append("<i>'삼전'으로 종합 분석</i>")

            await update.message.reply_text("\n".join(lines), parse_mode='HTML')

        except Exception as e:
            logger.error(f"❌ 신호 명령어 오류: {e}")
            await update.message.reply_text(f"⚠️ 오류 발생: {e}")

    # ============================================================
    # 🔥 핵심: 종합 분석 리포트 (완전 방어)
    # ============================================================
    async def _send_comprehensive_report(self, update: Update, ticker: str):
        if not self._analyzer:
            await update.message.reply_text("⚠️ 분석 엔진이 초기화되지 않았습니다.")
            return

        try:
            await update.message.reply_text(f"📊 {ticker} 종합 분석 중... (30초 이내)")

            # 1. 종목명 조회
            try:
                from data.stock_universe import get_universe
                universe = get_universe()
                stock_name = universe.get(ticker, ticker)
            except:
                stock_name = ticker

            # 2. 🔥 가격 조회 (강화)
            price, price_status = await self._get_price_robust(ticker)

            # 3. 병렬 데이터 수집 (에러 허용)
            tech_data, financials, news_items, supply = await asyncio.gather(
                self._get_technical_data(ticker),
                self._get_financial_data(ticker),
                self._get_news(ticker),
                self._get_supply_demand(ticker),
                return_exceptions=True
            )

            # 4. AI 분석 실행
            stock_data = {
                "ticker": ticker,
                "name": stock_name,
                "price": price if price > 0 else 1.0,
                "entry_price": price if price > 0 else 1.0,
                "imbalance": 0.5,
                "regime": "Sideways",
                "momentum": 0.0,
                "timestamp": datetime.now().isoformat()
            }

            try:
                analysis = await self._analyzer.analyze(stock_data)
            except Exception as e:
                logger.error(f"❌ AI 분석 실패: {e}")
                analysis = {"action": "ERROR", "score": 0, "confidence": 0, "positives": [], "negatives": [], "regime": "N/A"}

            # 5. 리포트 생성
            report = self._build_safe_report(ticker, stock_name, price, price_status, tech_data, financials, news_items, supply, analysis)

            # 6. 전송
            if len(report) > 4000:
                summary, detail = self._split_report(report)
                await update.message.reply_text(summary, parse_mode='HTML')
                await update.message.reply_text(detail, parse_mode='HTML')
            else:
                await update.message.reply_text(report, parse_mode='HTML')

            logger.info(f"✅ 종합 리포트 전송 성공 ({ticker})")

        except Exception as e:
            logger.error(f"❌ 리포트 생성 오류: {e}")
            await update.message.reply_text(f"⚠️ 분석 중 오류 발생: {str(e)[:100]}")

    # ============================================================
    # 🔥 가격 조회 강화 (상태 정보 포함)
    # ============================================================
    async def _get_price_robust(self, ticker: str) -> tuple:
        """(가격, 상태문자열) 반환"""
        price = 0
        status = "조회 불가"

        # 1. 실시간 가격 (RealtimeMonitor)
        if self._monitor:
            price = self._monitor.get_latest_price(ticker)
            if price and price > 0:
                status = "실시간"
                return price, status

        # 2. DB OHLCV (최근 종가)
        if self._db_manager:
            try:
                ohlcv = await self._db_manager.get_ohlcv(ticker, period=1)
                if ohlcv and len(ohlcv) > 0:
                    price = ohlcv[-1].get('close', 0)
                    if price > 0:
                        status = "DB (전일 종가)"
                        return price, status
            except Exception as e:
                logger.debug(f"DB 가격 조회 실패 ({ticker}): {e}")

        # 3. 장 마감 여부 확인
        now = datetime.now()
        if 9 <= now.hour <= 15 and not (now.hour == 15 and now.minute >= 20):
            status = "실시간 가격 없음 (장중 데이터 필요)"
        else:
            status = "조회 불가 (장 마감 후 OHLCV 데이터 부족)"

        return 0, status

    # ============================================================
    # 데이터 수집 헬퍼 (에러 허용)
    # ============================================================
    async def _get_technical_data(self, ticker: str) -> Dict:
        try:
            if self._db_manager:
                data = await self._db_manager.get_ohlcv(ticker, period=30)
                if len(data) >= 5:
                    closes = [d['close'] for d in data]
                    volumes = [d.get('volume', 0) for d in data if d.get('volume', 0) > 0]
                    current_price = closes[-1]

                    ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else current_price
                    ma60 = sum(closes[-60:]) / 60 if len(closes) >= 60 else current_price
                    avg_vol = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else 1
                    vol_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 1.0

                    return {
                        "price": current_price,
                        "ma20": ma20,
                        "ma60": ma60,
                        "volume_ratio": vol_ratio,
                        "data_count": len(data)
                    }
            return {"error": "데이터 부족"}
        except Exception as e:
            logger.debug(f"기술적 지표 수집 실패 ({ticker}): {e}")
            return {"error": str(e)}

    async def _get_financial_data(self, ticker: str) -> Dict:
        try:
            if not self._dart:
                return {"error": "DART 미연동"}
            corp_code = self._dart.get_corp_code_sync(ticker)
            if not corp_code:
                return {"error": "corp_code 없음 (DART API 오류)"}
            fin = self._dart.get_financials_sync(corp_code, "2024")
            if not fin:
                return {"error": "재무 데이터 없음 (API 응답 없음)"}
            return {
                "revenue": fin.get("매출액", 0),
                "operating_profit": fin.get("영업이익", 0),
                "net_profit": fin.get("당기순이익", 0),
                "roe": fin.get("ROE", 0),
                "debt_ratio": fin.get("부채비율", 0),
                "op_margin": fin.get("영업이익률", 0)
            }
        except Exception as e:
            logger.debug(f"재무 데이터 수집 실패 ({ticker}): {e}")
            return {"error": str(e)}

    async def _get_news(self, ticker: str) -> Dict:
        try:
            if not self._news:
                return {"error": "뉴스 미연동"}
            items, sentiment = await self._news.get_news_with_sentiment(ticker, limit=3, cache_seconds=3600)
            headlines = [item.get('title', '') for item in items[:3]]
            return {
                "sentiment": sentiment if isinstance(sentiment, (int, float)) else 0.0,
                "headlines": headlines,
                "count": len(items)
            }
        except Exception as e:
            logger.debug(f"뉴스 수집 실패 ({ticker}): {e}")
            return {"error": str(e)}

    async def _get_supply_demand(self, ticker: str) -> Dict:
        try:
            if not self._kiwoom:
                return {"error": "키움 미연동"}

            # 장중에만 수급 데이터 요청
            now = datetime.now()
            is_trading = (9 <= now.hour <= 15) and not (now.hour == 15 and now.minute >= 20)
            if not is_trading:
                return {"error": "장 마감 후 수급 데이터 비활성화"}

            foreign = await self._kiwoom.request_tr(ticker, "외국인수급")
            inst = await self._kiwoom.request_tr(ticker, "기관수급")

            # 응답 검증
            foreign_net = foreign.get('net_buy', 0) if isinstance(foreign, dict) else 0
            inst_net = inst.get('net_buy', 0) if isinstance(inst, dict) else 0

            return {
                "foreign_net": foreign_net,
                "inst_net": inst_net,
                "status": "OK"
            }
        except Exception as e:
            logger.debug(f"수급 데이터 수집 실패 ({ticker}): {e}")
            return {"error": str(e)}

    # ============================================================
    # 리포트 빌더 (안전)
    # ============================================================
    def _build_safe_report(self, ticker: str, name: str, price: float, price_status: str,
                           tech: Any, fin: Any, news: Any, supply: Any,
                           analysis: Dict) -> str:
        """각 섹션별 예외를 독립적으로 처리"""

        # 가격 표시
        if price > 0:
            price_display = f"{price:,.0f}원 ({price_status})"
        else:
            price_display = f"⚠️ {price_status}"

        # AI 분석 결과
        action = analysis.get('action', 'HOLD')
        score = analysis.get('score', 0)
        confidence = analysis.get('confidence', 0)
        positives = analysis.get('positives', [])[:4]
        negatives = analysis.get('negatives', [])[:3]
        regime = analysis.get('regime', 'N/A')

        if action in ['BUY', 'SIGNAL_ENTRY']:
            emoji = "🟢"
            action_label = "매수 추천"
            strength = "🔥 강력" if score >= 0.8 else "✅ 보통" if score >= 0.65 else "⚠️ 약한"
        elif action in ['SELL', 'EXIT']:
            emoji = "🔴"
            action_label = "매도 추천"
            strength = "⚠️ 주의"
        else:
            emoji = "⚪"
            action_label = "관망"
            strength = "💤 대기"

        # 기술적 지표
        tech_str = "데이터 부족"
        try:
            if isinstance(tech, dict) and 'error' not in tech:
                ma20 = tech.get('ma20', 0)
                ma60 = tech.get('ma60', 0)
                vol_ratio = tech.get('volume_ratio', 1.0)
                if isinstance(ma20, (int, float)) and isinstance(ma60, (int, float)) and ma20 > 0 and ma60 > 0:
                    tech_str = f"20일선 {ma20:,.0f} | 60일선 {ma60:,.0f} | 거래량 {vol_ratio:.1f}배"
                else:
                    tech_str = "기술 데이터 수집 중"
        except:
            pass

        # 재무 지표
        fin_str = "데이터 부족"
        try:
            if isinstance(fin, dict) and 'error' not in fin:
                roe = fin.get('roe', 0)
                debt = fin.get('debt_ratio', 0)
                op_margin = fin.get('op_margin', 0)
                if isinstance(roe, (int, float)) and isinstance(debt, (int, float)):
                    fin_str = f"ROE {roe:.1f}% | 부채비율 {debt:.1f}% | 영업이익률 {op_margin:.1f}%"
                else:
                    fin_str = f"재무 데이터 수집 중 (DART API)"
        except:
            pass

        # 뉴스
        news_str = "데이터 부족"
        try:
            if isinstance(news, dict) and 'error' not in news:
                sentiment = news.get('sentiment', 0)
                if isinstance(sentiment, (int, float)):
                    sentiment_label = "긍정" if sentiment > 0.2 else "부정" if sentiment < -0.2 else "중립"
                    headlines = news.get('headlines', [])
                    news_str = f"감성 {sentiment:+.2f} ({sentiment_label})"
                    if headlines and isinstance(headlines[0], str):
                        news_str += f"\n📰 {headlines[0][:50]}..." if len(headlines[0]) > 50 else f"\n📰 {headlines[0]}"
                else:
                    news_str = "뉴스 수집 중"
        except:
            pass

        # 수급
        supply_str = "데이터 부족"
        try:
            if isinstance(supply, dict) and 'error' not in supply:
                foreign = supply.get('foreign_net', 0)
                inst = supply.get('inst_net', 0)
                if isinstance(foreign, (int, float)) and isinstance(inst, (int, float)):
                    foreign_label = "순매수" if foreign > 0 else "순매도" if foreign < 0 else "중립"
                    inst_label = "순매수" if inst > 0 else "순매도" if inst < 0 else "중립"
                    supply_str = f"외국인 {foreign_label} ({foreign:+,.0f}억) | 기관 {inst_label} ({inst:+,.0f}억)"
                else:
                    supply_str = "수급 데이터 수집 중 (장중 필요)"
            else:
                supply_str = supply.get('error', '수급 데이터 수집 중')
        except:
            pass

        msg = f"""
{emoji} <b>📊 {name} ({ticker}) 종합 분석 리포트</b>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📌 <b>기본 정보</b>
💰 현재가: <code>{price_display}</code>
📈 종합 점수: <code>{score:.1%}</code>
🎯 액션: <b>{action_label}</b> ({strength})
📌 시장 국면: {regime}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 <b>재무 지표</b>
{fin_str}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📉 <b>기술적 지표</b>
{tech_str}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📰 <b>뉴스 및 감성</b>
{news_str}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 <b>수급 동향</b>
{supply_str}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📌 <b>매수 근거</b>
{"• " + "\n• ".join(positives) if positives else "• 없음"}

⚠️ <b>주의 사항</b>
{"• " + "\n• ".join(negatives) if negatives else "• 없음"}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
<i>🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} KST</i>
<i>📌 투자 결정은 본인 책임입니다.</i>
"""
        return msg

    def _split_report(self, report: str) -> tuple:
        lines = report.split('\n')
        summary_lines = lines[:15]
        detail_lines = lines[15:]
        return "\n".join(summary_lines), "\n".join(detail_lines)

    async def stop(self):
        if self.app and self._running:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
            self._running = False
            logger.info("🛑 Telegram 명령어 리스너 종료")