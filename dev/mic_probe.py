#!/usr/bin/env python3
"""Watch the Steam Frame's microphone path while Bigscreen runs, to find where it cuts out.

    mic_probe.py                      # run during a session; press Enter at every cut you hear, Ctrl-C to stop
    mic_probe.py --out work/mic1      # choose the output folder

Passive: nothing on the Frame is changed (default audio source, routing and settings stay as they are).
Several taps run at the same time and are put on one clock (PC time):

  source   `pw-record` of the Frame's default mic through SSH (FramePort's key) -> level per 20 ms,
           so a silence gap in what the OS offers Bigscreen shows up here
  pwtop    `pw-top` snapshots: xruns / errors per PipeWire node
  logcat   `adb logcat` lines from BigSoup, AudioFlinger, AudioRecord, audio policy, Unity pause/resume,
           MediaCodec / FramePort (decoder stalls)
  audio    `dumpsys audio` / `dumpsys media.audio_flinger` recording sections, saved whenever they change
           (a recording client that Android silences shows up as a change)
  net      `adb shell tcpdump` of the mic transport (the UDP flow BigSoup logs as "mic audio connection"):
           packets per second leaving/arriving and gaps in what leaves = what the other people in the room get
  marks    your Enter presses (optional with --duration)

It writes events.jsonl (everything, timestamped) and prints a summary: each mark with the events around it,
and every source gap. See docs/OPEN_ISSUES.md for what each layer would mean.
"""

import argparse
import array
import hashlib
import json
import math
import pathlib
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'frameport-decoder'))

import frame_ssh  # noqa: E402

RATE = 48000
FRAME_MS = 20
ACTIVE_DB = -45.0          # a 20 ms frame above this counts as sound (voice)
SILENT_DB = -60.0          # below this a 20 ms frame counts as silent
GAP_MS = 300               # a silent run at least this long is reported as a gap
LOGCAT_TAGS = ('BigSoup', 'AudioFlinger', 'AudioRecord', 'AudioPolicy', 'audio_hw', 'Unity',
               'FramePortVideo', 'MediaCodec', 'ACodec', 'OMXMaster')
RAW_NODE = 'alsa_input.platform-sound.HiFi__Mic__source'
AUDIO_DUMPS = ('dumpsys audio', 'dumpsys media.audio_flinger', 'dumpsys media.audio_policy')


class Log:
    def __init__(self, path: pathlib.Path):
        self.file = path.open('w')
        self.lock = threading.Lock()
        self.start = time.time()

    def add(self, kind: str, **data) -> dict:
        event = {'t': round(time.time() - self.start, 3), 'kind': kind, **data}
        with self.lock:
            self.file.write(json.dumps(event, ensure_ascii=False) + '\n')
            self.file.flush()
        return event


def ssh_command(target: str, key, remote: str) -> list[str]:
    return ['ssh', *frame_ssh.options(key), target, remote]


