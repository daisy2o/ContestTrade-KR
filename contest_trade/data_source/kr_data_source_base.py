"""
KRDataSourceBase — 한국 데이터 소스 공통 부모 (팀 문서 §4.6 ②층, 역할 B).

모든 KR 어댑터가 상속하며, 이 층이 강제하는 것:
1. **as-of 필터**: pub_time < trigger_time 인 행만 반환. 서브클래스가 실수로
   미래 데이터를 반환해도 여기서 잘린다 (look-ahead 방지, 팀 규칙 4 / D30 기준 1).
2. **컬럼 계약**: ['title', 'content', 'pub_time', 'url'] (원본 DataSourceBase와 동일).
3. **캐시**: 부모의 trigger_time 단위 pkl 캐시 재사용 → 재생(replay) 재현성.

서브클래스는 `fetch_raw(trigger_time) -> DataFrame`만 구현한다.
pub_time 형식: 'YYYY-MM-DD HH:MM:SS' (KST 고정, 프로젝트 규약).
시각이 날짜 단위로만 확실한 소스는 보수 규칙을 적용해 서브클래스에서
pub_time을 그 날짜의 다음 거래일 00:00 등으로 밀어 기록한다 (스펙 D22 §5-0).
"""
import pandas as pd

from data_source.data_source_base import DataSourceBase

REQUIRED_COLUMNS = ["title", "content", "pub_time", "url"]


class KRDataSourceBase(DataSourceBase):

    def __init__(self, name: str, cache_dir=None):
        super().__init__(name)
        if cache_dir is not None:  # 테스트 격리용 주입 지점
            from pathlib import Path

            self.data_cache_dir = Path(cache_dir)
            self.data_cache_dir.mkdir(parents=True, exist_ok=True)

    def fetch_raw(self, trigger_time: str) -> pd.DataFrame:
        """서브클래스 구현: trigger_time 참고용. as-of 필터는 부모가 보장하지만
        불필요한 과대 조회를 피하기 위해 소스 측 기간 제한을 함께 걸 것."""
        raise NotImplementedError

    def get_data(self, trigger_time: str) -> pd.DataFrame:
        cached = self.get_data_cached(trigger_time)
        if cached is not None:
            return cached

        df = self.fetch_raw(trigger_time)
        if df is None:
            df = pd.DataFrame(columns=REQUIRED_COLUMNS)

        missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"{self.name}: 필수 컬럼 누락 {missing}")

        df = df[REQUIRED_COLUMNS].copy()
        df["pub_time"] = df["pub_time"].astype(str)

        # ── as-of 필터 (엄격 미만: pub_time == trigger_time 도 제외) ──
        before = len(df)
        df = df[df["pub_time"] < trigger_time]
        dropped = before - len(df)
        if dropped:
            from loguru import logger
            logger.warning(
                f"{self.name}: as-of 필터가 {dropped}건 제거 "
                f"(trigger_time={trigger_time} 이후 데이터가 소스에서 넘어옴)"
            )

        df = df.sort_values("pub_time").reset_index(drop=True)
        # 빈 결과는 캐시하지 않음 (일시 장애를 박제하지 않기 — kr_data_utils와 동일 원칙)
        if len(df) > 0:
            self.save_data_cached(trigger_time, df)
        return df
