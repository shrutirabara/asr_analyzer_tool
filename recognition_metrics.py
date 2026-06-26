"""
UniMRCP Recognition Metrics
============================

Aggregate recognition health across all sessions in UniMRCP server logs.

One recognition turn == one MRCP RECOGNITION-COMPLETE event (RFC 6787). Each
turn's outcome is read from that event's Completion-Cause.

Metrics:
  1. Outcome rates: success (000) / no-input (002) / no-match (001) / other
  2. Confidence distribution of successful transcripts (top-1 NBest)

Usage:
  CLI:        python recognition_metrics.py [LOGS_DIR]
  Streamlit:  streamlit run recognition_metrics.py
"""

import json
import os
import re
import sys
from collections import defaultdict

TIMESTAMP_RE = re.compile(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}:\d+')
DNIS_RE = re.compile(r'DNIS=([^"|]+)')
OFFSET_RE = re.compile(r'"Offset"\s*:\s*(\d+)')


def parse_log_file(filepath):
    """Parse a single log file and return per-channel recognition data."""
    with open(filepath, 'r', errors='replace') as f:
        lines = f.readlines()

    # Pass 1: map channel_id -> DNIS and params from SET-PARAMS blocks
    channel_info = {}  # channel_id -> {dnis, no_input_timeout, speech_complete_timeout, initial_silence_ms}
    # Pass 2: collect recognition results per channel

    i = 0
    while i < len(lines):
        line = lines[i]

        # Detect SET-PARAMS MRCP block with DNIS
        if 'SET-PARAMS' in line and 'MRCP/2.0' in line and 'Receive' not in line:
            channel_id = None
            dnis = None
            no_input_timeout = None
            speech_complete_timeout = None
            for j in range(i + 1, min(i + 25, len(lines))):
                if TIMESTAMP_RE.match(lines[j]):
                    break
                if 'Channel-Identifier:' in lines[j]:
                    m = re.search(r'Channel-Identifier:\s*([^@\s]+)@', lines[j])
                    if m:
                        channel_id = m.group(1)
                if 'DNIS=' in lines[j]:
                    m = DNIS_RE.search(lines[j])
                    if m:
                        dnis = m.group(1)
                if 'no-input-timeout:' in lines[j]:
                    m = re.search(r'no-input-timeout:\s*(\d+)', lines[j])
                    if m:
                        no_input_timeout = int(m.group(1))
                if 'speech-complete-timeout:' in lines[j]:
                    m = re.search(r'speech-complete-timeout:\s*(\d+)', lines[j])
                    if m:
                        speech_complete_timeout = int(m.group(1))
            if channel_id and dnis:
                channel_info[channel_id] = {
                    'dnis': dnis,
                    'no_input_timeout': no_input_timeout,
                    'speech_complete_timeout': speech_complete_timeout,
                }
        i += 1

    # Pass 2: find initialSilenceTimeoutMs per channel
    for i, line in enumerate(lines):
        if 'Set Parameter [azure.stt.initialSilenceTimeoutMs]' in line:
            m = re.search(r'\[(\d+)\].*<([^@>]+)@', line)
            if m:
                val, ch = int(m.group(1)), m.group(2)
                if ch in channel_info:
                    channel_info[ch]['initial_silence_ms'] = val

    # Pass 3: collect speech.phrase JSON per channel
    recognitions = []  # list of dicts
    for i, line in enumerate(lines):
        if 'Path:speech.phrase' in line:
            # Find the channel from the WS msg header above
            channel_id = None
            for j in range(i - 1, max(i - 5, 0), -1):
                if 'Received WS msg' in lines[j]:
                    m = re.search(r'<([^>]+)>', lines[j])
                    if m:
                        channel_id = m.group(1)
                    break
            # Find the JSON body below
            json_str = None
            for j in range(i + 1, min(i + 5, len(lines))):
                stripped = lines[j].strip()
                if stripped.startswith('{'):
                    json_str = stripped
                    break
            if channel_id and json_str and channel_id in channel_info:
                try:
                    data = json.loads(json_str)
                    recognitions.append({
                        'channel_id': channel_id,
                        'dnis': channel_info[channel_id]['dnis'],
                        'no_input_timeout': channel_info[channel_id].get('no_input_timeout'),
                        'speech_complete_timeout': channel_info[channel_id].get('speech_complete_timeout'),
                        'initial_silence_ms': channel_info[channel_id].get('initial_silence_ms'),
                        'nbest': data.get('NBest', []),
                        'offset': data.get('Offset'),
                        'duration': data.get('Duration'),
                        'status': data.get('RecognitionStatus'),
                    })
                except json.JSONDecodeError:
                    pass

    # Pass 4: collect no-input-timeout completions per channel
    no_inputs = []
    for i, line in enumerate(lines):
        if 'Completion-Cause: 002 no-input-timeout' in line:
            # Look backwards for Channel-Identifier
            for j in range(i - 1, max(i - 5, 0), -1):
                if 'Channel-Identifier:' in lines[j]:
                    m = re.search(r'Channel-Identifier:\s*([^@\s]+)@', lines[j])
                    if m and m.group(1) in channel_info:
                        no_inputs.append({
                            'channel_id': m.group(1),
                            'dnis': channel_info[m.group(1)]['dnis'],
                        })
                    break

    # Pass 4b: collect recognition completion causes — ONE per RECOGNITION-COMPLETE
    # event. We anchor on the actual MRCP "RECOGNITION-COMPLETE ... COMPLETE" event
    # header and read the Completion-Cause from that event's own header block. This
    # is important: the "Completion-Cause" line also rides along on STOP and other
    # MRCP "200 COMPLETE" responses for the same channel, so counting every
    # Completion-Cause line would multi-count successes and dilute no-input. One
    # RECOGNITION-COMPLETE event == one recognition turn (RFC 6787).
    #
    # Each turn's audio (size/dur of what was streamed to the recognizer) comes from
    # the "Input Complete" line. Those interleave across channels and can appear just
    # before or after the completion, so we pair each turn with the NEAREST (by line)
    # not-yet-consumed Input Complete for the SAME channel.
    completion_causes = []
    recog_complete_re = re.compile(r'MRCP/2\.0\s+\d+\s+RECOGNITION-COMPLETE\s+\d+\s+COMPLETE')
    cause_re = re.compile(r'Completion-Cause:\s*(\d+)\s+(\S+)')
    chan_res_re = re.compile(r'Channel-Identifier:\s*([^@\s]+)@(\S+)')
    input_re = re.compile(r'Input Complete .*?size=(\d+) bytes, dur=(\d+) ms.*?<([^@>]+)@')

    # Collect all Input Complete events per channel: [line_idx, size, dur, consumed]
    ic_by_channel = defaultdict(list)
    for i, line in enumerate(lines):
        im = input_re.search(line)
        if im:
            ic_by_channel[im.group(3)].append([i, int(im.group(1)), int(im.group(2)), False])

    for i, line in enumerate(lines):
        if not recog_complete_re.search(line):
            continue
        # This is a real recognition turn. Read its Channel-Identifier and
        # Completion-Cause from the event header block immediately below.
        channel_id, resource, code, name = None, None, None, None
        for j in range(i + 1, min(i + 8, len(lines))):
            if TIMESTAMP_RE.match(lines[j]):
                break  # left this event's header block
            if channel_id is None:
                idm = chan_res_re.search(lines[j])
                if idm:
                    channel_id, resource = idm.group(1), idm.group(2).strip()
            if code is None:
                cm = cause_re.search(lines[j])
                if cm:
                    code, name = cm.group(1), cm.group(2).strip()
        if resource != 'speechrecog' or code is None:
            continue
        # Pair with the nearest unconsumed Input Complete for this channel.
        size = dur = None
        best, best_dist = None, None
        for rec in ic_by_channel.get(channel_id, []):
            if rec[3]:
                continue
            dist = abs(rec[0] - i)
            if best_dist is None or dist < best_dist:
                best, best_dist = rec, dist
        if best is not None:
            best[3] = True
            size, dur = best[1], best[2]
        completion_causes.append({
            'channel_id': channel_id,
            'dnis': channel_info.get(channel_id, {}).get('dnis'),
            'code': code,
            'name': name,
            'audio_size': size,
            'audio_dur': dur,
        })

    # Pass 5: collect Input Complete durations per channel
    input_completes = []
    for i, line in enumerate(lines):
        if 'Input Complete' in line:
            m = re.search(r'dur=(\d+) ms.*<([^@>]+)@', line)
            if m:
                dur, ch = int(m.group(1)), m.group(2)
                if ch in channel_info:
                    input_completes.append({
                        'channel_id': ch,
                        'dnis': channel_info[ch]['dnis'],
                        'duration_ms': dur,
                        'speech_complete_timeout': channel_info[ch].get('speech_complete_timeout'),
                    })

    # Pass 6: speech.startDetected offset per channel
    start_detected = {}
    for i, line in enumerate(lines):
        if 'Path:speech.startDetected' in line:
            channel_id = None
            for j in range(i - 1, max(i - 5, 0), -1):
                if 'Received WS msg' in lines[j]:
                    m = re.search(r'<([^>]+)>', lines[j])
                    if m:
                        channel_id = m.group(1)
                    break
            for j in range(i + 1, min(i + 5, len(lines))):
                m = OFFSET_RE.search(lines[j])
                if m:
                    if channel_id and channel_id in channel_info:
                        start_detected[channel_id] = int(m.group(1)) / 10000  # convert to ms
                    break

    return {
        'channel_info': channel_info,
        'recognitions': recognitions,
        'no_inputs': no_inputs,
        'completion_causes': completion_causes,
        'input_completes': input_completes,
        'start_detected': start_detected,
    }


