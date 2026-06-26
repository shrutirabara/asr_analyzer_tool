# UniMRCP ASR Recognition Metrics

`recognition_metrics.py` reads UniMRCP server logs and reports speech-recognition
health, as a CLI report or a Streamlit dashboard.

```
CLI:        python recognition_metrics.py [LOGS_DIR]
Dashboard:  streamlit run recognition_metrics.py
```

**Unit of analysis:** one recognition turn = one MRCP `RECOGNITION-COMPLETE`
event (RFC 6787), scoped to the recognizer resource.

## Metrics

### Recognition outcome distribution
- **From:** each `RECOGNITION-COMPLETE` event's `Completion-Cause` (RFC 6787).
- **Built by:** counting one cause per event — success (000) / no-input (002) /
  no-match (001) / other — as a count and % of all turns.
- **Why one-per-event:** the `Completion-Cause` header also rides on `STOP` and
  other `200 COMPLETE` responses; counting every line over-counts success.

### Confidence distribution (successful turns)
- **From:** the Azure `speech.phrase` result JSON — top-1 `NBest` `Confidence`.
- **Built by:** taking each successful turn's top-1 confidence; reported as
  scored-turn count, median, and a 0.0–1.0 histogram.
- **Note:** engine self-reported, not verified accuracy — there is no
  ground-truth transcript in the logs.

The method behind these metrics (vendor-neutral, adapter model) is in `DESIGN.md`.
`session_analyzer.py` traces a single session across turns.
