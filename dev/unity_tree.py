#!/usr/bin/env python3
"""Print a GameObject hierarchy from a Unity APK: active state, transforms, components, materials and shaders.

    unity_tree.py app.apk sharedassets0.assets 1223        # ScreensContainer and children
    unity_tree.py app.apk --find RegularScreen             # GameObjects by name (file#pid)
    unity_tree.py app.apk --refs sharedassets0.assets 18   # renderers using a material

MonoBehaviours are shown with their script class name. Built while tracing which
mesh/overlay draws Bigscreen's screens (docs/PATCHES.md).
"""

import argparse
import pathlib
import struct
import sys
import zipfile

BUNDLE = 'assets/bin/Data/data.unity3d'


class Scene:
    def __init__(self, path: pathlib.Path):
        import UnityPy
        env = UnityPy.load(zipfile.ZipFile(path).read(BUNDLE) if path.suffix == '.apk' else str(path))
        self.objs = {(o.assets_file.name, o.path_id): o for o in env.objects}
        self.scripts = {}
        for k, o in self.objs.items():
            if o.type.name == 'MonoScript':
                try:
                    self.scripts[k] = o.read().m_ClassName
                except Exception:
                    pass

    def ref(self, src, pptr):
        fid, pid = pptr['m_FileID'], pptr['m_PathID']
        name = src.assets_file.name if fid == 0 else src.assets_file.externals[fid - 1].path.split('/')[-1]
        return self.objs.get((name, pid)), (name, pid)

    def describe_material(self, src, pptr):
        mo, key = self.ref(src, pptr)
        if mo is None:
            return f'{key}?'
        mt = mo.read_typetree()
        so, skey = self.ref(mo, mt['m_Shader'])
        shader = (so.read_typetree().get('m_ParsedForm') or {}).get('m_Name') if so else f'{skey}?'
        return f'{mt["m_Name"]}({key[0]}#{key[1]}) shader={shader}'

    def dump(self, key, depth=0):
        go = self.objs[key]
        g = go.read_typetree()
        print('  ' * depth + f'[{key[1]}] {g["m_Name"]} active={g["m_IsActive"]}')
        children = []
        for c in g['m_Component']:
            co, ckey = self.ref(go, c['component'])
            if co is None:
                continue
            t = co.type.name
            pad = '  ' * depth + '    '
            if t in ('Transform', 'RectTransform'):
                tr = co.read_typetree()
                pos = tuple(round(v, 3) for v in tr['m_LocalPosition'].values())
                scale = tuple(round(v, 3) for v in tr['m_LocalScale'].values())
                print(f'{pad}{t} pos={pos} scale={scale}')
                children = [self.ref(co, ch) for ch in tr['m_Children']]
            elif t in ('MeshRenderer', 'SkinnedMeshRenderer'):
                r = co.read_typetree()
                mats = [self.describe_material(co, m) for m in r['m_Materials']]
                print(f'{pad}{t}[{ckey[1]}] enabled={r["m_Enabled"]} {mats}')
            elif t == 'MonoBehaviour':
                raw = co.get_raw_data()
                sfid, spid = struct.unpack_from('<iq', raw, 16)
                so, skey = self.ref(co, {'m_FileID': sfid, 'm_PathID': spid})
                print(f'{pad}MonoBehaviour[{ckey[1]}] {self.scripts.get(skey, skey)} enabled={raw[12]}')
            else:
                print(f'{pad}{t}[{ckey[1]}]')
        for cto, _ in children:
            if cto is not None:
                gref = cto.read_typetree()['m_GameObject']
                self.dump(self.ref(cto, gref)[1], depth + 1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('bundle', type=pathlib.Path, help='APK or data.unity3d')
    ap.add_argument('file', nargs='?', help='assets file, e.g. sharedassets0.assets')
    ap.add_argument('pid', nargs='?', type=int, help='GameObject path id')
    ap.add_argument('--find', help='list GameObjects with this name')
    ap.add_argument('--refs', nargs=2, metavar=('FILE', 'PID'), help='renderers that use this material')
    args = ap.parse_args()
    scene = Scene(args.bundle)

    if args.find:
        for k, o in scene.objs.items():
            if o.type.name == 'GameObject' and o.peek_name() == args.find:
                print(f'{k[0]}#{k[1]}')
    elif args.refs:
        want = (args.refs[0], int(args.refs[1]))
        for k, o in scene.objs.items():
            if o.type.name in ('MeshRenderer', 'SkinnedMeshRenderer'):
                try:
                    r = o.read_typetree()
                except Exception:
                    continue
                if any(scene.ref(o, m)[1] == want for m in r['m_Materials']):
                    print(f'{k[0]}#{k[1]}')
    elif args.file and args.pid is not None:
        scene.dump((args.file, args.pid))
    else:
        ap.error('give FILE PID, --find or --refs')
    return 0


if __name__ == '__main__':
    sys.exit(main())
