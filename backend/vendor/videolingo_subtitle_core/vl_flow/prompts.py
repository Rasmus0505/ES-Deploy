from __future__ import annotations

import json


def get_split_prompt(sentence: str, num_parts: int, word_limit: int, source_language: str) -> str:
    return (
        "## Role\n"
        f"You are a professional subtitle splitter in {source_language}.\n\n"
        "## Task\n"
        f"Split the sentence into {num_parts} parts. Keep each part under {word_limit} words when possible. "
        "Keep original word order and do not paraphrase.\n\n"
        "## Input\n"
        f"{sentence}\n\n"
        "## Output JSON only\n"
        "{\"split\": \"part1 [br] part2\"}"
    )


def get_summary_prompt(text: str, source_language: str, target_language: str) -> str:
    return (
        "## Role\n"
        "You are a video translation expert.\n\n"
        "## Task\n"
        f"For the provided {source_language} text, generate a short topic summary and extract key terms with "
        f"{target_language} translations.\n"
        "Return less than 15 terms.\n\n"
        "## Input\n"
        f"{text}\n\n"
        "## Output JSON only\n"
        "{\"theme\": \"...\", \"terms\": [{\"src\": \"...\", \"tgt\": \"...\", \"note\": \"...\"}]}"
    )


def get_translate_chunk_prompt(
    *,
    lines: list[str],
    previous_lines: list[str],
    after_lines: list[str],
    theme: str,
    terms: list[dict[str, str]],
    source_language: str,
    target_language: str,
) -> str:
    payload = {
        str(idx + 1): {
            "origin": line,
            "translation": f"{target_language} translation",
        }
        for idx, line in enumerate(lines)
    }
    return (
        "## Role\n"
        f"You are a Netflix subtitle translator from {source_language} to {target_language}.\n\n"
        "## Task\n"
        "Translate each input line faithfully and naturally. Keep line count and order unchanged.\n"
        "Do not output empty translations.\n\n"
        "## Context\n"
        f"Previous lines: {json.dumps(previous_lines, ensure_ascii=False)}\n"
        f"Next lines: {json.dumps(after_lines, ensure_ascii=False)}\n"
        f"Theme: {theme}\n"
        f"Terms: {json.dumps(terms, ensure_ascii=False)}\n\n"
        "## Input lines\n"
        f"{json.dumps(lines, ensure_ascii=False)}\n\n"
        "## Output JSON only\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )


def get_align_prompt(
    *,
    source_text: str,
    translation: str,
    source_parts: list[str],
    source_language: str,
    target_language: str,
) -> str:
    example_parts = [
        {
            f"src_part_{idx + 1}": part,
            f"target_part_{idx + 1}": f"aligned {target_language} part",
        }
        for idx, part in enumerate(source_parts)
    ]
    return (
        "## Role\n"
        f"You are a subtitle alignment expert for {source_language} and {target_language}.\n\n"
        "## Task\n"
        "Split the translation into aligned parts matching source split count and meaning.\n"
        "Do not leave empty parts.\n\n"
        "## Input\n"
        f"Source text: {source_text}\n"
        f"Translation text: {translation}\n"
        f"Source parts: {json.dumps(source_parts, ensure_ascii=False)}\n\n"
        "## Output JSON only\n"
        f"{{\"align\": {json.dumps(example_parts, ensure_ascii=False)}}}"
    )
