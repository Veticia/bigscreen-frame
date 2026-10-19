"""libil2cpp.so patches. Addresses are file offsets (== VAs) in the supported build.

Every patch lists the original words it expects (fingerprint) and its new words;
`apply()` refuses to touch anything unless every fingerprint matches, then checks
the result by disassembly. See docs/PATCHES.md for the reasoning behind each one.
"""

from dataclasses import dataclass, field

from . import arm64

RET, NOP = 0xD65F03C0, 0xD503201F

# Functions the new code calls; their prologues are checked too.
BEHAVIOUR_SET_ENABLED = 0x18639EC      # UnityEngine.Behaviour$$set_enabled
GAMEOBJECT_SET_ACTIVE = 0x186E980      # UnityEngine.GameObject$$SetActive
UPDATE_REGULAR_OVERLAY = 0x149FD1C     # Bigscreen.Media.BigMediaRenderer$$UpdateRegularOverlay
UPDATE_REGULAR_SCREEN = 0x14A0584      # Bigscreen.Media.BigMediaRenderer$$UpdateRegularScreen
SUBTITLE_TIMER_FCVT = 0xDB973C         # <SubtitleTimer>d__103$$MoveNext: fcvt s0, d8
SUBTITLE_CAVE = 0x149FD88              # dead tail of UpdateRegularOverlay once the stub is in place
THREAD_SLEEP = 0x1A8E9B4               # System.Threading.Thread$$Sleep(int)
SOCKET_SEND_TO_ASYNC = 0x1015000       # System.Net.Sockets.Socket$$SendToAsync
SSDP_SEND_DONE = 0xE90368              # DLNAService$$OnSocketSendEventCompleted: `cmp w8, #9` (LastOperation == SendTo)
SSDP_RETURN = 0xE90348                 # ... its epilogue
SSDP_START_RECEIVE = 0xE90370          # ... its SendTo branch: switch the event args over to ReceiveFromAsync
SSDP_CAVE = SUBTITLE_CAVE + 6 * 4      # dead tail of UpdateRegularOverlay, after the subtitle cave

CALLEE_PROLOGUES = [
    (BEHAVIOUR_SET_ENABLED, [0xF81D0FF5, 0xA9014FF4, 0xA9027BFD, 0x910083FD], 'Behaviour$$set_enabled'),
    (GAMEOBJECT_SET_ACTIVE, [0xF81D0FF5, 0xA9014FF4, 0xA9027BFD, 0x910083FD], 'GameObject$$SetActive'),
    (UPDATE_REGULAR_SCREEN, [0xD10143FF, 0xA9015FF8, 0xA90257F6, 0xA9034FF4, 0xA9047BFD, 0x910103FD],
     'BigMediaRenderer$$UpdateRegularScreen'),
    (0x103736C, [0xF9406A60], 'BigUIBrowser$$UpdateActiveStates (Regular branch)'),
    (0x108068C, None, 'EntitlementCheck$$HandleEntitlementCheckResult'),
    (THREAD_SLEEP, [0x3100081F, 0x5400004D], 'Thread$$Sleep'),
    (SOCKET_SEND_TO_ASYNC, [0xF81B0FF9, 0xA9015FF8, 0xA90257F6, 0xA9034FF4], 'Socket$$SendToAsync'),
]


@dataclass
class Patch:
    key: str
    addr: int
    original: list[int]
    new: list[int]
    asm: list[str] = field(default_factory=list)   # expected disassembly of `new` (optional)
    why: str = ''


