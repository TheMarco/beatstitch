"""User-facing timing states. Detection results and control availability are separate."""
from dataclasses import dataclass

from studio_timing import VERSION, source_matches


@dataclass(frozen=True)
class TimingGuidance:
    title: str
    detail: str
    explanation: str
    available: bool = False


def timing_guidance(timing, enabled=False, busy=False, *, track=None):
    state = timing.get("state") if timing.get("version") == VERSION else None
    if state == 'authored':
        # Authored demo timing is provenance, not a successful analyzer result.
        # It must still describe this exact source and the currently applied BPM.
        from studio_cache import source_signature
        try:
            valid = track is not None and timing.get('bpm') == track.bpm and timing.get('source') == source_signature(track.path)
        except OSError:
            valid = False
        state = 'authored' if valid else None
    elif state in ('drift', 'steady', 'uncertain') and track is not None and not source_matches(timing, track):
        state = 'stale'
    if state == 'stale':
        result = TimingGuidance(
            'Timing needs updating',
            'Saved analysis does not match this source.\nReconnect missing audio or Analyze again.',
            'The source audio changed, moved, is unavailable, or has no saved identity. '
            'The old timing result and BPM suggestion are not current. Reconnect missing audio first, '
            'then Analyze timing again. You can turn an existing correction off; '
            'the app will not render a correction map from a different source.')
    elif state == "drift" and timing.get("map"):
        coverage = min(1., max(0., timing.get("coverage", 0.)))
        result = TimingGuidance(
            "Correction available",
            f"{'On' if enabled else 'Off'} · covers {coverage:.0%} of the song.\n"
            + ("Other passages keep fixed timing." if enabled else "Compare A/B, then enable if it helps."),
            "Reliable tempo drift was detected. Stabilize gently evens out beat timing in the supported passages. "
            "Coverage is the portion of the source song with correction, not a confidence score. "
            "Other passages keep fixed timing. Listen before exporting; detection does not guarantee a perfect beat grid.",
            True)
    elif state == 'authored':
        result = TimingGuidance(
            'Steady demo tempo',
            'Authored on a regular beat grid.\nNo drift correction needed.',
            'This original demo was synthesized at a fixed tempo. Its BPM and regular grid come from '
            'the composition, not an analyzer result. There is no drift-correction map to enable. '
            'Project BPM matching still changes its speed normally.')
    elif state == "steady":
        result = TimingGuidance(
            "No significant drift detected",
            "Measured passages only.\nListen to check the rest.",
            "The analyzer found no significant drift in the passages it could measure reliably. "
            "This does not establish that the entire song is steady. There is no correction map to enable. "
            "Project BPM matching still works. Check the BPM and listen to transitions; Analyze may reuse the same result.")
    elif state in ("uncertain", "drift"):
        result = TimingGuidance(
            "Timing inconclusive",
            "Reliable correction unavailable.\nCheck BPM; listen by ear.",
            "The analyzer could not build a reliable correction map. Beats may be sparse or conflicting, "
            "or drift may be present but too uncertain to correct. This does not mean the song is steady. "
            "Check the source BPM, audition the transitions, and consider shorter blends. "
            "Repeating Analyze with the same inputs may reuse the same result.")
    else:
        result = TimingGuidance(
            "Timing not analyzed",
            "Click Analyze to check for drift.\nYour BPM and cues will stay as set.",
            "Analyze timing before enabling correction. "
            + ("The saved analysis needs to be updated. " if timing else "")
            + "Analysis looks for reliable rhythm passages across the song. It does not change your source BPM, "
            "cue points, or original audio. Apply a suggested BPM separately if you want it.")
    if busy:
        return TimingGuidance(
            "Processing in progress", "Timing controls are paused.\nWait for processing to finish.",
            "Timing controls are temporarily unavailable during processing. The status bar shows the active work. "
            f"Previous result: {result.title}. {result.explanation}")
    return result
