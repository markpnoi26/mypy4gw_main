# PyCtoS stub — Reforged Native surface
# Matches src/GW/ctos/ctos_bindings.cpp (native repo, `personal` branch).
# words[0] is the RAW unmasked opcode; the DLL validates the header against the
# game's message table and queues the send on the game thread. False = empty or
# oversized packet; True = queued, NOT delivered (dropped silently when the map
# or connection is not ready).

def SendPacket(words: list[int]) -> bool: ...
