"""Client-to-server packet sender over the embedded PyCtoS module.

words[0] is the RAW opcode — the game OR-masks 0x8000 itself, never pre-mask.
Body layout follows the message's unpacked struct: one dword per u32 field, two
per u64, and a wchar[N] string field occupies N/2 dwords. True means queued on
the game thread, not delivered; the DLL drops the packet when no connection is
ready.
"""

import struct
from typing import List
from typing import Sequence

import PyCtoS

U32_MASK = 0xFFFFFFFF
U64_MASK = 0xFFFFFFFFFFFFFFFF

CTOS_OPCODE_CHANGE_WEAPON_SET = 0x32


class Packet:
    @staticmethod
    def SendRaw(words: Sequence[int]) -> bool:
        return PyCtoS.SendPacket([int(w) & U32_MASK for w in words])

    @staticmethod
    def Send(header: int, *dwords: int) -> bool:
        return Packet.SendRaw([header, *dwords])

    @staticmethod
    def PackWString(text: str, wchar_capacity: int) -> List[int]:
        """Pack a fixed-width wchar[wchar_capacity] field into dwords.

        Clamps to capacity-1 chars so the NUL terminator always fits — the game
        asserts (and crashes) on a string field with no NUL inside its capacity.
        """
        if wchar_capacity <= 0 or wchar_capacity % 2 != 0:
            raise ValueError('wchar_capacity must be a positive even number')
        encoded = text.encode('utf-16-le')[: (wchar_capacity - 1) * 2]
        buffer = encoded.ljust(wchar_capacity * 2, b'\x00')
        return list(struct.unpack(f'<{wchar_capacity // 2}I', buffer))

    @staticmethod
    def ChangeWeaponSet(set_number: int) -> bool:
        """Switch to weapon set 1..4. The wire index is zero-based (RE: CharMsgSendOrderEquipSet)."""
        if set_number < 1 or set_number > 4:
            return False
        return Packet.SendRaw([CTOS_OPCODE_CHANGE_WEAPON_SET, set_number - 1])

    class Builder:
        """Assemble a packet field-by-field, then Send() or Build().

        Packet.Builder(header).Dword(field).WString('text', 64).Send()
        """

        def __init__(self, header: int) -> None:
            self.words: List[int] = [int(header) & U32_MASK]

        def Dword(self, value: int) -> 'Packet.Builder':
            self.words.append(int(value) & U32_MASK)
            return self

        def Dwords(self, values: Sequence[int]) -> 'Packet.Builder':
            self.words.extend(int(v) & U32_MASK for v in values)
            return self

        def Qword(self, value: int) -> 'Packet.Builder':
            value = int(value) & U64_MASK
            self.words.append(value & U32_MASK)
            self.words.append((value >> 32) & U32_MASK)
            return self

        def WString(self, text: str, wchar_capacity: int) -> 'Packet.Builder':
            self.words.extend(Packet.PackWString(text, wchar_capacity))
            return self

        def Bytes(self, data: bytes) -> 'Packet.Builder':
            padded = bytes(data)
            if len(padded) % 4 != 0:
                padded = padded.ljust((len(padded) + 3) // 4 * 4, b'\x00')
            self.words.extend(struct.unpack(f'<{len(padded) // 4}I', padded))
            return self

        def Build(self) -> List[int]:
            return list(self.words)

        def Send(self) -> bool:
            return Packet.SendRaw(self.words)


__all__ = ['Packet', 'CTOS_OPCODE_CHANGE_WEAPON_SET']
