# Detector-confidence pilot

This is a separate, native review tool for the 25 audio files supplied in
`/Users/marcovhv/Downloads/dataset`. It does not alter DJ Studio, your mix, source
files, pitch, tempo, or the installed detector settings. Its predictions are
development observations, not verified truth or accuracy percentages.

Double-click **Launch Detector Pilot.command** to open it after preparation.
The output folder is `outputs/dataset-pilot`. All analysis stays on this Mac.
The originals stay in Downloads. Generated listening WAVs and waveforms use
roughly 650 MB, plus a smaller detector cache; this avoids decoding again while
you review. Keep the original files in place so their identities can be checked.

## Start with an easy judgment

1. Enter a reviewer name or alias. A second reviewer should use a different alias
   and work independently before either person reveals detector predictions.
2. Select a **Beginning**, **Middle**, or **Near ending** passage and play it.
   It is ten seconds of the original song, at its original tempo and pitch.
3. Answer **Voices present?** with Yes, No, or Unclear. Count singing, speech and
   identifiable vocal samples anywhere in the passage. No does not mean the
   entire song has no voices.
4. Answer the other fields only if you can judge them. Leaving a field unreviewed
   is valid. Use Not qualified for key if you cannot independently identify it.
5. Save the reference, or Save & Next. Answers are saved in `references.json`
   separately from the model results. You can close and resume later.

There are 100 tasks: three short passages and one first-minute key task per
song. You do not need to complete every field or every task in one sitting.
A progress count means tasks with some human judgment; it does not mean every
beat, key and vocal observation has been verified.

## What each judgment means

- **Voices:** singing, speech or vocal samples are audible somewhere in the
  passage. Unclear is preferable to guessing about a synth resembling a voice.
- **Pitched material:** musical notes are audible, including melodic bass,
  chords, singing and tuned percussion. Low-pitched sound can still have notes.
- **Drums only:** only unpitched percussion is audible. Bass notes, singing,
  chords, tuned percussion, and lingering tonal effects make this No. It is
  valid for a silent or confusing passage to remain Unclear rather than Yes.
- **Key:** identify the key of this specific listening range. The first-minute
  task corresponds to the global source-key detector; the short tasks correspond
  to local tonal evidence. A changing or ambiguous passage should be Unclear.
  No clear key is not proof that the passage is drums only. Identifying a key
  does not establish that it will sound good blended with another song.
- **Timing:** mark the actual musical beats, including beat 1 of the bar when
  that is clear. The largest waveform peak can be an offbeat or a snare; waveform
  size alone does not identify the beat or the beginning of the bar.

## Beat references

Use the waveform's beat-marking controls to add markers on source-audio times,
adjust their positions, mark a selected beat as beat 1, and remove mistakes.
These reference markers start empty: a detector grid is never inserted for you.
Use normal waveform seeking to audition difficult points and replay the passage.

Choose Marked beats when you have added markers. Leave the completeness checkbox
off until you have checked and marked **every audible beat in the selected
passage**. Partial markers can establish selected positive references; they
cannot be used to count every unmarked detection as wrong or calculate recall.
Choose No clear beat/Unclear when appropriate. Different interpretations of
half-time rhythm or beat 1 should be documented in the notes and adjudicated,
not silently forced into the detector's interpretation.

Precise beat and key references need musically competent review. This tool makes
the process manageable; it does not turn a beginner's uncertain answers into
expert ground truth. A second independent reviewer and disagreement resolution
are needed before treating labels as a reliable reference collection.

## Revealing predictions

Detector suggestions start hidden. Save your own answer before revealing them.
Revealing a prediction records exposure for your alias for the **whole song**.
Later answers or edits for that song are marked as exposed; earlier saved
answers remain in the history. Switching to a fresh alias after seeing an answer
does not make you a new independent reviewer. The record only establishes what
this tool displayed, not whether you previously saw results elsewhere.

Raw model strengths, profile correlations and class scores are not accuracy
percentages. Several tools agreeing is not a substitute for an independent
reference. Differences between a source-key estimate and a short passage's key
do not automatically prove either one wrong.

## What happens next

We compare provisional labels, resolve genuine disagreements, and measure
specific detector failures. Any algorithm changes developed using these songs
must be evaluated on a separate, fresh collection. This entire pilot is for
development: it has no independent held-out split, established creator grouping,
confirmed AI-generated coverage or judgments of actual two-song blend quality.

No new confidence map or percentage is installed from these model predictions.
The production app remains usable with its existing editable estimates.

## Reusing analysis

Preparation freezes file hashes, source signatures, detector identities and
task positions before inference. Analysis resumes completed stages and retains
failed stages explicitly. Listening and seeking do not run detectors.

```sh
.venv/bin/python detector_pilot.py prepare ~/Downloads/dataset --out outputs/dataset-pilot
.venv/bin/python detector_pilot.py analyze outputs/dataset-pilot/pilot.json
.venv/bin/python detector_pilot.py summary outputs/dataset-pilot/pilot.json
.venv/bin/python detector_pilot_review.py outputs/dataset-pilot/pilot.json
```

If source files or detector identities change, use a fresh pilot folder. Preserve
the earlier predictions, references and original files for traceability.
