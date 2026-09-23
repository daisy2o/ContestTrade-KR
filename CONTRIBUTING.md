# 팀 작업 규칙 (내부용)

3인 프로젝트라 규칙은 최소한으로:

1. **커밋 전 pull** — 자기 feat 브랜치에서 `git pull origin main` 후 작업, 완료되면 PR
2. **키·비밀번호·데이터 커밋 금지** — API 키는 환경변수(OPENAI_API_KEY) 권장. 공개 저장소이므로 sqlite 데이터·실험 결과 수치도 올리지 않기 (공유는 드라이브로)
3. **코드를 고쳤으면 테스트 통과 확인** — `python -m pytest contest_trade/tests -q -m "not network"` (57건, 1초)
4. 각자 영역: 데이터 수집·문서는 자유롭게 커밋, `contest_trade/` 코드 수정은 하희정과 상의 후
5. 문서에 팀원 나열 시 순서: 정선우 · 이수정 · 하희정
6. 실험 설계의 정본은 이 저장소가 아니라 별도 연구 노트에 있음 — 궁금하면 하희정에게

원본 프로젝트(ContestTrade)에 기여하려는 경우: [docs/upstream/CONTRIBUTING_upstream.md](docs/upstream/CONTRIBUTING_upstream.md)
