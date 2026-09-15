# 팀 작업 규칙 (내부용)

3인 프로젝트라 규칙은 최소한으로:

1. **커밋 전 pull** — `git pull daisy kr-research` 한 번이면 충돌 대부분 예방
2. **키·비밀번호 커밋 금지** — `config.yaml`/`config_us.yaml`에 API 키를 넣은 상태로 커밋하지 않기 (히스토리에 남으면 지우기 어려움)
3. **코드를 고쳤으면 테스트 통과 확인** — `python -m pytest contest_trade/tests -q` (30건, 1초)
4. 각자 영역: 데이터 수집·문서는 자유롭게 커밋, `contest_trade/` 코드 수정은 하희정과 상의 후
5. 문서에 팀원 나열 시 순서: 정선우 · 이수정 · 하희정
6. 실험 설계의 정본은 이 저장소가 아니라 별도 연구 노트에 있음 — 궁금하면 하희정에게

원본 프로젝트(ContestTrade)에 기여하려는 경우: [docs/upstream/CONTRIBUTING_upstream.md](docs/upstream/CONTRIBUTING_upstream.md)
