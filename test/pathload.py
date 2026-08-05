"""Load a source file as a module without importing its package.

Some modules are stdlib-only by contract and must stay importable outside the
injected client. Loading them by path proves that contract still holds, where a
package import would drag ``Core/__init__.py`` and its eager Py4GW import along.

The mirror under ``test/`` means a suite no longer sits beside its target, so the
path is expressed relative to the repo root.

    loader = pathload.load("Core/py4gwcorelib_src/script_manager/loader.py")
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).resolve().parents[1]


def load(relative_path: str) -> ModuleType:
    path = REPO / relative_path
    if not path.is_file():
        raise FileNotFoundError("no such source file: %s" % path)
    # keyed by full relative path, so two targets sharing a basename stay distinct
    name = "pathload_%s" % relative_path.replace("/", "_").replace(".py", "")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Registered BEFORE exec: `@dataclass(slots=True)` rebuilds the class and
    # looks its own module up in sys.modules to resolve annotations, so a target
    # using slotted dataclasses fails to import at all without this.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module
