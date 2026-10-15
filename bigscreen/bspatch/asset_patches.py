"""data.unity3d patches (UnityPy). Objects are addressed by (assets file, path id) and checked by name/content."""

import copy

from . import shader_blob

S0 = 'sharedassets0.assets'
GGM = 'globalgamemanagers.assets'
RES = 'resources.assets'

OESBLIT = (GGM, 6, 'BigscreenVR/Unlit/OESBlit')
TEXTURE3D = (S0, 319, 'Unlit/BSVR_Texture3D')
TEXTURE3D_OES = (RES, 825, 'Unlit/BSVR_Texture3D_OES')
DESKTOP_SHADER = (S0, 327, 'BigscreenVR/Unlit/Desktop')
DESKTOP_MAT = (S0, 12, 'Desktop')
PRESENTATION_MATS = [(S0, 9, 'BlankPresentationScreen'), (S0, 25, 'PresentationScreen')]
IMPOSTORS = [(S0, 3538), (S0, 3566)]           # "Underlay Impostor" MeshRenderers under BigMediaRenderer's overlays
IMPOSTOR_MAT = (0, 54)
UNLIT_TEXTURE = 301                            # PresentationScreen materials' original shader (sharedassets0)

ALL_KEYS = [OESBLIT[:2], TEXTURE3D[:2], TEXTURE3D_OES[:2], DESKTOP_SHADER[:2], DESKTOP_MAT[:2],
            *[m[:2] for m in PRESENTATION_MATS], *IMPOSTORS]


def collect(env) -> dict:
    found = {}
    wanted = set(ALL_KEYS)
    for obj in env.objects:
        key = (obj.assets_file.name, obj.path_id)
        if key in wanted and obj.type.name in ('Shader', 'Material', 'MeshRenderer'):
            found[key] = obj
    missing = wanted - set(found)
    if missing:
        raise SystemExit(f'ABORT: objects not found in data.unity3d: {sorted(missing)}')
    return found


def _shader_name(tree) -> str:
    return (tree.get('m_ParsedForm') or {}).get('m_Name')


def _save_same_size(obj, tree, what: str) -> None:
    before = len(obj.get_raw_data())
    if len(obj.save_typetree(tree)) != before:
        raise SystemExit(f'ABORT: {what} changed size')


def patch_oesblit(objs) -> None:
    """Shader 6: uniform precision (fails to link on Mesa) + vertical flip. Used by OVROverlay's OES copy."""
    obj = objs[OESBLIT[:2]]
    t = obj.read_typetree()
    if _shader_name(t) != OESBLIT[2]:
        raise SystemExit('ABORT: shader 6 is not OESBlit')
    shader_blob.store(t, shader_blob.map_sources(shader_blob.decompress(t), shader_blob.fix_oesblit))
    obj.save_typetree(t)


def patch_texture3d(objs) -> None:
    """Shader 319 (MovieScreen: main media screen + browser screens) becomes a copy of Bigscreen's own
    OES variant 825 (samplerExternalOES, multiview, opaque), keeping 319's name."""
    dst, src = objs[TEXTURE3D[:2]], objs[TEXTURE3D_OES[:2]]
    dt, st = dst.read_typetree(), src.read_typetree()
    if _shader_name(dt) != TEXTURE3D[2] or _shader_name(st) != TEXTURE3D_OES[2]:
        raise SystemExit('ABORT: BSVR_Texture3D shaders not as expected')
    if list(dt) != list(st) or st['m_Dependencies'] or st['m_NonModifiableTextures'] or st['m_ParsedForm']['m_Dependencies']:
        raise SystemExit('ABORT: shader 825 is not a self-contained drop-in for 319')
    st['m_ParsedForm']['m_Name'] = TEXTURE3D[2]
    st['m_Name'] = dt['m_Name']
    dst.save_typetree(st)


