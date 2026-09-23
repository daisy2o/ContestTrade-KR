"""
Research预测模块
"""

import logging
import pandas as pd
import joblib
import numpy as np
from pathlib import Path
from typing import List, Dict, Optional, Union
from research_contest_types import SignalData
import lightgbm as lgb

logger = logging.getLogger(__name__)


class ResearchPredictor:
    """研究信号预测器 - 基于LightGBM预测模型"""
    
    def __init__(self, history_window_days: int = 5, prediction_window_days: int = 3):
        self.history_window_days = history_window_days
        self.prediction_window_days = prediction_window_days
        self.use_lightgbm = False

        # KR 수정: 모델 파일이 없어도 초기화가 죽지 않게 (원본은 FileNotFoundError로
        # 콘테스트 전체가 기동 불가 — 죽은 코드였던 원인 중 하나)
        try:
            self._load_lightgbm_models()
        except Exception as e:
            logger.warning(f"LightGBM 모델 미로드 ({e}) — judge 폴백 모드로 동작")

    def _load_lightgbm_models(self) -> bool:
        """
        加载LightGBM模型
        """
        # 模型文件路径
        model_dir = Path(__file__).parent / "lightgbm_predictor"
        mean_model_path = model_dir / "lgbm_mean_model.joblib"
        std_model_path = model_dir / "lgbm_std_model.joblib"

        if mean_model_path.exists() and std_model_path.exists():
            self.model_mean = joblib.load(mean_model_path)
            self.model_std = joblib.load(std_model_path)
            self.use_lightgbm = True
            logger.info("LightGBM模型加载成功")
            return True
        else:
            raise FileNotFoundError(f"LightGBM模型文件未找到: {model_dir}")

    def predict_signal_scores(self, current_date: str,
                              agent_signals: Dict[str, List[Optional[SignalData]]],
                              current_judge_scores: Dict[str, List[float]]) -> Dict[str, float]:
        """KR 복원: 원본에서 def 줄이 소실돼 본문이 _load_lightgbm_models 안에
        고아로 남아 있던 메서드 (research_contest.py:311이 호출하는 공개 API)."""
        logger.info(f"开始预测信号得分 - 日期: {current_date}")

        agent_rewards = self._extract_historical_rewards(agent_signals)
        predicted_scores = self._calculate_predicted_scores(agent_rewards, current_judge_scores)

        self._log_prediction_summary(predicted_scores)

        return predicted_scores
    
    def _extract_historical_rewards(self, agent_signals: Dict[str, List[Optional[SignalData]]]) -> Dict[str, List[Optional[float]]]:
        """
        从历史信号中提取agent的日收益率数据
        """
        agent_rewards = {}
        
        for agent_name, signals_list in agent_signals.items():
            rewards_list = []
            
            # 对于每一天的信号，提取该agent当天的收益率
            for signal in signals_list:
                if signal is None:
                    rewards_list.append(None)  # 该天没有信号
                elif signal.has_contest_data() and 'reward' in signal.contest_data:
                        # 提取该信号的收益率，这应该是该agent当天的收益率
                    reward = signal.contest_data["reward"]
                    if abs(reward) > 0.40:
                        logger.debug(f"过滤 {agent_name} 的异常收益率: {reward:.2%} (疑似涨跌停)")
                        rewards_list.append(0)
                    else:
                        rewards_list.append(reward)
                else:
                    rewards_list.append(None)
                    
            agent_rewards[agent_name] = rewards_list
        
        return agent_rewards
    
    def _calculate_predicted_scores(self, agent_rewards: Dict[str, List[Optional[float]]], 
                                  current_judge_scores: Dict[str, List[float]] = None) -> Dict[str, float]:
        """计算预测得分 - 使用LightGBM模型"""
        predicted_scores = {}

        if not self.use_lightgbm:
            # KR 폴백 (잠정, D-결정 대기): 학습된 모델이 없으면 judge 평균(0~100)을
            # 0~1로 정규화한 값을 û로 사용 — 부호·순위만 소비하는 가중 규칙과 호환.
            # 리플레이 초기 구간(학습 이력 부족)의 웜업 경로이기도 하다.
            logger.warning("LightGBM 미학습 — judge 평균 폴백으로 점수 산출 (selection=judge_fallback)")
            for agent_name in agent_rewards.keys():
                js = current_judge_scores.get(agent_name, []) if current_judge_scores else []
                predicted_scores[agent_name] = (sum(js) / len(js) / 100.0) if js else 0.0
            return predicted_scores

        logger.info("使用LightGBM模型进行预测")
        for agent_name, rewards in agent_rewards.items():
            judge_scores = current_judge_scores.get(agent_name, []) if current_judge_scores else []
            predicted_score = self._predict_single_agent_lightgbm(rewards, judge_scores)
            predicted_scores[agent_name] = predicted_score

        return predicted_scores
    
    def _create_features_from_history_and_scores(self, historical_rewards: List[Union[float, None]], 
                                               judge_scores: List[float]) -> pd.DataFrame:
        """
        从历史收益 + 当天judge评分创建模型特征
        """
        if len(judge_scores) < 5:
            raise ValueError(f"judge_scores数量不足，需要5个评分，当前只有{len(judge_scores)}个")
        
        s = pd.Series(historical_rewards)
        
        # 检查历史数据是否全部为空
        if s.isna().all():
            raise ValueError("历史收益数据全部为空，无法进行预测")
        
        # 使用局部中位数填充NaN
        valid_values = s.dropna()
        if not valid_values.empty:
            median_val = valid_values.median()
        else:
            raise ValueError("历史收益数据中没有有效值")
        s_imputed = s.fillna(median_val)
        
        # 特征构造
        features = {}
        features['reward_mean_1d'] = s_imputed.rolling(window=1).mean().iloc[-1]
        features['reward_mean_3d'] = s_imputed.rolling(window=3).mean().iloc[-1]
        features['reward_std_3d'] = s_imputed.rolling(window=3).std(ddof=0).iloc[-1]
        features['reward_mean_5d'] = s_imputed.rolling(window=5).mean().iloc[-1]
        features['reward_std_5d'] = s_imputed.rolling(window=5).std(ddof=0).iloc[-1]

        judge_scores_array = np.array(judge_scores[:5])
        features['judge_0'] = judge_scores_array[0]
        features['judge_1'] = judge_scores_array[1] 
        features['judge_2'] = judge_scores_array[2]
        features['judge_3'] = judge_scores_array[3]
        features['judge_4'] = judge_scores_array[4]
        
        # 统计特征
        features['judge_mean'] = np.mean(judge_scores_array)
        features['judge_std'] = np.std(judge_scores_array)
        
        # 构建DataFrame
        feature_df = pd.DataFrame([features])
        feature_df = feature_df.fillna(0)
        
        # 特征列顺序 (历史收益率特征 + judge评分特征)
        feature_cols_order = [
            'reward_mean_1d', 'reward_mean_3d', 'reward_std_3d', 
            'reward_mean_5d', 'reward_std_5d',
            'judge_0', 'judge_1', 'judge_2', 'judge_3', 'judge_4',
            'judge_mean', 'judge_std'
        ]
        
        return feature_df[feature_cols_order]
    
    def _predict_single_agent_lightgbm(self, historical_rewards: List[Union[float, None]], 
                                      judge_scores: List[float] = None) -> float:
        """使用LightGBM预测单个agent的得分 - 基于历史收益率 + 当天5个judge评分特征"""
        if not self.use_lightgbm:
            raise RuntimeError("LightGBM模型未加载，无法进行预测")
        
        if len(historical_rewards) != 5:
            raise ValueError(f"历史收益率数据长度不正确，需要5天数据，当前有{len(historical_rewards)}天")
        
        # 检查是否有judge评分
        if judge_scores is None or len(judge_scores) == 0:
            raise ValueError("LightGBM预测需要judge评分数据！模型使用12个特征（5个历史收益率 + 7个judge评分），缺少judge评分将导致预测失败。")
        
        # 创建特征 (历史收益率 + 当天judge评分)
        X_pred = self._create_features_from_history_and_scores(historical_rewards, judge_scores)
        
        # 进行预测
        pred_mean = self.model_mean.predict(X_pred)[0]
        pred_std = self.model_std.predict(X_pred)[0]
        
        # 计算夏普比率作为预测得分
        predicted_sharpe = pred_mean / max(pred_std, 0.01)
        
        return float(predicted_sharpe)
    
    def _log_prediction_summary(self, predicted_scores: Dict[str, float]):
        """打印预测结果摘要"""
        if not predicted_scores:
            return
        
        # 按分数排序
        sorted_scores = sorted(predicted_scores.items(), key=lambda x: x[1], reverse=True)
        
        logger.info("预测结果摘要 (Top 5):")
        for i, (agent_name, score) in enumerate(sorted_scores[:5]):
            logger.info(f"  {i+1}. {agent_name}: {score:.3f}")
    
    def train_lightgbm_model(self, training_data: Dict[str, List[SignalData]], save_path: Optional[str] = None) -> bool:
        try:            
            logger.info("开始训练LightGBM模型...")
            
            # 准备训练数据
            X_train, y_mean_train, y_std_train = self._prepare_training_data(training_data)
            
            if len(X_train) < 10:
                logger.warning(f"训练样本数量不足: {len(X_train)}，需要至少10个样本")
                return False
            
            logger.info(f"准备了 {len(X_train)} 个训练样本")
            
            # 训练均值预测模型
            logger.info("训练均值预测模型...")
            self.model_mean = lgb.LGBMRegressor(
                n_estimators=100,
                learning_rate=0.1,
                max_depth=6,
                num_leaves=31,
                random_state=42,
                verbose=-1,
                objective='regression',
                metric='rmse'
            )
            self.model_mean.fit(X_train, y_mean_train)
            
            # 训练标准差预测模型
            logger.info("训练标准差预测模型...")
            self.model_std = lgb.LGBMRegressor(
                n_estimators=100,
                learning_rate=0.1,
                max_depth=6,
                num_leaves=31,
                random_state=42,
                verbose=-1,
                objective='regression',
                metric='rmse'
            )
            self.model_std.fit(X_train, y_std_train)
            
            # 保存模型
            if save_path is None:
                save_path = Path(__file__).parent / "lightgbm_predictor"
            else:
                save_path = Path(save_path)
            
            save_path.mkdir(parents=True, exist_ok=True)
            
            mean_model_path = save_path / "lgbm_mean_model.joblib"
            std_model_path = save_path / "lgbm_std_model.joblib"
            
            joblib.dump(self.model_mean, mean_model_path)
            joblib.dump(self.model_std, std_model_path)
            
            logger.info(f"LightGBM模型训练完成，已保存到: {save_path}")
            
            # 更新使用标志
            self.use_lightgbm = True
            
            return True
            
        except Exception as e:
            logger.error(f"LightGBM模型训练失败: {e}")
            return False
    
    def _prepare_training_data(self, training_data: Dict[str, List[SignalData]]) -> tuple:
        has_judge_scores = False
        for agent_name, signals_list in training_data.items():
            for signal in signals_list:
                if signal and signal.has_contest_data() and 'judge_scores' in signal.contest_data:
                    has_judge_scores = True
                    break
            if has_judge_scores:
                break
        
        if not has_judge_scores:
            raise ValueError(
                "训练数据中缺少judge评分信息！\n"
                "模型需要使用12个特征进行训练（5个历史收益率特征 + 7个judge评分特征）。\n"
                "请确保训练数据包含完整的judge评分信息。"
            )
        X_list = []
        y_mean_list = []
        y_std_list = []
        
        for agent_name, signals_list in training_data.items():
            # 提取该agent的日收益率序列和judge评分序列
            rewards = []
            judge_scores_list = []
            
            for signal in signals_list:
                if signal and signal.has_contest_data():
                    contest_data = signal.contest_data
                    
                    # 提取收益率
                    if 'reward' in contest_data:
                        reward = contest_data['reward']
                        # 涨跌停过滤：排除超过±40%的收益率
                        if abs(reward) > 0.40:
                            logger.debug(f"训练数据过滤 {agent_name} 的异常收益率: {reward:.2%} (疑似涨跌停)")
                            rewards.append(None)
                        else:
                            rewards.append(reward)
                    else:
                        rewards.append(None)
                    
                    # 提取judge评分
                    if 'judge_scores' in contest_data:
                        judge_scores = contest_data['judge_scores']
                        if isinstance(judge_scores, list) and len(judge_scores) > 0:
                            judge_scores_list.append(judge_scores)
                        else:
                            judge_scores_list.append(None)
                    else:
                        judge_scores_list.append(None)
                else:
                    rewards.append(None)
                    judge_scores_list.append(None)
            
            # 需要足够的历史数据（至少history_window + prediction_window天）
            min_required_days = self.history_window_days + self.prediction_window_days
            if len(rewards) < min_required_days or len(judge_scores_list) < min_required_days:
                continue
            
            # 滑动窗口创建训练样本
            for i in range(self.history_window_days, len(rewards) - self.prediction_window_days + 1):
                history_window = rewards[i-self.history_window_days:i]
                history_judge_scores = judge_scores_list[i-1]  # 使用当前时间点的judge评分
                
                # 计算未来n天的夏普比率作为目标
                future_rewards = rewards[i:i+self.prediction_window_days]
                future_sharpe = self._calculate_future_sharpe(future_rewards)
                
                # 跳过无效样本
                if future_sharpe is None:
                    continue
                
                # 检查历史窗口中是否有足够的有效数据
                valid_history = [r for r in history_window if r is not None and abs(r) <= 0.40]
                if len(valid_history) < 2:
                    continue
                
                # 检查是否有judge评分数据
                if history_judge_scores is None:
                    continue
                
                # 创建特征（使用完整的12个特征：历史收益率 + judge评分）
                features = self._create_features_from_history_and_scores(history_window, history_judge_scores)
                X_list.append(features.iloc[0].values)
                
                # 目标变量：未来n天的夏普比率分解为均值和标准差
                future_valid = [r for r in future_rewards if r is not None and abs(r) <= 0.40]
                if len(future_valid) >= 2:
                    future_mean = np.mean(future_valid)
                    future_std = np.std(future_valid, ddof=1)
                    y_mean_list.append(future_mean)
                    y_std_list.append(max(future_std, 0.01))
                else:
                    # 如果未来有效数据不足，使用单天数据
                    if len(future_valid) == 1:
                        y_mean_list.append(future_valid[0])
                        y_std_list.append(0.01)
                    else:
                        raise ValueError("未来有效数据不足，无法创建训练样本")
        
        X_train = np.array(X_list)
        y_mean_train = np.array(y_mean_list)
        y_std_train = np.array(y_std_list)
        
        logger.info(f"准备训练数据完成:")
        logger.info(f"  - 特征数量: {X_train.shape[1] if len(X_train) > 0 else 0}")
        logger.info(f"  - 样本数量: {len(X_train)}")
        logger.info(f"  - 目标window: {self.prediction_window_days}天")
        logger.info(f"  - 均值目标范围: [{np.min(y_mean_train):.4f}, {np.max(y_mean_train):.4f}]" if len(y_mean_train) > 0 else "  - 均值目标: 无数据")
        logger.info(f"  - 标准差目标范围: [{np.min(y_std_train):.4f}, {np.max(y_std_train):.4f}]" if len(y_std_train) > 0 else "  - 标准差目标: 无数据")
        
        return X_train, y_mean_train, y_std_train
    
    def _calculate_future_sharpe(self, future_rewards: List[Union[float, None]]) -> Optional[float]:
        """计算未来n天的夏普比率"""
        valid_rewards = [r for r in future_rewards if r is not None and abs(r) <= 0.40]
        
        if len(valid_rewards) < 1:
            return None
        
        if len(valid_rewards) == 1:
            return valid_rewards[0] / 0.01
        
        mean_return = np.mean(valid_rewards)
        std_return = np.std(valid_rewards, ddof=1)
        
        if std_return < 0.01:
            std_return = 0.01
        
        return mean_return / std_return
