"""Minimal ARM64 helpers: word access, branch encoding, disassembly."""

import struct

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN

_md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)


def words(data, addr: int, count: int) -> list[int]:
    return list(struct.unpack_from(f'<{count}I', data, addr))


def put(data: bytearray, addr: int, ws: list[int]) -> None:
    for i, w in enumerate(ws):
        struct.pack_into('<I', data, addr + 4 * i, w)


def branch(src: int, dst: int, link: bool = False) -> int:
    """Encode `b dst` / `bl dst` placed at `src`."""
    off = (dst - src) // 4
    if (dst - src) % 4 or not -(1 << 25) <= off < (1 << 25):
        raise ValueError(f'branch 0x{src:x} -> 0x{dst:x} out of range')
    return (0x94000000 if link else 0x14000000) | (off & 0x3FFFFFF)


def disasm(data, addr: int, count: int) -> list[str]:
    """`mnemonic operands` per instruction (capstone formatting)."""
    return [f'{i.mnemonic} {i.op_str}' for i in _md.disasm(bytes(data[addr:addr + 4 * count]), addr)]
