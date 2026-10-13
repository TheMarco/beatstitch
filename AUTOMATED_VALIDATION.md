# DJ Studio automated validation results

The pitch and tempo engine passed the controlled audio checks. OpenKeyScan remains a sensible default, with manual key overrides. The main weaknesses are imperfect bar recognition, uncertain local harmony and content prompts that can miss quiet or difficult material. The Mac app has been updated with fixes for stereo analysis, prepared-audio reuse and reliable packaging. Your current two-song arrangement and original audio were preserved.

These results distinguish three things: correctness against an explicit reference, consistency after an audio transformation, and a listener's judgment of a blend. Agreement between detectors cannot establish that an unlabeled song's key is correct. A model score cannot establish that a transition sounds good.

## Changes in the installed app

- **Audible stereo survives analysis.** Combining left and right channels could erase the signal presented to beat and key detectors, even though stereo playback remained audible. Analysis now checks channel energy and uses one fixed, stronger channel when the stereo average cancels substantially. Ordinary mono conversions remain sample-for-sample identical to the previous decoder. Source and exported audio are unchanged by this analysis choice.
- **Valid prepared audio remains reusable when a bookkeeping write fails.** Updating a cache entry's access timestamp used to be part of the successful-read path. A permission failure could turn valid cached audio into a cache miss. Timestamp updates are now best effort; audio format, duration, fingerprint and file-size validation still apply.
- **Packaged key detection has a writable compilation cache.** The key worker locates collected library sources inside the app bundle and stores compiled analysis data in the analysis cache. This avoids a library-cache failure encountered during the restricted package checks.
- **Release builds discard stale packaging caches.** Inspection of a generated Python archive showed old module code despite the corrected source. Clean packaging is now required. The final installed build passed all 14 packaged checks.

The native app reopened with 11 library songs, the same two-song mix, 128 BPM, Friday Night LFG shifted by +2 semitones, eight bars of overlap, volume 70 and mastering off. Paused seeking loaded the existing full mix without processing. Both the raw and mastered prepared mixes were found intact. The previous app and recovery files are retained in [the update backup](/Users/marcovhv/projects/GIT/DJ/outputs/analysis-audio-update/before-update).

The analyzer update invalidates old analysis estimates, while keeping your chosen labels, BPM and cues. A fresh Detect key or Analyze result restores those analysis controls. This is separate from preparing the mix audio.

## What was exercised

| Experiment | Completed coverage | Meaning |
| --- | ---: | --- |
| Key comparison | 3,768 backend results | Six detectors across 628 controlled audio cases |
| Timing comparison | 2,244 metric rows | Raw Beat This, its retained app grid and librosa across 748 cases |
| Content and local harmony | 628 observations | Voice prompts, tonal evidence, abstentions and transformation sensitivity |
| Stereo timing fix | 468 rows | Baseline and reversed-channel-polarity cases across all 78 fixtures |
| Stereo key fix | 60 observations | Musical and percussion controls plus the supplied songs |
| Physical pitch and tempo | 150 cases | Known frequencies, sample counts and timed percussion attacks |
| Stabilization | 40 initial rows and six gentle-drift rows | Detector support kept separate from rendering with a known map |
| Additional annotated timing references | 705 attempts | 704 scored recordings; one missing official source retained as a failure |

The primary fixture collection contains all 25 MP3s from `/Users/marcovhv/Downloads/dataset`, all 24 constructed major/minor keys, nine fixed-tempo rhythm patterns, two broad tempo-drift patterns, silence, noise and 16 previously observed Jamendo singing controls. The supplied originals were checked by SHA-256 after the experiments: **all 25 are unchanged**.

The controlled changes include pitch shifts of −2, +2, +5 and +12 semitones, tempo changes of −5% and +5%, lower gain, channel swapping, reversed right-channel polarity, and timing-only prefix/crop changes. The separate physical engine check covers every integer pitch shift from −12 through +12 and tempo factors 0.8, 0.95, 1.0, 1.05 and 1.2.

