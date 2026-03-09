"""Checks that local package shadowing is removed for backtesting.py."""
import importlib.util
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parent


class BacktestingImportResolutionTests(unittest.TestCase):
    def test_local_backtesting_folder_removed(self) -> None:
        self.assertFalse((PROJECT_ROOT / "backtesting").exists())

    def test_backtesting_module_not_resolved_from_local_path(self) -> None:
        spec = importlib.util.find_spec("backtesting")
        if spec is None or spec.origin is None:
            self.skipTest("`backtesting` package is not installed in this environment.")

        origin = Path(spec.origin).resolve()
        local_shadow_path = (PROJECT_ROOT / "backtesting").resolve()
        self.assertFalse(str(origin).startswith(str(local_shadow_path)))


if __name__ == "__main__":
    unittest.main()
