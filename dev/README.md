# Developer tools

Reverse-engineering helpers used to build the patches. Usage and background: [docs/REVERSING.md](../docs/REVERSING.md).

| Script | Needs |
|---|---|
| `il2cpp_disasm.py` | `libil2cpp.so`, Il2CppDumper output (`script.json`, `stringliteral.json`), capstone, pyelftools (for string literals) |
| `apk_diff.py` | two APKs; UnityPy for the asset comparison |
| `unity_shaders.py` | an APK or `data.unity3d`; UnityPy, lz4 |
| `unity_tree.py` | an APK or `data.unity3d`; UnityPy |

Loading Bigscreen's `data.unity3d` takes about a minute and a few GB of RAM.
