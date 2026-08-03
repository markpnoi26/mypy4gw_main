"""Unit tests for script_manager/loader.py.

Loaded by file path rather than package import: loader.py is deliberately
stdlib-only so it runs outside the game, and a package import would pull in
Core/__init__.py and its eager Py4GW import.
"""

import os
import shutil
import sys
import tempfile
import unittest

import pathload

loader_mod = pathload.load("Core/py4gwcorelib_src/script_manager/loader.py")

REPO = str(pathload.REPO)


class Sandbox(unittest.TestCase):
    """A temp tree with a fake reload root ('Support') and a script that imports it."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.scripts = os.path.join(self.root, "Scripts")
        self.widgets = os.path.join(self.root, "Widgets")
        os.makedirs(self.scripts)
        os.makedirs(self.widgets)
        os.makedirs(os.path.join(self.root, "Support"))
        with open(os.path.join(self.root, "Support", "__init__.py"), "w") as h:
            h.write("")
        self.write_support("VALUE = 'before'\n")
        sys.path.insert(0, self.root)
        self.loader = loader_mod.ScriptLoader(widgets_path=self.widgets, reload_roots=("Support",))

    def tearDown(self):
        if self.root in sys.path:
            sys.path.remove(self.root)
        for name in [k for k in sys.modules if k.split(".")[0] == "Support"]:
            sys.modules.pop(name, None)
        for name in [k for k in sys.modules if k.startswith(loader_mod.MODULE_PREFIX)]:
            sys.modules.pop(name, None)
        shutil.rmtree(self.root, ignore_errors=True)

    def write_support(self, body, name="helper.py"):
        with open(os.path.join(self.root, "Support", name), "w") as h:
            h.write(body)

    def write_script(self, name="Demo", body=None):
        path = os.path.join(self.scripts, name + ".py")
        with open(path, "w") as h:
            h.write(
                body
                if body is not None
                else '__script__ = {"name": "%s", "function": "tool", "tags": [], "claims": []}\n'
                "from Support.helper import VALUE\n"
                "def main():\n    return VALUE\n" % name
            )
        return path

    def write_widget(self, imports, name="W.py"):
        with open(os.path.join(self.widgets, ".widget"), "w") as h:
            h.write("")
        with open(os.path.join(self.widgets, name), "w") as h:
            h.write("import %s\n" % imports)


class LoadTests(Sandbox):
    def test_loads_and_exposes_entry_points(self):
        path = self.write_script()
        module, _ = self.loader.load("Demo", path)
        self.assertEqual(module.main(), "before")
        self.assertIn("main", self.loader.entry_points(module))

    def test_supporting_code_edit_takes_effect_on_reload(self):
        path = self.write_script()
        module, _ = self.loader.load("Demo", path)
        self.assertEqual(module.main(), "before")

        self.write_support("VALUE = 'after'\n")
        module, dropped = self.loader.load("Demo", path)
        self.assertEqual(module.main(), "after")
        self.assertIn("Support.helper", dropped)

    def test_without_dependency_reload_edit_is_not_seen(self):
        path = self.write_script()
        self.loader.load("Demo", path)
        self.write_support("VALUE = 'after'\n")
        module, dropped = self.loader.load("Demo", path, reload_dependencies=False)
        self.assertEqual(module.main(), "before")
        self.assertEqual(dropped, [])

    def test_script_body_edit_takes_effect(self):
        path = self.write_script()
        self.loader.load("Demo", path)
        self.write_script(
            body='__script__ = {"name": "Demo", "function": "tool",'
            ' "tags": [], "claims": []}\ndef main():\n    return "v2"\n'
        )
        module, _ = self.loader.load("Demo", path)
        self.assertEqual(module.main(), "v2")

    def test_failed_import_does_not_leave_module_registered(self):
        path = self.write_script(body="raise RuntimeError('boom')\n")
        with self.assertRaises(RuntimeError):
            self.loader.load("Broken", path)
        self.assertNotIn(loader_mod.module_name_for("Broken"), sys.modules)

    def test_unload_removes_module(self):
        path = self.write_script()
        self.loader.load("Demo", path)
        self.assertTrue(self.loader.unload("Demo"))
        self.assertNotIn(loader_mod.module_name_for("Demo"), sys.modules)


class ProtectionTests(Sandbox):
    def test_widget_shared_module_is_protected(self):
        self.write_widget("Support.helper")
        path = self.write_script()
        self.loader.load("Demo", path)
        self.write_support("VALUE = 'after'\n")
        module, dropped = self.loader.load("Demo", path)
        self.assertNotIn("Support.helper", dropped)
        self.assertEqual(module.main(), "before")

    def test_unshared_module_is_not_protected(self):
        self.write_widget("Support.other")
        path = self.write_script()
        self.loader.load("Demo", path)
        _, dropped = self.loader.load("Demo", path)
        self.assertIn("Support.helper", dropped)

    def test_protection_covers_submodules(self):
        self.write_widget("Support")
        os.makedirs(os.path.join(self.root, "Support", "sub"))
        for f in ("__init__.py", "leaf.py"):
            with open(os.path.join(self.root, "Support", "sub", f), "w") as h:
                h.write("X = 1\n")
        path = self.write_script(
            body='__script__ = {"name": "D", "function": "tool",'
            ' "tags": [], "claims": []}\nimport Support.sub.leaf\n'
        )
        self.loader.load("Demo", path)
        _, dropped = self.loader.load("Demo", path)
        self.assertNotIn("Support.sub.leaf", dropped)

    def test_corelib_is_never_purgeable(self):
        wide = loader_mod.ScriptLoader(widgets_path=self.widgets, reload_roots=loader_mod.PROTECTED_ROOTS)
        sys.modules.setdefault("Core", type(sys)("Core"))
        try:
            self.assertNotIn("Core", wide.purgeable())
        finally:
            if getattr(sys.modules.get("Core"), "__file__", None) is None:
                sys.modules.pop("Core", None)


class RealRepoTests(unittest.TestCase):
    def setUp(self):
        if not os.path.isdir(os.path.join(REPO, "Widgets")):
            self.skipTest("Widgets/ not present")

    def test_shared_modules_are_detected(self):
        shared = loader_mod.shared_with_widgets(os.path.join(REPO, "Widgets"))
        self.assertIn("HeroAI.cache_data", shared)
        self.assertTrue(all(m.split(".")[0] in loader_mod.RELOAD_ROOTS for m in shared))


if __name__ == "__main__":
    unittest.main(verbosity=2)
