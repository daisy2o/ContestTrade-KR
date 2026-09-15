"""문서 재검토용 읽기 전용 진단. 실제 LLM·금융 API는 호출하지 않는다."""
import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from datetime import date, timedelta

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent
sys.path.insert(0, str(ROOT / 'contest_trade'))
sys.path.insert(0, str(ROOT / 'contest_trade/contest/researcher'))

from evaluation.signal_parser import parse_final_result
from research_predictor import ResearchPredictor
from research_weight_optimizer import ResearchWeightOptimizer

result = {'repo': str(ROOT), 'scope': '로컬 코드 진단 + 합성 예제. LLM 성능 실측 아님.'}
result['head'] = subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip()
result['tracked_tests'] = subprocess.check_output(['git','ls-files','contest_trade/tests'], cwd=ROOT, text=True).splitlines()
r = subprocess.run(['git','check-ignore','-v','contest_trade/tests/test_signal_parser.py'], cwd=ROOT, text=True, capture_output=True)
result['ignored_test_rule'] = r.stdout.strip()
result['predict_method_exists'] = hasattr(ResearchPredictor, 'predict_signal_scores')
try:
    ResearchPredictor()
    result['predictor_init'] = 'success'
except Exception as exc:
    result['predictor_init'] = {'type': type(exc).__name__, 'message': str(exc)}
weights = ResearchWeightOptimizer.__new__(ResearchWeightOptimizer).optimize_weights_by_sharpe({'A': -0.2, 'B': 0.0}, '2025-01-06 16:00:00')
result['nonpositive_scores'] = {'weights': weights, 'sum': sum(weights.values()), 'WDA_denominator_is_zero': sum(weights.values()) == 0}

block = '<signal><has_opportunity>yes</has_opportunity><action>buy</action><symbol_code>005930</symbol_code><probability>{}</probability></signal>'
result['parser_probes'] = {}
for probability in ['80','80 to 90','0.8','1e2']:
    p = parse_final_result(block.format(probability))
    result['parser_probes'][probability] = {'rate':p.parse_success_rate, 'probability':p.signals[0].probability, 'errors':p.signals[0].errors}
p = parse_final_result(block.format('80') + '<signal><has_opportunity>yes</has_opportunity>')
result['unclosed_extra_block'] = {'detected_blocks':p.raw_block_count,'rate':p.parse_success_rate}

# 동일한 합성 일봉을 사용해 네트워크 없이 시각 처리의 반례를 확인한다.
import pandas as pd
import utils.kr_data_utils as kr
from utils.market_manager import MarketManager
class FixedDailyBackend:
    def get_ohlcv(self, symbol, start, end):
        return pd.DataFrame({'Open':[100,102], 'High':[105,110], 'Low':[99,101], 'Close':[101,109], 'Volume':[1000,2000]}, index=pd.to_datetime(['2025-01-03','2025-01-06']))
manager = MarketManager.__new__(MarketManager)
manager.get_trade_date = lambda market: ['20250103','20250106']
original = kr.GLOBAL_KR_CLIENT
try:
    kr.GLOBAL_KR_CLIENT = FixedDailyBackend()
    morning = manager.get_symbol_price('KR-Stock','005930','2025-01-06 09:00:00')
    evening = manager.get_symbol_price('KR-Stock','005930','2025-01-06 16:00:00')
    result['intraday_probe'] = {'morning_close':morning['close'], 'evening_close':evening['close'], 'identical_daily_record':morning == evening, 'interpretation':'판단용으로 09:00에 호출하면 당일 종가를 얻음. 채점용 사용과 구분 필요.'}
finally:
    kr.GLOBAL_KR_CLIENT = original

# C1 평균과 균등 C2의 신호 적중률은 같은 값일 수 있다.
agent_hits = [[1,1,0,0],[1,0,0,0]]
scores = [sum(h)/len(h) for h in agent_hits]
result['equal_weight_identity'] = {'single_agent_scores':scores,'mean_C1':sum(scores)/len(scores),'C2_signal_accuracy':sum(map(sum,agent_hits))/sum(map(len,agent_hits)), 'interpretation':'동일 신호 수·동일 평가일 합성 예제. 이 지표의 C1 평균→C2 차이는 앙상블 개선을 측정하지 못함.'}

# 원문 월 20.4일 가정을 그대로 적용한 산술 점검. 실제 거래일 수가 아님.
result['document_arithmetic'] = [
 {'cutoff_month':'2025-05','start':'2025-06','end':'2026-10','months':17,'document_total':306,'same_assumption_total':17*20.4},
 {'cutoff_month':'2026-01','start':'2026-02','end':'2026-10','months':9,'document_upper_bound':143,'same_assumption_total':9*20.4},
 {'cutoff_month':'2026-02','start':'2026-03','end':'2026-10','months':8,'document_upper_bound':143,'same_assumption_total':8*20.4},
]
result['hypothetical_api_cost'] = {'input_tokens':120_000_000,'output_tokens':30_000_000,'gpt_4_1_usd':120*2+30*8,'gpt_4_1_mini_usd':120*.4+30*1.6,'note':'공식 표준 단가를 가정한 산술 예시. 캐시/배치 할인, 도구/데이터 비용, 재시도는 미포함. 실측 비용 아님.'}
paths = ['contest_trade/utils/market_manager.py','contest_trade/utils/kr_data_utils.py','contest_trade/evaluation/signal_parser.py','contest_trade/contest/researcher/research_predictor.py','contest_trade/contest/researcher/research_contest.py','contest_trade/contest/researcher/research_data_manager.py','contest_trade/contest/researcher/research_weight_optimizer.py','.gitignore']
result['source_sha256'] = {p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths}
(OUT/'evidence.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(result,ensure_ascii=False,indent=2))