def watch_source(log: Log, target: str, key, stop: threading.Event, node: str | None = None, name: str = 'mic'):
    """Mic level per 20 ms; silent runs become `gap` events.

    node=None taps the default source (what Bigscreen reads, after the Frame's EQ, echo cancel, noise
    suppression and voice gate); a node name taps that node instead (e.g. the raw ALSA mic). Per second a
    `level` event says how many of the 50 frames are active (> ACTIVE_DB) and how many are digital zero."""
    pick = f'--target {node} ' if node else ''
    cmd = ssh_command(target, key, f'pw-record {pick}--rate {RATE} --channels 1 --format s16 --latency 20ms - ')
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    frame_bytes = RATE * FRAME_MS // 1000 * 2
    silent_since = None
    run_frames = run_zero = 0
    run_before = prev_db = -120.0
    last_report = 0.0
    frames = active = zeros = 0
    peak_db = -120.0
    while not stop.is_set():
        chunk = proc.stdout.read(frame_bytes)
        if len(chunk) < frame_bytes:
            break
        samples = array.array('h')
        samples.frombytes(chunk[:frame_bytes])
        rms = math.sqrt(sum(x * x for x in samples) / len(samples)) / 32768
        db = 20 * math.log10(rms) if rms > 0 else -120.0
        now = time.time() - log.start
        frames += 1
        active += db > ACTIVE_DB
        zeros += not any(samples)
        peak_db = max(peak_db, db)
        if db < SILENT_DB:
            if silent_since is None:
                silent_since, run_frames, run_zero, run_before = now, 0, 0, prev_db
            run_frames += 1
            run_zero += not any(samples)
        elif silent_since is not None:
            if (now - silent_since) * 1000 >= GAP_MS:
                # exact zeros mean a gate / mute upstream, not a quiet room (a real mic has a noise floor)
                log.add('gap', source=name, start=round(silent_since, 3), seconds=round(now - silent_since, 3),
                        digital_zero=f'{run_zero}/{run_frames}', level_before_db=round(run_before, 1),
                        level_after_db=round(db, 1))
            silent_since = None
        prev_db = db
        if now - last_report >= 1.0:
            log.add('level', source=name, db=round(peak_db, 1), frames=frames, active=active, zeros=zeros)
            last_report, frames, active, zeros, peak_db = now, 0, 0, 0, -120.0
    proc.terminate()


def watch_raw(log: Log, target: str, key, stop: threading.Event):
    watch_source(log, target, key, stop, RAW_NODE, 'raw')


def watch_pwtop(log: Log, target: str, key, stop: threading.Event):
    """pw-top in batch mode prints a table every second; keep the rows with errors or xruns."""
    cmd = ssh_command(target, key, 'pw-top -b')
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    header = None
    for line in proc.stdout:
        if stop.is_set():
            break
        cols = line.split()
        if not cols:
            continue
        if cols[0] == 'S':
            header = cols
            continue
        if header and len(cols) >= len(header):
            row = dict(zip(header, cols))
            try:
                errors = int(row.get('ERR', 0))
            except ValueError:
                continue
            if errors:
                log.add('pw_xrun', node=' '.join(cols[len(header) - 1:]), err=errors, row=line.strip())
    proc.terminate()


def watch_logcat(log: Log, stop: threading.Event):
    proc = subprocess.Popen(['adb', 'logcat', '-v', 'threadtime', '-T', '1'], stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, errors='replace')
    for line in proc.stdout:
        if stop.is_set():
            break
        if any(tag in line for tag in LOGCAT_TAGS) and ('Mic' in line or 'overflow' in line or 'underrun' in line.lower()
                                                           or 'paused' in line or 'resum' in line.lower()
                                                           or 'record' in line.lower() or 'silenc' in line.lower()
                                                           or 'blocked' in line or 'timed out' in line
                                                           or 'reclaim' in line or ' -110' in line
                                                           or 'Mediasoup' in line or 'Data connection' in line):
            log.add('logcat', line=line.strip()[:300])
    proc.terminate()


def watch_audio(log: Log, stop: threading.Event):
    """Save the recording-related parts of the audio dumps whenever they change."""
    seen = {}
    while not stop.is_set():
        for dump in AUDIO_DUMPS:
            result = subprocess.run(['adb', 'shell', dump], capture_output=True, text=True, errors='replace')
            lines = [l.rstrip() for l in result.stdout.splitlines()
                     if any(w in l.lower() for w in ('record', 'silenc', 'capture', 'input', 'xrun', 'overflow', 'underrun'))]
            lines = [l for l in lines if 'dump time' not in l.lower() and 'timestamp' not in l.lower()]
            digest = hashlib.sha1('\n'.join(lines).encode()).hexdigest()
            if seen.get(dump) != digest:
                seen[dump] = digest
                log.add('audio_dump', dump=dump, lines=lines[:80])
        stop.wait(1.0)


