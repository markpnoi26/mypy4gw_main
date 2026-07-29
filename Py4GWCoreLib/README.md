# Launcher marker directory — not a Python package

The launcher resolves the mod root by walking up from the exe looking for a
directory literally named `Py4GWCoreLib`
(`launcher_core/mod_repo.py: MOD_REPO_MARKER_DIR`). This tree renamed that
library to `Core/`, so without this marker the frozen
`Py4GW_Reforged_Launcher.exe` finds nothing, falls back to `exe_dir.parent`,
and resolves the mod root to the parent of this repo instead of this repo.

Nothing imports from here — project code has zero `Py4GWCoreLib` references.
Delete this only if the launcher's marker constant changes.
