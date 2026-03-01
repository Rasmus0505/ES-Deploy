from __future__ import annotations

import math
from decimal import Decimal

DEFAULT_MODEL_PRICING = {
    'paraformer-v2': {'unit': 'second', 'cost_per_unit': Decimal('0.25')},
    'qwen3-asr-flash': {'unit': 'second', 'cost_per_unit': Decimal('0.35')},
    'qwen-mt': {'unit': 'segment', 'cost_per_unit': Decimal('2.0')},
}


def calculate_job_cost_credits(*, duration_seconds: float, segment_count: int, asr_model: str, mt_model: str, asr_multiplier: float = 1.0, mt_multiplier: float = 1.0) -> int:
    duration = max(0.0, float(duration_seconds or 0.0))
    segs = max(0, int(segment_count or 0))

    asr_price = DEFAULT_MODEL_PRICING.get(asr_model, DEFAULT_MODEL_PRICING['paraformer-v2'])['cost_per_unit']
    mt_price = DEFAULT_MODEL_PRICING.get(mt_model, DEFAULT_MODEL_PRICING['qwen-mt'])['cost_per_unit']

    asr_cost = Decimal(str(duration)) * asr_price * Decimal(str(max(0.0, asr_multiplier)))
    mt_cost = Decimal(str(segs)) * mt_price * Decimal(str(max(0.0, mt_multiplier)))
    return int(math.ceil(float(asr_cost + mt_cost)))
