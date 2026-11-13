"""Shared alias installer for supported legacy module paths."""
from importlib import import_module
import runpy
import sys


def install_alias(namespace, target):
    name = namespace['__name__']
    if name == '__main__':
        runpy.run_module(target, run_name='__main__')
        return
    module = import_module(target)
    # Preserve identity for monkeypatches, caches and callers using either import path.
    # Updating the original namespace also supports old spec.loader.exec_module users.
    namespace.update(vars(module))
    sys.modules[name] = module
