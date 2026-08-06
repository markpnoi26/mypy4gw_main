

from Core.Agent import Agent
from Core.AgentArray import AgentArray
from Core.Item import Bag, Item
from Core.ItemArray import ItemArray
from Core.Player import Player
from Core.Routines import Routines
from Core.enums_src.GameData_enums import Range

from dev.reference.frenkeyLib.ItemHandling.GlobalConfigs.RuleConfig import RuleConfig

class SalvageConfig(RuleConfig):    
    def GetSalvageItems(self, bags : list[Bag]) -> list[int]:                        
        if not Routines.Checks.Map.MapValid():
            return []
            
        item_ids = ItemArray.GetItemArray(ItemArray.CreateBagList(*bags))        
        filtered_array = []

        for item_id in item_ids[:]:  # Iterate over a copy to avoid modifying while iterating
            if not self.EvaluateItem(item_id):
                continue
            
            filtered_array.append(item_id)
            
        return filtered_array
