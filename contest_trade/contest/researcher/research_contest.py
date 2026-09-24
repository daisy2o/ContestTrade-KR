"""
ResearchContest - 统一的研究信号竞争系统

核心功能：
1. Evaluation: 评估历史信号的市场表现，计算reward  
2. Prediction: 基于历史reward预测信号排序
3. Selection: 选择优质信号为投资提供权重分配
"""

import json
import os
import sys
import logging
import asyncio
from pathlib import Path
from typing import List, Dict, Optional, Any
from datetime import datetime, timedelta

PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
sys.path.append(str(PROJECT_ROOT))

from models.llm_model import GLOBAL_LLM
from utils.market_manager import GLOBAL_MARKET_MANAGER
from config.config import cfg
from research_contest_types import SignalData, ResearchContestResult
from research_data_manager import ResearchDataManager
from research_predictor import ResearchPredictor
from research_weight_optimizer import ResearchWeightOptimizer
from research_signal_judger import ResearchSignalJudger

logger = logging.getLogger(__name__)


class ResearchContest:
    """研究信号竞争系统主控制器"""
    
    def __init__(self, target_agents: List[str] = None):
        self.history_window_days = 5
        self.target_agents = target_agents or []
        # KR 수정: PROJECT_ROOT는 contest_trade 디렉토리인데 하위 모듈들은 '저장소
        # 루트'(그 아래 contest_trade/agents_workspace)를 기대 — 원본은 유령 경로를
        # 가리켜 이력이 항상 비어 있었다 (죽은 코드 원인 중 하나)
        repo_root = PROJECT_ROOT.parent
        workspace = PROJECT_ROOT / "agents_workspace"
        self.data_manager = ResearchDataManager(self.history_window_days, repo_root, target_agents)
        self.data_manager.set_market_manager(GLOBAL_MARKET_MANAGER)
        self.predictor = ResearchPredictor(self.history_window_days)
        self.weight_optimizer = ResearchWeightOptimizer(str(workspace))
        self.signal_judger = ResearchSignalJudger(str(workspace), self.history_window_days, self.data_manager)
        
        logger.info(f"ResearchContest初始化完成 - 历史窗口: {self.history_window_days}天, 目标agents: {len(self.target_agents)}个")
    
    async def run_research_contest(self, trigger_time: str, current_signals: Dict[str, SignalData] = None) -> ResearchContestResult:
        logger.info(f"🎯 开始运行ResearchContest - {trigger_time}")
        
        try:
            current_date = trigger_time.split(' ')[0]
            
            # 步骤1: 加载历史信号数据
            logger.info("步骤1: 加载历史信号数据")
            agent_signals = self.data_manager.load_historical_signals(current_date)
            
            # 统计信息
            total_signals = sum(len([s for s in signals_list if s is not None]) for signals_list in agent_signals.values())
            total_evaluated = sum(len([s for s in signals_list if s is not None and s.has_contest_data()]) for signals_list in agent_signals.values())
            logger.info(f"加载了 {total_signals} 个历史信号")
            logger.info(f"其中 {total_evaluated} 个已有评估数据，{total_signals - total_evaluated} 个需要评估")
            
            # 步骤2: 评估历史信号
            await self._evaluate_missing_signals(agent_signals, current_date)
            
            # 步骤3: 获取当天信号的judge评分
            if not current_signals:
                raise ValueError("预测模型需要当天信号的judge评分数据！请提供current_signals参数。")
            
            logger.info("步骤3: 获取当天信号judge评分")
            current_judge_scores = await self._get_current_judge_scores(current_signals, trigger_time)
            
            if not current_judge_scores:
                raise ValueError("无法获取当天信号的judge评分！预测模型需要12个特征，包括7个judge评分特征。")
            
            # 步骤4: 预测未来n天夏普比率
            logger.info("步骤4: 预测未来夏普比率")
            predicted_sharpe_ratios = self._predict_signal_values(current_date, agent_signals, current_judge_scores)
            
            # 步骤5: 基于预测夏普比率分配权重
            logger.info("步骤5: 基于预测夏普比率分配权重")
            optimized_weights = self.weight_optimizer.optimize_weights_by_sharpe(predicted_sharpe_ratios, trigger_time)
            
            # 步骤6: 保存结果
            result = self.weight_optimizer.save_final_results_by_sharpe(trigger_time, optimized_weights, predicted_sharpe_ratios)
            
            logger.info(f"✅ ResearchContest完成: {result.get_summary()}")
            return result
            
        except Exception as e:
            logger.error(f"ResearchContest运行失败: {e}")
            raise RuntimeError(f"运行失败: {e}")

    async def run_research_pipeline(self, trigger_time: str, workspace_dir: str = None) -> Dict[str, Any]:
        print(f"🔬 开始运行Research Contest流程，时间: {trigger_time}")
        
        try:
            result = await self.run_research_contest(trigger_time)
            
            if result:
                print("✅ Research Contest流程完成")
                print(f"   优化权重数量: {len(result.optimized_weights)}")
                print(f"   有效信号数量: {result.valid_signals}")
                
                return {
                    'status': 'success',
                    'trigger_time': trigger_time,
                    'optimized_weights': result.optimized_weights,
                    'predicted_sharpe_ratios': result.predicted_sharpe_ratios,
                    'total_signals': result.total_signals,
                    'valid_signals': result.valid_signals,
                    'selection_method': result.selection_method
                }
            else:
                raise RuntimeError("Research Contest流程失败: 结果为空")
                
        except Exception as e:
            raise RuntimeError(f"Research Contest流程异常: {e}")

    def filter_valid_signals(self, signals_data: Dict[str, SignalData]) -> Dict[str, SignalData]:
        valid_signals = {}
        
        for signal_name, signal_data in signals_data.items():
            has_opportunity = signal_data.has_opportunity
            if has_opportunity.lower() == 'yes':
                valid_signals[signal_name] = signal_data
                print(f"   ✅ 保留有效研究信号: {signal_name} ({signal_data.symbol_name})")
            else:
                print(f"   ❌ 过滤无效研究信号: {signal_name} (has_opportunity={has_opportunity})")
        
        return valid_signals

    def get_signal_details(self, trigger_time: str, signal_names: List[str]) -> Dict[str, Dict]:
        signal_details = {}
        
        current_signals = self.data_manager.load_current_signals(trigger_time)
        
        for signal_name in signal_names:
            if signal_name in current_signals:
                signal = current_signals[signal_name]
                signal_details[signal_name] = {
                    'symbol_name': signal.symbol_name,
                    'action': signal.action,
                    'probability': signal.probability
                }
            else:
                raise ValueError(f"信号 {signal_name} 不存在于当前信号中")
        
        return signal_details

    def format_signal_output(self, optimized_weights: Dict[str, float], 
                           signal_details: Dict[str, Dict]) -> List[str]:
        output_lines = []

        sorted_weights = sorted(optimized_weights.items(), key=lambda x: x[1], reverse=True)
        
        valid_signals_count = 0
        for signal_name, weight in sorted_weights:
            if weight > 0:
                valid_signals_count += 1
                details = signal_details.get(signal_name, {'symbol_name': 'N/A', 'action': 'N/A', 'probability': 'N/A'})
                symbol_name = details['symbol_name']
                action = details['action']
                probability = details.get('probability', 'N/A')
                output_lines.append(f"   {valid_signals_count}. {symbol_name} - {action} - 概率: {probability} - 权重: {weight:.1%}")
        
        if valid_signals_count == 0:
            output_lines.append("   📊 暂无有效研究信号")
        
        return output_lines

    async def train_prediction_model(self, as_of_date: str = None) -> dict:
        """as-of 시점까지의 자료만으로 학습한다. **폴백을 조용히 쓰지 않는다.**

        돌려주는 dict의 `method`는 셋 중 하나다:
          lightgbm / judge_fallback / insufficient_history
        서로 다른 방식의 결과를 같은 C3로 합치지 않기 위해 호출부가 이 값을 기록한다.
        """
        from contest.researcher import training_asof as TA

        model_dir = Path(__file__).parent / "lightgbm_predictor"
        if as_of_date is None:
            return {"method": TA.METHOD_JUDGE,
                    "사유": "as_of_date 미지정 — 학습 시도 자체를 하지 않는다"}
        try:
            # ③ 모델 파일의 시점 제한: 학습 as-of 가 판단일보다 뒤면 재사용 거부
            usable, why = TA.model_usable_for(model_dir, as_of_date)
            if usable:
                self.predictor._load_lightgbm_models()
                if self.predictor.use_lightgbm:
                    return {"method": TA.METHOD_LIGHTGBM, "사유": f"기존 모델 재사용 — {why}"}
            else:
                print(f"🔒 기존 모델 재사용 불가: {why}")

            training_data = self._collect_historical_training_data(as_of_date)
            if not training_data:
                return {"method": TA.METHOD_INSUFFICIENT, "사유": "학습 자료 0건",
                        "진단": {"수집된_신호수(파일 기준)": 0,
                                 "유효_학습표본수(보상 확정)": 0}}

            # 소급 생성한 judge 점수를 학습 경로(contest_data['judge_scores'])에 주입한다.
            # 학습기는 SignalData에서 읽으므로, 별도 파일에만 저장하면 연결되지 않는다.
            # ⚠️ 각 신호는 **그 신호 당시의 판단 시각**으로 생성된 점수를 받는다
            #    (학습일 기준이 아니다).
            from evaluation.backfill_judge_scores import STORE as _BF
            n_injected, n_missing = 0, 0
            for agent_name, signals in training_data.items():
                for sig in signals:
                    d = sig.trigger_time.split(" ")[0]
                    f = _BF / f"backfill_{d}.json"
                    if not f.exists():
                        n_missing += 1
                        continue
                    raw = json.loads(f.read_text()).get("scores", {}).get(agent_name)
                    if not raw:
                        n_missing += 1
                        continue
                    vals = [x.get("score") if isinstance(x, dict) else x for x in raw]
                    cd = dict(getattr(sig, "contest_data", None) or {})
                    cd["judge_scores"] = vals
                    cd["judge_source"] = "소급 재구성 (당시 판단 시각 기준)"
                    sig.contest_data = cd
                    n_injected += 1
            print(f"   judge 특징 주입: {n_injected}건 (미보유 {n_missing}건)")

            # 유효 (특징, 보상) 쌍을 센다 — 파일 수가 아니다
            pairs = []
            for agent_name, signals in training_data.items():
                for sig in signals:
                    r = None
                    if getattr(sig, "contest_data", None):
                        r = sig.contest_data.get("reward")
                    if r is None:
                        try:
                            r = await self.data_manager.calculate_signal_reward(sig)
                        except Exception:
                            r = None
                    pairs.append((sig, r))
            diag = TA.summarize(pairs)

            # ⚠️ 위 수는 **중복 누적된 후보 표본**이다.
            # _collect_historical_training_data가 날짜마다 '창'을 통째로 모으므로
            # 같은 (날짜, 에이전트)가 여러 번 들어간다. 실제로 만들 수 있는 학습
            # 표본은 **고유 (날짜, 에이전트) 쌍에서 연속 8개(이력 5 + 예측 3) 창**의
            # 수다. 이것을 따로 세지 않으면 학습 가능성을 과대평가한다.
            uniq = {}
            for sig, r in pairs:
                d = sig.trigger_time.split(" ")[0]
                has_j = bool((getattr(sig, "contest_data", None) or {}).get("judge_scores"))
                uniq[(d, sig.agent_name)] = (r is not None) and has_j
            by_agent = {}
            for (d, a), ok in uniq.items():
                by_agent.setdefault(a, []).append((d, ok))
            H, P = self.predictor.history_window_days, self.predictor.prediction_window_days
            buildable = 0
            for a, rows in by_agent.items():
                usable = sorted(d for d, ok in rows if ok)
                buildable += max(0, len(usable) - (H + P) + 1)
            diag["고유_(날짜,에이전트)_쌍"] = len(uniq)
            diag["judge·보상_모두_확보"] = sum(1 for v in uniq.values() if v)
            diag["실제_구성가능_학습표본"] = buildable
            diag["창_요건"] = f"연속 {H + P}개 (이력 {H} + 예측 {P})"
            diag["주의"] = ("'유효_학습표본수(보상 확정)'는 창 누적으로 중복된 수다. "
                            "학습 가능성은 '실제_구성가능_학습표본'으로 판단한다.")

            method, why = TA.decide_method(diag)
            if method != TA.METHOD_LIGHTGBM:
                # 부족한 자료를 억지로 학습시켜 '완료'로 처리하지 않는다
                return {"method": method, "사유": why, "진단": diag, "as_of": as_of_date}

            ok = self.predictor.train_lightgbm_model(training_data)
            if not ok:
                return {"method": TA.METHOD_JUDGE, "사유": "LightGBM 학습 실패",
                        "진단": diag, "as_of": as_of_date}
            TA.save_train_meta(model_dir, as_of_date,
                               diag["유효_학습표본수(보상 확정)"], diag)
            return {"method": TA.METHOD_LIGHTGBM, "사유": why, "진단": diag,
                    "as_of": as_of_date}

        except Exception as e:  # noqa: BLE001
            return {"method": TA.METHOD_JUDGE, "사유": f"학습 경로 예외: {e}",
                    "as_of": as_of_date}

    def get_model_status(self) -> Dict[str, Any]:
        model_dir = Path(__file__).parent / "lightgbm_predictor"
        mean_model_path = model_dir / "lgbm_mean_model.joblib"
        std_model_path = model_dir / "lgbm_std_model.joblib"
        
        status = {
            'model_dir': str(model_dir),
            'mean_model_exists': mean_model_path.exists(),
            'std_model_exists': std_model_path.exists(),
            'models_loaded': self.predictor.use_lightgbm,
            'mean_model_path': str(mean_model_path),
            'std_model_path': str(std_model_path)
        }
        
        if mean_model_path.exists():
            status['mean_model_size'] = os.path.getsize(mean_model_path)
            status['mean_model_modified'] = os.path.getmtime(mean_model_path)
                
        if std_model_path.exists():
            status['std_model_size'] = os.path.getsize(std_model_path)
            status['std_model_modified'] = os.path.getmtime(std_model_path)
        
        return status

    def _collect_historical_training_data(self, as_of_date: str = None) -> Dict[str, List]:
        """학습 자료 수집 — **재생 판단 시각(as_of_date) 기준**.

        기존 구현은 `datetime.now()`(실행 시점)로 180일을 훑어, 재생 실험에서
        판단 시각과 무관한 자료를 학습에 넣었다. as_of_date를 명시적으로 받는다.

        시점 제한을 두 겹으로 건다:
          ① 특징: 신호 생성일 < as_of_date
          ② 정답: 보상 계산에 필요한 가격이 as_of_date 이전에 확정된 것만
                  (`load_historical_signals`가 이미 보상 확정 규칙을 적용한다)
        """
        from contest.researcher.training_asof import TRAIN_WINDOW_DAYS

        if as_of_date is None:
            raise ValueError(
                "as_of_date가 필요하다. datetime.now() 기준 수집은 재생 실험에서 "
                "미래 자료를 학습에 넣는다 — 명시적으로 판단일을 넘길 것.")

        print(f"📊 학습 자료 수집 (as-of {as_of_date}, 창 {TRAIN_WINDOW_DAYS}일)")

        training_data = {}
        current_date = datetime.strptime(as_of_date, "%Y-%m-%d")
        valid_days = 0

        for days_back in range(TRAIN_WINDOW_DAYS, 0, -1):
            date = current_date - timedelta(days=days_back)
            date_str = date.strftime("%Y-%m-%d")
            if date_str >= as_of_date:      # ① 특징의 시점 제한
                continue
            
            try:
                day_signals = self.data_manager.load_historical_signals(date_str)
                
                day_has_data = False
                for agent_name, signals_list in day_signals.items():
                    if agent_name not in training_data:
                        training_data[agent_name] = []
                    
                    for signal in signals_list:
                        if signal is not None:
                            training_data[agent_name].append(signal)
                            day_has_data = True
                
                if day_has_data:
                    valid_days += 1
            
            except Exception as e:
                continue
        
        # ⚠️ 아래 수는 **파일 기준 신호 수**이지 유효 학습 표본 수가 아니다.
        # 보상이 확정된 쌍의 수·결측·분포는 train_prediction_model에서 별도로 센다.
        total_samples = sum(len(signals) for signals in training_data.values())
        print(f"📈 {valid_days}일치 수집 | 신호 {total_samples}건(파일 기준), "
              f"agent {len(training_data)}개")

        return training_data

    async def _evaluate_missing_signals(self, agent_signals: Dict[str, List[Optional[SignalData]]], current_date: str):
        """评估缺失reward数据的信号（使用数据管理器计算收益率）"""
        logger.info("评估缺失的信号数据")
        
        signals_to_evaluate = []
        for agent_name, signals_list in agent_signals.items():
            for signal in signals_list:
                if signal is None:
                    continue
                if not signal.has_contest_data():
                    signal_date = signal.trigger_time.split(' ')[0]
                    signals_to_evaluate.append((signal, signal_date))
        
        if not signals_to_evaluate:
            logger.info("所有信号都已有评估数据，跳过评估步骤")
            return
        
        logger.info(f"需要评估 {len(signals_to_evaluate)} 个信号")
        
        n_abstain = 0
        for signal, signal_date in signals_to_evaluate:
            # 정상 기권(has_opportunity=no 또는 빈 제출)은 **실패가 아니다**.
            # 보상이 정의되지 않을 뿐이므로 reward=None 으로 두고 넘어간다.
            # (예외를 던지면 과거에 기권이 하나라도 있는 날은 콘테스트가 통째로
            #  죽는다 — 실측으로 06-19·06-24가 그렇게 중단됐다.)
            ho = (getattr(signal, "has_opportunity", "") or "").strip().lower()
            if ho != "yes":
                signal.contest_data = {
                    'reward': None,
                    'evaluation_date': signal_date,
                    'evaluation_method': 'abstained_no_reward',
                    'note': '정상 기권 — 보상 미정의(실행 실패 아님)'
                }
                n_abstain += 1
                continue
            try:
                reward = await self.data_manager.calculate_signal_reward(signal)
                method = 'market_return'
            except Exception as e:  # noqa: BLE001
                # 가격 결측·거래정지 등은 그 신호를 이력에서 빼되 실행은 계속한다
                logger.warning(f"보상 계산 실패({signal.agent_name} {signal_date}): {e}")
                reward, method = None, 'reward_unavailable'
            signal.contest_data = {
                'reward': reward,
                'evaluation_date': signal_date,
                'evaluation_method': method
            }
        if n_abstain:
            logger.info(f"정상 기권 {n_abstain}건 — 보상 미정의로 처리(실패 아님)")
        
        logger.info(f"评估完成: {len(signals_to_evaluate)} 个信号全部成功")

    def _predict_signal_values(self, current_date: str, agent_signals: Dict[str, List[Optional[SignalData]]], 
                             current_judge_scores: Dict[str, List[float]]) -> dict:
        """预测信号得分（夏普比率）"""
        logger.info("预测未来夏普比率")
        
        signal_scores = self.predictor.predict_signal_scores(current_date, agent_signals, current_judge_scores)
        return signal_scores
    
    async def _get_current_judge_scores(self, current_signals: Dict[str, SignalData], 
                                      trigger_time: str) -> Dict[str, List[float]]:
        """获取当天信号的judge评分"""
        logger.info(f"获取当天信号judge评分 - {len(current_signals)} 个信号")
        
        # KR 수정: cfg.llm은 dict (속성 접근은 AttributeError — 죽은 코드 원인 중 하나).
        # 판정기는 완전한 chat/completions URL을 기대한다.
        base = (cfg.llm.get("base_url") or "").rstrip("/")
        llm_config = {
            "api_key": cfg.llm.get("api_key", ""),
            "api_base": f"{base}/chat/completions",
            "model_name": cfg.llm.get("model_name", "")
        }
        
        judge_scores = await self.signal_judger.judge_signals(
            signals=current_signals,
            trigger_time=trigger_time,
            num_judgers=5,
            llm_config=llm_config
        )
        
        logger.info(f"获得 {len(judge_scores)} 个信号的judge评分")
        return judge_scores


if __name__ == "__main__":
    async def main():
        """测试函数"""
        research_contest = ResearchContest()
        
        test_time = "2025-08-20 09:00:00"
        result = await research_contest.run_research_contest(test_time)
        
        print("\n" + "="*60)
        print("测试结果:")
        print(f"总信号数: {result.total_signals}")
        print(f"有效信号数: {result.valid_signals}")
        print(f"选择方法: {result.selection_method}")
        
        if result.optimized_weights:
            print("\n权重分配 (Top 5):")
            sorted_weights = sorted(result.optimized_weights.items(), key=lambda x: x[1], reverse=True)
            for i, (signal_name, weight) in enumerate(sorted_weights[:5]):
                if weight > 0:
                    print(f"  {i+1}. {signal_name}: {weight:.1%}")
    
    asyncio.run(main())
