# MRCP ASR Health Analysis

A **vendor-neutral** method for measuring how well speech recognition is working
in an MRCP-based IVR, straight from the MRCP server logs — before deciding what
to improve.

It rests on one fact of the protocol: every MRCPv2 recognizer emits a
`RECOGNITION-COMPLETE` event carrying a `Completion-Cause` (RFC 6787). Count those
events, bucket them by cause, and you have a picture of recognition health that
holds on any MRCP stack — whatever MRCP server or speech engine you run.

**Reference implementation (this repo):** MRCP client = an Avaya IVR, MRCP server
= UniMRCP, speech engine = Azure Speech. Those products are named here once; the
rest of the doc speaks in MRCP roles.

**What it's for & how to read it.** I built this to baseline the recognition
health of one MRCP IVR before deciding what to improve — and to be reusable on any
MRCP stack (the method is §2 and §5). The script is a *reference implementation*,
not a turnkey tool: reusing it elsewhere means re-implementing the two adapters in
§5, not just pointing it at new logs.

---

## 1. The setup

Several layers sit between the caller and a recognition outcome. Only the MRCP
server's logs are analyzed.

```text
  Caller
    |  speech (and DTMF keypad)
    v
  IVR platform (MRCP client)  --keypad-->  DTMF handled by the platform
    |
    |  speech audio (MRCPv2)
    v
  MRCP server  <--- audio out / result + confidence back --->  speech engine
    |
    |  writes: RECOGNITION-COMPLETE + Completion-Cause
    v
  MRCP server logs
    |
    v
  recognition_metrics.py   (reads the logs, reports health)
```

- **IVR platform (MRCP client)** — runs prompts, captures input (speech and DTMF),
  and issues `RECOGNIZE` requests.
- **MRCP server** — speaks MRCPv2, streams the caller's *speech* audio to the
  engine, and writes the logs analyzed here.
- **Speech engine** — the recognizer; returns the transcript and, if supported, a
  self-reported confidence.
- **`recognition_metrics.py`** — reads the logs and reports the health metrics.

Key consequence: **where the IVR platform collects DTMF outside MRCP (as in this
deployment), the recognizer never sees keypad input.** Some prompts open DTMF and
speech at once, so a keypad answer leaves the parallel speech request running with
no audio — surfacing as a no-input turn (see §4).

---

## 2. How the analysis works

The unit is the **recognition turn**: one `RECOGNIZE` → `RECOGNITION-COMPLETE`
cycle — one moment the platform listened and got an outcome. A call has many turns.

Count one outcome per `RECOGNITION-COMPLETE` event, reading the `Completion-Cause`
from that event's own header, and scope to the recognizer resource to ignore
synthesizer/recorder events. (RFC 6787 names that resource `speechrecog`; servers
or profiles may label it differently — e.g., `azuresr` in this deployment's logs.)

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

**Confidence (secondary).** The speech engine's self-reported top-1 confidence on
successful turns — a sanity check on how solid successes look, read via the median
and distribution. It is **not** verified accuracy: there is no ground-truth
transcript in the logs to confirm it. (Here, this is Azure's `NBest` confidence.)

---

## 4. What the logs *cannot* tell you

State these limits wherever results are shown.

**Universal (any MRCP stack):**
1. **Silence vs missed speech.** A no-input cannot, from logs alone, be told apart
   as genuine silence vs speech the recognizer missed. Only saved audio can.
2. **Transcript correctness.** Logs give the *outcome*, not whether the transcript
   was *right*. Confidence is self-reported.

**Specific to this deployment (DTMF collected outside MRCP):**
3. **DTMF folded into no-input.** Where the IVR platform handles DTMF outside MRCP,
   a keypad answer on a dual-input prompt leaves the parallel speech request with
   no audio, so it ends as no-input. So no-input = DTMF + genuine silence +
   VAD-missed speech, and a high no-input rate is not automatically a defect. On a
   stack that recognizes DTMF *within* MRCP (`dtmfrecog`), DTMF appears as its own
   turns instead.

---

## 5. Use it on your own stack

The *method* is portable; the script is a reference implementation. Keep the core
and replace the two adapters — expect to re-implement them, not just re-point the
tool.

**Core (unchanged):** turn = the `RECOGNITION-COMPLETE` event; outcome = its
`Completion-Cause`; scope = the recognizer resource name.

**Server adapter:** your MRCP server's log-line format and its audio-accounting
line (UniMRCP example: `Input Complete ... size=<bytes>, dur=<ms>`).

**Engine adapter:** where the transcript and confidence appear (Azure example:
NLSML result + per-result confidence).

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
