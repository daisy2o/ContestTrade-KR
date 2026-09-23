"""
config module for trade agent
"""
from pathlib import Path
import yaml
import os

PROJECT_ROOT = Path(__file__).parent.parent.resolve()


class ProjectConfig:

    def __init__(self) -> None:
        # Get market type from environment variable, default to CN-Stock
        market_type = os.environ.get('CONTEST_TRADE_MARKET', 'CN-Stock')
        
        # Choose config file based on market type
        if market_type == 'US-Stock':
            config_filename = "config_us.yaml"
        elif market_type == 'KR-Stock':
            config_filename = "config_kr.yaml"
        else:
            config_filename = "config.yaml"
        
        yaml_path = PROJECT_ROOT.parent / config_filename
        print(f"Loading config from: {yaml_path} (Market: {market_type})")

        with open(yaml_path, "r", encoding="utf-8") as fr:
            config = yaml.load(fr, Loader=yaml.FullLoader)
        for k in config:
            setattr(self, k, config[k])

        # 키 미기입 시 환경변수 폴백 (팀 가이드 방법 1: 파일 무편집 → 실수 커밋 원천 차단)
        env_key = os.environ.get("OPENAI_API_KEY", "")
        if env_key and isinstance(getattr(self, "llm", None), dict) and not self.llm.get("api_key"):
            self.llm["api_key"] = env_key

        # Store the market type for reference
        self.market_type = market_type

cfg = ProjectConfig()

if __name__ == "__main__":
    print(f"Market Type: {cfg.market_type}")
    print(f"Data Agents Config: {cfg.data_agents_config}")
    print(f"Research Agent Config: {cfg.research_agent_config}")
    print(f"Market Config File: {cfg.market_config_file}")
    print(f"System Language: {cfg.system_language}")
    print(f"LLM Config: {cfg.llm}")
    print(f"Available attributes: {[attr for attr in dir(cfg) if not attr.startswith('_')]}")