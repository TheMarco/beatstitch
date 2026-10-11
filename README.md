# DJ Studio

A native **Qt / PySide6 desktop application** for arranging EDM mixes. No browser,
webview, server, or online audio service is required. The code supports macOS,
Windows, and Linux; this development build has been exercised on macOS.

## Desktop app

On this Mac, double-click `Launch DJ Studio.command` to open the app and restore
the current session from `outputs/studio`. A packaged app is built at
`dist/DJ Studio.app`; its default project storage is the operating system's local
application-data directory. Use **File > Open** to open an existing `.djmix` file.

The current example arrangement is stored at `outputs/studio/autosave.djmix`.
Projects reference original audio paths rather than copying music into the project.
Moving a project to another machine currently requires updating those source paths.

1. Import a folder (subfolders included) or individual songs, or drop files and
   folders onto the library. BPM and key analysis runs locally in the background.
   Sort the library by name, BPM, or key; a green dot marks songs whose key fits
   the selected clip.
2. Drag songs onto the timeline, or double-click a library row. Drag arranged clips
   to reorder them, or use **Earlier / Later**. Overlaps are laid out automatically.
3. Select a clip to edit its source BPM and key, pitch, timing, and cue points in
   the panel on the right. Changes apply immediately and can be undone and redone.
   Drag the **In** and **Out** handles on the song waveform; they snap to bars from
   the analyzed grid (hold Shift for beats, Option for free placement). Cue times
   refer to the original song. The out point is the end of the outgoing overlap.
4. The transition panel under the timeline shows the selected blend: its length,
   bass handoff, beats-only flags for the outro and intro, and a key verdict. Step
   through transitions with its arrows or `[` and `]`, then click **Preview
   transition** (P). A preview includes four bars on each side of the blend.
5. Original pitch is preserved while matching the project BPM. **Transpose this
   song** enables a small suggested shift when available; the semitone value remains
   editable. Switch it off to restore the original key.
6. Mark an outro or intro as **beats only** when that whole overlap contains no
   pitched bass, vocals, melody, or tonal tails. This relaxes harmonic guidance; it
   does not change the audio. **Suggest order** shows a proposal to apply or keep
   your own order; it retains the opening song and all pitch and cue choices.
7. **Render full mix** for playback and seeking, or **Export mix** to a stereo
   48 kHz, 24-bit WAV. A running render can be cancelled from the status bar.
   **File > Save** writes to the current project file, and edits also autosave.
   New and Open keep a recovery copy of unsaved work.

The timeline draws each song as a three-band waveform (lows blue, mids cyan, highs
white) with the crossfade and bass-handoff curves over each overlap. The overview
strip above it shows the whole mix; click it to jump there. An amber line marks the
range that has rendered audio. Waveform bands are computed once per song, cached in
`library-cache`, and used for display only.

Keyboard: Space plays or pauses, P previews the selected transition, `[` and `]`
step through transitions, Delete removes the selected clip, and Cmd+Z and
Cmd+Shift+Z undo and redo (Ctrl on Windows and Linux). Cmd+scroll or a trackpad
pinch zooms the timeline.

The speaker menu in the top bar follows the system default output or routes playback
to a particular device such as AirPods or a USB audio interface. Device choices
are remembered. If an explicitly selected device disconnects, playback pauses and
mutes until that device returns or another output is selected.

### Optional tempo stabilization

New imports estimate BPM using reliable rhythm passages throughout the song.
For an existing arranged track, select it and click **Analyze timing**.
This preserves the saved BPM and source cues; **Use … BPM** applies
the new estimate explicitly. Integer BPM is suggested only when long beat fits
support it. A small error in the old BPM estimate does not itself trigger warping.

When confident drift is found, **Stabilize tempo** enables gentle correction for
that clip. It is off by default. The clip panel reports how much of the source has
usable correction; sparse or conflicting beat observations are excluded, with
short smooth joins to fixed timing. This detector targets regular EDM kick
patterns and does not establish downbeats or guarantee a correct grid everywhere.
Listen before using a correction in a final mix.