def analyze_directory(logs_dir):
    """Parse all log files and aggregate results."""
    all_recognitions = []
    all_no_inputs = []
    all_completion_causes = []
    all_input_completes = []
    all_start_detected = {}
    all_channel_info = {}

    for fname in sorted(os.listdir(logs_dir)):
        if not fname.endswith('.log'):
            continue
        filepath = os.path.join(logs_dir, fname)
        print(f"  Parsing {fname}...", file=sys.stderr)
        result = parse_log_file(filepath)
        all_recognitions.extend(result['recognitions'])
        all_no_inputs.extend(result['no_inputs'])
        all_completion_causes.extend(result['completion_causes'])
        all_input_completes.extend(result['input_completes'])
        all_start_detected.update(result['start_detected'])
        all_channel_info.update(result['channel_info'])

    return {
        'recognitions': all_recognitions,
        'no_inputs': all_no_inputs,
        'completion_causes': all_completion_causes,
        'input_completes': all_input_completes,
        'start_detected': all_start_detected,
        'channel_info': all_channel_info,
    }


# ---------------------------------------------------------------------------
# CLI output
# ---------------------------------------------------------------------------

def print_report(data):
    recognitions = data['recognitions']
    no_inputs = data['no_inputs']
    input_completes = data['input_completes']
    start_detected = data['start_detected']
    channel_info = data['channel_info']

    if not recognitions and not no_inputs:
        print("No recognition data found.")
        return

    # Group by DNIS
    by_dnis = defaultdict(lambda: {'recognitions': [], 'no_inputs': 0, 'total': 0})
    for rec in recognitions:
        by_dnis[rec['dnis']]['recognitions'].append(rec)
        by_dnis[rec['dnis']]['total'] += 1
    for ni in no_inputs:
        by_dnis[ni['dnis']]['no_inputs'] += 1
        by_dnis[ni['dnis']]['total'] += 1

    print(f"\n{'=' * 70}")
    print(f"RECOGNITION METRICS BY DNIS")
    print(f"{'=' * 70}")

    for dnis in sorted(by_dnis.keys(), key=lambda d: by_dnis[d]['total'], reverse=True):
        info = by_dnis[dnis]
        recs = info['recognitions']
        total = info['total']
        no_input_count = info['no_inputs']

        print(f"\n{'─' * 70}")
        print(f"DNIS: {dnis} | Total events: {total}")
        print(f"{'─' * 70}")

        # Metric 1: Confidence scores
        all_confidences = []
        for rec in recs:
            for nb in rec['nbest']:
                if nb.get('Confidence', 0) > 0:
                    all_confidences.append(nb['Confidence'])

        if all_confidences:
            avg_conf = sum(all_confidences) / len(all_confidences)
            min_conf = min(all_confidences)
            max_conf = max(all_confidences)
            print(f"\n  Confidence (N={len(all_confidences)} scores from {len(recs)} recognitions):")
            print(f"    Avg: {avg_conf:.4f}  Min: {min_conf:.4f}  Max: {max_conf:.4f}")
        else:
            print(f"\n  No confidence scores available.")

        # Metric 3: No-input rate
        print(f"\n  No-input-timeout: {no_input_count}/{total} ({no_input_count/total*100:.1f}%)")

        # Metric 5: Timing — speech start offset
        ch_ids = [rec['channel_id'] for rec in recs]
        start_offsets = [start_detected[ch] for ch in ch_ids if ch in start_detected]
        if start_offsets:
            avg_start = sum(start_offsets) / len(start_offsets)
            max_start = max(start_offsets)
            print(f"\n  Speech start offset (N={len(start_offsets)}):")
            print(f"    Avg: {avg_start:.0f} ms  Max: {max_start:.0f} ms")

    # Overall summary
    all_top1 = [rec['nbest'][0]['Confidence'] for rec in recognitions if rec['nbest']]
    total_events = sum(info['total'] for info in by_dnis.values())
    total_no_input = sum(info['no_inputs'] for info in by_dnis.values())
    print(f"\n{'=' * 70}")
    print(f"OVERALL: {total_events} events, {len(recognitions)} recognitions, {total_no_input} no-inputs ({total_no_input/total_events*100:.1f}%)")
    if all_top1:
        print(f"  Avg top-1 confidence: {sum(all_top1)/len(all_top1):.4f}")
    print(f"{'=' * 70}")


