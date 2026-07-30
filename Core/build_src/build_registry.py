"""Build discovery, scoring and fallback resolution for every build engine."""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path
from typing import Any
from typing import cast

from .combat_services import CombatServices

FARM_BUILD_PACKAGE = "Core.BTBuilds.FarmBuilds"


def is_purpose_specific_build(build: Any) -> bool:
    """Builds under BTBuilds/FarmBuilds exist for one scenario and must never be
    auto-selected for an account. Excluded by location so it cannot be forgotten
    the way an is_combat_automator_compatible=False flag can."""
    module_name = type(build).__module__ or ""
    return module_name == FARM_BUILD_PACKAGE or module_name.startswith(FARM_BUILD_PACKAGE + ".")


class BuildRegistry:
    _cached_build_types: list[type[CombatServices]] | None = None

    def __init__(self, default_fallback_name: str | None = None, build_init_kwargs: dict[str, Any] | None = None):
        self.default_fallback_name = default_fallback_name
        self.build_init_kwargs = dict(build_init_kwargs or {})
        self._runtime_build_instances: dict[type[CombatServices], CombatServices | None] = {}
        self._match_only_build_instances: dict[type[CombatServices], CombatServices | None] = {}
        self._cached_runtime_builds: list[CombatServices] | None = None
        self._cached_match_only_builds: list[CombatServices] | None = None
        self._cached_runtime_matchable_builds: list[CombatServices] | None = None
        self._cached_match_only_matchable_builds: list[CombatServices] | None = None
        self._cached_runtime_fallback_builds: list[CombatServices] | None = None
        self._cached_match_only_fallback_builds: list[CombatServices] | None = None

    @classmethod
    def _scan_build_types(cls) -> list[type[CombatServices]]:
        build_types: list[type[CombatServices]] = []
        seen_module_names: set[str] = set()

        package_names = ("Core.Builds", "Core.BTBuilds")
        module_specs: list[tuple[str, Path, Path]] = []
        for package_name in package_names:
            try:
                package = importlib.import_module(package_name)
            except ModuleNotFoundError:
                continue
            package_root = Path(package.__path__[0])
            for module_path in package_root.rglob("*.py"):
                module_specs.append((package.__name__, package_root, module_path))

        for package_module_name, package_root, module_path in module_specs:
            if module_path.name == "__init__.py":
                continue

            relative_path = module_path.relative_to(package_root).with_suffix("")
            module_name = ".".join((package_module_name, *relative_path.parts))
            if module_name in seen_module_names:
                continue
            seen_module_names.add(module_name)

            module = importlib.import_module(module_name)
            for _, value in inspect.getmembers(module, inspect.isclass):
                if value.__module__ != module.__name__:
                    continue
                # marker rather than issubclass: BldMgrBT builds are discoverable
                # too, and neither base imports the other
                if not getattr(value, "is_build_type", False):
                    continue
                build_types.append(value)

        return build_types

    @classmethod
    def GetBuildTypes(cls) -> list[type[CombatServices]]:
        if cls._cached_build_types is None:
            cls._cached_build_types = cls._scan_build_types()
        return list(cls._cached_build_types)

    @classmethod
    def ClearCache(cls) -> None:
        cls._cached_build_types = None

    def _call_build_ctor(self, build_type: type[CombatServices], *args: Any, **kwargs: Any) -> CombatServices | None:
        try:
            ctor = cast(Any, build_type)
            build = ctor(*args, **kwargs)
        except TypeError:
            return None
        return cast("CombatServices | None", build)

    def _instantiate_build(self, build_type: type[CombatServices], match_only: bool = False) -> CombatServices | None:
        cache = self._match_only_build_instances if match_only else self._runtime_build_instances

        if build_type in cache:
            build = cache[build_type]
            if build is not None and "cached_data" in self.build_init_kwargs and hasattr(build, "set_cached_data"):
                build.set_cached_data(self.build_init_kwargs["cached_data"])
            return build

        if match_only:
            build = self._call_build_ctor(build_type, match_only=True, **self.build_init_kwargs)
            if build is None:
                build = self._call_build_ctor(build_type, match_only=True)
            if build is None:
                build = self._call_build_ctor(build_type, **self.build_init_kwargs)
            if build is None:
                build = self._call_build_ctor(build_type)
        else:
            build = self._call_build_ctor(build_type, **self.build_init_kwargs)
            if build is None:
                build = self._call_build_ctor(build_type)

        if build is not None and "cached_data" in self.build_init_kwargs and hasattr(build, "set_cached_data"):
            build.set_cached_data(self.build_init_kwargs["cached_data"])

        cache[build_type] = build
        return build

    def _iter_builds(self, match_only: bool = False) -> list[CombatServices]:
        cached_builds = self._cached_match_only_builds if match_only else self._cached_runtime_builds
        if cached_builds is not None:
            return list(cached_builds)

        builds: list[CombatServices] = []
        for build_type in self.GetBuildTypes():
            build = self._instantiate_build(build_type, match_only=match_only)
            if build is not None:
                builds.append(build)

        if match_only:
            self._cached_match_only_builds = builds
            return list(self._cached_match_only_builds)

        self._cached_runtime_builds = builds
        return list(self._cached_runtime_builds)

    def _iter_matchable_builds(self, match_only: bool = False) -> list[CombatServices]:
        cached_builds = (
            self._cached_match_only_matchable_builds if match_only else self._cached_runtime_matchable_builds
        )
        if cached_builds is not None:
            return list(cached_builds)

        matchable_builds: list[CombatServices] = []
        for build in self._iter_builds(match_only=match_only):
            if is_purpose_specific_build(build):
                continue
            if build.is_template_only:
                continue
            if build.is_fallback_candidate:
                continue
            if build.IsFixedBuild:
                continue
            if not build.is_combat_automator_compatible:
                continue
            matchable_builds.append(build)

        if match_only:
            self._cached_match_only_matchable_builds = matchable_builds
            return list(self._cached_match_only_matchable_builds)

        self._cached_runtime_matchable_builds = matchable_builds
        return list(self._cached_runtime_matchable_builds)

    def _iter_fallback_builds(self, match_only: bool = False) -> list[CombatServices]:
        cached_builds = self._cached_match_only_fallback_builds if match_only else self._cached_runtime_fallback_builds
        if cached_builds is not None:
            return list(cached_builds)

        fallback_builds: list[CombatServices] = []
        for build in self._iter_builds(match_only=match_only):
            if build.is_fallback_candidate:
                fallback_builds.append(build)

        if match_only:
            self._cached_match_only_fallback_builds = fallback_builds
            return list(self._cached_match_only_fallback_builds)

        self._cached_runtime_fallback_builds = fallback_builds
        return list(self._cached_runtime_fallback_builds)

    def ResolveFallback(self, fallback_name: str | None = None) -> CombatServices | None:
        requested_name = (fallback_name or self.default_fallback_name or "").strip().casefold()
        fallback_builds = self._iter_fallback_builds(match_only=True)

        if requested_name:
            for build in fallback_builds:
                if (
                    build.build_name.casefold() == requested_name
                    or build.__class__.__name__.casefold() == requested_name
                ):
                    return self._instantiate_build(build.__class__)

        if fallback_builds:
            return self._instantiate_build(fallback_builds[0].__class__)

        return None

    def GetBestBuild(
        self,
        current_primary=None,
        current_secondary=None,
        current_skills: list[int] | None = None,
        fallback_name: str | None = None,
    ) -> CombatServices | None:
        best_build_type: type[CombatServices] | None = None
        best_score = -1

        for build in self._iter_matchable_builds(match_only=True):
            if build.is_template_only:
                continue
            score = build.ScoreMatch(
                current_primary=current_primary,
                current_secondary=current_secondary,
                current_skills=current_skills,
            )
            if score > best_score:
                best_score = score
                best_build_type = build.__class__

        if best_build_type is not None:
            return self._instantiate_build(best_build_type)

        return self.ResolveFallback(fallback_name=fallback_name)

    def ResolveBuild(
        self,
        current_primary=None,
        current_secondary=None,
        current_skills: list[int] | None = None,
        fallback_name: str | None = None,
    ) -> CombatServices | None:
        return self.GetBestBuild(
            current_primary=current_primary,
            current_secondary=current_secondary,
            current_skills=current_skills,
            fallback_name=fallback_name,
        )
