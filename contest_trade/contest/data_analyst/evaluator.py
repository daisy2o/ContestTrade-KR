"""
Contest评估模块

负责：
- 因子文本的LLM评估
- observation提取
- mention识别和symbol创建
- 评分和reward计算
"""

import asyncio
import logging
import re
from typing import List, Dict, Optional

from data_contest_types import (
    FactorData, Observation, Mention, Symbol, Rating, 
    EvaluationResult
)

logger = logging.getLogger(__name__)


class ContestEvaluator:
    """因子评估器"""
    
    def __init__(self, llm, market_manager):
        self.llm = llm
        self.market_manager = market_manager
    
    async def evaluate_factor(self, factor: FactorData, factor_date: str) -> Optional[EvaluationResult]:
        """
        评估单个因子
        
        Args:
            factor: 因子数据
            factor_date: 因子日期 YYYY-MM-DD
            
        Returns:
            评估结果，失败时返回None
        """
        try:
            logger.info(f"开始评估因子: {factor.agent_name} - {factor_date}")
            
            # 1. 提取observations
            observations = await self._extract_observations(
                factor.context_string, 
                f"{factor_date} 09:00:00"
            )
            
            if not observations:
                logger.warning(f"未提取到observations: {factor.agent_name}")
                return None
            
            # 2. 处理每个observation
            await self._process_observations(observations)
            
            # 3. 计算总reward
            total_reward, valid_count = self._calculate_total_reward(observations)
            
            # 即使没有有效价格数据，也要给出评估结果，reward为0
            if valid_count == 0:
                logger.warning(f"没有有效的价格数据，reward设为0: {factor.agent_name}")
                final_reward = 0.0
            else:
                final_reward = total_reward / valid_count
            
            # 4. 构建评估结果
            result = EvaluationResult(
                factor_agent_name=factor.agent_name,
                factor_date=factor_date,
                reward=final_reward,
                observations=observations,
                meta={
                    "reward": final_reward,
                    "symbols_count": sum(len(obs.symbols) for obs in observations),
                    "observations_count": len(observations)
                }
            )
            
            logger.info(f"评估完成: {factor.agent_name} - reward: {result.reward:.3f}")
            return result
            
        except Exception as e:
            logger.error(f"评估因子异常: {factor.agent_name} - {e}")
            return None
    
    async def _extract_observations(self, factor_content: str, factor_timestamp: str) -> List[Observation]:
        """从因子内容中提取observations"""
        try:
            prompt = f"""
请把下面的文本进行拆分，拆分成观点和事实，形成独立的observation。

## 因子内容
{factor_content}

## 抽取要求
1. 每个observation应该是一个独立的、完整的观点或事实，没有任何其他的依赖。
2. 每个observation的字数控制在100字左右。
3. 必须包含足够的要素，主谓宾完整，不省略关键信息，让其他人能够立刻理解这个observation的含义。
4. 所有的observations加在一起要涵盖文章提到的所有内容，不要有缺失。

## 输出格式
请严格按照以下格式输出，enclosed by <Output> and </Output>：

<Output>
<observation>xxx</observation>
<observation>xxx</observation>
...
</Output>

## 注意事项
1. 确保每个observation都是独立的，不重复
2. 内容要具体、准确，避免模糊表述
3. 如果因子内容质量不高或信息不足，可以返回较少的observation

请开始抽取observation。
"""
            
            messages = [{"role": "user", "content": prompt}]
            response = await self.llm.a_run(messages)
            response_content = response.content.strip()
            
            # 解析结果
            observation_blocks = re.findall(r'<observation>(.*?)</observation>', response_content, re.DOTALL)
            
            observations = []
            for i, block in enumerate(observation_blocks, 1):
                content_text = block.strip()
                if content_text:  # 确保内容不为空
                    obs = Observation(
                        id=f"obs_{i:03d}",
                        content=content_text,
                        timestamp=factor_timestamp
                    )
                    observations.append(obs)
            
            logger.debug(f"提取了 {len(observations)} 个observations")
            return observations
            
        except Exception as e:
            logger.error(f"提取observations失败: {e}")
            return []
    
    async def _process_observations(self, observations: List[Observation]):
        """批量处理observations"""
        # 并发处理所有observations
        tasks = [self._process_single_observation(obs) for obs in observations]
        await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _process_single_observation(self, observation: Observation):
        """处理单个observation"""
        try:
            # 1. 提取mentions
            mentions = await self._extract_mentions(observation.content)
            observation.mentions = mentions
            
            # 2. 创建symbols
            symbols = await self._create_symbols(mentions)
            observation.symbols = symbols
            
            # 3. 评分symbols
            await self._rate_symbols(observation)
            
            # 4. 获取价格变化数据
            self._get_price_changes(observation)
            
        except Exception as e:
            logger.error(f"处理observation失败: {e}")
    
    async def _extract_mentions(self, observation_content: str) -> List[Mention]:
        """从observation中提取mentions"""
        try:
            prompt = f"""
你是一个精通金融的实体识别助手。你的任务是从一段文本(Observation)内容中识别出所有可能受文本描述事件影响的mentions（个股或公司、行业或板块）。

## 文本(Observation)内容
{observation_content}

## 任务要求
1.  mention的类型有两种：company(公司), industry(行业或板块)。
2.  输出的mention数量在1-3个之间。每个mention的content字段中，只包含一个词。
3.  如果没有直接提及任何company，则进一步推理出可能受影响的company。
4.  如果也无法推理出具体的company，则进一步推理出可能受影响的industry。

## 输出格式
请严格按照以下格式输出，enclosed by <Output> and </Output>：

<Output>
<mention>
<content>xxx</content>
<type>company</type>
</mention>
<mention>
<content>xxx</content>
<type>industry</type>
</mention>
...
</Output>

请开始抽取。
"""
            
            messages = [{"role": "user", "content": prompt}]
            response = await self.llm.a_run(messages)
            response_content = response.content.strip()
            
            # 解析mentions
            mention_blocks = re.findall(r'<mention>(.*?)</mention>', response_content, re.DOTALL)
            mentions = []
            
            for block in mention_blocks:
                content_match = re.search(r'<content>(.*?)</content>', block, re.DOTALL)
                type_match = re.search(r'<type>(.*?)</type>', block, re.DOTALL)
                
                if content_match and type_match:
                    mentions.append(Mention(
                        content=content_match.group(1).strip(),
                        type=type_match.group(1).strip()
                    ))
            
            return mentions  # 返回所有解析到的mentions
            
        except Exception as e:
            logger.error(f"提取mentions失败: {e}")
            return []
    
    async def _create_symbols(self, mentions: List[Mention]) -> List[Symbol]:
        """根据mentions创建symbols"""
        symbols = []
        
        for mention in mentions:
            try:
                if mention.type == "company":
                    # 使用市场管理器查找股票代码
                    symbol_name, symbol_code = self.market_manager.fix_symbol_code(
                        "CN-Stock", mention.content, ""
                    )
                    
                    symbol = Symbol(
                        name=symbol_name,
                        market="CN-Stock",
                        code=symbol_code,
                        type="company",
                        description=f"公司: {symbol_name}"
                    )
                    symbols.append(symbol)
                    
                elif mention.type == "industry":
                    # 行业mentions
                    symbol = Symbol(
                        name=mention.content,
                        market="CN-Stock", 
                        code="",
                        type="industry",
                        description=f"行业: {mention.content}"
                    )
                    symbols.append(symbol)
                    
            except Exception as e:
                logger.error(f"创建symbol失败: {mention.content} - {e}")
                continue
        
        return symbols
    
    async def _rate_symbols(self, observation: Observation):
        """对observation中的symbols进行评分"""
        if not observation.symbols:
            return
        
        try:
            # 构建symbol列表字符串
            symbol_list_str = ""
            for i, symbol in enumerate(observation.symbols, 1):
                symbol_list_str += f"{i}. 名称: {symbol.name}, 代码: {symbol.code}, 类型: {symbol.type}\n"
            
            prompt = f"""
你的任务是基于一个核心"事件"，分析它对"标的列表"中每一个标的可能产生的短期影响。评估其下一个交易日的潜在表现，并形成一个融合**"预期涨跌幅"**与**"预测置信度"**的综合决策分数。

## 核心事件 (Observation)
{observation.content}

## 标的列表 (Symbol List)
{symbol_list_str}

## 任务要求
1.  分析"事件"对"标的列表"中每一个标的可能产生的短期影响，给出分析过程，不超过100字。
2.  对每个标的的预测应该是独立的。
3.  按照以下五档标准，给出你的**综合决策分数**。

**打分标准：-2到2的整数，融合了"预期幅度"与"置信度"**

2 : 认为股价会显著上涨或对上涨有很高置信度
1 : 认为股价会小幅上涨或对上涨有较高置信度
0 : 认为股价会窄幅震荡或对涨跌没有明确倾向
-1 : 认为股价会小幅下跌或对下跌有较高置信度
-2 : 认为股价会显著下跌或对下跌有很高置信度

## 输出格式
请严格按照以下格式输出，enclosed by <Output> and </Output>：

<Output>
<result>
<symbol_name>标的1的名称</symbol_name>
<reason>对标的1的分析原因</reason>
<rating>对标的1的评分</rating>
</result>
<result>
<symbol_name>标的2的名称</symbol_name>
<reason>对标的2的分析原因</reason>
<rating>对标的2的评分</rating>
</result>
...
</Output>

请开始分析。
"""
            
            messages = [{"role": "user", "content": prompt}]
            response = await self.llm.a_run(messages)
            response_content = response.content.strip()
            
            # 解析评分结果
            result_blocks = re.findall(r'<result>(.*?)</result>', response_content, re.DOTALL)
            
            for block in result_blocks:
                symbol_name_match = re.search(r'<symbol_name>(.*?)</symbol_name>', block, re.DOTALL)
                reason_match = re.search(r'<reason>(.*?)</reason>', block, re.DOTALL)
                rating_match = re.search(r'<rating>(.*?)</rating>', block, re.DOTALL)
                
                if symbol_name_match and reason_match and rating_match:
                    symbol_name = symbol_name_match.group(1).strip()
                    reason = reason_match.group(1).strip()
                    try:
                        rating_value = int(rating_match.group(1).strip())
                        # 限制rating范围
                        rating_value = max(-2, min(2, rating_value))
                    except:
                        continue
                    
                    # 找到对应的symbol并添加rating
                    for symbol in observation.symbols:
                        if symbol.name == symbol_name:
                            symbol.rating = Rating(rating=rating_value, reason=reason)
                            break
            
        except Exception as e:
            logger.error(f"评分symbols失败: {e}")
    
    def _get_price_changes(self, observation: Observation):
        """
        获取股票价格变化数据 - 使用GLOBAL_MARKET_MANAGER获取真实价格
        """
        for symbol in observation.symbols:
            if symbol.rating is None:
                continue
                
            if symbol.code and symbol.type == "company":
                try:
                    day_price_chg = self._calculate_day_price_change(symbol, observation.timestamp)
                    if day_price_chg is not None:
                        symbol.day_price_chg = day_price_chg
                    else:
                        logger.warning(f"无法获取股票价格数据: {symbol.name}:{symbol.code}")
                except Exception as e:
                    logger.error(f"获取价格数据异常: {symbol.name}:{symbol.code} - {e}")
    
    def _calculate_day_price_change(self, symbol: Symbol, timestamp: str) -> Optional[float]:
        """计算日价格变化百分比"""
        # 获取当前价格
        current_price = self.market_manager.get_symbol_price(
            market_name=symbol.market,
            symbol=symbol.code,
            trigger_time=timestamp,
            date_diff=0
        )
        
        if current_price is None:
            return None
        
        # 如果是涨跌停，返回0
        if current_price['open'] == current_price['limit_price']:
            return 0
        
        # 获取下一交易日价格
        next_price = self.market_manager.get_symbol_price(
            market_name=symbol.market,
            symbol=symbol.code,
            trigger_time=timestamp,
            date_diff=1
        )
        
        if next_price is None:
            return None
            
        # 计算价格变化百分比
        price_change_pct = (next_price['open'] - current_price['open']) / current_price['open'] * 100
        return round(price_change_pct, 4)
    
    def _calculate_total_reward(self, observations: List[Observation]) -> tuple[float, int]:
        """
        计算所有observations的总reward
        
        Returns:
            (总reward, 有效样本数)
        """
        total_reward = 0.0
        valid_count = 0
        
        for obs in observations:
            for symbol in obs.symbols:
                if symbol.rating and symbol.day_price_chg is not None:
                    rating = symbol.rating.rating
                    day_price_chg = symbol.day_price_chg
                    
                    # 价格变化裁剪（复用原代码逻辑，写死-20到20）
                    capped_price_chg = max(-20.0, min(20.0, day_price_chg))
                    
                    if rating > 0:
                        reward = float(rating) * capped_price_chg
                        total_reward += reward
                    
                    valid_count += 1
        
        return total_reward, valid_count
    
