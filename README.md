# ASR Analyzer

ASR Analyzer is an open-source Python and Streamlit application for analyzing MRCP speech recognition logs from enterprise IVR systems.

The project automates analysis that is traditionally performed manually by parsing MRCP server logs and surfacing recognition health metrics, confidence distributions, grammar usage, and session-level insights through both a command-line interface and an interactive dashboard.

Although the reference implementation was built using **UniMRCP** and **Azure Speech Services**, the underlying methodology is designed to be adaptable to other MRCP-based speech recognition platforms.

---

## Why I Built This

While supporting production speech recognition systems, I found that diagnosing ASR issues often required manually tracing MRCP sessions and searching through thousands of lines of logs. ASR Analyzer automates that process by extracting recognition events and surfacing meaningful metrics, making troubleshooting faster and more consistent.

---

## Features

* Analyze MRCP recognition logs from the command line or a Streamlit dashboard
* Calculate recognition outcome distributions from `RECOGNITION-COMPLETE` events
* Visualize confidence score distributions for successful recognitions
* Separate protocol-independent analysis from vendor-specific parsing logic
* Built around the MRCPv2 recognition lifecycle defined in RFC 6787

---

## Architecture

```text
Caller
   │
   ▼
IVR Platform (MRCP Client)
   │
   ▼
MRCP Server
   │
   ▼
Speech Recognition Engine
   │
   ▼
MRCP Server Logs
   │
   ▼
ASR Analyzer
```

---

## Getting Started

```bash
# Command-line report
python recognition_metrics.py [LOGS_DIR]

# Interactive dashboard
streamlit run recognition_metrics.py
```

---

## Metrics Reported

### Recognition Outcome Distribution

Computed by analyzing each MRCP `RECOGNITION-COMPLETE` event and its corresponding `Completion-Cause` value.

Examples include:

* Success
* No Match
* No Input
* Recognizer Error

Each recognition event contributes exactly one outcome to avoid double-counting protocol messages.

### Confidence Distribution

For successful recognitions, the analyzer extracts the speech engine's reported confidence score and summarizes:

* Median confidence
* Confidence histogram
* Number of scored recognitions

Confidence values represent the speech engine's own assessment and are **not** measures of transcript correctness.

---

## Documentation

For a detailed explanation of the analysis methodology, protocol assumptions, portability, and implementation decisions, see **DESIGN.md**.

DESIGN.md explains:

* recognition turn modeling
* protocol assumptions
* MRCP event selection
* portability to other MRCP implementations
* limitations of log-only analysis
