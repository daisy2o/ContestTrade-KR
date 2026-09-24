"""
보상 확정 시각 규칙 — 누수 차단의 단일 기준.

원칙(이것 하나만 지킨다):

    보상 계산에 필요한 **모든 데이터의 이용 가능 시각 ≤ 판단 시각**

날짜 간격(`D-2`)으로 쓰지 않는다. `D-2`는 현재 설정에서 이 원칙을 만족하는
**결과**일 뿐이고, 보상 정의나 판단 시각이 바뀌면 달라진다.

현재 설정:
  · 판단 시각 = 판단일 08:30 KST (개장 전)
  · 보상      = 신호일 시가 → **다음 거래일** 시가
  ⟹ 신호일 S의 보상은 S+1 시가를 쓴다. 그 값이 08:30에 확정돼 있으려면
     S+1 이 **판단일보다 앞선 거래일**이어야 한다. 즉 S ≤ D-2 (거래일 기준).

`D-2`는 달력상 이틀 전이 아니라 **KRX 거래일 기준**이다. 휴장은 거래일
목록에서 자연히 빠지고, 거래정지·가격 결측은 보상 계산 실패로 드러나므로
**그 신호를 이력에서 제외**한다(임의 대체값을 넣지 않는다).

이력이 부족하면 콘테스트 가중치를 만들 수 없다. 그때의 초기 가중치는
**동일가중**으로 고정한다(사후에 유리한 값을 고르지 않기 위해).
"""
from __future__ import annotations

from datetime import datetime, timedelta

TRIGGER_HOUR = "08:30:00"


def last_reward_ready_date(judgment_date: str, kr_client, trigger_hour: str = TRIGGER_HOUR) -> str | None:
    """판단일 기준으로 **보상이 확정된 마지막 신호일**을 거래일로 돌려준다.

    반환값이 None이면 쓸 수 있는 이력이 없다는 뜻이다(→ 동일가중 초기값).
    """
    d = datetime.strptime(judgment_date, "%Y-%m-%d")
    start8 = (d - timedelta(days=60)).strftime("%Y%m%d")
    end8 = d.strftime("%Y%m%d")
    tdays = kr_client.get_trade_dates(start8, end8)
    tdays = [t for t in tdays if t <= d.strftime("%Y%m%d")]
    if len(tdays) < 3:
        return None
    # tdays[-1] == 판단일(거래일이면). 보상이 확정되려면 S+1 < 판단일 이어야 하므로
    # S 는 뒤에서 세 번째 거래일까지.
    if tdays[-1] == d.strftime("%Y%m%d"):
        s = tdays[-3]
    else:
        # 판단일이 거래일이 아닌 경우: 마지막 거래일을 기준으로 같은 규칙
        s = tdays[-2]
    return f"{s[:4]}-{s[4:6]}-{s[6:]}"


def eligible_history_dates(judgment_date: str, window: int, kr_client,
                           trigger_hour: str = TRIGGER_HOUR) -> list:
    """보상이 확정된 거래일만 최신순 window개 → 오래된 순으로 돌려준다."""
    cutoff = last_reward_ready_date(judgment_date, kr_client, trigger_hour)
    if cutoff is None:
        return []
    d = datetime.strptime(judgment_date, "%Y-%m-%d")
    start8 = (d - timedelta(days=window * 4 + 30)).strftime("%Y%m%d")
    c8 = cutoff.replace("-", "")
    tdays = [t for t in kr_client.get_trade_dates(start8, c8) if t <= c8]
    tdays = tdays[-window:]
    return [f"{t[:4]}-{t[4:6]}-{t[6:]}" for t in tdays]


def explain(judgment_date: str, kr_client, trigger_hour: str = TRIGGER_HOUR) -> dict:
    """점검용 — 왜 그 날짜가 경계인지 드러낸다."""
    d = datetime.strptime(judgment_date, "%Y-%m-%d")
    tdays = kr_client.get_trade_dates((d - timedelta(days=30)).strftime("%Y%m%d"),
                                      d.strftime("%Y%m%d"))
    cutoff = last_reward_ready_date(judgment_date, kr_client, trigger_hour)
    nxt = None
    if cutoff:
        c8 = cutoff.replace("-", "")
        after = [t for t in kr_client.get_trade_dates(c8, d.strftime("%Y%m%d")) if t > c8]
        nxt = f"{after[0][:4]}-{after[0][4:6]}-{after[0][6:]}" if after else None
    return {
        "판단일": judgment_date, "판단시각": trigger_hour,
        "보상확정_마지막_신호일": cutoff,
        "그_신호의_청산일(필요 데이터)": nxt,
        "규칙": "보상 계산에 필요한 모든 데이터의 이용 가능 시각 ≤ 판단 시각",
        "확인": (f"{nxt} 시가는 판단일 {judgment_date} {trigger_hour} 이전에 확정됨"
                 if nxt and nxt < judgment_date else "경계 확인 필요"),
        "최근_거래일": [f"{t[:4]}-{t[4:6]}-{t[6:]}" for t in tdays[-5:]],
    }