def current_mic_server() -> tuple[str, str] | None:
    """The mic transport of the newest BigSoup connection, from Bigscreen's log: (ip, port)."""
    out = subprocess.run(['adb', 'logcat', '-d', '-v', 'threadtime', '-s', 'BigSoup'], capture_output=True,
                         text=True, errors='replace').stdout
    found = None
    for line in out.splitlines():
        if 'Creating mic audio connection to Mediasoup server:' in line:
            address = line.rsplit(' ', 1)[-1].rstrip('.')
            ip, _, port = address.partition(':')
            found = (ip, port)
    return found


def watch_net(log: Log, stop: threading.Event):
    """Packets of the mic transport (adb shell tcpdump): per second out/in counts, and gaps in what leaves.

    Each Opus frame Bigscreen sends is one UDP packet. While you talk a stream of packets leaves about every
    20 ms; a gap in it is a cut that other people hear. (Silence may also be sent as small or no packets: compare
    with the `level` events.)"""
    server = None
    proc = None
    while not stop.is_set():
        current = current_mic_server()
        if current and current != server:
            if proc:
                proc.terminate()
            server = current
            log.add('net_target', ip=server[0], port=server[1])
            proc = subprocess.Popen(['adb', 'shell', f'tcpdump -i any -n -tt -l -s 60 udp and host {server[0]} and port {server[1]}'],
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, errors='replace')
            threading.Thread(target=_net_reader, args=(proc, log, server, stop), daemon=True).start()
        stop.wait(3.0)
    if proc:
        proc.terminate()


def _net_reader(proc, log: Log, server, stop: threading.Event):
    """Uses tcpdump's own timestamps (adb delivers its output in ~1 s batches, so arrival time is useless)."""
    AUDIO_MIN = 30   # keepalives are 13 bytes; Opus frames are bigger
    last_audio = None
    bucket = {'out': 0, 'out_audio': 0, 'out_bytes': 0, 'in': 0, 'max_audio_gap_ms': 0}
    bucket_second = None
    for line in proc.stdout:
        if stop.is_set():
            break
        parts = line.split()
        # 1793437439.181695 IP 192.0.2.10.41733 > 198.51.100.7.21880: UDP, length 76
        if len(parts) < 8 or parts[1] != 'IP':
            continue
        try:
            ts = float(parts[0])
        except ValueError:
            continue
        size = int(parts[-1]) if parts[-1].isdigit() else 0
        outgoing = not parts[2].startswith(server[0])
        if outgoing:
            bucket['out'] += 1
            if size >= AUDIO_MIN:
                if last_audio is not None:
                    gap = (ts - last_audio) * 1000
                    bucket['max_audio_gap_ms'] = max(bucket['max_audio_gap_ms'], gap)
                    if gap >= GAP_MS:
                        log.add('net_gap', seconds=round(gap / 1000, 3), frame_clock=round(ts, 3))
                last_audio = ts
                bucket['out_audio'] += 1
                bucket['out_bytes'] += size
        else:
            bucket['in'] += 1
        if bucket_second is None:
            bucket_second = int(ts)
        if int(ts) != bucket_second:
            bucket['max_audio_gap_ms'] = round(bucket['max_audio_gap_ms'])
            log.add('net', **bucket)
            bucket = {'out': 0, 'out_audio': 0, 'out_bytes': 0, 'in': 0, 'max_audio_gap_ms': 0}
            bucket_second = int(ts)


def watch_marks(log: Log, stop: threading.Event):
    print('Press Enter every time you hear / see the microphone cut out. Ctrl-C ends the capture.')
    for _ in sys.stdin:
        log.add('mark')
        print('  mark', time.strftime('%H:%M:%S'))
        if stop.is_set():
            break


