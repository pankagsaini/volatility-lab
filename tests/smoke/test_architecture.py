import ast
from importlib import import_module
from pathlib import Path
import subprocess
import sys

from volatility_lab import PACKAGE_ROOT


def test_root_modules_are_only_compatibility_shims():
    infrastructure = {'__init__', '__main__', '_compat', 'launch_dashboard'}
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
    result = subprocess.run([sys.executable, '-c', "import volatility_lab.pricing, volatility_lab.ui, volatility_lab.runtime.bootstrap; import sys; assert 'streamlit' not in sys.modules; assert 'numpy' not in sys.modules"], capture_output=True,text=True)
    assert result.returncode == 0, result.stderr


def test_all_streamlit_entries_delegate_to_single_bootstrap():
    root = PACKAGE_ROOT.parent
    for path in [root/'streamlit_app.py', root/'streamlit_desk/app.py', PACKAGE_ROOT/'runtime/streamlit_entry.py']:
        source = path.read_text(encoding='utf-8')
        assert 'from volatility_lab.runtime.bootstrap import run' in source
        assert 'compile(' not in source and 'exec(' not in source


def test_bootstrap_reexecutes_cached_code_for_streamlit_reruns(tmp_path, monkeypatch):
    from volatility_lab.runtime import bootstrap
    source = tmp_path/'dashboard.py'
    output = tmp_path/'reruns.txt'
    source.write_text(f"assert 'session_client' not in globals()\nsession_client=object()\nwith open({str(output)!r},'a') as f: f.write('1')\n")
    monkeypatch.setattr(bootstrap, 'DASHBOARD_FILE', source)
    monkeypatch.setattr(sys, bootstrap._CACHE_KEY, None, raising=False)
    monkeypatch.setenv('VOL_LAB_DATA_DIR', str(tmp_path / 'references'))
    bootstrap.run(); bootstrap.run()
    assert output.read_text() == '11'


def test_launcher_propagates_exit_status_and_defaults_to_loopback(monkeypatch):
    from types import SimpleNamespace
    from volatility_lab.runtime import launcher
    commands=[]
    monkeypatch.setattr(launcher.subprocess, 'run', lambda cmd: (commands.append(cmd) or SimpleNamespace(returncode=7)))
    assert launcher.main([]) == 7
    assert '--server.address=127.0.0.1' in commands[0]
    assert launcher.main(['--lan','--port','8502']) == 7
    assert '--server.address=0.0.0.0' in commands[1]
    assert '--server.port=8502' in commands[1]


def test_real_streamlit_entry_starts_offline(tmp_path):
    import os

    # Isolate module-level paths and broker clients from other tests.
    env = {**os.environ, 'VOL_LAB_OFFLINE': 'true', 'VOL_LAB_DATA_DIR': str(tmp_path)}
    entry = PACKAGE_ROOT.parent / 'streamlit_app.py'
    code = (
        'from streamlit.testing.v1 import AppTest\n'
        f'app = AppTest.from_file({str(entry)!r}, default_timeout=60).run()\n'
        'assert not app.exception, [item.message for item in app.exception]\n'
        'assert any("Volatility Lab" in md.value for md in app.markdown)\n'
    )
    result = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stderr
