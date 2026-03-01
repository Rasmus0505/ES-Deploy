from __future__ import annotations

import json
import re
from pathlib import Path

from openai import OpenAI

from listening_v2_shared.config import get_settings

settings = get_settings()


def mock_segments() -> list[dict]:
    return [
        {'start_ms': 0, 'end_ms': 2800, 'text': 'Welcome to your listening practice.'},
        {'start_ms': 3000, 'end_ms': 5900, 'text': 'Type every word before seeing Chinese translation.'},
        {'start_ms': 6200, 'end_ms': 9200, 'text': 'Keep practicing every day to build momentum.'},
    ]


def transcribe_audio(audio_path: str, model: str) -> list[dict]:
    if settings.enable_mock_pipeline or not settings.dashscope_api_key:
        return mock_segments()

    client = OpenAI(api_key=settings.dashscope_api_key, base_url=settings.dashscope_base_url)
    with open(audio_path, 'rb') as stream:
        result = client.audio.transcriptions.create(model=model, file=stream, response_format='verbose_json')

    raw_segments = getattr(result, 'segments', None)
    if raw_segments:
        parsed: list[dict] = []
        for idx, item in enumerate(raw_segments):
            start = int(float(getattr(item, 'start', 0)) * 1000)
            end = int(float(getattr(item, 'end', start / 1000 + 1)) * 1000)
            text = str(getattr(item, 'text', '')).strip()
            if not text:
                continue
            parsed.append({'start_ms': start, 'end_ms': max(end, start + 300), 'text': text})
        if parsed:
            return parsed

    text = str(getattr(result, 'text', '') or '').strip()
    if not text:
        return mock_segments()
    sentences = [part.strip() for part in re.split(r'(?<=[.!?])\s+', text) if part.strip()]
    out: list[dict] = []
    cursor = 0
    for sentence in sentences:
        duration = max(1800, min(7000, len(sentence) * 85))
        out.append({'start_ms': cursor, 'end_ms': cursor + duration, 'text': sentence})
        cursor += duration + 300
    return out or mock_segments()


def translate_to_zh(text: str, model: str) -> str:
    if settings.enable_mock_pipeline or not settings.dashscope_api_key:
        return f'示例翻译：{text}'

    client = OpenAI(api_key=settings.dashscope_api_key, base_url=settings.dashscope_base_url)
    completion = client.chat.completions.create(
        model=model,
        temperature=0,
        messages=[
            {'role': 'system', 'content': 'Translate English sentence into concise natural Chinese.'},
            {'role': 'user', 'content': text},
        ],
    )
    return str(completion.choices[0].message.content or '').strip() or f'翻译失败：{text}'