# ---------------------------------------------------------------------------
# Log locations
# ---------------------------------------------------------------------------

# Default per-environment log directories. Point these at your own log roots,
# or pass a directory on the command line (see __main__).
LOGS_PATHS = {
    'qa': os.path.join(os.path.expanduser("~"), "Unimrcp_Server_Logs", "unimrcp-asr", "qa"),
    'prod': os.path.join(os.path.expanduser("~"), "Unimrcp_Server_Logs", "unimrcp-asr", "prod"),
}


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def _is_running_under_streamlit():
    try:
        import streamlit as st
        return hasattr(st, 'runtime') and st.runtime.exists()
    except ImportError:
        return False


if _is_running_under_streamlit():
    import streamlit as st
    import pandas as pd

    st.set_page_config(page_title="UniMRCP ASR Recognition Metrics", layout="wide")
    st.title("UniMRCP ASR Recognition Metrics")

    env = st.sidebar.selectbox("Environment", ["qa", "prod"])
    logs_dir = LOGS_PATHS[env]

    if st.sidebar.button("Analyze"):
        if not os.path.isdir(logs_dir):
            st.error(f"Directory not found: {logs_dir}. Place log files there first.")
        else:
            with st.spinner("Parsing log files..."):
                st.session_state['metrics_data'] = analyze_directory(logs_dir)
                st.session_state['metrics_env'] = env

    if 'metrics_data' in st.session_state:
        data = st.session_state['metrics_data']
        recognitions = data['recognitions']
        no_inputs = data['no_inputs']

        if not recognitions and not no_inputs:
            st.warning("No recognition data found.")
        else:
            with st.container():
                cc_f = data.get('completion_causes', [])

                if not cc_f:
                    st.info("No recognition completion causes found.")
                else:
                    # --- Overall health summary band ---
                    # Aggregate recognition health across the current filter. Turns are
                    # counted from completion causes (one per recognition turn); confidence
                    # comes from the speech.phrase NBest top-1 of successful recognitions.
                    st.subheader("Overall Recognition Health")
                    total_turns = len(cc_f)
                    st.metric("Total turns", f"{total_turns:,}",
                              help="One per RECOGNITION-COMPLETE event (one prompt the recognizer ran, not one call)")

                    def _pct(x):
                        return f"{x/total_turns*100:.1f}%" if total_turns else "0.0%"

                    cause_counts = {}
                    for c in cc_f:
                        key = (c['code'], f"{c['code']} {c['name']}")
                        cause_counts[key] = cause_counts.get(key, 0) + 1
                    rows = [{"Outcome": label, "Turns": v, "%": _pct(v)}
                            for (code, label), v in sorted(cause_counts.items())]
                    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                    st.caption("Outcome of each recognition turn (MRCP RFC 6787 RECOGNITION-COMPLETE "
                               "causes), one row per cause.")

                    # --- Confidence distribution of successful transcripts ---
                    # Azure's self-reported top-1 confidence on successful turns. This is
                    # a sanity check on how solid successes look, NOT verified accuracy —
                    # a low score doesn't prove the transcript is wrong (no ground truth).
                    top1 = [r['nbest'][0]['Confidence'] for r in recognitions
                            if r['nbest'] and r['nbest'][0].get('Confidence') is not None]
                    if top1:
                        st.subheader("Confidence Distribution (successful turns)")
                        st.caption("Azure's self-reported top-1 confidence on successful turns. A sanity "
                                   "check on how solid successes look — not verified accuracy, since we "
                                   "have no ground-truth transcripts to compare against.")
                        n = len(top1)
                        srt = sorted(top1)
                        median_c = srt[n // 2]
                        m1, m2 = st.columns(2)
                        m1.metric("Scored turns", f"{n:,}",
                                  help="Successful turns that carried a confidence score")
                        m2.metric("Median confidence", f"{median_c:.3f}")

                        # Histogram across confidence bands
                        bands = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)]
                        labels = ["0.0–0.2", "0.2–0.4", "0.4–0.6", "0.6–0.8", "0.8–1.0"]
                        counts = [sum(1 for s in top1 if lo <= s < hi) for lo, hi in bands]
                        hist_df = pd.DataFrame({"Count": counts}, index=labels)
                        hist_df.index.name = "Confidence band"
                        st.bar_chart(hist_df)

else:
    # CLI mode
    logs_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.expanduser("~"), "Unimrcp_Server_Logs", "unimrcp-asr", "prod")
    if not os.path.isdir(logs_dir):
        print(f"Error: directory not found: {logs_dir}", file=sys.stderr)
        sys.exit(1)

    print("Scanning log files...", file=sys.stderr)
    data = analyze_directory(logs_dir)
    print_report(data)
