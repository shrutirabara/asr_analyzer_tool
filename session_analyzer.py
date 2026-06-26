"""
UniMRCP ASR Log Analyzer
========================

Single-script tool for parsing UniMRCP server logs and inspecting the
RECOGNIZE lifecycle for a given AEP session ID.

Two modes:
  1. Streamlit UI (default):    streamlit run session_analyzer.py
  2. CLI / headless:            python session_analyzer.py <AEP_SESSION_ID> [LOGS_DIR]

For each request the tool extracts:
  - SET-PARAMS MRCP block (raw)
  - RECOGNITION-COMPLETE MRCP block
  - Azure speech.phrase WS JSON response
  - Completion-Cause (used for labels and summary)
"""

import json
import os
import re
import sys

# ---------------------------------------------------------------------------
# Parsing core (UI-independent — also used by CLI mode)
# ---------------------------------------------------------------------------

CHANNEL_RE = re.compile(r'Channel-Identifier:\s*([^@\s]+)@speechrecog')
TIMESTAMP_RE = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}:\d+)')


def find_set_params_block(lines, logging_tag_line):
    """Find start/end line indices of the SET-PARAMS MRCP block surrounding
    the given logging-tag line."""
    start = logging_tag_line
    for j in range(logging_tag_line, max(logging_tag_line - 15, 0), -1):
        if "Receive MRCPv2 Data" in lines[j] or (
            "MRCP/2.0" in lines[j] and "SET-PARAMS" in lines[j]
        ):
            start = j
            break

    end = logging_tag_line
    found_blank = False
    for j in range(logging_tag_line + 1, min(logging_tag_line + 20, len(lines))):
        if lines[j].strip() == '':
            found_blank = True
        elif found_blank and TIMESTAMP_RE.match(lines[j]):
            end = j
            break
    else:
        end = min(logging_tag_line + 15, len(lines))
    return start, end


def get_identifier(lines, start, end):
    """Extract Channel-Identifier from a block of lines."""
    for j in range(start, end):
        m = CHANNEL_RE.search(lines[j])
        if m:
            return m.group(1)
    return None


def find_recognition_complete_block(lines, search_start, search_end, identifier):
    """Return the RECOGNITION-COMPLETE MRCP block as a list of stripped lines,
    or None if not found for this channel."""
    for idx in range(search_start, search_end):
        if "RECOGNITION-COMPLETE" in lines[idx] and "MRCP/2.0" in lines[idx]:
            if (
                idx + 1 < len(lines)
                and f"Channel-Identifier: {identifier}@speechrecog" in lines[idx + 1]
            ):
                block = [lines[idx].strip()]
                for k in range(idx + 1, min(idx + 30, len(lines))):
                    if TIMESTAMP_RE.match(lines[k]):
                        break
                    block.append(lines[k].strip())
                return block
    return None


def find_azure_ws_json(lines, search_start, search_end, identifier):
    """Locate the Azure speech.phrase WS JSON for this channel within the
    given range. Returns the JSON string (joined) or None."""
    for i in range(search_start, search_end):
        if "Received WS msg" in lines[i] and f"<{identifier}>" in lines[i]:
            for j in range(i + 1, min(i + 10, len(lines))):
                if "Path:speech.phrase" in lines[j]:
                    for k in range(j + 1, min(j + 5, len(lines))):
                        stripped = lines[k].strip()
                        if stripped.startswith('{'):
                            json_parts = []
                            brace_count = 0
                            for m in range(k, min(k + 10, len(lines))):
                                line = lines[m].strip()
                                if line:
                                    json_parts.append(line)
                                    brace_count += line.count('{') - line.count('}')
                                    if brace_count == 0:
                                        break
                            return ''.join(json_parts)
                    return None
    return None


def find_all_recognize_requests(lines, search_start, search_end, identifier):
    """Return all line indices where a RECOGNIZE request starts for this channel."""
    positions = []
    for i in range(search_start, search_end):
        if f"Process RECOGNIZE Request <{identifier}@speechrecog>" in lines[i]:
            positions.append(i)
    return positions


