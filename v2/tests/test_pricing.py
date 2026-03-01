from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRICING_FILE = ROOT / 'packages' / 'shared_py' / 'listening_v2_shared' / 'pricing.py'

spec = spec_from_file_location('pricing_module', PRICING_FILE)
module = module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)

calculate_job_cost_credits = module.calculate_job_cost_credits


def test_calculate_job_cost_credits_positive():
    cost = calculate_job_cost_credits(
        duration_seconds=120,
        segment_count=10,
        asr_model='paraformer-v2',
        mt_model='qwen-mt',
        asr_multiplier=1.0,
        mt_multiplier=1.0,
    )
    assert cost > 0


def test_calculate_job_cost_credits_multiplier():
    normal = calculate_job_cost_credits(
        duration_seconds=60,
        segment_count=6,
        asr_model='paraformer-v2',
        mt_model='qwen-mt',
        asr_multiplier=1.0,
        mt_multiplier=1.0,
    )
    high = calculate_job_cost_credits(
        duration_seconds=60,
        segment_count=6,
        asr_model='paraformer-v2',
        mt_model='qwen-mt',
        asr_multiplier=2.0,
        mt_multiplier=2.0,
    )
    assert high >= normal
