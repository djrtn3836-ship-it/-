# v5.1.2 FINAL — 설치 및 실행 가이드

> **📌 시스템명**: 국내주식 전방위 감시 시스템
> **📌 버전**: v5.1.2 FINAL
> **📌 상태**: Phase 1 Shadow Mode 가동 준비 완료

---

## 📋 1. 시스템 개요

KOSPI/KOSDAQ 2,300+ 종목을 실시간 감시하고, 투자 의사결정을 지원하는 Python 기반 시스템입니다.

**핵심 기능**:
- 2300+ 종목 Tiered 실시간 감시 (Push 기반)
- 13개 지표 기반 종목 분석 (호가잔량 포함)
- 18개 이벤트 엔진
- 6개 시장 국면 판정
- 7개 엔진 Adaptive Consensus
- VaR/Kelly/ATR 포트폴리오 할당
- Safety Guard + Circuit Breaker
- Telegram 보고서 (Why Now? + Why NOT? + Counterfactual)

---

## 🖥️ 2. 시스템 요구사항

| 항목 | 사양 |
|------|------|
| **CPU** | Intel 225F 이상 |
| **RAM** | 32GB DDR5 이상 |
| **Storage** | SSD 1TB 이상 |
| **Python** | 3.10 이상 |
| **OS** | Windows 10/11, Linux, macOS |

---

## 🔧 3. 설치 절차

### 3.1 가상환경 생성

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