def extract_completion_cause(lines, search_start, search_end, channel_id):
    """Return the Completion-Cause string for this channel's RECOGNITION-COMPLETE,
    or None if not found."""
    for j in range(search_start, search_end):
        if "RECOGNITION-COMPLETE" in lines[j] and "MRCP/2.0" in lines[j]:
            if (
                j + 1 < len(lines)
                and f"{channel_id}@speechrecog" in lines[j + 1]
            ):
                if j + 2 < len(lines) and "Completion-Cause:" in lines[j + 2]:
                    return lines[j + 2].strip().split("Completion-Cause:")[1].strip()
                break
    return None


def parse_session(lines, aep_session_id):
    """Parse all RECOGNIZE requests for an AEP session in a single file's lines.
    Returns a list of dicts (one per RECOGNIZE request)."""
    tag_positions = [
        i for i, line in enumerate(lines) if f"logging-tag: {aep_session_id}" in line
    ]
    results = []

    for tag_idx, tag_line in enumerate(tag_positions):
        search_end = (
            tag_positions[tag_idx + 1]
            if tag_idx + 1 < len(tag_positions)
            else min(tag_line + 2000, len(lines))
        )
        block_start, block_end = find_set_params_block(lines, tag_line)
        identifier = get_identifier(lines, block_start, block_end)

        if not identifier:
            continue

        set_params_text = ''.join(lines[block_start:block_end]).strip()
        recognize_positions = find_all_recognize_requests(
            lines, tag_line, search_end, identifier
        )

        if not recognize_positions:
            results.append({
                'channel_id': identifier,
                'set_params_text': set_params_text,
                'recognition_complete': None,
                'azure_json': None,
                'completion_cause': extract_completion_cause(
                    lines, tag_line, search_end, identifier
                ),
            })
            continue

        for rec_idx, rec_line in enumerate(recognize_positions):
            rec_end = (
                recognize_positions[rec_idx + 1]
                if rec_idx + 1 < len(recognize_positions)
                else search_end
            )
            recog_block = find_recognition_complete_block(
                lines, rec_line, rec_end, identifier
            )
            json_data = (
                find_azure_ws_json(lines, rec_line, rec_end, identifier)
                if recog_block
                else None
            )
            results.append({
                'channel_id': identifier,
                'set_params_text': set_params_text,
                'recognition_complete': '\n'.join(recog_block) if recog_block else None,
                'azure_json': json_data,
                'completion_cause': extract_completion_cause(
                    lines, rec_line, rec_end, identifier
                ),
            })

    return results


def analyze_directory(logs_dir, aep_session_id):
    """Walk a logs directory and aggregate parse results across all .log files."""
    all_results = []
    log_files = sorted(f for f in os.listdir(logs_dir) if f.endswith('.log'))
    for filename in log_files:
        filepath = os.path.join(logs_dir, filename)
        with open(filepath, 'r', errors='replace') as f:
            lines = f.readlines()
        for r in parse_session(lines, aep_session_id):
            r['file'] = filename
            all_results.append(r)
    return all_results


# ---------------------------------------------------------------------------
# CLI mode
# ---------------------------------------------------------------------------

def run_cli(aep_session_id, logs_dir):
    """Headless mode — prints results to stdout."""
    if not os.path.isdir(logs_dir):
        print(f"Error: directory not found: {logs_dir}", file=sys.stderr)
        sys.exit(1)

    results = analyze_directory(logs_dir, aep_session_id)
    if not results:
        print(f"No requests found for session: {aep_session_id}")
        return

    for i, req in enumerate(results, start=1):
        print(f"\n{'=' * 60}")
        print(f"REQUEST {i} | file: {req['file']} | channel: {req['channel_id']}")
        print(f"Outcome: {req['completion_cause'] or 'stopped/no-completion'}")
        print('=' * 60)

        print("\n--- SET-PARAMS ---")
        print(req['set_params_text'])

        if req['recognition_complete']:
            print("\n--- RECOGNITION-COMPLETE ---")
            print(req['recognition_complete'])

        if req['azure_json']:
            print("\n--- Azure WS Response ---")
            print(req['azure_json'])

    # Summary
    print(f"\n{'=' * 60}")
    print(f"TOTAL: {len(results)} request(s)")
    outcomes = {}
    for req in results:
        cause = req['completion_cause'] or 'stopped/no-completion'
        outcomes[cause] = outcomes.get(cause, 0) + 1
    for cause, count in outcomes.items():
        print(f"  {cause}: {count}")