def summarize(path: pathlib.Path):
    events = [json.loads(l) for l in path.read_text().splitlines()]
    marks = [e for e in events if e['kind'] == 'mark']
    gaps = [e for e in events if e['kind'] == 'gap']
    net_gaps = [e for e in events if e['kind'] == 'net_gap']
    interesting = [e for e in events if e['kind'] in ('logcat', 'pw_xrun', 'audio_dump', 'gap', 'net_gap')]
    print(f'\n{len(events)} events, {len(marks)} marks, {len(gaps)} mic-source gaps >= {GAP_MS} ms')
    for gap in gaps:
        print(f'  {gap.get("source", "mic")} gap {gap["start"]:.1f}s for {gap["seconds"]:.2f}s, digital-zero frames {gap["digital_zero"]}, '
              f'level {gap["level_before_db"]} -> {gap["level_after_db"]} dB')
    by = {}
    for e in events:
        if e['kind'] == 'level':
            by.setdefault(e.get('source', 'mic'), []).append(e)
    if 'raw' in by:
        raw_active = sum(e['active'] for e in by['raw'])
        proc_active = sum(e['active'] for e in by['mic'])
        both = sum(min(r['active'], 50) for r, m in zip(by['raw'], by['mic']) if m['active'] >= 0)
        print(f'\nraw mic active frames: {raw_active}, after the Frame\'s processing: {proc_active} '
              f'({100 * proc_active / max(raw_active, 1):.0f}% of the raw activity survives)')
        print('per second  raw/processed active frames (of 50):')
        print('  ' + ' '.join(f'{r["active"]}/{m["active"]}' for r, m in zip(by['raw'], by['mic'])))
    print(f'{len(net_gaps)} gaps >= {GAP_MS} ms in the packets leaving for the mic transport')
    for gap in net_gaps:
        print(f'  net gap at {gap["t"]:.1f}s of {gap["seconds"]:.2f}s')
    for mark in marks:
        t = mark['t']
        print(f'\nmark at {t:.1f}s: events within -10 s .. +3 s')
        near = [e for e in interesting if t - 10 <= e['t'] <= t + 3]
        for e in near[-25:]:
            if e['kind'] == 'audio_dump':
                print(f'  {e["t"]:7.1f}  audio_dump {e["dump"]}: {len(e["lines"])} lines changed')
            else:
                print(f'  {e["t"]:7.1f}  {e["kind"]}: {e.get("line") or e.get("row") or e}')
        if not near:
            print('  nothing logged: the cut is not visible in any tap')


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--out', type=pathlib.Path, default=pathlib.Path('work/mic_probe'))
    ap.add_argument('--frame', default=None, help='user@host of the Frame (default: from FramePort)')
    ap.add_argument('--key', type=pathlib.Path, default=None)
    ap.add_argument('--duration', type=float, default=None, help='stop after this many seconds (no keyboard needed)')
    ap.add_argument('--raw', action='store_true',
                    help='also tap the raw ALSA mic (before EQ / echo cancel / noise suppression) to compare')
    ap.add_argument('--no-source', action='store_true', help='skip the pw-record tap (it wakes the mic source)')
    args = ap.parse_args()
    target = args.frame or frame_ssh.default_target()
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f'events-{time.strftime("%Y%m%d-%H%M%S")}.jsonl'
    log = Log(path)
    stop = threading.Event()
    taps = [(watch_pwtop, (target, args.key)), (watch_logcat, ()), (watch_audio, ()), (watch_net, ())]
    if not args.no_source:
        taps.insert(0, (watch_source, (target, args.key)))
        if args.raw:
            taps.insert(1, (watch_raw, (target, args.key)))
    threads = [threading.Thread(target=fn, args=(log, *a, stop), daemon=True) for fn, a in taps]
    threads.append(threading.Thread(target=watch_marks, args=(log, stop), daemon=True))
    for t in threads:
        t.start()
    print(f'capturing to {path}')
    try:
        end = time.time() + args.duration if args.duration else None
        while end is None or time.time() < end:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    stop.set()
    time.sleep(0.5)
    log.file.flush()
    summarize(path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