The figures count results and transformed windows, not thousands of independent songs. Raw and retained Beat This rows share the same model observations. The 16 singing controls are development examples, not fresh held-out confidence evidence.

## Pitch and fixed tempo

All **150 physical checks passed** their predeclared bounds: pitch within five cents, length within two sample frames, and measured percussion timing within 30 ms. There were no invalid or nonfinite rendered samples. The existing Rubber Band processing remains the audio engine; its [official documentation](https://breakfastquay.com/rubberband/code-doc/) describes independent pitch/time processing and offline key-frame maps.

This verifies frequency and timing behavior. It does not prove that a heavily shifted vocal or a large tempo change is pleasing. Formant preservation and optional transposition remain useful, and listening remains the way to judge their timbre.

## Key detection

The following compares the **written composition keys of constructed musical controls**, excluding the channel-cancellation stress. It is not an accuracy estimate for your 25 songs.

| Detector | Exact composition labels |
| --- | ---: |
| OpenKeyScan | 226 / 226 |
| Essentia edma | 226 / 226 |
| Essentia bgate | 226 / 226 |
| madmom | 225 / 226 |
| libKeyFinder | 218 / 226 |
| HPSS and pitch-profile baseline | 194 / 226 |

OpenKeyScan recovered the written key in **all 26 musical cancellation controls** after the stereo fix. All 60 corrected stereo-key observations produced an estimate. An estimate on percussion or noise still does not establish a musical key; these classifiers can assign a label to unsuitable material.

The real-song comparisons measure whether an estimate follows a known pitch shift or stays stable under other changes. They do not determine which baseline label is right. There were no usable embedded key tags in the supplied MP3s. Eight files contain BPM tags; six disagree substantially with the beat detector. Several repeat 140 BPM. Those tags are comparison data, not trusted references.

Every key backend received the same first, at most 60 seconds of a transformed case. In a slowed 60-second case, this cap contains less original musical material than the baseline minute. Tempo-case key differences therefore also reflect a changed endpoint. Pitch, gain and channel comparisons are the cleaner tests of equivalent content.

Essentia's expensive startup was reused inside a dedicated headless research process, with unchanged profiles and parameters. Its [KeyExtractor documentation](https://essentia.upf.edu/reference/std_KeyExtractor.html) defines the edma and bgate profiles. Completed key results were distributed into independent record groups: **546 existing inferences were reused**, with no repeated inference for those completed rows.

## Beats and bar starts

The larger reference evaluation uses the [official Beat This research archive](https://zenodo.org/records/13922116) and [human beat/downbeat annotations](https://github.com/CPJKU/beat_this_annotations). Previously observed recording IDs, their known artists and connected duplicate groups were excluded. Unknown artists remain descriptive only. Fit, validation and test groups were fixed before inference.

The four-beat test subset contains **153 recordings**. Within 70 ms of a reference event:

| Output | Beat precision | Beat recall | Bar-start precision | Bar-start recall |
| --- | ---: | ---: | ---: | ---: |
| Raw observations | 87.9% | 92.8% | 74.6% | 86.7% |
| Retained app grid | 93.4% | 78.9% | 85.9% | 78.1% |

Precision means how many offered events match; recall means how many reference events were retained. The app's conservative grid offers fewer questionable events and leaves more gaps. Bar starts remain less reliable than beats. A supported grid can still have the wrong bar phase.

![Timing reference precision and recall](/Users/marcovhv/projects/GIT/DJ/outputs/detector-experiments-20261012/timing-reference-results.png)

At the stricter 30 ms tolerance, retained-grid beat precision/recall were 84.1%/71.1%; bar-start precision/recall were 78.1%/71.0%. These are reference-collection results, not promised performance on AI-generated EDM.

The archive contains float16 precomputed features. A previously observed audio control checked the frontend shape and quantified the small feature differences; detected events agreed within the control's timing bound. The archive remains a proxy for the application's float32 frontend. One recording in the canonical index, reggae 00086, has neither supplied spectrogram nor annotation and remains an explicit failed attempt.

The original controlled timing run retained 117 failures: 105 caused by cancellation and 12 legitimate refusals on silent inputs. The corrected baseline/polarity run has only six silent-input refusals. No audible cancellation case remains a processing failure in that corrected run.

## Stabilization

The broad constructed tempo trends, from 116 toward 132 BPM, did not receive a trusted correction map. This is an abstention, not evidence that the material is steady. Of the supplied first-minute passages, 16 were reported steady and nine remained uncertain; none offered a correction map.

Two purpose-matched gentle-wobble controls did receive correction maps. Their known beat times vary sinusoidally. Against a fitted uniform clock, the source's 95th-percentile deviation was **92.4 ms** and the corrected whole passage's was **17.4 ms**. In supported regions, it fell from **95.1 ms to 0.49 ms**. Those regions covered about 84% of the passage; the edges remain part of the whole-passage result.

Rendering with an explicitly supplied known map was tested independently from finding that map. Constructed broadband beat markers stayed within approximately six milliseconds in the tested corrected excerpts, including +2 semitone transposition. Frame counts remained exact.

A pitched kick's loudest oscillation can move even when its onset stays aligned. The initial low-band peak measurement flagged that movement. An independent broadband transient check distinguished the clock from the kick's changing shape. Both the original diagnostic and the subsequent check are retained. The R2 and R3 time-map alternatives both met the transient bound; this experiment does not establish a listener's preference between their textures.

## Voices and local harmony

At the current 0.08 voice-prompt cutoff, all eight singing and all eight nonsinging baseline controls were classified as expected. Across their four gain/channel variants, 30 of 32 singing windows and all 32 nonsinging windows were classified as expected. Two singing windows fell below the cutoff after right-channel polarity reversal. The older 0.20 cutoff found only three of eight baseline singing windows.

These are 16 reused development clips. They support retaining the sensitive listening prompt, not displaying a vocal-absence guarantee or a confidence percentage. No false voice prompt occurred in the 1,416 transformed constructed-tonal windows, but this does not establish performance on real synthesized voices or actual vocal clashes.

Local key evidence stayed uncertain on 24 of the 25 real baseline openings. The constructed musical openings also remained locally uncertain while their whole-passage key estimates were clear. A short passage may expose a single chord, too few notes or competing context. It is appropriate to retain that uncertainty. **An uncertain local key never proves drums only.** Source-key compatibility, including relative major/minor keys, remains useful advice with that limit.

The previously frozen timing-confidence maps were checked on the larger reference collection without refitting. Neither passed the complete comparison rule. For test beats, the map's Brier error was worse than the raw score; for bar starts the average improved, but the grouped uncertainty still failed the acceptance rule. Calibrated timing, harmonic and overlap-quality percentages remain unfinished. None of these experiments authorizes a new percentage in the UI.

## What still needs ears

The app now offers **Listening notes…** in the Transition box. This optional
checklist stores separate opinions about beat alignment, harmony and competing
voices or melodies with the project. Any question can remain unchecked or unsure.
Saving a note leaves audio and playback unchanged; edited sound needs a new
listen. Keep markers remain separate and temporary. Notes record their prepared
audio recipe and source identities, and explicitly record exposure to the normal
interface's estimates. They are subjective listening evidence, not blind
reference annotations or calibrated confidence.

The accompanying update also makes integer and decimal BPM values share the same
prepared-audio identity after saving and reopening. Previously enabled correction
maps from analyzer version 1 remain usable because their map format and renderer
are unchanged. The UI identifies this as **Saved correction on**; those older
measurements do not become current detector assessments.

There is no requirement to manually label all 25 songs before using the app. The remaining useful listening checks are focused:

1. Compare an original vocal with a modest pitch shift in the blend editor. Listen for changed vowels, metallic tails or softened attacks.
2. Review a couple of difficult joins with **Align beats**. Listen for doubled kicks, then check whether the visually marked hits and musical phrase starts agree.
3. Compare a blend containing two voices with the competing-parts reduction draft. Check that the intended melody survives.
4. Compare optional mastering at matched listening volume, then verify an exported file on your normal headphones.

Those checks concern perceived quality and the actual audio device. Automated measurements cannot substitute for them. Native Windows/Linux device checks remain deferred under the earlier agreement.

## Evidence and reproduction

[Machine-readable summary](/Users/marcovhv/projects/GIT/DJ/outputs/detector-experiments-20261012/summary.json) links each main result by SHA-256. The experiment directory retains frozen source snapshots, plans, raw outputs, failures and model/archive identities. Originals and generated controls are separate. Incompatible resumes are rejected; a changed experiment needs a new output directory.

- [Key results](/Users/marcovhv/projects/GIT/DJ/outputs/detector-experiments-20261012/round-1/key-combined/key-report.json)
- [Timing results](/Users/marcovhv/projects/GIT/DJ/outputs/detector-experiments-20261012/round-1/timing/timing-report.json)
- [Content results](/Users/marcovhv/projects/GIT/DJ/outputs/detector-experiments-20261012/round-1/content/content-report.json)
- [Annotated reference results](/Users/marcovhv/projects/GIT/DJ/outputs/detector-experiments-20261012/fresh-timing-v3/summary.json)
- [Key stereo fix](/Users/marcovhv/projects/GIT/DJ/outputs/detector-experiments-20261012/key-phase-proof/key-phase-report.json)
- [Pitch and tempo checks](/Users/marcovhv/projects/GIT/DJ/outputs/detector-experiments-20261012/dsp/dsp-report.json)
- [Gentle correction measurements](/Users/marcovhv/projects/GIT/DJ/outputs/detector-experiments-20261012/gentle-drift/clock-spacing-diagnostics.json)
- [Installed update and preservation checks](/Users/marcovhv/projects/GIT/DJ/outputs/analysis-audio-update/installation.json)
- [Latest listening checklist update and native verification](/Users/marcovhv/projects/GIT/DJ/outputs/listening-notes-update/installation.json)

The latest Mac update passed all 15 packaged checks, and its 10 checked source
modules match the packaged bytecode. The focused playback, persistence, correction,
relinking and portable-project checks passed. Native verification confirmed that
Return selects checklist answers without starting audio, F1 opens the Transitions
guide, and a paused seek loads the cached full mix. The two-song mix, preferences,
and raw/mastered audio hashes remain unchanged. No human listening judgments were
created by automated verification.

Superseded generated app bundles were removed after the final checks. The latest
installation record identifies the retained rollback app and project backups.

The subsequent readability update makes small explanations, time/bar labels,
library text and placeholders brighter while keeping unavailable controls dim.
Warning text has a separate brighter color for selected rows and raised buttons.
Qt's automatic half-opacity placeholder was replaced with an explicit opaque
color. The check accounts for transparency when calculating effective contrast;
active and inactive text editors were checked. The 4.5:1 floor is a practical
desktop readability benchmark drawn from the [W3C contrast guidance](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html),
not a claim that the entire app meets a web accessibility standard. Qt documents
the [explicit placeholder color property](https://doc.qt.io/qt-6/stylesheet-reference.html#placeholder-text-color).

The update passed 22 relevant Qt checks and 15 packaged checks. Native screenshots
confirmed clearer explanations, an opaque placeholder and readable timeline
labels. Both prepared full-mix fingerprints and audio hashes remain unchanged.
The [current installation record](/Users/marcovhv/projects/GIT/DJ/outputs/readability-update/installation.json)
identifies the retained compatible rollback. Earlier app bundles have been
superseded; their project backups and validation evidence remain available.

The CLI runners are `prepare_detector_experiments.py`, `detector_experiment_key.py`, `detector_experiment_timing.py`, `detector_experiment_content.py`, `detector_experiment_drift.py`, `detector_experiment_dsp.py`, and `prototype_fresh_timing_reference.py`. Their help describes their inputs. For exact reproduction, use the saved runtime snapshot and its corresponding frozen plan. A fresh run against changed source is a new experiment.
