"""Unit tests for script_manager/discovery.py.

Loaded by file path rather than package import: discovery.py is deliberately
stdlib-only so it runs outside the game, but importing it through Core
would pull in PySystem, which only exists in-process.
"""

import os
import tempfile
import time
import unittest

import pathload

discovery = pathload.load("Core/py4gwcorelib_src/script_manager/discovery.py")


def write_script(directory, name, function="tool", claims=(), tags=(), lead=""):
    body = '__script__ = {"name": "%s", "function": "%s", "tags": %s, "claims": %s}\n' % (
        name,
        function,
        list(tags),
        list(claims),
    )
    path = os.path.join(directory, name + ".py")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(lead + body)
    return path


class FindBlockTests(unittest.TestCase):
    def test_nested_dict(self):
        self.assertEqual(discovery.find_block('__script__ = {"a": {"b": 1}, "c": 2}'), '{"a": {"b": 1}, "c": 2}')

    def test_brace_inside_string(self):
        self.assertEqual(discovery.find_block('__script__ = {"n": "a}b"}'), '{"n": "a}b"}')

    def test_escaped_quote(self):
        self.assertEqual(discovery.find_block(r'__script__ = {"n": "a\"}b"}'), r'{"n": "a\"}b"}')

    def test_truncated_block_returns_empty(self):
        self.assertEqual(discovery.find_block('__script__ = {"a": 1'), "")

    def test_absent_block_returns_empty(self):
        self.assertEqual(discovery.find_block("x = 1\n"), "")


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_block_beyond_header_window_still_found(self):
        lead = '"""%s"""\n\n' % ("padding " * (discovery.HEADER_WINDOW // 8 + 200))
        path = write_script(self.dir, "Deep", lead=lead)
        self.assertGreater(len(lead), discovery.HEADER_WINDOW)
        self.assertEqual(discovery.build_meta(path, 0.0).name, "Deep")

    def test_missing_block_records_error(self):
        path = os.path.join(self.dir, "Bare.py")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("x = 1\n")
        self.assertIn("no __script__", discovery.build_meta(path, 0.0).error)

    def test_unknown_claim_flagged(self):
        path = write_script(self.dir, "Odd", claims=["telepathy"])
        self.assertIn("unknown claims", discovery.build_meta(path, 0.0).error)


class ConflictTests(unittest.TestCase):
    def meta(self, name, claims):
        return discovery.ScriptMeta(id=name, name=name, path="", claims=tuple(claims))

    def test_same_claim_conflicts(self):
        self.assertTrue(self.meta("a", ["character"]).conflicts_with(self.meta("b", ["character"])))

    def test_character_subsumes_dialog(self):
        self.assertTrue(self.meta("bot", ["character"]).conflicts_with(self.meta("npc", ["dialog"])))

    def test_unrelated_claims_coexist(self):
        self.assertFalse(self.meta("a", ["ui"]).conflicts_with(self.meta("b", ["inventory"])))

    def test_no_claims_never_conflicts(self):
        self.assertFalse(self.meta("a", []).conflicts_with(self.meta("b", ["character"])))


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.registry = discovery.ScriptRegistry(self.dir)

    def test_add_seen_on_refresh(self):
        self.registry.reload()
        write_script(self.dir, "Added", function="farmer", claims=["character"])
        self.assertTrue(self.registry.changed_on_disk())
        self.assertTrue(self.registry.refresh())
        self.assertEqual(self.registry.get("Added").function, "farmer")

    def test_inplace_edit_seen_on_refresh(self):
        write_script(self.dir, "Edited", function="tool")
        self.registry.reload()
        time.sleep(0.05)
        write_script(self.dir, "Edited", function="farmer")
        self.assertTrue(self.registry.changed_on_disk())
        self.assertTrue(self.registry.refresh())
        self.assertEqual(self.registry.get("Edited").function, "farmer")

    def test_changed_on_disk_is_false_when_clean(self):
        write_script(self.dir, "Quiet")
        self.registry.reload()
        self.assertFalse(self.registry.changed_on_disk())
        self.assertFalse(self.registry.refresh())

    def test_changed_on_disk_does_not_mutate(self):
        self.registry.reload()
        write_script(self.dir, "Peek")
        self.assertTrue(self.registry.changed_on_disk())
        self.assertIsNone(self.registry.get("Peek"))

    def test_delete_seen_on_refresh(self):
        path = write_script(self.dir, "Doomed")
        self.registry.reload()
        os.remove(path)
        self.assertTrue(self.registry.refresh())
        self.assertIsNone(self.registry.get("Doomed"))

    def test_pinned_script_not_reread(self):
        write_script(self.dir, "Running", function="tool")
        self.registry.reload()
        self.registry.pin("Running")
        time.sleep(0.05)
        write_script(self.dir, "Running", function="farmer")
        self.registry.refresh()
        self.assertEqual(self.registry.get("Running").function, "tool")
        self.assertIn("Running", self.registry.stale)

    def test_unpin_refreshes(self):
        self.test_pinned_script_not_reread()
        self.registry.unpin("Running")
        self.assertEqual(self.registry.get("Running").function, "farmer")
        self.assertNotIn("Running", self.registry.stale)

    def test_blocked_by(self):
        write_script(self.dir, "BotA", claims=["character"])
        write_script(self.dir, "BotB", claims=["character"])
        write_script(self.dir, "Viewer", claims=[])
        self.registry.reload()
        self.assertEqual(self.registry.blocked_by("BotB", ["BotA"]), ["BotA"])
        self.assertEqual(self.registry.blocked_by("Viewer", ["BotA"]), [])
        self.assertEqual(self.registry.blocked_by("BotA", ["BotA"]), [])

    def test_query_filters(self):
        write_script(self.dir, "Alpha", function="farmer", tags=["title"], claims=["character"])
        write_script(self.dir, "Beta", function="debug")
        self.registry.reload()
        self.assertEqual([s.id for s in self.registry.query(function="farmer")], ["Alpha"])
        self.assertEqual([s.id for s in self.registry.query(tag="title")], ["Alpha"])
        self.assertEqual([s.id for s in self.registry.query(claim="character")], ["Alpha"])
        self.assertEqual([s.id for s in self.registry.query(text="bet")], ["Beta"])

    def test_missing_directory_is_not_fatal(self):
        registry = discovery.ScriptRegistry(os.path.join(self.dir, "nope"))
        self.assertFalse(registry.reload())
        self.assertEqual(registry.all(), [])


class RealScriptsTests(unittest.TestCase):
    """Runs against the repo's actual Scripts/ folder when it holds scripts."""

    def setUp(self):
        self.root = str(pathload.REPO / "Scripts")
        # Migration moves scripts in one at a time, so an existing-but-empty
        # Scripts/ is a normal state, not a failure.
        if not discovery.ScriptRegistry(self.root).scan_mtimes():
            self.skipTest("no scripts in Scripts/")

    def test_every_script_parses(self):
        registry = discovery.ScriptRegistry(self.root)
        registry.reload()
        self.assertGreater(len(registry.scripts), 0)
        self.assertEqual([s.id + ": " + s.error for s in registry.errors()], [])

    def test_clean_refresh_is_cheap(self):
        registry = discovery.ScriptRegistry(self.root)
        registry.reload()
        start = time.perf_counter()
        for _ in range(20):
            registry.refresh()
        self.assertLess((time.perf_counter() - start) / 20 * 1000, 40.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