def _screen_stub(base: int) -> list[int]:
    """UpdateRegularOverlay replacement: disable the OVROverlay quad, keep the in-scene screen
    active, then tail-call UpdateRegularScreen with the unchanged arguments."""
    return [
        0xA9BD7BFD,  # stp x29, x30, [sp, #-0x30]!
        0x910003FD,  # mov x29, sp
        0xA90153F3,  # stp x19, x20, [sp, #0x10]
        0xA9025BF5,  # stp x21, x22, [sp, #0x20]
        0xAA0003F3,  # mov x19, x0              this
        0xAA0103F4,  # mov x20, x1              texId
        0xAA0203F5,  # mov x21, x2              frameSize
        0x2A0303F6,  # mov w22, w3              resizeRequired
        0xF9401660,  # ldr x0, [x19, #0x28]     regularOverlay (OVROverlay)
        0xB4000080,  # cbz x0, +0x10
        0x2A1F03E1,  # mov w1, wzr
        0xAA1F03E2,  # mov x2, xzr
        arm64.branch(base + 12 * 4, BEHAVIOUR_SET_ENABLED, link=True),   # .enabled = false
        0xF9401260,  # ldr x0, [x19, #0x20]     regularScreen (GameObject)
        0xB4000080,  # cbz x0, +0x10
        0x52800021,  # mov w1, #1
        0xAA1F03E2,  # mov x2, xzr
        arm64.branch(base + 17 * 4, GAMEOBJECT_SET_ACTIVE, link=True),   # .SetActive(true)
        0xAA1303E0,  # mov x0, x19
        0xAA1403E1,  # mov x1, x20
        0xAA1503E2,  # mov x2, x21
        0x2A1603E3,  # mov w3, w22
        0xAA1F03E4,  # mov x4, xzr
        0xA9425BF5,  # ldp x21, x22, [sp, #0x20]
        0xA94153F3,  # ldp x19, x20, [sp, #0x10]
        0xA8C37BFD,  # ldp x29, x30, [sp], #0x30
        arm64.branch(base + 26 * 4, UPDATE_REGULAR_SCREEN),
    ]


def _subtitle_cave(base: int) -> list[int]:
    """s0 = (float)d8 > 0.125 ? (float)d8 : 4.0, then back into SubtitleTimer (encodings: aarch64-linux-gnu-as)."""
    return [
        0x1E624100,  # fcvt  s0, d8
        0x1E221001,  # fmov  s1, #4.0
        0x1E281002,  # fmov  s2, #0.125
        0x1E222000,  # fcmp  s0, s2
        0x1E21CC00,  # fcsel s0, s0, s1, gt
        arm64.branch(base + 5 * 4, SUBTITLE_TIMER_FCVT + 4),
    ]


def _ssdp_cave(base: int) -> list[int]:
    """Send the SSDP search a second time, 1.5 s after the first, before switching to receiving.

    Lepton's pasta forwards replies to a container UDP port only after its port scan (about once a second) has
    seen the port, so the answers to the first search are usually lost. x19 = event args (reused for the send
    and the receive), x20 = this; event args.UserToken (+0x90, unused by Bigscreen) marks the second send."""
    return [
        0x7100251F,  # cmp  w8, #9                LastOperation == SendTo?
        0x54000040,  # b.eq +8
        arm64.branch(base + 2 * 4, SSDP_RETURN),
        0xF9404A68,  # ldr  x8, [x19, #0x90]      UserToken
        0xB4000048,  # cbz  x8, +8
        arm64.branch(base + 5 * 4, SSDP_START_RECEIVE),               # second send done: receive as before
        0xF9004A73,  # str  x19, [x19, #0x90]     mark the first send
        0x5280BB80,  # mov  w0, #1500
        0xAA1F03E1,  # mov  x1, xzr
        arm64.branch(base + 9 * 4, THREAD_SLEEP, link=True),
        0xF9401A80,  # ldr  x0, [x20, #0x30]      this.socket
        0xB4000080,  # cbz  x0, +16
        0xAA1303E1,  # mov  x1, x19
        0xAA1F03E2,  # mov  x2, xzr
        arm64.branch(base + 14 * 4, SOCKET_SEND_TO_ASYNC, link=True),  # completes into this handler again
        arm64.branch(base + 15 * 4, SSDP_RETURN),
    ]