# ---------------------------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------------------------

def run_streamlit():
    import streamlit as st

    st.set_page_config(
        page_title="UniMRCP ASR Log Analyzer",
        layout="wide",
    )

    # Hide Streamlit's top-right toolbar (three-dot menu, Deploy, etc.)
    # and the "Press Enter to apply" hint shown under text inputs.
    st.markdown(
        """
        <style>
            [data-testid="stToolbar"] { display: none !important; }
            #MainMenu { visibility: hidden; }
            footer { visibility: hidden; }
            [data-testid="InputInstructions"] { display: none !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # --- Sidebar: inputs ---
    with st.sidebar:
        st.header("Search")
        logs_dir = st.text_input("Logs directory", value=os.path.join(os.path.expanduser("~"), "Unimrcp_Server_Logs", "unimrcp-asr", "prod"))
        session_id = st.text_input("AEP Session ID", placeholder="")
        analyze = st.button("Analyze", type="primary", use_container_width=True)

    # --- Main area ---
    st.title("UniMRCP ASR Log Analyzer")

    if not analyze:
        return

    if not session_id:
        st.warning("Please enter an AEP Session ID.")
        return

    if not os.path.isdir(logs_dir):
        st.error(f"Directory not found: `{logs_dir}`")
        return

    with st.spinner(f"Scanning `{logs_dir}` for session `{session_id}`..."):
        results = analyze_directory(logs_dir, session_id)

    if not results:
        st.warning(f"No requests found for session: `{session_id}`")
        return

    # --- Top summary metrics ---
    total = len(results)
    outcomes = {}
    for req in results:
        cause = req['completion_cause'] or 'stopped/no-completion'
        outcomes[cause] = outcomes.get(cause, 0) + 1

    cols = st.columns(min(len(outcomes) + 1, 5))
    cols[0].metric("Total Requests", total)
    for i, (cause, count) in enumerate(outcomes.items(), start=1):
        if i < len(cols):
            cols[i].metric(cause, count)

    if len(outcomes) + 1 > 5:
        st.table([{"Outcome": k, "Count": v} for k, v in outcomes.items()])

    # --- Per-request details ---
    st.subheader("Requests")
    for i, req in enumerate(results, start=1):
        outcome = req['completion_cause'] or 'stopped/no-completion'
        label = f"{i}  •  {req['channel_id']}  •  {outcome}  •  {req['file']}"

        with st.expander(label, expanded=(i == 1)):
            st.markdown("**SET-PARAMS**")
            st.code(req['set_params_text'], language="text")

            if req['recognition_complete']:
                st.markdown("**RECOGNITION-COMPLETE**")
                st.code(req['recognition_complete'], language="text")

            if req['azure_json']:
                st.markdown("**Azure WS Response**")
                try:
                    st.json(json.loads(req['azure_json']))
                except json.JSONDecodeError:
                    st.code(req['azure_json'], language="json")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _is_running_under_streamlit():
    """Detect whether the script is being executed via `streamlit run`."""
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        return get_script_run_ctx() is not None
    except Exception:
        return False


if __name__ == '__main__':
    if _is_running_under_streamlit():
        run_streamlit()
    elif len(sys.argv) >= 2:
        aep_session_id = sys.argv[1]
        logs_dir = sys.argv[2] if len(sys.argv) >= 3 else os.path.join(os.path.expanduser("~"), "Unimrcp_Server_Logs", "unimrcp-asr", "prod")
        run_cli(aep_session_id, logs_dir)
    else:
        print(__doc__)
        print("Usage:")
        print("  Streamlit UI:  streamlit run session_analyzer.py")
        print("  CLI:           python session_analyzer.py <AEP_SESSION_ID> [LOGS_DIR]")
        sys.exit(1)
else:
    if _is_running_under_streamlit():
        run_streamlit()
