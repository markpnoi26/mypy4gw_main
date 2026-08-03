"""Serves stubs/*.pyi as importable stand-ins for the DLL's native modules.

Outside the injected process `import PyAgent` fails, so nothing downstream can
be imported at all. The stubs are the declared contract with Py4GW.dll and are
valid Python, so they double as the fake: enum values are real, class and method
names are real, and every body is `...`.
"""

from __future__ import annotations

import enum
import importlib.abc
import importlib.util
import inspect
import re
import sys
import typing
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STUBS = REPO / "stubs"


class Anything:
    """Absorbs whatever is done to it.

    Stub attributes are bare annotations, so `PySkill.Skill("x").id` resolves to
    nothing while real modules chain off it at import time. This keeps the chain
    alive without pretending to model native behaviour.
    """

    def __getattr__(self, name):
        return Anything()

    def __call__(self, *args, **kwargs):
        return Anything()

    def __getitem__(self, key):
        return Anything()

    def __iter__(self):
        return iter(())

    def __len__(self):
        return 0

    def __bool__(self):
        return False

    def __int__(self):
        return 0

    def __index__(self):
        return 0

    def __float__(self):
        return 0.0

    def __str__(self):
        return ""

    def __repr__(self):
        return "<Anything>"

    def __hash__(self):
        return 0

    def __eq__(self, other):
        return isinstance(other, Anything)

    def __add__(self, other):
        return Anything()

    __radd__ = __sub__ = __rsub__ = __mul__ = __rmul__ = __add__
    __truediv__ = __floordiv__ = __mod__ = __or__ = __and__ = __xor__ = __add__
    __ror__ = __rand__ = __rxor__ = __neg__ = __invert__ = __add__

    def __lt__(self, other):
        return False

    __le__ = __gt__ = __ge__ = __lt__


def anything_fn(*args, **kwargs):
    return Anything()


def default_for(annotation):
    """A value matching the stub's declared return type.

    int is 1, not 0: Scanner.Find and the NativeSymbol resolvers treat 0 as
    "symbol not located" and raise at module scope, which would make every
    Tier 0.5 module unimportable here. 1 is truthy and small enough that a
    `range(count)` loop somewhere cannot hang the suite.
    """
    if annotation is None or annotation is type(None):
        return None
    if annotation is bool:
        return False
    if annotation is int:
        return 1
    if annotation is float:
        return 0.0
    if annotation is str:
        return ""
    if annotation is bytes:
        return b""
    origin = typing.get_origin(annotation)
    if origin in (list, set):
        return origin()
    if origin is dict:
        return {}
    if origin is tuple:
        args = typing.get_args(annotation)
        return tuple(default_for(a) for a in args) if args else ()
    return Anything()


def returning(annotation):
    def stub(*args, **kwargs):
        return default_for(annotation)

    return stub


def return_type(func):
    try:
        return typing.get_type_hints(func).get("return")
    except Exception:
        return func.__annotations__.get("return") if hasattr(func, "__annotations__") else None


def permissive_class(cls: type) -> None:
    for name, value in list(vars(cls).items()):
        if isinstance(value, type):
            if not issubclass(value, enum.Enum):
                permissive_class(value)
        elif isinstance(value, staticmethod):
            setattr(cls, name, staticmethod(returning(return_type(value.__func__))))
        elif isinstance(value, classmethod):
            inner = returning(return_type(value.__func__))
            setattr(cls, name, classmethod(lambda c, *a, **k: inner()))
        elif inspect.isfunction(value) and name not in ("__init__", "__getattr__"):
            setattr(cls, name, returning(return_type(value)))
    try:
        cls.__init__ = lambda self, *a, **k: None
        cls.__getattr__ = lambda self, name: Anything()
    except TypeError:
        pass


def flag_like(cls: type) -> bool:
    """A bitmask wearing an IntEnum's clothes.

    Mostly powers of two, plus a few named combinations (WindowFlags scores
    21/22; ProfessionType, whose values are just 1..10, scores 4/10).
    """
    try:
        values = [m.value for m in cls]
    except Exception:
        return False
    if not all(isinstance(v, int) and v >= 0 for v in values):
        return False
    nonzero = [v for v in values if v]
    if len(nonzero) < 4:
        return False
    powers = [v for v in nonzero if v & (v - 1) == 0]
    return len(powers) / len(nonzero) >= 0.7


def widen_flags(module, name: str, cls: type) -> None:
    """Callers pass real combinations — `WindowFlags(65)` is NoTitleBar|
    AlwaysAutoResize — but the stub declares IntEnum, so construction raises.
    Upstream's .pyi is wrong here; the native binding behaves like IntFlag.
    Worth sending upstream as a stub fix."""
    try:
        widened = enum.IntFlag(cls.__name__, {m.name: m.value for m in cls}, boundary=enum.KEEP)
    except Exception:
        return
    widened.__module__ = getattr(cls, "__module__", module.__name__)
    setattr(module, name, widened)


def make_permissive(module) -> None:
    """Enums keep their real values; everything else absorbs."""
    for name, value in list(vars(module).items()):
        if name.startswith("__"):
            continue
        if isinstance(value, type):
            if issubclass(value, enum.Enum):
                if issubclass(value, int) and not issubclass(value, enum.Flag) and flag_like(value):
                    widen_flags(module, name, value)
            else:
                permissive_class(value)
        elif inspect.isfunction(value):
            setattr(module, name, returning(return_type(value)))


# @overload bodies raise NotImplementedError when actually called, which turns a
# module-scope `PyAgent.Profession()` into an import failure. Runtime typing
# imports go the same way: the stub only needs to define names.
STRIP = re.compile(r"^(\s*)@overload\s*$", re.M)


class StubLoader(importlib.abc.Loader):
    def __init__(self, path: Path) -> None:
        self.path = path

    def create_module(self, spec):
        return None

    def exec_module(self, module) -> None:
        source = STRIP.sub("", self.path.read_text(encoding="utf-8"))
        module.__file__ = str(self.path)
        if self.path.name == "__init__.pyi":
            module.__path__ = [str(self.path.parent)]
        exec(compile(source, str(self.path), "exec"), module.__dict__)
        make_permissive(module)


class StubFinder(importlib.abc.MetaPathFinder):
    """Resolves native module names against stubs/, packages included."""

    def find_spec(self, fullname, path=None, target=None):
        rel = fullname.replace(".", "/")
        for candidate in (STUBS / (rel + ".pyi"), STUBS / rel / "__init__.pyi"):
            if candidate.is_file():
                spec = importlib.util.spec_from_loader(fullname, StubLoader(candidate))
                if candidate.name == "__init__.pyi":
                    spec.submodule_search_locations = [str(candidate.parent)]
                return spec
        return None


def native_module_names() -> list[str]:
    names = {p.stem for p in STUBS.glob("*.pyi")}
    names |= {p.name for p in STUBS.iterdir() if p.is_dir() and (p / "__init__.pyi").is_file()}
    return sorted(names)


def install() -> None:
    if not any(isinstance(f, StubFinder) for f in sys.meta_path):
        sys.meta_path.insert(0, StubFinder())
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
