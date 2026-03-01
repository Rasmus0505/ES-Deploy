from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDER_FILE = ROOT / 'services' / 'worker' / 'app' / 'exercise_builder.py'

spec = spec_from_file_location('builder_module', BUILDER_FILE)
module = module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)

build_item_payload = module.build_item_payload
check_needs_review = module.check_needs_review
tokenize_words = module.tokenize_words


def test_tokenize_words_basic():
    words = tokenize_words("I'm gonna do it.")
    assert words == ["I'm", 'gonna', 'do', 'it']


def test_build_item_payload():
    words, accepted = build_item_payload(text="Don't stop now")
    assert words == ["Don't", 'stop', 'now']
    assert accepted == ['dont', 'stop', 'now']


def test_check_needs_review_thresholds():
    assert check_needs_review(0, 500) is True
    assert check_needs_review(0, 3000) is False
    assert check_needs_review(0, 20000) is True
