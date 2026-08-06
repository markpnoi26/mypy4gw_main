import PyImGui
from glm import c_uint32
from Core import GLOBAL_CACHE, Agent, Player, Party
from Core.GlobalCache.shared_memory_src.AccountStruct import AccountStruct
from Core.enums_src.UI_enums import WindowID
from Core import UIManager
import Py4GW

initial_sample = {}
end_sample = {}


def main():
    global initial_sample, end_sample
        
    if PyImGui.begin("WindowID Tester"):
        if PyImGui.button("Take initial sample"):
            for i in range(WindowID.WindowID_Count.value):
                initial_sample[i] = UIManager.IsWindowVisible(i)
                
        if PyImGui.button("Take end sample"):
            for i in range(WindowID.WindowID_Count.value):
                end_sample[i] = UIManager.IsWindowVisible(i)

        if initial_sample and end_sample:
            PyImGui.text("WindowID visibility changes:")
            for i in range(WindowID.WindowID_Count.value):
                if initial_sample.get(i) != end_sample.get(i):
                    status = "Visible" if end_sample.get(i) else "Hidden"
                    PyImGui.text(f"WindowID {hex(i)}: {status}")
                    
    PyImGui.end()

if __name__ == "__main__":
    main()
