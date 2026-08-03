import sys
from pathlib import Path

# test/ is deliberately NOT on pytest's pythonpath — a top-level `test` package
# would shadow CPython's stdlib `test`. Only this file needs it, so it adds it.
sys.path.insert(0, str(Path(__file__).parent))

import nativestub

nativestub.install()