def patch_desktop_stereo(objs) -> None:
    """Shader 327 (presentation / room screens): per-eye 3D split."""
    obj = objs[DESKTOP_SHADER[:2]]
    t = obj.read_typetree()
    if _shader_name(t) != DESKTOP_SHADER[2]:
        raise SystemExit('ABORT: shader 327 is not BigscreenVR/Unlit/Desktop')
    shader_blob.store(t, shader_blob.map_sources(shader_blob.decompress(t), shader_blob.fix_desktop_stereo))
    obj.save_typetree(t)


def patch_presentation_materials(objs) -> None:
    """PresentationScreen / BlankPresentationScreen (Unlit/Texture, no YUV planes, black _Color) become full
    copies of the `Desktop` material (BigscreenVR/Unlit/Desktop + its property block), keeping their names."""
    ref = objs[DESKTOP_MAT[:2]].read_typetree()
    if ref['m_Name'] != DESKTOP_MAT[2] or ref['m_Shader']['m_PathID'] != DESKTOP_SHADER[1]:
        raise SystemExit("ABORT: reference material 'Desktop' not as expected")
    for key in PRESENTATION_MATS:
        obj = objs[key[:2]]
        tree = obj.read_typetree()
        if tree['m_Name'] != key[2] or tree['m_Shader']['m_PathID'] != UNLIT_TEXTURE:
            raise SystemExit(f'ABORT: material {key} not as expected')
        clone = copy.deepcopy(ref)
        clone['m_Name'] = key[2]
        obj.save_typetree(clone)


def patch_impostors(objs) -> None:
    """Underlay Impostors punch a hole for a Quest underlay; with no overlay they draw a gray panel."""
    for key in IMPOSTORS:
        obj = objs[key]
        rt = obj.read_typetree()
        mats = [(m['m_FileID'], m['m_PathID']) for m in rt['m_Materials']]
        if mats != [IMPOSTOR_MAT] or not rt['m_Enabled']:
            raise SystemExit(f'ABORT: renderer {key} is not an enabled Underlay Impostor')
        rt['m_Enabled'] = type(rt['m_Enabled'])(0)
        _save_same_size(obj, rt, f'renderer {key}')


ALL = [patch_oesblit, patch_texture3d, patch_desktop_stereo, patch_presentation_materials, patch_impostors]


def apply(env) -> None:
    objs = collect(env)
    for fn in ALL:
        fn(objs)


def verify(env) -> list[str]:
    """Re-read a saved bundle and check every patched object."""
    objs = collect(env)
    problems = []
    raw6 = shader_blob.decompress(objs[OESBLIT[:2]].read_typetree())
    if not shader_blob.table_ok(raw6) or any(shader_blob.UV_Y_FLIPPED not in s or shader_blob.OESBLIT_INT_RX.search(s)
                                             for s in shader_blob.sources(raw6)):
        problems.append('OESBlit')
    t319, t825 = objs[TEXTURE3D[:2]].read_typetree(), objs[TEXTURE3D_OES[:2]].read_typetree()
    if _shader_name(t319) != TEXTURE3D[2] or t319['compressedBlob'] != t825['compressedBlob']:
        problems.append('BSVR_Texture3D')
    raw327 = shader_blob.decompress(objs[DESKTOP_SHADER[:2]].read_typetree())
    if not shader_blob.table_ok(raw327) or any(shader_blob.MP_UV_NEW not in s or shader_blob.MV_UV_NEW not in s
                                               for s in shader_blob.sources(raw327)):
        problems.append('Desktop stereo')
    for key in PRESENTATION_MATS:
        t = objs[key[:2]].read_typetree()
        if t['m_Shader']['m_PathID'] != DESKTOP_SHADER[1] or '_YPlane' not in {k for k, _ in t['m_SavedProperties']['m_TexEnvs']}:
            problems.append(key[2])
    for key in IMPOSTORS:
        if objs[key].read_typetree()['m_Enabled']:
            problems.append(f'impostor {key}')
    return problems