**Compare A and B** prepares up to 90 seconds from a detected drift
passage inside the applied cues. It uses the same processing as previews/exports,
including the applied pitch shift and project tempo, and matches loudness.
Both versions share one playback clock; switching uses a five-millisecond fade
without reloading, pausing or seeking. The optional click stays on that clock.
The comparison uses the main app's selected output device and volume.

Stabilization uses the auditioned Rubber Band R2 crispness-4 time-map processing.
When transposition is also enabled, an R3 pitch-only pass runs first: our Rubber
Band 4.0 test found that combining R2 pitch and a time map in one pass displaced
the beat grid. Cues remain source timestamps; corrected clip lengths and timeline
positions come from the actual map. Existing version-1 projects open with
stabilization disabled; saves use version 2.

### Processing cache

Processed source excerpts and source loudness measurements persist across runs.
Adding a third track to a previously rendered two-track arrangement reuses the
first two tracks' processed audio. Transposition, source tempo, project tempo,
source-file identity/size/mtime, and source in points govern reuse. Disabled
transposition is equivalent to zero semitones. Changing blend lengths, bass
handoffs, order, or key labels does not invalidate the source transformation.

A full cached track can also serve a shorter preview. If only a short excerpt has
been processed, a later request for more of that song must process the new range.
The app still assembles the requested mix and checks its final headroom on each
render. The status bar reports reused and newly processed tracks. The audio cache
is limited to 8 GiB and evicts the least recently used completed entries. Sources
and saved projects are never part of cache eviction.

Corrected clips are processed in full on their first preview, then reused by
other previews and exports. Their timing map and both cues participate in the
cache key; changing one track's correction does not invalidate other tracks.
Turning correction off restores the original processing cache. Whole-song timing
analysis is cached by source file, algorithm version and initial BPM. A/B audio
also shares the 8 GiB cache budget. Changed source files require fresh timing
analysis before a saved correction can be rendered.

### Install from source

Use Python 3.11–3.13, FFmpeg/FFprobe, and Rubber Band 3+ with the R3 engine.

macOS:

```sh
brew install ffmpeg rubberband
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-desktop.txt
./launch_studio.sh
```

Linux (Debian/Ubuntu package names):

```sh
sudo apt install python3-venv ffmpeg rubberband-cli libxcb-cursor0
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-desktop.txt
./launch_studio.sh
```

On Windows, install Python and put `ffmpeg.exe`, `ffprobe.exe`, and
`rubberband.exe` with R3 support on PATH, or in a project-local `bin` folder
alongside their required DLLs. Then:

```bat
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-desktop.txt
launch_studio.bat
```

### Native packaging

Run on each target OS and architecture; the script bundles Python, Qt, analysis
dependencies, and the installed audio executables:

```sh
.venv/bin/python -m pip install 'pyinstaller>=6.16,<7'
.venv/bin/python build_desktop.py
```

