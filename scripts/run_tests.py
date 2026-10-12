from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_*.py")
result = unittest.TextTestRunner(verbosity=2).run(suite)
if result.wasSuccessful():
    print("full-suite-passed")
raise SystemExit(not result.wasSuccessful())
