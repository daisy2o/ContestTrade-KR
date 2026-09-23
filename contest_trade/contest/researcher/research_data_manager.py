"""
Research数据管理器
"""

import re
import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from research_contest_types import SignalData

logger = logging.getLogger(__name__)

class ResearchDataManager:
    """研究数据管理器"""
    
    def __init__(self, history_window_days: int, project_root: Path, target_agents: List[str] = None):
        self.history_window_days = history_window_days
        self.project_root = project_root
        self.target_agents = target_agents or []
        self.workspace_dir = project_root / "contest_trade" / "agents_workspace"
        self.reports_dir = self.workspace_dir / "reports"
        self.factors_dir = self.workspace_dir / "factors"
        
        logger.info(f"ResearchDataManager初始化 - 历史窗口: {history_window_days}天")
    
    def load_historical_signals(self, current_date: str) -> Dict[str, List[Optional[SignalData]]]:
        """
        加载历史研究信号数据
        
        Args:
            current_date: 当前日期，格式为 "YYYY-MM-DD"
            
        Returns:
            Dict[agent_name, List[SignalData]]: 每个agent对应固定天数的信号列表，按时间顺序
        """
        logger.info(f"加载历史信号数据 - 当前日期: {current_date}, 历史窗口: {self.history_window_days}天")
        
        # 生成历史日期列表
        # KR 수정: 달력일이 아닌 '거래일' 기준 창 — 주말·휴일이 창을 갉아먹지 않게
        current_dt = datetime.strptime(current_date, "%Y-%m-%d")
        historical_dates = []
        try:
            from config.config import cfg
            if getattr(cfg, "market_type", "") == "KR-Stock":
                from utils.kr_data_utils import GLOBAL_KR_CLIENT
                start8 = (current_dt - timedelta(days=self.history_window_days * 3 + 10)).strftime("%Y%m%d")
                end8 = (current_dt - timedelta(days=1)).strftime("%Y%m%d")
                tdays = GLOBAL_KR_CLIENT.get_trade_dates(start8, end8)[-self.history_window_days:]
                historical_dates = [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in tdays]
        except Exception:
            historical_dates = []
        if not historical_dates:
            for i in range(self.history_window_days, 0, -1):
                hist_date = current_dt - timedelta(days=i)
                historical_dates.append(hist_date.strftime("%Y-%m-%d"))
        
        logger.info(f"历史日期范围: {historical_dates[0]} ~ {historical_dates[-1]}")
        
        # 获取所有agent目录
        agent_dirs = self._get_agent_directories()
        
        # 加载每个agent的历史信号
        agent_signals = {}
        
        for agent_name in agent_dirs:
            signals_list = []
            
            for date_str in historical_dates:
                signal_data = self._load_signal_for_date(agent_name, date_str)
                signals_list.append(signal_data)
            
            agent_signals[agent_name] = signals_list
            
            # 统计信息
            valid_count = sum(1 for s in signals_list if s is not None)
            logger.info(f"{agent_name}: {valid_count}/{len(signals_list)} 个有效信号")
        
        return agent_signals
    
    def _get_agent_directories(self) -> List[str]:
        """获取所有agent目录名称"""
        agent_dirs = []
        
        if self.reports_dir.exists():
            for agent_dir in self.reports_dir.iterdir():
                if agent_dir.is_dir() and agent_dir.name.startswith('agent_'):
                    if not self.target_agents or agent_dir.name in self.target_agents:
                        agent_dirs.append(agent_dir.name)
        
        agent_dirs.sort()
        return agent_dirs
    
    def _load_signal_for_date(self, agent_name: str, date_str: str) -> Optional[SignalData]:
        """
        加载指定agent在指定日期的信号数据
        
        Args:
            agent_name: agent名称
            date_str: 日期字符串，格式为 "YYYY-MM-DD"
            
        Returns:
            SignalData: 信号数据，如果不存在则返回None
        """
        # KR 수정: 저장기는 콜론을 대시로 치환("09-00-00") — 원본 로더는 "09:00:00"을
        # 찾아 항상 미발견이었다 (죽은 코드 원인). 두 형식 모두 시도.
        signal_file_pattern = f"{date_str}_09-00-00.json"
        signal_file = self.reports_dir / agent_name / signal_file_pattern
        
        if not signal_file.exists():
            return None
        
        with open(signal_file, 'r', encoding='utf-8') as f:
            signal_raw = json.load(f)
        
        # 解析信号数据
        signal_data = self._parse_signal_data(agent_name, signal_raw, str(signal_file))
        
        return signal_data
    
    def _parse_signal_data(self, agent_name: str, signal_raw: Dict, file_path: str) -> SignalData:
        """
        解析原始信号数据为SignalData对象
        
        Args:
            agent_name: agent名称
            signal_raw: 原始信号数据
            file_path: 文件路径
            
        Returns:
            SignalData: 解析后的信号数据
        """
        # 解析final_result字段
        final_result = signal_raw.get('final_result', '')
        parsed_result = self._parse_final_result(final_result)
        
        # 构建SignalData对象
        signal_data = SignalData(
            agent_name=agent_name,
            trigger_time=signal_raw.get('trigger_time', ''),
            thinking=signal_raw.get('final_result_thinking', ''),
            has_opportunity=parsed_result.get('has_opportunity', 'no'),
            action=parsed_result.get('action', 'none'),
            symbol_code=parsed_result.get('symbol_code', ''),
            symbol_name=parsed_result.get('symbol_name', ''),
            evidence_list=parsed_result.get('evidence_list', []),
            limitations=parsed_result.get('limitations', []),
            probability=parsed_result.get('probability', '0'),
            belief=signal_raw.get('belief', ''),
            background_information=signal_raw.get('background_information', ''),
            file_path=file_path
        )
        
        return signal_data
    
    def _parse_final_result(self, final_result: str) -> Dict:
        """解析final_result字符串，提取结构化数据"""
        
        # 移除<Output>标签
        if '<Output>' in final_result:
            final_result = final_result.split('<Output>')[-1].strip()
        
        # 提取各个字段
        has_opportunity = self._extract_field(final_result, 'has_opportunity')
        action = self._extract_field(final_result, 'action')
        symbol_code = self._extract_field(final_result, 'symbol_code')
        symbol_name = self._extract_field(final_result, 'symbol_name')
        probability = self._extract_field(final_result, 'probability')
        
        # 提取evidence_list
        evidence_list = self._extract_evidence_list(final_result)
        
        # 提取limitations
        limitations = self._extract_limitations(final_result)
        
        return {
            'has_opportunity': has_opportunity,
            'action': action,
            'symbol_code': symbol_code,
            'symbol_name': symbol_name,
            'evidence_list': evidence_list,
            'limitations': limitations,
            'probability': probability
        }
    
    def _extract_field(self, text: str, field_name: str) -> str:
        """提取单个字段"""
        pattern = f"<{field_name}>(.*?)</{field_name}>"
        match = re.search(pattern, text, flags=re.DOTALL)
        return match.group(1).strip() if match else ''
    
    def _extract_evidence_list(self, text: str) -> List[Dict]:
        """提取evidence_list"""
        evidence_list = []
        
        # 提取整个evidence_list内容
        evidence_list_match = re.search(r"<evidence_list>(.*?)</evidence_list>", text, flags=re.DOTALL)
        if not evidence_list_match:
            return evidence_list
        
        evidence_list_content = evidence_list_match.group(1)
        
        # 分割每个evidence块
        evidence_blocks = re.split(r"<evidence>", evidence_list_content)
        
        for block in evidence_blocks:
            if '</evidence>' in block:
                evidence_parts = block.split('</evidence>')
                if len(evidence_parts) >= 1:
                    evidence_content = evidence_parts[0].strip()
                    
                    # 提取time和from_source
                    time_match = re.search(r"<time>(.*?)</time>", evidence_parts[0] if len(evidence_parts) > 1 else block, flags=re.DOTALL)
                    source_match = re.search(r"<from_source>(.*?)</from_source>", evidence_parts[0] if len(evidence_parts) > 1 else block, flags=re.DOTALL)
                    
                    evidence_list.append({
                        'description': evidence_content,
                        'time': time_match.group(1).strip() if time_match else '',
                        'from_source': source_match.group(1).strip() if source_match else ''
                    })
        
        return evidence_list
    
    def _extract_limitations(self, text: str) -> List[str]:
        """提取limitations"""
        limitations = []
        
        # 提取整个limitations内容
        limitations_match = re.search(r"<limitations>(.*?)</limitations>", text, flags=re.DOTALL)
        if not limitations_match:
            return limitations
        
        limitations_content = limitations_match.group(1)
        
        # 提取每个limitation
        limitation_matches = re.findall(r"<limitation>(.*?)</limitation>", limitations_content, flags=re.DOTALL)
        for limitation in limitation_matches:
            limitations.append(limitation.strip())
        
        return limitations
    
    def load_current_signals(self, trigger_time: str) -> Dict[str, SignalData]:
        """
        加载当前时间点的信号数据
        
        Args:
            trigger_time: 触发时间，格式为 "YYYY-MM-DD HH:MM:SS"
            
        Returns:
            Dict[agent_name, SignalData]: 当前信号数据字典
        """
        signals = {}
        
        # KR 수정: 저장 파일명 규칙(공백→_, 콜론→-)과 일치시킴
        filename = f"{trigger_time.replace(' ', '_').replace(':', '-')}.json"
        
        # 遍历所有agent目录
        if self.reports_dir.exists():
            for agent_dir in self.reports_dir.iterdir():
                if agent_dir.is_dir() and agent_dir.name.startswith('agent_'):
                    if self.target_agents and agent_dir.name not in self.target_agents:
                        continue
                        
                    signal_file = agent_dir / filename
                    if signal_file.exists():
                        with open(signal_file, 'r', encoding='utf-8') as f:
                            signal_raw = json.load(f)
                        
                        signal_data = self._parse_signal_data(agent_dir.name, signal_raw, str(signal_file))
                        signals[agent_dir.name] = signal_data
        
        return signals

    def load_signals_data(self, date_str: str) -> Dict[str, "SignalData"]:
        """KR 복원: 판정기가 호출하는 미구현 메서드 — 해당 날짜의 저장 신호를 로드."""
        return self.load_current_signals(f"{date_str} 09:00:00")

    def set_market_manager(self, market_manager):
        """设置市场管理器（用于计算收益率）"""
        self.market_manager = market_manager
    
    async def calculate_signal_reward(self, signal: SignalData) -> Optional[float]:
        """
        计算单个信号的收益率
        
        Args:
            signal: 信号数据
            
        Returns:
            float: 收益率
        """
        # 只计算有机会的信号
        if signal.has_opportunity.lower() != 'yes':
            raise ValueError(f"信号 {signal.agent_name} 没有机会(has_opportunity={signal.has_opportunity})")
        
        # 获取股票代码
        symbol_code = signal.symbol_code
        if not symbol_code:
            raise ValueError(f"信号 {signal.agent_name} 缺少股票代码")
        
        # 解析信号时间
        signal_time = signal.trigger_time
        signal_dt = datetime.strptime(signal_time, "%Y-%m-%d %H:%M:%S")
        
        # KR 수정: 시장 하드코딩("CN-Stock") 제거 + 달력일 +1 대신 다음 '거래일' 사용
        # (원본은 금요일 신호의 청산일이 토요일이 되어 가격 조회 실패 → 죽은 코드 원인)
        from config.config import cfg
        market_type = getattr(cfg, "market_type", "CN-Stock")
        entry_date = signal_dt.strftime("%Y-%m-%d %H:%M:%S")
        if market_type == "KR-Stock":
            from utils.kr_data_utils import GLOBAL_KR_CLIENT
            d8 = signal_dt.strftime("%Y%m%d")
            lookahead = (signal_dt + timedelta(days=21)).strftime("%Y%m%d")
            nxt = GLOBAL_KR_CLIENT.get_trade_dates(d8, lookahead)
            if len(nxt) < 2:
                raise ValueError(f"{symbol_code}: {entry_date} 의 다음 거래일 미도래 — reward 계산 불가")
            n = nxt[1]
            exit_date = f"{n[:4]}-{n[4:6]}-{n[6:]} {signal_dt.strftime('%H:%M:%S')}"
        else:
            exit_date = (signal_dt + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

        # 获取入场价格（开盘价）
        entry_price_data = self.market_manager.get_symbol_price(market_type, symbol_code, entry_date, 0)
        if not entry_price_data or 'open' not in entry_price_data:
            raise ValueError(f"无法获取 {symbol_code} 在 {entry_date} 的入场价格")

        entry_price = float(entry_price_data['open'])

        # 获取出场价格（次日开盘价）
        exit_price_data = self.market_manager.get_symbol_price(market_type, symbol_code, exit_date, 0)
        if not exit_price_data or 'open' not in exit_price_data:
            raise ValueError(f"无法获取 {symbol_code} 在 {exit_date} 的出场价格")
        
        exit_price = float(exit_price_data['open'])
        
        # 计算原始收益率
        if signal.action.lower() == 'buy':
            raw_return = (exit_price - entry_price) / entry_price
        elif signal.action.lower() == 'sell':
            raw_return = (entry_price - exit_price) / entry_price
        else:
            raise ValueError(f"信号 {signal.agent_name} 动作类型未知: {signal.action}")
        
        # 涨跌停处理：对于日收益率超过40%的情况报错
        if abs(raw_return) > 0.40:
            raise ValueError(f"信号 {signal.agent_name} 标的 {symbol_code} 收益率 {raw_return:.2%} 超过40%，疑似涨跌停")
        
        return raw_return