On Windows use `.venv\Scripts\python` for these commands. The result is a macOS
`.app`, Windows executable folder, or Linux executable folder under `dist/`.
Keep the full output folder together on Windows/Linux. Each platform needs its own
build and playback/export test. The local macOS bundle is ad-hoc signed, not notarized.
Engine and dependency licenses apply to any distribution of the bundled binaries.
The interface font, Barlow, is bundled from `assets/fonts` under the SIL Open Font
License (`assets/fonts/OFL.txt`); without those files the app falls back to the
system font.
See [PyInstaller packaging](https://pyinstaller.org/en/stable/usage.html).

### Current scope

This is an offline arrangement editor: changes are rendered before auditioning.
It assumes 4/4 and a fixed project BPM. Beat/key estimates are editable suggestions;
optional tempo stabilization corrects confidently detected passages. Phrase starts,
vocals, local key changes and ambiguous rhythms still need review. Ordering is a
simple key/BPM heuristic, not a model of musical energy.
The supplied first transition was auditioned; new transitions still need listening.

## Original audio proof and CLI

A local, offline proof of the audio pipeline for a user-controlled EDM mix builder.
It uses **Rubber Band R3** for independent pitch/tempo changes and **FFmpeg** for
decoding, EQ crossover, loudness measurements, and WAV export.

The original two-track CLI remains available alongside the native editor. The CLI
needs explicit BPMs and cue points; the separate analysis helper supplies provisional
estimates. Its HTML reports are legacy listening artifacts, not the desktop UI.

## First real-track audition

Open `outputs/listen.html` in a browser for two 30-second drafts of **Friday Night LFG
→ Lose Yourself in the Latent Space**, using the files supplied from
`/Users/marcovhv/album1/test`.

Both use 128 BPM and an eight-bar overlap from 7.5 to 22.5 seconds. One shifts the
incoming song down two semitones; the other keeps its original key for comparison.
The estimates are provisional, especially the outgoing key, and phrase boundaries
are not verified. The previews passed their duration and true-peak checks. Subjective
sound quality and musical fit still need a listening decision.

The inputs and their estimated grids are recorded in `outputs/analysis/`. Each
preview's `report.json` preserves the exact render settings for reproduction.

## Listen to the generated demonstration

```sh
cd /Users/marcovhv/projects/GIT/DJ
python3 djmix.py demo
```

This creates original synthetic test tracks and a 31.5-second blend:

- A: 120 BPM, C minor → 122 BPM, unchanged key.
- B: 124 BPM, D minor → 122 BPM, shifted down two semitones to C minor.
- Eight-bar overlap with a one-bar bass handoff in its middle.

Open the printed `report.html` file in a browser. It contains players for the mix
and the original/processed excerpts. The fixture demonstrates functioning processing,
not sound quality on commercial recordings or vocals. Only one player runs at a time.

The first checked render is in `outputs/demo-verified/preview/`.

## Mix your own two tracks

For example, **if** A is 126 BPM / D minor, B is 128 BPM / E minor, and the desired
outgoing downbeat is at 2:00 in A:

```sh
python3 djmix.py mix "/path/to/track-a.wav" "/path/to/track-b.flac" \
  --bpm-a 126 --bpm-b 128 --target-bpm 128 \
  --key-a "D minor" --key-b "E minor" \
  --cue-a 2:00 --cue-b 0:00 --bars 16 \
  --out outputs/my-first-preview
```

Replace the filenames and settings with your actual tracks. FFmpeg supports WAV,
FLAC, MP3, AIFF, M4A, and other audio formats. The source files are never modified.
No audio is uploaded or sent to an external service.

**Keep original key is the default for each track, even when its BPM changes.**
Pitch adjustment is optional: add `--shift-b -2` to audition B two semitones lower,
or use `--shift-a` for A. Supplying key labels only enables harmonic guidance;
it never authorizes the tool to transpose a track. Preview a suggested shift and
keep it only if you prefer how it sounds.

**Cues are timestamps in the original files, before any tempo change.** Both cues
should mark the first beat of your intended transition phrase. A must have the full
overlap remaining after its cue; B must have enough audio too. The tool deliberately
rejects a too-short section rather than silently padding it with silence.

By default, it renders four bars of A before the overlap, the blend, and four bars
of B after it (or less context if the file boundary is reached). Add `--full` to
render all of A before the transition and all of B from its incoming cue onward.
A's material after the overlap is omitted, as expected for a transition out of A.
Pitch and target tempo remain constant across each included track segment.

## Controls

| Option | Behavior |
| --- | --- |
| `--target-bpm` | Common tempo for both tracks. |
| `--shift-a`, `--shift-b` | Optional per-track pitch adjustment; default 0 keeps the original key. |
| `--key-a`, `--key-b` | User-supplied keys of the overlapping sections. |
| `--cue-a`, `--cue-b` | Original-file overlap entry points, in seconds or MM:SS. |
| `--bars` | Overlap length in 4/4 bars; default 16. |
| `--context-bars` | Preview context on either side; default 4. |
| `--texture-a percussive` | Designate A's entire overlap as free of harmonic content. |
| `--texture-b percussive` | The same designation for B. |
| `--gain-a`, `--gain-b` | Additional static track gain in dB after loudness matching. |
| `--no-bass-swap` | Use a conventional full-band equal-power crossfade. |
| `--no-formants` | Disable formant preservation to audition the alternative. |
| `--full` | Export the full two-track arrangement. |
| `--estimated-inputs` | Label the selected BPM/key/cue settings as provisional estimates. |

Keys accept names like `Cm`, `F# minor`, `Bb`, or `D major`. Camelot numbers are not
accepted. Suggested shifts of up to two semitones appear in the report but are
**never applied automatically**. The user must choose a pitch shift explicitly.
Same-key, relative-major/minor, and neighboring-circle-of-fifths matches are useful
suggestions; they do not guarantee that melodies or vocals will fit.

Only mark an overlap percussive if it remains so for the **whole overlap**. A pitched
bass line or synth reverb tail still counts as musical content. This prototype
trusts that designation; it does not identify those elements itself.

## Optional local analysis

For files without BPM/key tags, `analyze_audio.py` uses librosa to inspect a bounded
excerpt. It produces candidate beat grids and key correlations in a JSON file:

```sh
.venv/bin/python analyze_audio.py "/path/to/track.wav" \
  --start 0:30 --duration 60 --bars 8 --out outputs/analysis/track-intro.json
```

The project virtual environment has these optional dependencies installed. On a
fresh machine, install `requirements-analysis.txt` into your virtual environment.

It separates harmonic/percussive content, uses librosa's beat tracker and constant-Q
chromagram, and compares chroma against Krumhansl-Kessler key profiles. The resulting
key scores are correlations, **not probabilities**. Candidate cue points are beats,
not verified downbeats or phrase starts. Fit residuals measure the consistency of
the detected grid, not an independent check that it found the correct beats.

Review the estimates before rendering, and use `--estimated-inputs` when creating a
draft from them so the listening report clearly identifies the remaining uncertainty.

## Processing and outputs

The renderer decodes stereo audio at 48 kHz, supplies neighboring audio to
Rubber Band's offline R3 engine at a fixed tempo ratio, and applies static track gain based on
whole-source integrated loudness. It uses an equal-power crossfade for the upper
bands and a complementary, one-bar bass handoff around a 180 Hz crossover. This
reduces overlapping low end; it does not guarantee phase-compatible kicks.

The mix receives only the static attenuation needed to meet a -1 dBTP ceiling with
a small margin. It is measured again after 24-bit export. There is no compressor
or loudness-maximizing limiter. Source/processed comparison pairs receive the same
gain and any shared attenuation needed to keep both safe to play.

Each **new** output directory contains:

- `mix.wav` — stereo 48 kHz / 24-bit preview or full mix.
- `a-original.wav`, `b-original.wav` — original excerpts at their original BPM/key.
- `a-processed.wav`, `b-processed.wav` — transformed excerpts before transition EQ.
- `report.html` — a local listening page with the selected settings and guidance.
- `report.json` — exact source selections, timeline, gains, and measured output.

Existing output directories are refused to protect earlier renders. If a render
fails, any partial output remains in that directory; choose a new directory for a
retry. Intermediate processing files are cleaned automatically.

## Requirements and focused verification

Python 3.10+, NumPy, FFmpeg, FFprobe, and Rubber Band 3+ (R3 support).
The development machine already has these dependencies. For another macOS machine:

```sh
brew install ffmpeg rubberband
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python djmix.py demo
```

Run the focused checks with:

```sh
python3 -m unittest discover -s tests -v
```

They verify source-time cue calculations, harmonic guidance without automatic
transposition, input rejection, actual pitch/tempo independence, stereo coherence,
and beat timing after a nonzero cue crop. The demo render separately checks its
planned output duration and post-export true-peak headroom.

Desktop checks additionally cover three-track timing/export, project round trips,
optional transposition, a nonmutating order proposal, and persistent cache reuse
after appending a track, previewing 2 → 3, changing pitch, replacing a source, and
changing project tempo.

Timing checks cover stable-versus-drifting evidence, uncertain gaps, version-1
project loading, corrected pitch and transient timing after nonzero cues, exact
preview reuse, source-change rejection, loudness-matched A/B caching, and one-clock
playback with repeated switches and partial device writes.

## Engine references

- [Rubber Band integration and quality options](https://breakfastquay.com/rubberband/integration.html)
- [Rubber Band distribution licensing](https://breakfastquay.com/rubberband/)
- [FFmpeg filter documentation](https://ffmpeg.org/ffmpeg-filters.html)

Packaging or distributing the app will need to respect the chosen engines' licenses.
