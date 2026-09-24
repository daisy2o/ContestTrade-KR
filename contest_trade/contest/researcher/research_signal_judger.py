"""
Research信号评分器
"""

import json
import logging
import asyncio
import requests
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from research_contest_types import SignalData, JudgerScore

logger = logging.getLogger(__name__)


class ResearchSignalJudger:
    """研究信号评分器 - 使用多个LLM对信号进行评分"""
    
    def __init__(self, workspace_dir: str, window_m: int = 5, data_manager=None):
        self.workspace_dir = Path(workspace_dir)
        self.judger_scores_dir = self.workspace_dir / "judger_scores"
        self.window_m = window_m
        self.data_manager = data_manager
        
        # 创建输出目录
        self.judger_scores_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"ResearchSignalJudger初始化完成 - 历史窗口: {window_m}天")
    
    def build_scoring_prompt(self, signals: Dict[str, SignalData], historical_returns: Optional[Dict[str, float]] = None) -> str:
        """
        构建Juder评分Prompt
        """
        date = list(signals.values())[0].trigger_time.split(' ')[0] if signals else 'unknown'
        
        # 构建历史表现信息
        historical_performance_text = ""
        if historical_returns:
            historical_performance_text = "\n历史表现 (过去5天平均收益率):\n"
            for agent_name, avg_return in historical_returns.items():
                if avg_return is not None:
                    historical_performance_text += f"  {agent_name}: {avg_return:.2%}\n"
                else:
                    historical_performance_text += f"  {agent_name}: 无历史数据\n"
            historical_performance_text += "\n"
        
        # 构建所有信号的信息
        signals_info = []
        for signal_name, signal_data in signals.items():
            # 构建证据列表
            evidence_text = ""
            for i, evidence in enumerate(signal_data.evidence_list, 1):
                evidence_text += f"    {i}. {evidence.get('description', '')} (时间: {evidence.get('time', '')}, 来源: {evidence.get('from_source', '')})\n"
            
            limitations_text = ""
            for i, limitation in enumerate(signal_data.limitations, 1):
                limitations_text += f"    {i}. {limitation}\n"
            
            signal_info = f"""
Researcher: {signal_name}
Stock: {signal_data.symbol_name} ({signal_data.symbol_code})
Action: {signal_data.action}
Opportunity: {signal_data.has_opportunity}
Belief: {signal_data.belief}

Thinking:
{signal_data.thinking}

Support Evidence:
{evidence_text}

Limitations:
{limitations_text}
"""
            signals_info.append(signal_info)
        
        all_signals_text = "\n" + "="*80 + "\n".join(signals_info)

        prompt = f"""
You are a strict stock investment analyst who needs to critically evaluate trading signals.

Evaluation date: {date}
{historical_performance_text}
Here is the signal information for all researchers:

{all_signals_text}

Please evaluate all signals based on the following critique criteria:

Critique Criteria (Starting from 100 points, only deduct points, no addition):
1. Analysis quality issues: Confused thinking, lack of depth, unclear logic
2. Insufficient evidence: Little evidence, poor quality, lack of persuasiveness, inadequate evidence
3. Risk assessment issues: Insufficient understanding of limitations, unreasonable probability assessment, weak risk awareness
4. Opportunity judgment issues: Inaccurate has_opportunity judgment, poor opportunity identification
5. Logical flaws: Logical contradictions in analysis, weak reasoning
6. Data issues: Improper data usage, incorrect data interpretation
7. Historical performance: Consider the researcher's recent track record when evaluating credibility

Strictly follow the format below for output, one line per researcher:
agent_0: 75|Lack of analytical depth (-15), Moderate evidence (-10)
...
agent_n: 45|Confused analysis logic (-25), Insufficient evidence (-15), Lack of risk assessment (-15)

Format notes:
- Each line format: Researcher ID: Final score|Critique reasons (only deductions)
- Final score range: 0 to 100 (deducted from 100)
- Only question signals and logic and deduct points, no additions
- Critique reasons should detail deduction reasons and specific issues
- Consider historical performance when evaluating researcher credibility
- Must use "|" to separate score and reasons, do not use other separators
"""

        return prompt
    
    async def call_llm_for_scoring(self, prompt: str, judger_id: int, llm_config: Dict, max_retries: int = 3) -> str:
        """调用LLM进行评分"""
        headers = {
            'Authorization': f'Bearer {llm_config["api_key"]}',
            'Content-Type': 'application/json'
        }
        
        data = {
            'model': llm_config["model_name"],
            'messages': [{'role': 'user', 'content': prompt}],
            'max_tokens': 10000,
            'temperature': 0.1
        }
        
        for attempt in range(max_retries + 1):
            response = requests.post(
                llm_config["api_base"],
                headers=headers,
                json=data,
                timeout=120
            )
            
            if response.status_code == 200:
                result = response.json()
                content = result['choices'][0]['message']['content']
                logger.debug(f"评分员 {judger_id} 评分完成")
                return content
            else:
                raise RuntimeError(f"评分员 {judger_id} API调用失败: {response.status_code}")
        
        raise RuntimeError(f"评分员 {judger_id} 经过{max_retries + 1}次尝试后仍然失败")
    
    def parse_llm_scores(self, content: str, judger_id: int) -> Dict[str, JudgerScore]:
        """解析LLM返回的评分结果"""
        scores = {}
        lines = content.strip().split('\n')
        
        for line in lines:
            line = line.strip()
            if ':' in line and '|' in line:
                parts = line.split(':', 1)
                if len(parts) == 2:
                    agent_name = parts[0].strip()
                    score_reason = parts[1].strip()
                    
                    if '|' in score_reason:
                        score_str, reasoning = score_reason.split('|', 1)
                        score = float(score_str.strip())
                        scores[agent_name] = JudgerScore(
                            score=score,
                            reasoning=reasoning.strip(),
                            judger_id=judger_id
                        )
                    else:
                        raise ValueError(f"评分格式错误，缺少'|'分隔符: {score_reason}")
                else:
                    raise ValueError(f"行格式错误，缺少':'分隔符: {line}")
            elif line.strip():  # 非空行但格式不对
                raise ValueError(f"行格式不符合要求: {line}")
        
        if not scores:
            raise ValueError("LLM返回的评分结果为空")
        
        return scores
    
    async def judge_signals(self, signals: Dict[str, SignalData], trigger_time: str, 
                          num_judgers: int, llm_config: Dict) -> Dict[str, List[float]]:
        """
        评估信号
        
        Args:
            signals: 信号数据
            trigger_time: 触发时间
            num_judgers: 评分员数量
            llm_config: LLM配置
            
        Returns:
            Dict[signal_name, List[scores]]: 原始评分结果，每个信号对应多个评分员的分数列表
        """
        logger.info(f"开始评估 {len(signals)} 个信号")
        
        historical_returns = await self._calculate_historical_returns(trigger_time, signals)
        
        prompt = self.build_scoring_prompt(signals, historical_returns)

        tasks = []
        for judger_id in range(num_judgers):
            task = self._score_with_single_judger(judger_id, prompt, llm_config)
            tasks.append(task)
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        all_scores = {}
        all_responses = {}
        
        for judger_id, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"评分员 {judger_id} 评分失败: {result}")
                all_responses[f"judger_{judger_id}"] = f"错误: {result}"
                continue
            
            response, scores = result
            all_responses[f"judger_{judger_id}"] = response
            
            for signal_name, score_obj in scores.items():
                if signal_name not in all_scores:
                    all_scores[signal_name] = []
                all_scores[signal_name].append(score_obj)
        
        self._save_judge_results(trigger_time, all_scores, all_responses)
        
        logger.info(f"信号评估完成: {len(all_scores)} 个信号获得评分")
        judge_scores = {}
        for signal_name, score_list in all_scores.items():
            judge_scores[signal_name] = [score.score for score in score_list]
        
        return judge_scores
    
    async def _score_with_single_judger(self, judger_id: int, prompt: str, llm_config: Dict) -> Tuple[str, Dict[str, JudgerScore]]:
        """单个评分员评分"""
        response = await self.call_llm_for_scoring(prompt, judger_id, llm_config)
        scores = self.parse_llm_scores(response, judger_id)
        return response, scores
    
    async def _calculate_historical_returns(self, trigger_time: str, signals: Dict[str, SignalData]) -> Optional[Dict[str, float]]:
        """
        计算历史收益率 - 计算每个研究员过去window_m天的日均收益率表现
        
        Args:
            trigger_time: 当前触发时间
            signals: 当前信号数据
            
        Returns:
            Dict[agent_name, avg_return]: 每个研究员的历史平均收益率
        """
        if not self.data_manager:
            raise ValueError("未提供数据管理器，无法计算历史收益率")
        
        # 解析当前时间
        current_dt = datetime.strptime(trigger_time.split(' ')[0], "%Y-%m-%d")
        
        # 获取所有研究员名称
        agent_names = list(signals.keys())
        historical_returns = {}
        
        for agent_name in agent_names:
            agent_returns = []
            
            # 누수 차단: 판정기도 과거 '보상'을 쓰므로 콘테스트와 같은 규칙을 적용한다.
            # days_back=1(직전 거래일) 신호의 보상은 **판단일 당일 시가**를 필요로 하는데
            # 판단 시각 08:30은 개장 전이라 그 값이 없다.
            # (load_historical_signals만 고치고 여기를 빼면 누수가 판정기로 새어 든다.)
            try:
                from contest.researcher.reward_availability import eligible_history_dates
                from utils.kr_data_utils import GLOBAL_KR_CLIENT
                allowed = set(eligible_history_dates(
                    current_dt.strftime("%Y-%m-%d"), self.window_m, GLOBAL_KR_CLIENT))
            except Exception:
                allowed = None

            for days_back in range(1, self.window_m + 1):
                history_date = current_dt - timedelta(days=days_back)
                history_date_str = history_date.strftime("%Y-%m-%d")
                if allowed is not None and history_date_str not in allowed:
                    continue
                
                history_signals = self.data_manager.load_signals_data(history_date_str)
                
                if history_signals and agent_name in history_signals:
                    signal = history_signals[agent_name]

                    # 检查信号是否有reward数据
                    if hasattr(signal, 'reward') and signal.reward is not None:
                        agent_returns.append(signal.reward)
                    else:
                        # KR 수정: 개별 신호의 reward 계산 실패(기회없음·가격 미도래 등)는
                        # 건너뛴다 — 원본은 예외로 전체 심사가 중단됐다
                        try:
                            reward = await self.data_manager.calculate_signal_reward(signal)
                            agent_returns.append(reward)
                        except Exception:
                            continue

            # 计算平均收益率
            # KR 수정: 이력 없는 에이전트는 None(프롬프트가 "무이력"으로 처리) —
            # 리플레이 초기 콜드 스타트에서 크래시하지 않도록
            if agent_returns:
                avg_return = sum(agent_returns) / len(agent_returns)
                historical_returns[agent_name] = avg_return
            else:
                historical_returns[agent_name] = None
                
        logger.info(f"计算历史收益率完成：{len(historical_returns)} 个研究员的历史数据")
        return historical_returns
    
    def _save_judge_results(self, trigger_time: str, all_scores: Dict[str, List[JudgerScore]], all_responses: Dict[str, str]):
        """保存评分结果"""
        timestamp = trigger_time.replace(' ', '_').replace(':', '')
        
        scores_file = self.judger_scores_dir / f"scores_{timestamp}.json"
        
        scores_data = {}
        for signal_name, scores_list in all_scores.items():
            scores_data[signal_name] = [
                {
                    'score': score.score,
                    'reasoning': score.reasoning,
                    'judger_id': score.judger_id
                }
                for score in scores_list
            ]
        
        with open(scores_file, 'w', encoding='utf-8') as f:
            json.dump({
                'trigger_time': trigger_time,
                'scores': scores_data,
                'responses': all_responses
            }, f, ensure_ascii=False, indent=2)
        
        logger.info(f"评分结果已保存到: {scores_file}")
