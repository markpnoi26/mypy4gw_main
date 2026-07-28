"""Engine seam for the HeroAI drivers.

Kept as a factory rather than a direct import in headless_tree.py and the
widget: the construction is lazy so importing this module does not pull
Core.BldMgrBT and the build registry into a startup path."""


def create_heroai_engine(cached_data=None, standalone_fallback: bool = False):
    from HeroAI.bt.bt_engine import HeroAIBTEngine

    return HeroAIBTEngine(cached_data, standalone_fallback=standalone_fallback)
