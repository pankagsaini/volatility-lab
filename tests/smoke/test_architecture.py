import ast
from importlib import import_module
from pathlib import Path
import subprocess
import sys

from volatility_lab import PACKAGE_ROOT


def test_root_modules_are_only_compatibility_shims():
    infrastructure = {'__init__', '__main__', '_compat'}
    for path in PACKAGE_ROOT.glob('*.py'):
        if path.stem in infrastructure:
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'))
        assert not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) for node in ast.walk(tree)), path
        assert 'install_alias(globals(),' in path.read_text(encoding='utf-8'), path


def test_import_paths_share_one_module_and_mutable_state():
    for name, domain in [('option_math','pricing'), ('pricing_policy','pricing'), ('forward_test','backtest'),
                         ('upstox_data','ingestion'), ('market_calendar','core'), ('collector_support','storage')]:
        canonical = import_module(f'volatility_lab.{domain}.{name}')
        assert canonical is import_module(name)
        assert canonical is import_module(f'volatility_lab.{name}')
        assert Path(canonical.__file__).parent.name == domain


def test_lightweight_packages_do_not_load_ui_or_numeric_dependencies():
    result = subprocess.run([sys.executable, '-c', "import volatility_lab.pricing, volatility_lab.ui; import sys; assert 'streamlit' not in sys.modules; assert 'numpy' not in sys.modules"], capture_output=True,text=True)
    assert result.returncode == 0, result.stderr










def test_shared_services_import_without_streamlit():
    code = """
import importlib.abc
import sys
class RejectStreamlit(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'streamlit' or fullname.startswith('streamlit.'):
            raise AssertionError('Retired UI dependency requested: ' + fullname)
sys.meta_path.insert(0, RejectStreamlit())
import volatility_lab.backtest.forward_test
import volatility_lab.backtest.backtest_report
import volatility_lab.ingestion.upstox_data
import volatility_lab.ingestion.upstox_research
import volatility_lab.research.restored_lab
import volatility_lab.research.run_model_validation
import volatility_lab.services.price_history
import volatility_lab.services.web_data
import volatility_lab.pricing.vol_surface_3d
from volatility_lab.runtime.launcher import main
assert main([]) == 0
"""
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
