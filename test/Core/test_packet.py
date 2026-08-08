# Contract tests for Core/Packet.py — the PyCtoS wrapper.
# Loaded via pathload so the eager Core facade stays out of the test;
# nativestub (installed by conftest) serves PyCtoS from stubs/PyCtoS.pyi.

import pytest

import pathload

packet_mod = pathload.load('Core/Packet.py')
Packet = packet_mod.Packet


def capture_sends(monkeypatch, result=True):
    calls = []

    def fake(words):
        calls.append(list(words))
        return result

    monkeypatch.setattr(packet_mod.PyCtoS, 'SendPacket', fake)
    return calls


def test_send_raw_masks_to_u32(monkeypatch):
    calls = capture_sends(monkeypatch)
    assert Packet.SendRaw([-1, 2**33 + 5]) is True
    assert calls == [[0xFFFFFFFF, 5]]


def test_send_prepends_header(monkeypatch):
    calls = capture_sends(monkeypatch)
    Packet.Send(0x21, 7, 9)
    assert calls == [[0x21, 7, 9]]


def test_pack_wstring_pads_to_full_width():
    words = Packet.PackWString('ab', 8)
    assert len(words) == 4
    assert words[0] == 0x00620061
    assert words[1:] == [0, 0, 0]


def test_pack_wstring_clamps_and_keeps_nul():
    words = Packet.PackWString('x' * 20, 8)
    # capacity 8 wchars: at most 7 chars of text, the 8th is always NUL
    raw = b''.join(w.to_bytes(4, 'little') for w in words)
    assert raw[:14] == 'x'.encode('utf-16-le') * 7
    assert raw[14:16] == b'\x00\x00'


@pytest.mark.parametrize('capacity', [0, -2, 7])
def test_pack_wstring_rejects_bad_capacity(capacity):
    with pytest.raises(ValueError):
        Packet.PackWString('a', capacity)


def test_builder_composes_fields_in_order(monkeypatch):
    calls = capture_sends(monkeypatch)
    built = Packet.Builder(0x10).Dword(1).Dwords([2, 3]).Qword((9 << 32) | 4).WString('a', 2).Build()
    assert built == [0x10, 1, 2, 3, 4, 9, 0x0061]
    Packet.Builder(0x10).Bytes(b'\x01\x02\x03\x04\x05').Send()
    assert calls == [[0x10, 0x04030201, 0x00000005]]


def test_change_weapon_set_wire_is_zero_based(monkeypatch):
    calls = capture_sends(monkeypatch)
    assert Packet.ChangeWeaponSet(1) is True
    assert Packet.ChangeWeaponSet(4) is True
    assert calls == [[0x32, 0], [0x32, 3]]


def test_change_weapon_set_rejects_out_of_range(monkeypatch):
    calls = capture_sends(monkeypatch)
    assert Packet.ChangeWeaponSet(0) is False
    assert Packet.ChangeWeaponSet(5) is False
    assert calls == []
