from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESPONSE_FILE = ROOT / 'services' / 'api' / 'app' / 'response.py'

spec = spec_from_file_location('response_module', RESPONSE_FILE)
module = module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)

ok = module.ok
fail = module.fail


def test_ok_envelope_shape():
    payload = ok(request_id='r1', data={'k': 1}, message='ok')
    assert payload['requestId'] == 'r1'
    assert payload['code'] == 'ok'
    assert payload['message'] == 'ok'
    assert payload['data']['k'] == 1


def test_fail_envelope_shape():
    payload = fail(request_id='r2', code='bad', message='x', data={'a': 1})
    assert payload['requestId'] == 'r2'
    assert payload['code'] == 'bad'
    assert payload['message'] == 'x'
    assert payload['data']['a'] == 1
