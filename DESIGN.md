# ASR Analyzer Design & Methodology

This document describes the methodology behind **ASR Analyzer**. While the README explains what the project does and how to use it, this document explains the protocol assumptions, metric definitions, and design decisions that make the analysis valid.

The implementation in this repository uses an Avaya IVR, UniMRCP, and Azure Speech Services as the reference stack, but the methodology is designed to be portable to any MRCP-based speech recognition platform.

---

## 1. Recognition Model

The fundamental unit of analysis is the **recognition turn**: one `RECOGNIZE` request followed by one `RECOGNITION-COMPLETE` event.

Each recognition turn contributes exactly one outcome to the analysis.

The outcome is determined exclusively from the `Completion-Cause` header contained within the `RECOGNITION-COMPLETE` event, as defined by RFC 6787.

> **Important:** Do not count every occurrence of `Completion-Cause`. The header also appears on `STOP` and other `200 COMPLETE` responses. Counting all occurrences will over-count successful recognitions and distort the overall distribution.

---

## 2. Metric Definitions

### Recognition Outcomes

Recognition health is measured by grouping each recognition turn according to its RFC 6787 completion cause.

The primary outcomes observed in the reference implementation are:

| Code | Meaning                 |
| ---- | ----------------------- |
| 000  | Success                 |
| 001  | No Match                |
| 002  | No Input Timeout        |
| 006  | Recognizer Error        |
| 008  | Success (Maximum Time)  |
| 015  | No Match (Maximum Time) |

The distribution of these outcomes provides an overall picture of recognition health.

### Confidence

Confidence is evaluated only for successful recognitions.

The implementation extracts the speech engine's reported top-confidence value (Azure `NBest` confidence in the reference implementation) and summarizes the distribution using aggregate statistics.

Confidence is **not** a measure of transcription accuracy. It represents only the recognizer's self-reported confidence.

---

## 3. Method Limitations

The analysis intentionally operates only on MRCP server logs.

As a result, several limitations apply regardless of implementation:

* A no-input event cannot distinguish true caller silence from speech that was not detected.
* Transcript correctness cannot be determined because no ground-truth transcript exists within the logs.
* Confidence values are engine-reported estimates rather than verified accuracy.

The reference deployment introduces one additional consideration:

* Because DTMF is processed outside MRCP, keypad input during dual-input prompts appears as no-input from the recognizer's perspective. Consequently, elevated no-input rates are not necessarily evidence of poor speech recognition performance.

---

## 4. Portability

The methodology is intentionally vendor-neutral.

To adapt the analyzer to another MRCP implementation, only the parsing logic must change. The underlying methodology remains the same:

* Identify each `RECOGNITION-COMPLETE` event.
* Read its associated `Completion-Cause`.
* Scope analysis to the recognizer resource.
* Map engine-specific confidence values into the common metric model.

Different MRCP servers and speech engines expose different log formats, but the recognition model itself remains unchanged.

---

## 5. Future Work

The current implementation analyzes recognition health over a single collection period.

Future enhancements include:

* Longitudinal trend analysis
* Grammar-level performance reporting
* Recognition latency metrics
* Comparative analysis across deployments
* Additional MRCP server and speech engine adapters
