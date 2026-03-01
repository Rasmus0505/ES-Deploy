import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / 'packages' / 'shared_py'
WORKER = ROOT / 'services' / 'worker'
API = ROOT / 'services' / 'api'

for path in (SHARED, WORKER, API):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)