PATCHES = [
    Patch('entitlement', 0x1080594, [0xAA1F03E0, 0x97FFDE76, 0x97FFE66D],
          [0xAA1303E0, 0x52800021, arm64.branch(0x108059C, 0x108068C)],
          ['mov x0, x19', 'mov w1, #1', 'b #0x108068c'],
          'EntitlementCheck$$Start: skip the Meta entitlement request, report success to its own handler'),
    Patch('pc-login', 0xB895CC, [0xF81E0FF3, 0xA9017BFD], [0x52800000, RET],
          ['mov w0, #0', 'ret'],
          'BIG_APP_LOADER.get_RequiresOculusPlatform -> false: Bigscreen account login (PC flow)'),
    Patch('clipboard-home', 0xBC85C4, [0xA9BE4FF4, 0xA9017BFD], [RET, NOP], ['ret ', 'nop '],
          'HomePage$$GetRoomCodeFromClipboard: no clipboard service on Lepton (crash)'),
    Patch('clipboard-join', 0xE3F778, [0xF81D0FF5, 0xA9014FF4], [RET, NOP], ['ret ', 'nop '],
          'JoinRoom$$GetRoomCodeFromClipboard'),
    Patch('clipboard-social', 0xD20078, [0xF81D0FF5, 0xA9014FF4], [RET, NOP], ['ret ', 'nop '],
          'SocialPage$$GetRoomCodeFromClipboard'),
    Patch('texture-wrapmode', 0x1E25EFC, [0xF81D0FF5, 0xA9014FF4, 0xA9027BFD, 0x910083FD], [RET, NOP, NOP, NOP],
          ['ret ', 'nop ', 'nop ', 'nop '], 'Texture.set_wrapMode -> no-op (GL_INVALID_VALUE on external textures)'),
    Patch('texture-filtermode', 0x1E25F8C, [0xF81D0FF5, 0xA9014FF4, 0xA9027BFD, 0x910083FD], [RET, NOP, NOP, NOP],
          ['ret ', 'nop ', 'nop ', 'nop '], 'Texture.set_filterMode -> no-op'),
    Patch('presentation-in-scene', 0xBFD1F0, [0x2A0103F4], [0x2A1F03F4], ['mov w20, wzr'],
          'PresentationOverlayController$$ToggleOverlay(enable) -> always false: room screens drawn in-scene'),
    Patch('browser-in-scene', 0x10372D8, [0x39443268], [arm64.branch(0x10372D8, 0x103736C)], ['b #0x103736c'],
          'BigUIBrowser$$UpdateActiveStates: always TextureTarget.Regular (in-scene browser screen)'),
    Patch('no-protected-overlay', 0xB98FD0, [0x39034268], [0x3903427F], ['strb wzr, [x19, #0xd0]'],
          'BigContentController$$.ctor: isProtectedExternalEnabled = false (TV channels on the regular screen)'),
    Patch('media-in-scene', UPDATE_REGULAR_OVERLAY,
          [0xD10183FF, 0xA90167FA, 0xA9025FF8, 0xA90357F6, 0xA9044FF4, 0xA9057BFD, 0x910143FD, 0xF000E537,
           0x3965BAE8, 0x2A0303F6, 0xAA0203F5, 0xAA0103F3, 0xAA0003F4, 0x37000548, 0x9000D420, 0xF9468C00,
           0x97D8618E, 0xF000D420, 0xF945A800, 0x97D8618B, 0xF000D400, 0xF9459000, 0x97D86188, 0xB000D520,
           0xF941C800, 0x97D86185, 0x9000D440],
          _screen_stub(UPDATE_REGULAR_OVERLAY),
          ['stp x29, x30, [sp, #-0x30]!', 'mov x29, sp', 'stp x19, x20, [sp, #0x10]', 'stp x21, x22, [sp, #0x20]',
           'mov x19, x0', 'mov x20, x1', 'mov x21, x2', 'mov w22, w3', 'ldr x0, [x19, #0x28]',
           'cbz x0, #0x149fd50', 'mov w1, wzr', 'mov x2, xzr', 'bl #0x18639ec', 'ldr x0, [x19, #0x20]',
           'cbz x0, #0x149fd64', 'mov w1, #1', 'mov x2, xzr', 'bl #0x186e980', 'mov x0, x19', 'mov x1, x20',
           'mov x2, x21', 'mov w3, w22', 'mov x4, xzr', 'ldp x21, x22, [sp, #0x20]', 'ldp x19, x20, [sp, #0x10]',
           'ldp x29, x30, [sp], #0x30', 'b #0x14a0584'],
          'BigMediaRenderer$$UpdateRegularOverlay -> stub: no OVROverlay quad, in-scene screen instead'),
    Patch('subtitle-cave', SUBTITLE_CAVE,
          [0xF943E000, 0x97D86182, 0xF000D380, 0xF946B000, 0x97D8617F, 0xB000D4E0],
          _subtitle_cave(SUBTITLE_CAVE),
          ['fcvt s0, d8', 'fmov *', 'fmov *', 'fcmp s0, s2', 'fcsel s0, s0, s1, gt', 'b #0xdb9740'],
          'subtitle fallback duration (lives in the dead tail of UpdateRegularOverlay)'),
    Patch('subtitle-duration', SUBTITLE_TIMER_FCVT, [0x1E624100], [arm64.branch(SUBTITLE_TIMER_FCVT, SUBTITLE_CAVE)],
          ['b #0x149fd88'],
          '<SubtitleTimer>d__103$$MoveNext: CEA-608 cues have no duration -> keep them 4 s'),
    Patch('ssdp-cave', SSDP_CAVE,
          [0xF9462C00, 0x97D8617C, 0xD000D420, 0xF947C000, 0x97D86179, 0xD000D4C0, 0xF9427C00, 0x97D86176,
           0x9000D3A0, 0xF9431400, 0x97D86173, 0xD000D3A0, 0xF942A800, 0x97D86170, 0x9000D360, 0xF941C400],
          _ssdp_cave(SSDP_CAVE),
          ['cmp w8, #9', 'b.eq #0x149fdac', 'b #0xe90348', 'ldr x8, [x19, #0x90]', 'cbz x8, #0x149fdb8',
           'b #0xe90370', 'str x19, [x19, #0x90]', 'mov w0, #0x5dc', 'mov x1, xzr', 'bl #0x1a8e9b4',
           'ldr x0, [x20, #0x30]', 'cbz x0, #0x149fddc', 'mov x1, x19', 'mov x2, xzr', 'bl #0x1015000',
           'b #0xe90348'],
          'DLNA discovery: second SSDP search after 1.5 s (lives in the dead tail of UpdateRegularOverlay)'),
    Patch('ssdp-resend', SSDP_SEND_DONE, [0x7100251F], [arm64.branch(SSDP_SEND_DONE, SSDP_CAVE)], ['b #0x149fda0'],
          'DLNAService$$OnSocketSendEventCompleted: Lepton drops replies to the first search -> search twice'),
]


