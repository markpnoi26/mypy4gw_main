# Deprecated leaves

25 files. **This list is generated — edit the rule, not the list.**

Under RS-004 a leaf that fails to load outside a protected pack is treated
as unwanted rather than broken. Nothing here is deleted: the files are still
in the tree and still in git. They are simply off the gate, so they cannot
hold up a sync, and listed here so they cannot disappear quietly either.

To rescue one, fix it and it leaves this list on the next run. To protect a
whole pack from the rule, add it to `PROTECTED` in `qa/breakage.py`.

`origin` is whether upstream's own copy loads: **ours** means our transform
broke a file that worked upstream, **inherited** means it was already broken.
Refresh it with `python qa/breakage.py --vs-upstream`.

| file | origin | error |
|---|---|---|
| `Scripts/py4gw-community-bots/legacy/Elite Tome Farm/elite_tome_farmer.py` | inherited | ModuleNotFoundError: No module named 'tome_targets' |
| `Scripts/py4gw-community-bots/legacy/Example Bots/YAVB 1.0 by Apo (Vaettir bot).py` | inherited | ModuleNotFoundError: No module named 'YAVB' |
| `Scripts/py4gw-community-bots/legacy/Example Bots/YAVB/FSM.py` | inherited | ImportError: attempted relative import with no known parent package |
| `Scripts/py4gw-community-bots/legacy/Example Bots/YAVB/FSMHelpers.py` | inherited | ImportError: attempted relative import with no known parent package |
| `Scripts/py4gw-community-bots/legacy/Example Bots/YAVB/GUI.py` | inherited | ImportError: attempted relative import with no known parent package |
| `Scripts/py4gw-community-bots/legacy/Example Bots/YAVB/YAVBMain.py` | inherited | ImportError: attempted relative import with no known parent package |
| `Scripts/py4gw-community-bots/legacy/Halloween/Every bit Helps.py` | ours | ModuleNotFoundError: No module named 'Bots' |
| `Scripts/py4gw-community-bots/scripts/Missions/Dungeons/Frog Scepter bot.py` | ours | ModuleNotFoundError: No module named 'Widgets.System' |
| `Scripts/py4gw-community-bots/scripts/Missions/Dungeons/SoO.py` | ours | ModuleNotFoundError: No module named 'Widgets.System' |
| `Scripts/py4gw-community-bots/scripts/Runners/Sulfurous Runner.py` | inherited | ValueError: invalid literal for int() with base 10: '' |
| `Scripts/py4gw-devtools/scripts/Examples/Skills/SkillInfo.py` | inherited | AttributeError: 'NoneType' object has no attribute '__dict__' |
| `Scripts/py4gw-devtools/scripts/Tools/Bridge Client.py` | ours | ModuleNotFoundError: No module named 'BridgeRuntime' |
| `Scripts/py4gw-examples/Coord_Logger.py` | inherited | AttributeError: module 'Py4GW' has no attribute 'Timer' |
| `Scripts/py4gw-examples/PingHandler.py` | inherited | ModuleNotFoundError: No module named 'Py4GWcorelib' |
| `Scripts/py4gw-examples/get_icons.py` | inherited | FileNotFoundError: [Errno 2] No such file or directory: 'icons.json' |
| `Scripts/py4gw-examples/mod handler.py` | inherited | NameError: name 'add_modifier' is not defined |
| `Scripts/py4gw-examples/package_template_tester.py` | inherited | ModuleNotFoundError: No module named 'PackageTemplate' |
| `Scripts/py4gw-examples/scraper_model_id_list.py` | inherited | ModuleNotFoundError: No module named 'bs4' |
| `Scripts/py4gw-examples/style_test.py` | inherited | ImportError: cannot import name 'Themes' from 'Core.ImGui' (C:\cygwin64\home\Mark\c |
| `Scripts/py4gw-tasks/scripts/EliteSkillsCapture.py` | ours | ModuleNotFoundError: No module named 'Widgets.Automation' |
| `Widgets/Client/Layout Manager.py` | inherited | RuntimeError: Shared memory not available (C++ writer not up). |
| `Widgets/Combat/EZ Cast.py` | ours | ModuleNotFoundError: No module named 'Widgets.Coding' |
| `Widgets/Overlays/Map Overlay.py` | inherited | ValueError: '' is not a valid OverlayMode |
| `Widgets/Panels/Messaging.py` | ours | ModuleNotFoundError: No module named 'Widgets.Automation' |
| `Widgets/Panels/Style Manager.py` | inherited | KeyError: '' |

