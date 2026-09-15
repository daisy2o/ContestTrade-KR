# 멀티에이전트 LLM 투자 판단에서 내부 경쟁 메커니즘의 기여 분석
### Contest-Mechanism Ablation of Multi-Agent LLM Trading — Korean Market (KOSPI) Study

KAIST 디지털금융MBA 현장적용프로젝트 (2026) · **Private research repository**

## 연구 질문

멀티에이전트 LLM 투자 판단의 개선 효과는 **경쟁(contest) 메커니즘**에서 오는가, **단순 다중화(앙상블)**에서 오는가?
동일 시스템에서 집계 방식만 바꾸는 통제된 ablation(단일 / 동일가중 / 콘테스트 / 셔플 대조)으로 분리 검증하고,
판단 품질(방향 정확도·확률 보정도)·선별 품질·비용 차감 성과를 나눠 평가한다.

- 평가 구간은 base LLM의 knowledge cutoff **이후**로 설정 (사전학습 오염 통제 + 진단 병행)
- 한국 시장 특수성 반영: KRX 거래일 캘린더, 가격제한폭 ±30%, 거래세, point-in-time KOSPI200

## 저장소 안내

| 문서 | 내용 |
|---|---|
| **[KR_RESEARCH_README.md](KR_RESEARCH_README.md)** | ⭐ 시작점 — 구현 현황 지도: 무엇이 구현·검증됐고 무엇이 아닌지, 사용 규칙 |
| [RUN_US_DEMO.md](RUN_US_DEMO.md) | 원본 파이프라인 데모 실행 가이드 (키 보안 주의 포함) |
| `contest_trade/tests/` | 회귀 테스트 — `python -m pytest contest_trade/tests -q` |
| [docs/upstream/](docs/upstream/) | 원본 프로젝트 문서 (보존용) |

작업 브랜치: **`kr-research`** (우리 기여) · `main`: 원본 기준점(commit `22432f9`)
→ `git log main..kr-research`가 곧 이 연구의 기여 목록이다.

## 원본 프로젝트 (Attribution)

본 연구는 **[ContestTrade](https://github.com/FinStep-AI/ContestTrade)** (FinStep-AI, Apache-2.0)를 기반으로 한다.
원본은 중국 A주·미국 시장을 지원하는 멀티에이전트 트레이딩 프레임워크이며(논문: [arXiv:2508.00554](https://arxiv.org/abs/2508.00554)),
우리는 이를 한국 시장에 이식하고 콘테스트 메커니즘의 기여를 **독립적으로 검증**한다.
라이선스 전문은 [LICENSE](LICENSE), 원본 문서는 [docs/upstream/](docs/upstream/) 참조.

## 팀

KAIST 디지털금융MBA — 정선우 · 하희정 · 이수정