def _asm_matches(got: list[str], exp: list[str]) -> bool:
    # fmov immediates are printed locale-dependently by capstone ("#4,00000000"): match by mnemonic only.
    return len(got) == len(exp) and all(
        g.rstrip() == e.rstrip() or (e.endswith('*') and g.split(' ')[0] == e.split(' ')[0]) for g, e in zip(got, exp))


def check_original(lib) -> list[str]:
    """Return a list of problems (empty = this is the supported, unpatched build)."""
    problems = []
    for p in PATCHES:
        if arm64.words(lib, p.addr, len(p.original)) != p.original:
            problems.append(f'{p.key} @0x{p.addr:x}')
    for addr, prologue, label in CALLEE_PROLOGUES:
        if prologue is not None and arm64.words(lib, addr, len(prologue)) != prologue:
            problems.append(f'{label} @0x{addr:x}')
    return problems


def check_patched(lib) -> list[str]:
    problems = []
    for p in PATCHES:
        if arm64.words(lib, p.addr, len(p.new)) != p.new:
            problems.append(f'{p.key}: words')
        elif p.asm and not _asm_matches(arm64.disasm(lib, p.addr, len(p.new)), p.asm):
            problems.append(f'{p.key}: disassembly {arm64.disasm(lib, p.addr, len(p.new))}')
    return problems


def apply(lib: bytearray) -> None:
    problems = check_original(lib)
    if problems:
        raise SystemExit('ABORT: libil2cpp.so is not the supported unpatched build; mismatching sites: '
                         + ', '.join(problems))
    for p in PATCHES:
        if len(p.new) > len(p.original):
            raise SystemExit(f'ABORT: {p.key} writes more words than it fingerprints')
        arm64.put(lib, p.addr, p.new)
    problems = check_patched(lib)
    if problems:
        raise SystemExit('ABORT: patched code does not verify: ' + '; '.join(problems))
