#!/usr/bin/env python3
"""Symbolized ARM64 disassembly of an IL2CPP libil2cpp.so, using Il2CppDumper output.

    il2cpp_disasm.py --lib libil2cpp.so --dump DUMPDIR 'BigMediaRenderer$$Update'
    il2cpp_disasm.py --lib libil2cpp.so --dump DUMPDIR 0x149f900 40
    il2cpp_disasm.py --lib libil2cpp.so --dump DUMPDIR --callers 'PresentationSection$$SetupDisplay'

DUMPDIR must contain script.json and stringliteral.json (Il2CppDumper output).
Calls are named; `adrp` + `ldr` pairs that load a metadata slot are resolved to
the method/type name or, through the ELF relocation, to the string literal.
For the Bigscreen build, file offsets equal virtual addresses.
"""

import argparse
import bisect
import json
import pathlib
import struct
import sys

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN


class Image:
    def __init__(self, lib: pathlib.Path, dump: pathlib.Path):
        self.data = lib.read_bytes()
        sj = json.loads((dump / 'script.json').read_text())
        self.methods = {}
        for m in sj['ScriptMethod']:
            self.methods.setdefault(m['Address'], m['Name'])
        self.starts = sorted(self.methods)
        self.meta = {}
        for k in ('ScriptMetadata', 'ScriptMetadataMethod'):
            for m in sj.get(k, []):
                self.meta[m['Address']] = m['Name']
        self.literals = {int(s['address'], 16): s['value']
                         for s in json.loads((dump / 'stringliteral.json').read_text())}
        self.relocs = self._relocs(lib)
        self.md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)

    @staticmethod
    def _relocs(lib):
        try:
            from elftools.elf.elffile import ELFFile
        except ImportError:
            return {}
        out = {}
        with open(lib, 'rb') as f:
            for s in ELFFile(f).iter_sections():
                if s.header.sh_type == 'SHT_RELA':
                    for r in s.iter_relocations():
                        out[r['r_offset']] = r['r_addend']
        return out

    def name(self, addr: int) -> str:
        i = bisect.bisect_right(self.starts, addr) - 1
        if i < 0:
            return '?'
        s = self.starts[i]
        return self.methods[s] if s == addr else f'{self.methods[s]}+{addr - s:#x}'

    def function_range(self, addr: int) -> tuple[int, int]:
        i = bisect.bisect_right(self.starts, addr) - 1
        start = self.starts[i]
        end = self.starts[i + 1] if i + 1 < len(self.starts) else start + 0x1000
        return start, min(end, start + 0x4000)

    def resolve(self, arg: str) -> int:
        try:
            return int(arg, 0)
        except ValueError:
            pass
        exact = [a for a, n in self.methods.items() if n == arg or n.endswith('.' + arg)]
        hits = exact or [a for a, n in self.methods.items() if arg in n]
        if len(hits) != 1:
            for a in hits[:40]:
                print(f'  {a:#x} {self.methods[a]}')
            sys.exit(f'{len(hits)} matches for {arg!r}; be more specific')
        return hits[0]

    def slot_note(self, slot: int) -> str:
        if slot in self.meta:
            return self.meta[slot]
        target = self.relocs.get(slot)
        if target in self.literals:
            return repr(self.literals[target])[:80]
        if target in self.meta:
            return self.meta[target]
        return ''

    def disasm(self, start: int, end: int):
        pages = {}
        for ins in self.md.disasm(self.data[start:end], start):
            ops, note = ins.op_str, ''
            if ins.mnemonic in ('bl', 'b') and ops.startswith('#'):
                t = int(ops[1:], 16)
                if ins.mnemonic == 'bl' or not (start <= t < end):
                    note = self.name(t)
            elif ins.mnemonic == 'adrp':
                reg, page = ops.split(', ')
                pages[reg] = int(page[1:], 16)
            elif ins.mnemonic in ('ldr', 'add'):
                parts = ops.replace('[', '').replace(']', '').replace('!', '').split(', ')
                if len(parts) >= 3 and parts[1] in pages and parts[2].startswith('#'):
                    note = self.slot_note(pages[parts[1]] + int(parts[2][1:], 16))
            yield ins.address, f'{ins.mnemonic:6} {ops}', note

    def callers(self, target: int):
        lo, hi = self.starts[0], min(self.starts[-1] + 0x1000, len(self.data))
        for off in range(lo - lo % 4, hi, 4):
            w = struct.unpack_from('<I', self.data, off)[0]
            if (w & 0xFC000000) in (0x94000000, 0x14000000):
                imm = w & 0x3FFFFFF
                if imm & 0x2000000:
                    imm -= 1 << 26
                if off + imm * 4 == target:
                    yield off


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--lib', type=pathlib.Path, required=True, help='libil2cpp.so')
    ap.add_argument('--dump', type=pathlib.Path, required=True, help='Il2CppDumper output directory')
    ap.add_argument('--callers', action='store_true', help='list direct callers (bl/b) of the target')
    ap.add_argument('target', help='address (0x...) or method name (substring)')
    ap.add_argument('count', nargs='?', type=int, help='instructions (default: whole function)')
    args = ap.parse_args()

    img = Image(args.lib, args.dump)
    addr = img.resolve(args.target)
    if args.callers:
        for off in img.callers(addr):
            print(f'{off:#x}  {img.name(off)}')
        return 0
    if args.count:
        start, end = addr, addr + 4 * args.count
    else:
        start, end = img.function_range(addr)
    print(f'== {img.name(addr)} @ {start:#x} .. {end:#x}')
    for a, text, note in img.disasm(start, end):
        print(f'{a:#9x}: {text:48} {"; " + note if note else ""}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
