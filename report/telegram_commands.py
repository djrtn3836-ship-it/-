"""
report/telegram_commands.py - v7.3.1 (mypy strict 완전 적용)
"""

import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, Callable

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from core.logger import setup_logger
from core.natural_language import nlp_engine

logger = setup_logger("telegram_cmd")


class TelegramCommandHandler:
    def __init__(self, token: str, chat_id: str, get_stats_callback: Callable[[], Dict[str, Any]]) -> None:
        self.token: str = token
        self.chat_id: str = str(chat_id).strip()
        self.get_stats: Callable[[], Dict[str, Any]] = get_stats_callback
        self.app: Any = None
        self._running: bool = False
        self._db_manager: Any = None
        self._analyzer: Any = None
        self._monitor: Any = None
        self._dart: Any = None
        self._news: Any = None
        self._kiwoom: Any = None

    def set_dependencies(
        self,
        db_manager: Any = None,
        analyzer: Any = None,
        monitor: Any = None,
        dart: Any = None,
        news: Any = None,
        kiwoom: Any = None,
    ) -> None:
        self._db_manager = db_manager
        self._analyzer = analyzer
        self._monitor = monitor
        self._dart = dart
        self._news = news
        self._kiwoom = kiwoom

    async def start(self) -> bool:
        if self._running:
            return True

        self.app = Application.builder().token(self.token).build()
        self.app.add_handler(CommandHandler("status", self._status_command))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._natural_language_handler))

        await self.app.initialize()
        await self.app.start()

        # 🔒 중복 인스턴스 가드: 다른 프로세스가 같은 봇을 폴링 중이면 Conflict 발생.
        # offset을 지정하지 않아 대기 중인 업데이트를 소비하지 않는다(비파괴 프로브).
        conflict = False
        try:
            await self.app.bot.get_updates(timeout=0, limit=1)
        except Exception as e:
            if "conflict" in str(e).lower():
                conflict = True
                logger.critical(
                    "🚨 텔레그램 중복 폴링 감지(getUpdates Conflict) — 다른 인스턴스가 "
                    "같은 봇을 사용 중입니다. 명령어 폴링을 시작하지 않습니다."
                )
            else:
                logger.warning(f"Telegram 프리플라이트 확인 실패(무시): {e}")

        if conflict:
            try:
                await self.app.stop()
                await self.app.shutdown()
            except Exception:
                pass
            self.app = None
            self._running = False
            return False

        await self.app.updater.start_polling(allowed_updates=["message", "callback_query"])

        self._running = True
        logger.info("📱 Telegram 봇 시작됨 (v7.3.1)")
        return True

    # ============================================================
    # 자연어 처리
    # ============================================================
    async def _natural_language_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_chat or not update.message or not update.message.text:
            return

        try:
            chat_id = update.effective_chat.id
            if str(chat_id) != self.chat_id:
                return

            text = update.message.text.strip()
            logger.info(f"🗣️ [자연어] {text}")

            if text.startswith("/"):
                if text == "/신호" or text == "/signal":
                    await self._signal_command(update, context)
                    return
                if text.startswith("/분석") or text.startswith("/analyze"):
                    args = text.split()
                    if len(args) >= 2:
                        await self._send_comprehensive_report(update, args[1].strip())
                    else:
                        await update.message.reply_text("⚠️ 종목 코드를 입력하세요.\n예: /분석 005930")
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
                    "❓ 어떤 종목을 분석할까요? 종목명이나 코드를 알려주세요.\n" "예: 삼전, 005930, 현대차"
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
            if update.message:
                await update.message.reply_text("⚠️ 처리 중 오류가 발생했어요.")

    # ============================================================
    # 상태 명령어
    # ============================================================
    async def _status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_chat or not update.message:
            return

        try:
            chat_id = update.effective_chat.id
            if str(chat_id) != self.chat_id:
                await update.message.reply_text("⛔ 접근 권한이 없습니다.")
                return

            stats = self.get_stats()
            uptime_seconds = float(stats.get("uptime_seconds", 0))
            hours = int(uptime_seconds // 3600)
            minutes = int((uptime_seconds % 3600) // 60)
            seconds = int(uptime_seconds % 60)

            msg = f"""
📊 <b>시스템 실시간 상태</b>
━━━━━━━━━━━━━━━━━━━━━
🟢 상태: <b>{stats.get('status', '알 수 없음')}</b>
⏱️ 가동 시간: {hours}시간 {minutes}분 {seconds}초
📡 구독 종목: <b>{stats.get('tickers', 0)}개</b>
🔄 마지막 데이터: {stats.get('last_data_ago', 'N/A')}
🔌 키움 연결: {'✅' if stats.get('kiwoom_connected') else '❌'}
📦 큐 사용률: {float(stats.get('queue_usage', 0)):.1f}%
📈 현재 국면: <b>{stats.get('regime', 'Sideways')}</b>
━━━━━━━━━━━━━━━━━━━━━
<i>🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>
"""
            await update.message.reply_text(msg, parse_mode="HTML")

        except Exception as e:
            logger.error(f"❌ 상태 명령어 오류: {e}")
            await update.message.reply_text(f"⚠️ 오류 발생: {e}")

    # ============================================================
    # 신호 목록
    # ============================================================
    async def _signal_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_chat or not update.message:
            return

        try:
            chat_id = update.effective_chat.id
            if str(chat_id) != self.chat_id:
                await update.message.reply_text("⛔ 접근 권한이 없습니다.")
                return

            if not self._db_manager:
                await update.message.reply_text("⚠️ DB 연결이 초기화되지 않았습니다.")
                return

            signals: List[Dict[str, Any]] = []
            for i in range(5):
                day = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
                day_signals = await self._db_manager.get_decisions_by_date(day)
                filtered = [s for s in day_signals if s.get("action") in ["BUY", "SELL", "SIGNAL_ENTRY"]]
                signals.extend(filtered[:3])

            if not signals:
                await update.message.reply_text("📭 최근 5일간 신호가 없습니다.")
                return

            signals = signals[:10]
            lines = ["📊 <b>최근 신호 (최대 10개)</b>", "━━━━━━━━━━━━━━━━━━━━━"]

            for s in signals:
                ticker = s.get("ticker", "N/A")
                action = s.get("action", "UNKNOWN")
                price = float(s.get("price_at_decision", s.get("price", 0)))
                score = float(s.get("score", 0))
                created = str(s.get("created_at", ""))[:16]
                emoji = "🟢" if action in ["BUY", "SIGNAL_ENTRY"] else "🔴"
                label = "매수" if action in ["BUY", "SIGNAL_ENTRY"] else "매도"
                lines.append(f"{emoji} <b>{ticker}</b> {label} @ {price:,.0f}원 (확신도 {score:.0%})")
                lines.append(f"   🕒 {created}")

            lines.append("━━━━━━━━━━━━━━━━━━━━━")
            lines.append("<i>'삼전'으로 종합 분석 리포트</i>")

            await update.message.reply_text("\n".join(lines), parse_mode="HTML")

        except Exception as e:
            logger.error(f"❌ 신호 명령어 오류: {e}")
            await update.message.reply_text(f"⚠️ 오류 발생: {e}")

    # ============================================================
    # 종합 분석 리포트
    # ============================================================
    async def _send_comprehensive_report(self, update: Update, ticker: str) -> None:
        if not update.message:
            return

        if not self._analyzer:
            await update.message.reply_text("⚠️ 분석 엔진이 초기화되지 않았습니다.")
            return

        try:
            await update.message.reply_text(f"🔍 {ticker} 종합 분석 중... (30초 이내)")

            try:
                from infrastructure.market_data.universe_provider import get_universe
                universe = get_universe()
                stock_name = str(universe.get(ticker, ticker))
            except:
                stock_name = ticker

            price, price_status = await self._get_price_robust(ticker)

            _gather_results = await asyncio.gather(
                self._get_technical_data(ticker),
                self._get_financial_data(ticker),
                self._get_news(ticker),
                self._get_supply_demand(ticker),
                return_exceptions=True,
            )
            tech_data: Any = _gather_results[0]
            financials: Any = _gather_results[1]
            news_items: Any = _gather_results[2]
            supply: Any = _gather_results[3]

            stock_data: Dict[str, Any] = {
                "ticker": ticker,
                "name": stock_name,
                "price": price if price > 0 else 1.0,
                "entry_price": price if price > 0 else 1.0,
                "imbalance": 0.5,
                "regime": "Sideways",
                "momentum": 0.0,
                "timestamp": datetime.now().isoformat(),
            }

            try:
                analysis = await self._analyzer.analyze(stock_data)
            except Exception as e:
                logger.error(f"❌ AI 분석 실패: {e}")
                analysis = {
                    "action": "ERROR",
                    "score": 0.0,
                    "confidence": 0.0,
                    "positives": [],
                    "negatives": [],
                    "regime": "N/A",
                }

            report = self._build_safe_report(
                ticker, stock_name, price, price_status, tech_data, financials, news_items, supply, analysis
            )

            if len(report) > 4000:
                summary, detail = self._split_report(report)
                await update.message.reply_text(summary, parse_mode="HTML")
                await update.message.reply_text(detail, parse_mode="HTML")
            else:
                await update.message.reply_text(report, parse_mode="HTML")

            logger.info(f"✅ 종합 리포트 전송 성공 ({ticker})")

        except Exception as e:
            logger.error(f"❌ 리포트 생성 오류: {e}")
            await update.message.reply_text(f"⚠️ 분석 중 오류 발생: {str(e)[:100]}")

    async def _get_price_robust(self, ticker: str) -> Tuple[float, str]:
        price: float = 0.0
        status: str = "조회 불가"

        if self._monitor:
            try:
                price = float(self._monitor.get_latest_price(ticker) or 0)
                if price > 0:
                    status = "실시간"
                    return price, status
            except:
                pass

        if self._db_manager:
            try:
                ohlcv = await self._db_manager.get_ohlcv(ticker, period=1)
                if ohlcv and len(ohlcv) > 0:
                    price = float(ohlcv[-1].get("close", 0))
                    if price > 0:
                        status = "DB (전일 종가)"
                        return price, status
            except Exception as e:
                logger.debug(f"DB 가격 조회 실패 ({ticker}): {e}")

        now = datetime.now()
        if 9 <= now.hour <= 15 and not (now.hour == 15 and now.minute >= 20):
            status = "실시간 가격 없음 (장중 데이터 필요)"
        else:
            status = "조회 불가 (장 마감 후 OHLCV 데이터 부족)"

        return 0.0, status

    async def _get_technical_data(self, ticker: str) -> Dict[str, Any]:
        try:
            if self._db_manager:
                data = await self._db_manager.get_ohlcv(ticker, period=30)
                if len(data) >= 5:
                    closes = [float(d["close"]) for d in data]
                    volumes = [float(d.get("volume", 0)) for d in data if float(d.get("volume", 0)) > 0]
                    current_price = closes[-1]

                    ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else current_price
                    ma60 = sum(closes[-60:]) / 60 if len(closes) >= 60 else current_price
                    avg_vol = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else 1.0
                    vol_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 1.0

                    return {
                        "price": current_price,
                        "ma20": ma20,
                        "ma60": ma60,
                        "volume_ratio": vol_ratio,
                        "data_count": len(data),
                    }
            return {"error": "데이터 부족"}
        except Exception as e:
            logger.debug(f"기술적 지표 수집 실패 ({ticker}): {e}")
            return {"error": str(e)}

    async def _get_financial_data(self, ticker: str) -> Dict[str, Any]:
        try:
            if not self._dart:
                return {"error": "DART 미연결"}
            corp_code = self._dart.get_corp_code_sync(ticker)
            if not corp_code:
                return {"error": "corp_code 없음 (DART API 오류)"}
            fin = self._dart.get_financials_sync(corp_code, "2024")
            if not fin:
                return {"error": "재무 데이터 없음 (API 응답 없음)"}
            return {
                "revenue": float(fin.get("매출액", 0)),
                "operating_profit": float(fin.get("영업이익", 0)),
                "net_profit": float(fin.get("당기순이익", 0)),
                "roe": float(fin.get("ROE", 0)),
                "debt_ratio": float(fin.get("부채비율", 0)),
                "op_margin": float(fin.get("영업이익률", 0)),
            }
        except Exception as e:
            logger.debug(f"재무 데이터 수집 실패 ({ticker}): {e}")
            return {"error": str(e)}

    async def _get_news(self, ticker: str) -> Dict[str, Any]:
        try:
            if not self._news:
                return {"error": "뉴스 미연결"}
            items, sentiment = await self._news.get_news_with_sentiment(ticker, limit=3, cache_seconds=3600)
            headlines = [str(item.get("title", "")) for item in items[:3]]
            return {
                "sentiment": float(sentiment) if isinstance(sentiment, (int, float)) else 0.0,
                "headlines": headlines,
                "count": len(items),
            }
        except Exception as e:
            logger.debug(f"뉴스 수집 실패 ({ticker}): {e}")
            return {"error": str(e)}

    async def _get_supply_demand(self, ticker: str) -> Dict[str, Any]:
        try:
            if not self._kiwoom:
                return {"error": "키움 미연결"}

            now = datetime.now()
            is_trading = (9 <= now.hour <= 15) and not (now.hour == 15 and now.minute >= 20)
            if not is_trading:
                return {"error": "장 마감 후 수급 데이터 비활성화"}

            foreign = await self._kiwoom.request_tr(ticker, "외국인순매수")
            inst = await self._kiwoom.request_tr(ticker, "기관수급")

            foreign_net = float(foreign.get("net_buy", 0)) if isinstance(foreign, dict) else 0.0
            inst_net = float(inst.get("net_buy", 0)) if isinstance(inst, dict) else 0.0

            return {"foreign_net": foreign_net, "inst_net": inst_net, "status": "OK"}
        except Exception as e:
            logger.debug(f"수급 데이터 수집 실패 ({ticker}): {e}")
            return {"error": str(e)}

    def _build_safe_report(
        self,
        ticker: str,
        name: str,
        price: float,
        price_status: str,
        tech: Any,
        fin: Any,
        news: Any,
        supply: Any,
        analysis: Dict[str, Any],
    ) -> str:
        if price > 0:
            price_display = f"{price:,.0f}원 ({price_status})"
        else:
            price_display = f"⚠️ {price_status}"

        action = str(analysis.get("action", "HOLD"))
        score = float(analysis.get("score", 0))
        positives = analysis.get("positives", [])[:4]
        negatives = analysis.get("negatives", [])[:3]
        regime = str(analysis.get("regime", "N/A"))

        if action in ["BUY", "SIGNAL_ENTRY"]:
            emoji = "🟢"
            action_label = "매수 추천"
            strength = "🔥 강력" if score >= 0.8 else "👍 보통" if score >= 0.65 else "⚠️ 약함"
        elif action in ["SELL", "EXIT"]:
            emoji = "🔴"
            action_label = "매도 추천"
            strength = "⚠️ 주의"
        else:
            emoji = "🟡"
            action_label = "관망"
            strength = "💤 대기"

        tech_str = "데이터 부족"
        try:
            if isinstance(tech, dict) and "error" not in tech:
                ma20 = float(tech.get("ma20", 0))
                ma60 = float(tech.get("ma60", 0))
                vol_ratio = float(tech.get("volume_ratio", 1.0))
                if ma20 > 0 and ma60 > 0:
                    tech_str = f"20일선 {ma20:,.0f} | 60일선 {ma60:,.0f} | 거래량 {vol_ratio:.1f}배"
                else:
                    tech_str = "기술 데이터 수집 중"
        except:
            pass

        fin_str = "데이터 부족"
        try:
            if isinstance(fin, dict) and "error" not in fin:
                roe = float(fin.get("roe", 0))
                debt = float(fin.get("debt_ratio", 0))
                op_margin = float(fin.get("op_margin", 0))
                fin_str = f"ROE {roe:.1f}% | 부채비율 {debt:.1f}% | 영업이익률 {op_margin:.1f}%"
        except:
            pass

        news_str = "데이터 부족"
        try:
            if isinstance(news, dict) and "error" not in news:
                sentiment = float(news.get("sentiment", 0))
                sentiment_label = "긍정" if sentiment > 0.2 else "부정" if sentiment < -0.2 else "중립"
                headlines = news.get("headlines", [])
                news_str = f"감성 {sentiment:+.2f} ({sentiment_label})"
                if headlines and isinstance(headlines[0], str):
                    news_str += f"\n📰 {headlines[0][:50]}..." if len(headlines[0]) > 50 else f"\n📰 {headlines[0]}"
        except:
            pass

        supply_str = "데이터 부족"
        try:
            if isinstance(supply, dict) and "error" not in supply:
                foreign = float(supply.get("foreign_net", 0))
                inst = float(supply.get("inst_net", 0))
                foreign_label = "순매수" if foreign > 0 else "순매도" if foreign < 0 else "중립"
                inst_label = "순매수" if inst > 0 else "순매도" if inst < 0 else "중립"
                supply_str = f"외국인 {foreign_label} ({foreign:+,.0f}주) | 기관 {inst_label} ({inst:+,.0f}주)"
            elif isinstance(supply, dict) and "error" in supply:
                supply_str = str(supply.get("error", "수급 데이터 수집 중"))
        except:
            pass

        msg = f"""
{emoji} <b>📊 {name} ({ticker}) 종합 분석 리포트</b>
━━━━━━━━━━━━━━━━━━━━━

📈 <b>기본 정보</b>
🔹 현재가: <code>{price_display}</code>
🎯 종합 점수: <code>{score:.1%}</code>
💡 액션: <b>{action_label}</b> ({strength})
📊 시장 국면: {regime}

━━━━━━━━━━━━━━━━━━━━━
🏢 <b>재무 지표</b>
{fin_str}

━━━━━━━━━━━━━━━━━━━━━
📉 <b>기술적 지표</b>
{tech_str}

━━━━━━━━━━━━━━━━━━━━━
📰 <b>뉴스 및 감성</b>
{news_str}

━━━━━━━━━━━━━━━━━━━━━
👥 <b>수급 동향</b>
{supply_str}

━━━━━━━━━━━━━━━━━━━━━
✅ <b>매수 근거</b>
{"\n• ".join([""] + [str(p) for p in positives]) if positives else "• 없음"}

⚠️ <b>주의 사항</b>
{"\n• ".join([""] + [str(n) for n in negatives]) if negatives else "• 없음"}

━━━━━━━━━━━━━━━━━━━━━
<i>🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} KST</i>
<i>⚠️ 투자 결정은 본인 책임입니다.</i>
"""
        return msg

    def _split_report(self, report: str) -> Tuple[str, str]:
        lines = report.split("\n")
        summary_lines = lines[:15]
        detail_lines = lines[15:]
        return "\n".join(summary_lines), "\n".join(detail_lines)

    async def stop(self) -> None:
        if self.app and self._running:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
            self._running = False
            logger.info("🛑 Telegram 명령어 리스너 종료")