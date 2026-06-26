# MRCP ASR Health Analysis

A **vendor-neutral** method for measuring how well speech recognition is working
in an MRCP-based IVR, straight from the MRCP server logs — before deciding what
to improve.

The parser reads the MRCP server logs and buckets every `RECOGNITION-COMPLETE`
event by its `Completion-Cause` (RFC 6787), showing where recognition is
succeeding, timing out, or failing. Because that signal is part of the protocol
rather than any one product, the same analysis carries to any MRCP stack — this
repo implements it for UniMRCP + Azure.

**What it's for.** I built this to baseline the speech-recognition health of one
UniMRCP → Azure IVR (over MRCP, on an Avaya platform) before deciding what to
improve. Beyond that baseline, it's meant to be reused: the *method* (§2, §5)
applies to any MRCP deployment, whatever server or engine you run.

**Method vs. implementation.** Sections 2 and 5 are the portable method; sections
1 and 3 are the worked example for the stack I built it for (Avaya → UniMRCP →
Azure), and §4 flags which limits are universal vs specific to it. The script
itself is a *reference implementation*, not a turnkey tool — it is wired to
UniMRCP log formats and Azure results, so reusing it on another stack means
re-implementing the two adapters in §5, not just pointing it at new logs.

---

## 1. The setup

```text
  Caller
    |  speech (and DTMF keypad)
    v
  Avaya IVR  --keypad-->  DTMF handled by Avaya
    |
    |  speech audio (MRCPv2)
    v
  UniMRCP Server  <--- audio stream out / result + confidence back --->  Azure Speech
    |
    |  writes: RECOGNITION-COMPLETE + Completion-Cause
    v
  UniMRCP Logs
    |
    v
  recognition_metrics.py   (reads the logs, reports health)
```

- **Avaya IVR** — telephony platform. Runs prompts and captures input (speech and
  DTMF).
- **UniMRCP server** — speaks MRCPv2, streams the caller's *speech* audio to the
  engine, and writes the logs we analyze.
- **Azure Speech** — the recognition engine; returns the transcript (NLSML) and a
  self-reported confidence.
- **`recognition_metrics.py`** — reads the logs and reports the health metrics.

The key consequence of this layout: **DTMF (keypad) is handled by Avaya, not sent
over MRCP. Some prompts open DTMF and speech at once, so a keypad answer leaves
the parallel speech request running with no audio** — surfacing as a no-input
turn (see §4).

---

## 2. How the analysis works

The unit is the **recognition turn**: one `RECOGNIZE` → `RECOGNITION-COMPLETE`
cycle — one moment the platform listened and got an outcome. A call has many
turns.

Count one outcome per `RECOGNITION-COMPLETE` event, reading the
`Completion-Cause` from that event's own header, and scope to the recognizer
resource to ignore synthesizer/recorder events. (RFC 6787 names that resource
`speechrecog`; some servers or profiles label it differently — e.g., `azuresr`
in our logs.)

> **The one pitfall.** Do *not* count every line containing `Completion-Cause:`.
> That header also rides on `STOP` and other `200 COMPLETE` responses, so raw
> counting inflates success and dilutes no-input — off by 2–3×. One cause per
> `RECOGNITION-COMPLETE` event.

---

## 3. The metrics

**Outcome distribution (primary).** Every turn bucketed by its RFC 6787
`Completion-Cause`. This is the heartbeat. The causes seen in practice here:

| Code | Name | Meaning |
|------|------|---------|
| 000 | success | Matched |
| 001 | no-match | Audio processed, no acceptable match |
| 002 | no-input-timeout | No speech detected before the timer |
| 006 | recognizer-error | Engine failed mid-request |
| 008 | success-maxtime | Matched, but speech hit the max time |
| 015 | no-match-maxtime | Recognition timer expired with no match |

The bulk is 000 / 001 / 002; the rest are exceptions worth watching. (Full
000–016 set: RFC 6787 §9.4.11.)

**Confidence (secondary).** Azure's self-reported top-1 confidence on successful
turns — a sanity check on how solid successes look, read via the median and
distribution. It is **not** verified accuracy: there is no ground-truth
transcript in the logs to confirm it.

---

## 4. What the logs *cannot* tell you

State these limits wherever results are shown.

**Universal (any MRCP stack):**
1. **Silence vs missed speech.** A no-input cannot, from logs alone, be told apart
   as genuine silence vs speech the recognizer missed. Only saved audio can.
2. **Transcript correctness.** Logs give the *outcome*, not whether the transcript
   was *right*. Confidence is self-reported.

**Specific to this deployment (DTMF collected outside MRCP):**
3. **DTMF folded into no-input.** Where the platform handles DTMF outside MRCP (as
   Avaya does here), a keypad answer on a dual-input prompt leaves the parallel
   speech request with no audio, so it ends as no-input. So no-input = DTMF +
   genuine silence + VAD-missed speech, and a high no-input rate is not
   automatically a defect. On a stack that recognizes DTMF *within* MRCP
   (`dtmfrecog`), DTMF appears as its own turns instead.

---

## 5. Use it on your own stack

The *method* is portable; the script is a reference implementation. To reuse it,
keep the core and replace the two adapters — expect to re-implement them, not
just re-point the tool.

**Core (unchanged):** turn = the `RECOGNITION-COMPLETE` event; outcome = its
`Completion-Cause`; scope = the recognizer resource name.

**Server adapter:** your MRCP server's log-line format and its audio-accounting
line (UniMRCP: `Input Complete ... size=<bytes>, dur=<ms>`).

**Engine adapter:** where the transcript and confidence appear (here: Azure NLSML
result + per-result confidence).

**Checklist for a new stack:**
1. Find the recognizer's `RECOGNITION-COMPLETE` event → turn anchor.
2. Confirm the `Completion-Cause` lives in that event's header → outcome.
3. Identify the recognizer resource name → scope filter.
4. *(optional)* Find the audio size/duration line → "was audio sent?" signal.
5. *(optional)* Find the result/confidence marker → confidence signal.

**Run it:**
```
CLI:        python recognition_metrics.py [LOGS_DIR]
Dashboard:  streamlit run recognition_metrics.py
```

> Computed per period and trended, the same distribution surfaces regressions
> (rising no-input → telephony/VAD; rising no-match → grammar/accent drift). The
> current tool reports a point-in-time snapshot; trending is the next step.
