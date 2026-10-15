# BeatStitch 0.1.3

A native **Qt / PySide6 desktop application** for arranging EDM mixes. No browser,
webview, server, or online audio service is required. The code supports macOS,
Windows, and Linux. Native interaction has been exercised on macOS; packaged audio
checks and both optional analysis workers also pass on Windows Server 2022 and
Ubuntu 24.04 x86-64 runners.

Crossfades have independent incoming/outgoing lengths and Smooth, Linear, Fast
and Slow curves under **Fades…**. Fades stay linked by default. Drag white fade
handles to change lengths; drag a song's body to move its entry time, snapping
to bars while later songs follow. The small title-bar grip reorders songs.
Drags preview visually and commit as one Undo step, without processing audio.

The original application code is **GPL-3.0-or-later**; see [LICENSE](LICENSE)
and [the licensing notice](LICENSE_NOTICE.txt). The planned public edition is
free, with entirely optional [Ko-fi support](https://ko-fi.com/aianddesign).
GPL permits commercial reuse of the covered code under its terms. Third-party
libraries, fonts, artwork, and model weights retain their own terms.

The production separator uses MIT-licensed Spleeter code and weights. The optional
advanced section-analysis download includes MuQ weights under CC BY-NC 4.0; that
restriction applies to those weights. The creator chose to retain the optional
coffee link without publisher clarification of this particular arrangement.
It says: “If you like this tool, consider buying me a coffee.” Donations are
optional and do not unlock features. GPL/LGPL source materials accompany each
Mac release separately; see [release instructions](RELEASE_MAC.md) and the
licensing notice for scope and evidence.

The Spleeter replacement has a private, level-matched comparison on this Mac:
`Launch Separator Comparison.command`. Buttons 1–5 switch continuously between
the original, former Demucs research reference, and Spleeter at 75% reduction.
The prototype leaves the working mix and installed app unchanged. Its prepared
user-song audio stays in ignored `outputs/`; no song excerpts belong in a release.

`release_source_bundle.py` packages locally collected, hash-verified source
archives, original notices, patches and rebuild recipes with an explicit coverage
manifest. It rejects missing/changed artifacts and refuses to overwrite an
existing bundle. Its receipt proves bytes/declared coverage, not legal clearance
or completeness of an unreviewed platform's corresponding source. Each public
binary needs its matching source materials. The default installer contains no model
weights. File → Downloads & features installs optional, pinned models in writable
user storage, with progress, cancellation, resume/retry and repair. Separate
downloads retain their licenses and do not expand their permitted uses.

The recommended beat, key and voice/drum detectors total approximately 125 MB.
Vocal removal is a separate 146 MB download. Advanced build/drop detection is a
large optional 3.07 GB download (2.76 GB installed), unchecked by default. Timeline
editing, manual BPM/keys, pitch/tempo processing, blends, mastering, export, the
original demo and listening lessons work without these downloads. Installed models
survive app updates and run offline; songs are not uploaded. The offline guide
explains each choice under **Choose optional features and downloads**.

## Desktop app

Open **About** from the application menu on macOS or the Help menu on
Windows/Linux. It shows the supplied PNG logo, credits creator Marco van Hylckama
Vlieg, and provides searchable project credits and bundled license notices. The
catalog is generated from the build's Python dependency graph, native audio tool
libraries, and curated model/font credits; optional features and build tools are
labeled separately. Original source/model notices are retained, including cases
where checkpoint terms are not established. The app icon uses the same PNG
emblem. `build_credits.py` refreshes the catalog during packaging; native icon
files are maintained with `tools/build_beatstitch_icons.py`.

**Updates.** A few seconds after launch, at most once a day, the app asks
`https://beatstitch.ai-created.com/latest.json` for the newest version. The request
carries only the app version in its User-Agent. **Help → Check for updates** runs the
check on demand, and **Check for updates automatically** turns the daily check off.
When a newer version exists, **Download** saves this platform's package to the
Downloads folder, resuming saved bytes and verifying the size and SHA-256 listed
in `latest.json`. It never overwrites another file, and installing stays manual.
Only `main()` schedules the check, so tests and screenshot captures never reach
the network. To publish a release, update `web/latest.json` with the
direct archive URLs, sizes and checksums, and the two `data-release`
versions in `web/index.html`. `tests.test_updates` keeps the three consistent.

The isolated **detector-confidence pilot** uses the 25 songs supplied in
`~/Downloads/dataset`. Double-click `Launch Detector Pilot.command` for native
listening, real waveforms and independently entered reference labels. Suggestions
start hidden, answers are saved separately, and revealing results records exposure.
It changes no mix or installed detector settings. See [the pilot instructions](DETECTOR_PILOT.md).
These development predictions do not establish accuracy or calibrated confidence.

On this Mac, double-click `Launch BeatStitch.command` to open the app and restore
the current session from `outputs/studio`. A packaged app is built at
`dist/BeatStitch.app`; its default project storage is the operating system's local
application-data directory. Use **File > Open** to open an existing `.djmix` file.

The current example arrangement is stored at `outputs/studio/autosave.djmix`.
Projects reference original audio paths rather than copying music into the project.
Use **File → Create portable project…** to take the project and its source audio
together, or **Reconnect source audio…** to locate an unchanged source you moved.

**Help → Beginner course…** (or **Help** at the bottom right) opens a 14-lesson
course in the native app. Start with the included demo, then learn to choose songs,
build an arrangement, check BPM and beats, set musical cues and overlap lengths,
compare pitch and blend choices, handle drift, troubleshoot, and save/export.
Each lesson includes a practical aim, steps, listening checkpoints and links to
the detailed reference. Previous/Next lesson controls follow the learning path;
the lesson number shows your location, not a saved completion score. Reading the
course does not change the mix. The listening-examples button opens the existing
three short A/B exercises.
Back and Forward restore your reading position within visited topics, including
after a search with no results. These reading positions last for the guide window's
lifetime; they are not saved course-completion marks.

**Help > BeatStitch guide** opens the searchable
offline guide in a native Qt text window. It covers the first-mix workflow, every
main control, BPM and key concepts, timing correction, troubleshooting and
shortcuts. Press **F1** on a control for its topic, or use a section's **?** button.
Search matches topic text as well as titles. The guide can stay open while you mix.

The workspace has a control bar, library, arrangement timeline, and details for
the selected clip and its incoming transition. The guide explains what each area
changes and how Simple view relates to Advanced controls.

**Tempo curve…** beside the main BPM control adds optional gradual speed changes.
Use the Bar and BPM steppers to set points; both songs follow the same curve during a blend.
Equal BPM values keep the tempo steady, which the dialog explains explicitly.
Keys are preserved, and accepted drift correction is composed with the set curve
in a single timing pass. Apply is one Undo action. Switching the curve off restores
the fixed project BPM while remembering the points. Points stay at their bars when
you add or reorder songs; the last BPM continues after the final point. The timeline
stays spaced in musical bars, while clocks, previews and exported tracklists show
the corresponding elapsed seconds. F1 opens the full guide. Start with small changes
and listen: extreme or abrupt ramps can still produce audible artifacts.
Editing a later ramp reuses earlier prepared audio when the tempo over its complete
processing passage is unchanged, including the short context around source cues.

The default **Simple** view keeps real arrangement and source waveforms, a song
summary and the everyday blend controls. The summary distinguishes source BPM
from mix speed, reports the actual pitch/correction state, and offers original-key
bypass or an available conservative pitch suggestion. **Advanced controls** above
the arrangement (View menu or Cmd/Ctrl+Shift+A) reveals exact cues, source labels,
manual pitch, timing correction and drum-only flags. **More settings…** opens the
same view for the selected song. Switching views changes no audio settings,
selection, playhead or prepared previews, and the preference survives restart.

**Mix guide** above the arrangement (also in File) provides a guided path: check
two or more songs, audition originals, review common tempo and an overlap stepper,
then hear a proposed order before **Create mix** or **Replace arrangement**. The
draft uses library cues and original keys; it does not copy existing clip edits.
Cancel preserves the arrangement. Imported songs stay in the library. Applying is
one Undo action, including any source BPM values entered for the selected songs.
The final step links to blend-by-blend review, whole-mix listening and export.
Real waveforms and the detailed controls remain available throughout normal editing.
Missing files are explained beside their songs. Restore the file or reconnect its
drive, then use **Refresh**; if it moved, close the guide and choose **File →
Reconnect source audio…**. The guide rechecks selected files before continuing and
before applying a draft. If one disappears, it names the songs and preserves the
current arrangement and draft selection so you can restore the files and retry.

**Help → Listening examples…** provides three original 16-second A/B lessons:
drifting beats, clashing notes and competing basslines. Their real waveforms and
shared playback clock let you switch continuously at matched listening levels.
The explanations link each example to the relevant controls. No imports or
project edits are needed; closing preserves your mix and playhead. Lesson volume
is separate, and a missing output pauses playback. These authored examples teach
listening, rather than benchmarking automatic analysis. The 26 MB of audio is
bundled; regenerate it with `python build_listening_lessons.py` before packaging.

**Edit → Start & ending…** auditions project fade-in/out choices in whole four-beat
bars, with seconds and a gold volume curve over real source waveforms. Fades stay
outside blends and protected passages. Optional source extension includes the
recording’s remaining beginning or ending, including any recorded reverb tail;
it does not generate missing audio. Listen to either draft edge before applying.
Apply is one Undo action, previews and exports use the same fade, and fade-only
edits reuse processed and assembled audio. A fresh Mix guide arrangement starts
with fades off. Context help explains the choices and available duration.

**Mastering: On/Off…** beside Export (also **Edit → Automatic mastering…**) adds an
optional conservative final pass: measured gentle dynamics control, compensated
oversampled peak limiting and a bounded loudness increase. It is off by default.
**Compare before / after** offers real waveforms and one playback clock; leave
**Match listening loudness** on to compare punch and texture fairly. A/B, matching,
looping and seeking use prepared audio. The checkbox is a draft until **Apply
changes**, one Undo action. Project format 15 remembers it; formats 1–14 still open
with mastering off. Main mix/blend playback and exports use the same full master;
short previews are excerpts of it. Deck/timing tools monitor audio before final
mastering. Switching the setting reuses assembled and transformed sources. Hover
for tooltips or press F1 for the full beginner explanation. This uses established
DSP rather than a neural model; it makes no automatic broad EQ or widening choices.

**Protect…** below the source waveform (also Song cues → Protect passages) marks
source ranges that must stay fully included and outside both adjacent blends.
Vocal/build/drop/favorite are user labels. **Find possible vocals** offers tentative
voice-like ranges from the same cached observations as Find drum-led passages.
Listen first: ten-second scan windows are approximate, instruments can be mistaken
for voices, and missed detections do not prove vocal-free audio. **Use this range**
only fills the manual form; review it, choose **Protect this passage**, then Apply.
Builds and drops still need manual marking. Conflicting drafts explain what to fix;
**Shorten blends to fit** proposes smaller overlaps
without changing cues. Apply saves marks and overlap changes as one Undo action.
Marks appear on both real waveforms and are saved per clip in project format 10
(formats 1–9 still open). Order suggestions keep protected songs even in a shorter
set. Removing a clip or explicitly replacing an arrangement removes its marks.
Mark-only edits preserve valid cached audio.

**Projects…** in the bottom bar (File → Projects and recovery, Cmd/Ctrl+Shift+O)
opens recent files and recovery copies with names, dates, song counts, durations
and locations. Recent projects retain the last 20 successful opens/saves; recovery
copies load in pages of 20, with search over loaded entries. Missing or unreadable
projects stay visible with a reason. Recover opens an unsaved copy and never
overwrites its original project; Save then asks for a destination. Switching
projects first preserves current unsaved work, and a failed recovery write blocks
the switch. The bottom bar shows saved/edited/not-saved state and autosave status.

**Reconnect source audio…** in File, a library song's context menu, or Mix Review
reconnects moved songs without replacing their clips. New imports record a content
fingerprint. An unchanged file preserves cue/pitch/protection settings and valid
analysis. Older projects without an identity require an explicit recording/version
confirmation; unverified beat/timing analysis is cleared and correction is turned
off for affected clips. The dialog provides a real waveform and source audition.
Apply is one Undo action and rechecks whether the chosen file changed meanwhile.

**File → Create portable project…** copies every project source (including unused
library songs) into a new folder with `Mix.djmix` and `media/`. It estimates space,
supports cancellation and never replaces an existing destination folder. Move the
whole folder together. Opening verifies bundled identities in a background job;
missing media stays relinkable, and changed media is reported. Saving a project
beside its contained `media/` retains relative paths. Save As elsewhere creates an
ordinary linked project; create another portable copy to copy its media too.

1. Click **Add folder…** (subfolders included) or **Add files…**, or drop files and
   folders onto the library. BPM and key analysis runs locally in the background.
   Click a column header to sort by name, BPM, key (Camelot order), or length. A
   colored square marks songs already in the mix; a green dot marks songs whose key
   fits the selected clip. Select a song and click **Listen**, **Start**, or
   **Ending** beneath its waveform to audition the original without adding it.
   The source slider seeks within the original song; its tempo and pitch are unchanged.
   Seeking keeps paused audio paused. Focus the slider and use Left/Right for one
   second, Page Up/Down for ten seconds, or Home/End for the beginning/end; choose
   **Listen** to start from there. Positioning never adds a clip or changes cues.
2. Drag songs onto the arrangement, or double-click a library row. Songs alternate
   between the Deck A and Deck B lanes and overlap automatically. Drag clips to
   reorder them, or use the arrow buttons in the Clip box.
3. Select a clip to edit it in the detail view. Number boxes change when you drag
   them up or down (Shift for fine steps); double-click one to type a value.
   Changes apply immediately and can be undone and redone. Drag the **Start** and
   **End** markers on the sample display; they snap to bars from the analyzed grid
   (hold Shift for beats, Option for free placement). Cue times refer to the
   original song. The end point is the end of the outgoing overlap. Below the
   waveform, **Start** and **End** zoom to either cue; **−**, **+**, and **Fit**
   adjust the view. Ctrl/Cmd+scroll zooms at the pointer. Scroll or drag the ruler
   to pan across a zoomed waveform. Keyboard: Tab to the source waveform, Up/Down
   selects Start/End, and Left/Right moves the active marker by a bar (Shift for a
   beat, Alt/Option for 10 milliseconds). Cue edits share the same validation and
   Undo as dragging. In the arrangement, Up/Down selects a song and Left/Right
   seeks one beat (Shift for a bar); Home/End seeks the mix boundaries. The overview
   uses one-bar steps, or eight bars with Shift. An orange outline shows keyboard
   focus; +/−/0 zooms or fits the arrangement or source waveform. Tab moves on.
4. Transitions appear as numbered locators above the bar ruler, with a green dot
   for a key match and a red one for a clash. The Transition box on the right of
   the detail view holds **Overlap** (up to 32 bars, limited by available audio), **Bass swap**, the drum-only flags, and
   the key verdict. Step through transitions with its arrows or `[` and `]`, then
   click **Listen to blend** (P). A preview includes four bars on each side of the blend.
   **Loop blend** repeats it. **Review all** walks through the transitions at your pace.
   **Keep and next** records that you like the current sound; **Change…** opens its
   draft editor and returns to the same blend to listen again. **Next** leaves a
   blend unchecked. Playback waits for your decision instead of advancing by itself.
   The review strip shows kept and unchecked counts; **Review remaining** revisits
   unchecked blends. Audio edits clear affected Keep decisions, including both
   neighbors when a song's pitch changes. **End review** or **Stop** pauses the walk.
   Keep markers are temporary choices, not quality scores or locks. They are
   not saved with the project; **Review all** starts a new pass.
   **Listening notes…** opens an optional checklist for beats, harmony and competing
   voices or melodies. Those separate notes are saved with the project and are
   undoable. Unchecked and unsure remain distinct; keeping a blend never supplies
   answers for you. Notes belong to the prepared sound and ask for another listen
   after audio edits. Saving them does not process audio or change playback.
   To make a blend longer or shorter, click **− / +**, drag the Overlap number or double-click to
   type. Seconds and the maximum available length appear below it; **Presets**
   offers 4/8/16/32-bar presets. Both songs' other blends count toward the limit.
   The selected pair is labeled **Out** and **In**. Drag the orange grip at the
   overlap's start to resize it in whole bars; Escape cancels. The outgoing end
   stays fixed and the incoming song and later songs move. **Reset** restores
   default overlap/bass/drum-label settings while keeping cues and pitch.
   Edits have named Undo actions. Preview again to hear each change.
5. Original pitch is preserved while matching the project BPM. In either view,
   choose **Play in key**, then its nearby **Listen to blend** button (or **Listen
   in mix** for one song) to prepare and hear the choice. Minor songs offer only
   minor destinations; major songs offer only major destinations. **Original**
   restores zero pitch shift. For incoming songs, the list defaults to keys that
   fit the previous song's playing key, including its pitch shift. Choices explain
   same, relative and neighbouring keys; the smallest shifts appear first.
   **Original fits** means no pitch change is needed. **Show all keys** opens every
   destination for experimentation without starting processing. Existing choices
   outside the suggestions are kept. First songs, unknown previous keys and marked
   drums-only overlaps show every destination. Advanced controls also offer a numeric semitone
   control and **Apply shift** checkbox, disabled at zero. **Try … st**, when
   available, applies advice checked against both neighboring blends. **Source
   key** corrects the original recording's label without changing its sound;
   the detected estimate remains visible, with **Restore detected** to accept
   it again. Use **Detect key** first if no usable estimate is saved. Pitch edits
   take effect on the next preview or render, not during current playback.
   The library's Listen button always plays the original recording.
   **Find next songs** beside Play in key shows compatible library songs at their
   original pitch. It clears search/collection restrictions and selects the key
   filter; **All songs** restores the full list. Selecting a candidate explains
   the relationship in words. A minor and C major are relative keys and need no
   extra pitch shift to form that key relationship. This remains listening advice,
   not a guarantee about competing melodies or voices.
6. Turn on **Drum outro** or **Drum intro** when that whole overlap contains no
   pitched bass, vocals, melody, or tonal tails. This relaxes harmonic guidance; it
   does not change the audio. **Suggest order** opens a draft planner. Choose an
   opener, closer and fixed positions; add optional energy ratings from 1–5 and
   choose a gentle flow, gradual build or rise-and-fall arc. By default, unrated
   clips receive an optional **Estimated 1–5** activity rating from their kept
   audio. Your own rating overrides it. Turn off **Estimate energy from audio**
   to leave unrated clips unknown. The planner also considers key labels and nearby source tempos.
   It keeps every song unless you explicitly enable **Choose a shorter set**.
   A shorter-set proposal lists omitted songs, its actual duration and the target
   difference. Fixed-position songs remain included. No cues, pitch, tempo or
   overlap lengths are changed to force a duration. Select a proposed incoming
   song and **Listen** to its blend before **Apply plan**. Apply is one named Undo
   action; **Keep current mix** discards the draft. Ratings, locks and planning
   preferences are saved with the project only after Apply. Position locks govern
   suggestions; you can still move a clip manually. Energy analysis is local,
   cached per source and cue range, and included in the storage budget. Adding a
   song reuses unchanged clips. Changing only mix BPM reuses the measured features.
7. **Play mix** always plays the full arrangement from the mix playhead. Click
   the timeline ruler to choose a starting point, even before audio is prepared.
   Moving the playhead never starts a render. It seeks in prepared full-mix audio,
   or within the current prepared blend; outside that audio it pauses and explains
   that Play mix will prepare the arrangement. A prominent banner names the task,
   reason and current stage, distinguishing cached audio, new processing and visual
   waveform loading. Cancel remains available for cancellable audio jobs. A first
   full listen may need passages that a short blend preview did not prepare.
   Transition auditions preserve the mix playhead. **Export…** offers a 48 kHz/24-bit WAV master, lossless FLAC,
   or 320 kbps MP3, with size estimates and an optional CSV tracklist. Exported
   files are checked for duration and headroom before replacing the destination.
   The completion message offers playback and file reveal. Rendered audio appears on the Mix lane. A running render can be
   cancelled from the status bar. **File > Save** writes to the current project
   file, and edits also autosave. New and Open keep a recovery copy of unsaved work.
   Recovery remembers the exact project file; a new mix stays unsaved after a
   restart. Older autosaves without this association require Save As once.
   Projects save as format version 8, including source identities, energy ratings,
   order locks/preferences, protected passages, optional blend level balancing,
   library collections and opening/ending fades. Versions 1–7 still open;
   older app builds cannot open version 8 projects.

The library supports Cmd/Ctrl-click, Shift-click and select-all for visible songs.
**Add selected to mix**, Enter, or dragging the selection inserts them in displayed
order as one undoable batch; invalid batches never partly insert. **Collections…**
groups songs within this project, without moving source files. Collection creation,
membership, renaming and removal are undoable. Search names/files/keys and combine
it with a collection plus filters for arranged/unarranged songs, compatible key,
nearby mix tempo, missing metadata or missing source audio. Hidden songs are
deselected. Reimported unchanged sources and identical file copies are reported
and skipped, preserving edits; similar names and different file encodings are
kept separately. Changed sources require explicit reconnection review.

Each song keeps one color. Its clip shows a three-band waveform in shades of that
color (dark lows, mid-tone mids, pale highs), with the crossfade and bass-swap
curves over each overlap. The overview strip above the ruler shows the whole mix;
click it to jump there. Waveform bands are computed once per song, cached in
`library-cache`, and used for display only.

**Mix review**, next to the Library tab, keeps checks and suggestions visible
without replacing the arrangement waveforms. It proposes a shared tempo that
balances the largest speed change, shows per-song before/after percentages,
and flags possible half/double-time BPM labels instead of guessing. Suggestions
are optional and undoable. Preferences start at a 6% tempo-change limit and ±2
semitones for pitch suggestions; zero semitones disables pitch suggestions.
These are user preferences, not measured sound-quality thresholds. Changing
them affects advice only. The review also lists timing concerns, missing files,
and every transition with Show blend/Listen actions. Key advice uses song labels;
it does not establish whether the actual overlap's vocals and melodies fit.

**Balance levels in this blend** is an optional draft setting in the blend editor.
It measures the tempo/pitch-processed audio inside the full overlap, then reduces
only the louder side by at most 3 dB with smooth ramps inside that overlap. It
never boosts quiet intros/tails or compresses the music. Outside the blend, the
usual whole-song loudness matching remains unchanged. Similar levels, very quiet
material and overlaps shorter than one second are left alone. Listen shows the
actual reduction; compare Current / Draft before applying. The setting is saved
per incoming blend, supports Undo, and affects both preview and export. Toggling
it reuses the processed songs; only affected blend assembly needs updating.

**Open blend editor** shows the two real source waveforms aligned to the mix,
including the timing map when correction is enabled. Its cue-based beat/bar grid
is a guide, not detected downbeats. Try overlap and bass-swap changes as a draft;
Listen prepares only this pair, reusing unchanged audio. Outgoing / Blend /
Incoming share one PCM playback clock and switch with a short click-free fade.
Solo preserves each song's mix gain and transition fades. Loop repeats the pair
without restarting the output device. Apply changes keeps the draft as one
undoable edit; Discard draft leaves the mix untouched. This editor's preview
context stops before neighboring blends so only the two named songs are heard.

**Align beats…** opens that pair and checks repeated low-frequency kick attacks.
With the optional beat detector installed it also measures both actual playback
beat rates, identifies a speed mismatch, and uses detected beat positions when
different kick shapes defeat the simpler check. Its measurements are displayed
in the editor and reused while moving B or reopening an unchanged pair. A
finished uncertain check says **Not aligned automatically** and makes no cue move.
**Source BPM** describes the original recording; **Project BPM** controls intended
playback speed. Different-speed recordings need their own correct source labels.
An agreeing estimate tries the smallest beat-phase correction in the draft;
clear repeated strong kick accents can extend the suggestion by whole beats.
ambiguous or drifting observations leave its cue unchanged. A dedicated view
shows real low-frequency envelopes and measured kick candidates for A and B.
Before / Adjusted, dragging B, and 10 ms or one-beat steps share a continuous
looping PCM check without rerunning pitch/tempo processing. Focus on kicks and
the check's even listening levels are audition aids only. Listen or Compare
current / draft prepares the actual production blend before Apply. Cue changes
use the accepted drift map, validate the whole arrangement and remain undoable.
Equally accented kicks do not identify beat 1; no bar-phase claim is made.

**Outgoing finish** offers optional **Filter out** and **Echo out** effects. The
filter gradually thins the outgoing song; the echo fades it early and adds four
quiet, filtered repeats. Both stay entirely inside the overlap, keeping protected
passages clear. They affect preview and export after Apply; source waveforms still
show the original audio. Compare Current / Draft, or solo Outgoing, before choosing.
The effect is saved per incoming blend, supports Undo, and defaults to off. Changing
it reuses pitch/tempo processing and unaffected assembled audio. Level balancing
measures the dry overlap, so it does not boost the filtered sound or echo repeats.

**Try an alternative** offers a shorter or longer overlap when it fits, the other
bass-handling style, and suitable incoming pitch choices within your Mix review
limit. These are starting points to audition. The editor explains the draft's
duration, timing changes for later songs and any pitch change affecting the whole
incoming clip. Pitch advice checks the next blend as well as the current one.
You can refine a choice with the normal draft controls before applying it.

**Compare current / draft** prepares two versions using the production renderer
and matches their perceived listening level with a constant gain per version.
Switch **A · Current mix / B · Draft blend** while playback keeps running on one
PCM clock. Both versions follow the same point in the outgoing song; changing the
overlap intentionally changes where the incoming song enters. Real waveforms
follow the selected version. The common preview excludes neighboring blends,
uses up to four bars of context, and can loop without restarting the output.
**Apply draft to mix** commits one undoable edit; **Back to editing** keeps the
mix unchanged. Comparison gains are for listening only and are not exported.
Comparisons are cached and share the app's storage allowance.

**Find musical cues…** in the clip panel runs local Beat This beat/downbeat
analysis and checks observed four-beat bars against the source BPM. The real
source waveform gains detected bar markers and estimated section changes.
Audition each suggestion, try Start/End in a draft, then Apply changes as one
undoable edit. The original-ending option preserves the tail; earlier endings
state how much audio they omit. Coverage is not an accuracy score. Eight-bar
phrase evidence is shown only when recurring section changes support it.
Neither this detector nor the section scorer identifies vocals, builds or drops.
Analysis alone changes no audio settings, and both raw observations and derived
cues are cached. Changing source BPM rechecks observations without rerunning the
model; changing the source file invalidates them.

The **Check beats and BPM** tab adds a reference click on the same PCM clock as
the original song. Check the beginning, middle and ending; try half/double BPM,
tap a tempo, or mark/nudge a known first beat of a bar. A manual regular grid
extends from that reference and guides cue snapping; it does not warp drifting
audio. Source BPM and grid changes apply to all clips using this song, while
Start/End edits remain clip-specific. The whole draft applies as one undoable
edit after validating all affected overlaps. Original playback stays at source
speed and pitch, with fixed headroom whether the click is on or off. The click
is never included in exported mixes.

**Drum-led passages** inside Find musical cues offers an explicit local scan with
PANNs CNN10. It checks overlapping ten-second windows for drums, voices and pitched
instruments, retaining candidate spans only where all covering windows agree.
These are tentative listening suggestions: scores and repeated observations are
not calibrated accuracy, and absent voice/instrument tags do not prove absence.
The current detector was checked on authored demo drums/music and synthetic speech;
its usefulness across the user's music still needs labeled listening checks.

Listen here plays from the candidate in the original song. Try Start/End aligns a
draft to the detected or manually checked bar grid and states the proposed overlap
length; it may shorten that blend to fit. It respects protected passages and both
neighboring overlaps, never enables drums-only key overrides, and applies as one
Undo action. Trying a candidate clears that passage's previous drums-only label.
The original ending remains available. Observations are cached by
source identity, remain separate from BPM, and follow verified media relocation.

The desktop dependency set includes `beat-this==1.1.0` and matching PyTorch /
torchaudio 2.11.0. Before packaging from source, run
`python download_structure_model.py` and `python download_content_model.py`
to fetch and verify the pinned models.
The app offers that model as an optional download and performs musical analysis offline after installation; songs are
never uploaded. The model and its code are MIT licensed by the
[Beat This authors](https://github.com/CPJKU/beat_this); the bundled model license
is in `assets/models/Beat-This-LICENSE.txt`. On Linux/Windows CPU-only builds,
install the matching CPU PyTorch wheels before the desktop requirements to
avoid bundling an unused GPU runtime. Platform packages still require native testing.

The 25 MB [PANNs CNN10 weights](https://doi.org/10.5281/zenodo.3987831) are
CC BY 4.0; its inference code derives from the authors' MIT-licensed implementation.
Attribution and license are bundled in `assets/models/PANNS-ABOUT.txt` and
`PANNS-LICENSE.MIT`. The app does not download models at runtime or upload songs.

Keyboard: Space plays or pauses, P previews the selected transition, `[` and `]`
step through transitions, Delete removes the selected clip when the arrangement has focus, and Cmd+Z and
Cmd+Shift+Z undo and redo (Ctrl on Windows and Linux). Drag the bar ruler up or
down to zoom and sideways to scroll, or use Cmd+scroll or a trackpad pinch.

The speaker menu in the control bar follows the system default output or routes
playback to a particular device such as AirPods or a USB audio interface. Device
choices are remembered. If an explicitly selected device disconnects, playback
pauses and mutes until that device returns or another output is selected.

### Optional tempo stabilization

New imports estimate BPM using reliable rhythm passages throughout the song.
For an existing arranged track, select it and click **Analyze** in the Sample box.
This preserves the saved BPM and source cues; **Use …** next to the BPM applies
the new estimate explicitly. Integer BPM is suggested only when long beat fits
support it. A small error in the old BPM estimate does not itself trigger warping.

When confident drift is found, **Stabilize** enables gentle correction for
that clip. It is off by default. The Sample box reports how much of the source has
usable correction; sparse or conflicting beat observations are excluded, with
short smooth joins to fixed timing. This detector targets regular EDM kick
patterns and does not establish downbeats or guarantee a correct grid everywhere.
Listen before using a correction in a final mix.

The Sample panel always explains the timing state:

- **Timing not analyzed:** analyze before enabling correction.
- **No significant drift detected:** no substantial drift was detected in reliably
  measured passages. This does not establish that the entire song is steady.
- **Timing inconclusive:** there is no reliable correction map; this does not
  establish that the song is steady either.
- **Correction available:** the on/off state and corrected proportion are shown.
  Coverage is not a confidence score. Compare A/B before deciding.
- **Processing in progress:** timing controls are temporarily paused. This is an
  availability message, not an analysis result.

Hover over the result or use its **?** button for the reason and next steps. A/B
requires at least eight seconds of supported drift within the current cue range;
its tooltip explains when the cues need widening. Repeating Analyze with unchanged
inputs may reuse the same result and does not guarantee a more confident answer.

**A/B** prepares up to 90 seconds from a detected drift
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
stabilization disabled; current saves retain the stabilization setting.

### Processing cache

Processed source excerpts and source loudness measurements persist across runs.
Adding a third track to a previously rendered two-track arrangement reuses the
first two tracks' processed audio. Transposition, source tempo, project tempo,
source-file identity/size/mtime, and source in points govern reuse. Disabled
transposition is equivalent to zero semitones. Changing blend lengths, bass
handoffs, order, or key labels does not invalidate the source transformation.

A full cached track can also serve a shorter preview. If only a short excerpt has
been processed, a later request for more of that song must process the new range.
Prepared mixes and transition previews also persist across runs. Their identities
follow the sound: renaming a song, correcting a key label or adjusting its display
beat grid keeps matching audio playable. An unrelated edit elsewhere in the mix
can leave a prepared transition usable, even if its timeline position moves.
Changes to tempo, enabled pitch shifts, cues or a transition's envelopes prepare
new audio where needed. The renderer reuses unchanged assembled sections and
checks final headroom for each newly assembled mix. The status bar reports reused
songs and the proportion of assembled audio reused.

**File → Storage and cache…** shows usage and free drive space, sets a total cache
allowance (2 GiB by default), and clears unused files. Processed songs, assembled
sections, prepared mixes, listening audio, analysis and real waveform data all
share this allowance. The oldest unused entries are removed after jobs finish;
clearing them means the relevant audio or analysis will be prepared again when
needed. Loaded playback and source files remain protected, including between
app instances. Cleanup waits while processing is active, so usage may temporarily
exceed the limit. Original media, saved projects, exports and the bundled analysis
model are outside this policy. Recognized interrupted working folders are cleaned
up once no job is using the cache. Large audio jobs check free space before making
temporary files; the cache allowance is not a hard limit on a job's working space.

Detailed real waveforms decode in small chunks and retain only compact band
envelopes, so loading a long song does not hold its full decoded audio in memory.
They use the same filters and scaling as before, and existing cached waveforms
are reused. Sources over about five and a half hours retain their source overview
instead of a detailed band waveform. **Cancel processing** in the blocking progress window interrupts running FFmpeg and
Rubber Band processes and waits for their cleanup. Analysis inside Python stops
at its next cancellation checkpoint; an individual model calculation may still
take a moment. A cancelled job cannot publish its unfinished waveform cache.

Timing analysis also streams the decoded audio. Its zero-phase kick filter uses
two temporary passes on disk, with free space checked before starting (about
11 MB per source minute plus the normal free-space reserve). It keeps only the
compact rhythm envelope in memory. Progress names the reading, filtering and
measurement stages, and cancellation removes unfinished analysis files. Fresh
timing analysis supports sources up to four hours; longer sources can still use
ordinary fixed-tempo processing. Compatible cached timing results remain reusable.

Beat, bar and section analysis likewise reads small audio blocks and preserves the
model's original frame alignment and endpoint padding. Its temporary mono decode
uses about 5 MB per source minute, with additional space budgeted for reusable
observations. The decode is removed before beat inference; model inputs and frame
levels are retained in compact arrays. Free space is checked before decoding and
before publishing the observation cache. Musical analysis supports sources up to
four hours; longer sources can use manual beat grids and cue points. Cancelling
at a decode, feature or model checkpoint cleans unfinished work and retains prior
complete observations.

Corrected clips are processed in full on their first preview, then reused by
other previews and exports. Their timing map and both cues participate in the
cache key; changing one track's correction does not invalidate other tracks.
Turning correction off restores the original processing cache. Whole-song timing
analysis is cached by source file, algorithm version and initial BPM. A/B audio
also shares the total cache allowance. Changed source files require fresh timing
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

Install the verified OpenKeyScan runtime after the desktop dependencies and
before packaging:

```sh
.venv/bin/python -m pip install -r requirements-key.txt
.venv/bin/python download_key_model.py
```

Use `.venv\Scripts\python` on Windows. The commands install the tested PyTorch
and librosa versions, plus the exact official `openkeyscan3.pt` checkpoint from
revision `d5cac64bc7195826b6eb10c55b9b0c97fb3287c0`. Its repository MIT license is
saved at `assets/key/LICENSE.openkeyscan`; the unchanged network source and a
license copy live in `vendor/openkeyscan`. The package includes only the active
key model and its license. Packaged users need no downloads or account. Windows/Linux
core packages have passed offline inference checks; physical-device and native-window
validation remains separate. Source installs without this dependency leave key entry available
and explain that detection is unavailable.

New imports use OpenKeyScan for the source-key estimate. **Detect key** in Song settings
(**Check key** in the library menu) retains the current label; **Restore detected**
(**Use estimate** in the library menu) accepts
the proposal as an undoable edit. Choosing a key or **Unknown** is an explicit
manual override, retained through reanalysis and project recovery. Older project
labels are treated as saved choices because their provenance is unknown. Project
version 13 records key ownership and source-bound analysis; versions 1–12 still
open. No key-label edit changes source audio or pitch. Tooltips and Keys and pitch
help explain the estimate's limits and alternate candidates. Numeric model scores
are not presented as a guarantee of correctness.

Focused checks cover manual and Unknown overrides, legacy labels, Undo/Redo,
recovery, cancellation, source/model cache invalidation and readable wrapped
guidance. The OpenKeyScan source and frozen macOS workers reproduced all 24
research scores exactly on three development recordings
(`outputs/openkeyscan-integration/source-parity/report.json` and
`outputs/openkeyscan-integration/release-parity/report.json`).
Changing detectors invalidates old observations without replacing saved song
labels; accepting a new estimate requires checking the key again. In an isolated
copy of the user's mix, native Check key kept the saved label, Use estimate
accepted the new proposal, Undo restored the old label, and keyboard selection
of Unknown persisted as a manual choice. All nine key/help tests and 11 packaged
checks passed. The initial sandboxed package check could not create Numba's
normal macOS cache; rerunning with normal app permissions and an empty developer
PATH passed (`outputs/openkeyscan-integration/dist/package-check-f1058c12/report.json`).
The main app was updated and reopened with the four-song arrangement and all
saved keys unchanged; the only project migration added key ownership metadata.
Installation and rollback paths are recorded in
`outputs/openkeyscan-integration/installation.json`. These are
integration checks; detector accuracy is
reported separately below. Physical Windows/Linux testing is deferred by the
user's current release acceptance; see the package-validation section below.

Run on each target OS and architecture; the script bundles Python, Qt, analysis
dependencies, and the installed audio executables:

```sh
.venv/bin/python -m pip install 'pyinstaller>=6.16,<7'
.venv/bin/python build_desktop.py --check
```

On Windows use `.venv\Scripts\python` for these commands. The result is a macOS
`.app`, Windows executable folder, or Linux executable folder under `dist/`.
Keep the full output folder together on Windows/Linux. Each platform needs its own
build and playback/export test. The local macOS bundle is ad-hoc signed, not notarized.
The macOS builder creates the `.app` directly from the executable and resources;
it avoids an extra distribution folder containing another copy of the model pack.
The macOS arm64 build also includes a locally tested Qt 6.11.2 accessibility
ownership fix: selecting library rows while an accessibility client inspects the
window could otherwise crash the app. Packaging verifies the exact matching Qt
runtime and patch assets, includes the corresponding source and notices, and
re-signs the bundle. Other Mac runtimes require a reviewed rebuild. See
[`vendor/qt-cocoa/README.md`](vendor/qt-cocoa/README.md) for the upstream change,
rebuild instructions and the limits of the native checks.
Engine and dependency licenses apply to any distribution of the bundled binaries.
The interface font, Barlow, is bundled from `assets/fonts` under the SIL Open Font
License (`assets/fonts/OFL.txt`); without those files the app falls back to the
system font.
See [PyInstaller packaging](https://pyinstaller.org/en/stable/usage.html).

`--check` runs the finished executable from a temporary working directory with an
empty PATH. It refuses audio tools outside the bundle, verifies the core model
and lesson assets, exercises offline key inference and reuse, imports a demo song
through the beat-based BPM detector and preserves both tempo alternatives through
project serialization without changing the source, measures pitch/tempo independence and stereo coherence, renders
a bundled demo transition with a tempo curve, exports FLAC and MP3, and decodes
all three formats through Qt's multimedia backend. A failing check fails the
build command. Results and short test audio stay in a new
`dist/package-check-…` directory; `report.json` identifies the OS and architecture.
This command opens no window, plays no sound, and does not access your mix or
preferences. It requires no network connection.

You can repeat the check on another machine with the packaged executable:

```sh
# macOS
"BeatStitch.app/Contents/MacOS/BeatStitch" --check-package /absolute/path/new-check
# Linux
"./BeatStitch/BeatStitch" --check-package /absolute/path/new-check
```

```powershell
# Windows PowerShell
& '.\BeatStitch\BeatStitch.exe' --check-package 'C:\Users\Public\DJ-new-check'
```

Use a new output directory each time; existing directories are refused. The
windowless Windows executable may return control before it exits, so inspect
`report.json` for a terminal `passed` or `failed` status. `build_desktop.py --check`
waits for it. A source development run can use
`python desktop_app.py --check-package /absolute/path/new-check --allow-source`;
its result is explicitly `source-check-passed`, never a verified package.

This is an audio/dependency check, not complete OS validation. On each target
machine, also open the app, import local songs, edit and save/reopen a mix, listen
through its selected output device, test a device disconnect, and export/reopen
audio. Verify native layout, keyboard access and the optional analysis packs there
too. Optional model inference, detection accuracy, musical quality and novice listening sessions are
not established by a passing core package report. The Windows/Linux builds and
both optional workers have passed the separate automated checks below; their
physical devices and native workflows have not been tested. On 2026-10-12 the
user accepted the current platform validation for this release and deferred those
hands-on checks unless a problem is reported. They are no longer a release gate
under that scope decision; automated results do not establish physical playback.

### Beginner tryout and listening validation

This is a **session protocol, not a completed test**. Use a participant who has
not used BeatStitch and has little or no DJ experience. Note the app build, OS,
output device and their experience; use an anonymous participant code. Before
starting, save any existing project. The included demo is a safe practice source;
a second session should use songs the participant is allowed to use, including
generated EDM when available. Demo results alone cannot establish detector or
real-song mixing quality.

Give the participant these outcomes, without pointing to specific controls:

1. Explore the included demo and explain which songs are playing during a blend.
2. Make a new three-song draft, hear it, and decide whether to apply it.
3. Listen to both joins, including songs 2 → 3. Make one overlap shorter, hear the
   difference, then undo the change and check that the earlier version returns.
4. Find the original-key and matched-key choices. Explain what changes and use
   an audible comparison where a pitch change is available. An unavailable
   comparison should be explained, never counted as a completed listening test.
5. Save the editable mix, export playable audio, find and play that file, then
   reopen the saved mix. Explain the difference between Save and Export.

Built-in help and tooltips are allowed. The observer should listen rather than
teach. For each task, record completion, elapsed time, help used, wrong turns,
the participant's own words and any intervention. If the observer supplies a
step, record the task as assisted even if it subsequently succeeds. Do not turn
a failed task into a pass by repeating it after coaching. Note clipped text,
unclear disabled controls, unexpected sound output, stale playback and lost work.

Separately compare the initial and chosen transitions using the app's
loudness-matched Current/Draft playback. Hide the version labels from the listener
and counterbalance which version they hear first across pairs. Record the song
pair, cue ranges, tempo, key/overlap changes, listening device, preferred version
or **no preference**, and why. Ask about rhythmic alignment, competing vocals or
melodies, sudden level changes and audible processing. Do not describe the draft
as the improved version. Preserve both settings so a reported problem can be
reproduced; do not share participants' audio without their permission.

Start with one participant to discover practical issues, then repeat with fresh
beginners after fixes. A useful pilot ends with a playable export, a reopenable
project, both joins reviewed, and reversible edits understood without observer
rescue. Report assistance and listening failures alongside successes. Actual
sessions and real-song comparisons remain required; this protocol and automated
checks do not establish that beginners reliably make excellent mixes.

### Current scope

This is an offline arrangement editor: changes are rendered before auditioning.
It assumes 4/4. A fixed project BPM is the default; the optional **Tempo curve**
sets gradual tempo changes across the mix. Beat/key estimates are editable suggestions;
optional tempo stabilization corrects confidently detected passages. Phrase starts,
vocals and ambiguous rhythms still need review. **Listen** in the blend editor
also checks local harmony from the two processed overlap stems, excluding their
surrounding preview context. It includes applied pitch, tempo, fades, bass handoff
and audible tails. The separate local result leaves short, weak, sparse or varying
evidence uncertain and warns when pitched content contradicts a manual drum-only
mark. It never changes key labels or drum marks. Cached checks follow the prepared
audio and are invalidated when the draft changes. This remains a pitch-profile
heuristic, without calibrated confidence; whole-song key advice elsewhere can differ.
The separate **Check voices in blend** result maps tentative source voice windows
into the actual overlap, including BPM changes, stabilization and echo repeats.
Listening to an unprepared blend also runs this check and reuses cached observations.
Possible simultaneous voices are flagged independently of key labels. Nearby
bar-aligned incoming starts with less observed vocal overlap can be auditioned as
drafts; each says how much source audio and mix duration changes. Apply/Undo and
Current/Draft comparison include the changed incoming cue. Protected passages and
both neighbors must still fit. Voice detection remains uncalibrated and does not
detect competing melodies; no flags are not proof that a blend is vocal-free.
The blend editor's optional **Check musical crowding** adds a separate experimental
check of the two rendered overlap stems. It looks for different moving pitched
patterns that remain audible together, suppressing bass harmonics and very quiet
layers. It is not melody transcription, instrument identification, or a verdict
on musical quality. Chords, arpeggios and voices may trigger it; slow or masked
melodies and leads doubling the bass can be missed. Approximate four-to-eight-second
windows are shown as listening prompts, with no confidence percentage. Overlaps
under four seconds stay uncertain. **Listen to flagged part** auditions the first
window. **Try shorter overlap** offers an unscored draft with unchanged cues and
pitch; use the existing seamless Current/Draft comparison and Apply/Undo. A shorter
blend is not automatically better. Checks are cancellable and cached by the exact
prepared audio, overlap bounds and analysis version; draft edits clear the displayed
result. No additional model, network access or project-schema change is required.
**Find another entrance** checks up to eight supported bar starts with the same
overlap length and processing. A result must reduce both flagged duration and
underlying pitch-activity evidence; a warning-threshold flip alone is insufficient.
Up to two choices can be tried as incoming-cue drafts with explicit source and mix
duration changes. The search checks both neighbors and protection, rejects silent
entries and worse known voice collisions, and keeps four bars outside the blends
(or the original clear duration if shorter). The End and pitch stay fixed and the
old drum-only intro mark is cleared. It reuses completed renders/checks, cancels
without publishing partial choices, and reports when nothing qualifies. New cues
still need listening for phrasing, local keys and content the detectors missed.
**Mix review** shows the separate cached voice result for each pair and counts
possible collisions even with related keys. Pending voice checks are called out
separately. **Review voices…** opens the correct pair; **Check voices…** starts
missing analysis there. Merely opening Mix review never processes audio.
Ordering uses a
bounded search with key/BPM preferences, personal energy ratings, optional audio
activity estimates and explicit constraints. Automatic activity combines attack
density, brightness, spectral spread and sustained level in bounded 20-second
windows; stereo power avoids phase cancellation and gain normalization avoids
mistaking a louder master for higher energy. It summarizes the kept clip, adjusts
attack density to mix tempo and gives estimates half the influence of personal
ratings. Silence, very short clips or unreadable audio remain unknown. This is a
transparent heuristic, not a trained or calibrated model of perceived musical
energy, and ordering is a suggestion rather than a proven optimum. Listen and
override estimates when the emotional feel differs from measured activity.
The supplied first transition was auditioned; new transitions still need listening.

### Extend drums in a mix

Select a clip and choose **Extend drums…** (also in Edit). Choose complete 1/2/4/8-bar
passages near its source beginning or ending, inside the kept cues and outside
protected passages. Choose 1–8 extra copies, optional 5 ms join smoothing, and an
optional neighboring overlap length. A current detected or manually checked bar
grid is required; tentative content labels never certify drums-only material.

**Prepare comparison** auditions either the repeated passage or its adjacent blend
through the production renderer at project BPM/pitch. Current/Extended share one
playback clock and matched listening levels, with real waveforms and repeat
markers. Passage comparisons align at the original loop start; blend comparisons
align at the outgoing ending. The UI explains this alignment and any silent tail
when one version ends first. Changing only the comparison area reuses the copy.

**Apply extended copy** keeps a full-length WAV under the app data `media/loops`
folder and replaces just the chosen clip in one undoable edit. Original audio and
other uses of it stay unchanged. Kept End and later protected times move with the
inserted bars. Accepted tempo correction is baked into the copy; normal project
tempo/pitch processing remains active. BPM/key are inherited, but source-position
analyses and drums-only labels are cleared and must be checked again. Undo restores
the original clip/overlap while keeping the new source in the library for Redo.
Save, normal playback/export, reconnecting media and portable projects use the new
ordinary source. Cache cleanup cannot remove applied media. Draft audio remains
bounded cache data; source changes and cancellation reject stale application.

Limits: individual sources up to 30 minutes, comparison excerpts up to three
minutes, and no claims that uncertain drums/melodies will make a good loop.
The offline **Extend a drum intro or outro** guide explains the workflow and limits.

### Separate drum-loop listening trial

`prototype_drum_loop.py` tries repeating complete bars near a song's beginning or
ending, using an existing detected or manually checked grid. Choose a passage,
1/2/4/8 bars per loop, and one to eight extra copies. **Prepare and listen** plays
the repeated passage with two bars of natural context on either side. **Hear
original passage** auditions that context without repetitions; it restarts the
original excerpt and pauses the repeated version.

**A · Direct repeats / B · Smoothed joins** switches on one continuous playback
clock. Both versions contain the same repetitions, duration and gain. B blends
the five milliseconds before each repeat into the source's natural lead-in;
the downbeat attack, loop duration and final continuation are unchanged. **Hear
first repeat** starts one second before the first splice. The waveform shows
actual amplitude and repeat boundaries. This is a splice treatment, not a
guarantee of a good musical loop. Repeating a fill, melody, voice or unstable
rhythm can still sound wrong. The detector's content labels remain tentative;
manual listening choices are allowed and are explicitly marked uncertain.

The prototype runs at the original speed; it does not apply project BPM,
transposition or tempo stabilization. It renders excerpts only, capped at three
minutes, and never changes the source song or DJ project. Direct insertion into
a mix and full-song extended-copy export are not implemented yet. First listen
to assess whether the repeated passage and return to the song are usable.

```sh
.venv/bin/python prototype_drum_loop.py /path/to/project.djmix --cache outputs/drum-loop-trial/audio-cache
```

The prepared local example is packaged separately as
`outputs/drum-loop-trial/dist/DJ Drum Loop Trial.app` on this Mac. It references
the existing local songs; it is not a portable example with bundled music.
`build_loop_trial.py` packages the prepared example using Qt and FFmpeg, without
inference model assets. Other platforms require their own build and native test.
The trial follows the system audio output, pauses on a device change, and has
its own listening volume. Completed excerpts are reused by source identity and
exact settings. Temporary work is cancellable; cached excerpts use the existing
two-GB default budget with active playback protected from cleanup.

Focused check: `python -m unittest tests.test_drum_loops -q`.

### Optional vocal/drum reduction in a blend

Select a song and choose **Reduce competing parts…** in either inspector mode or
the Edit menu. Choose the blend before or after that song, **Reduce vocals** or
**Keep mostly drums**, and a reduction amount. **Prepare blend comparison** uses
the actual two-song mix, including BPM, pitch, tempo correction and transition
processing. A/B shares one playback clock and matches listening levels.

The treatment changes source samples only inside the selected overlap, with
one-beat fades at its edges (shorter for very short overlaps). It leaves cues and
arrangement timing intact. Protected passages are excluded. Separation is
fallible: listen for missing instruments, voice bleed, hollow tones and softened
drum attacks; try less reduction or keep the current version if it sounds worse.

**Apply reduced-parts copy** saves a durable WAV outside the disposable cache and
replaces only that clip. The original stays in the library, and Undo/Redo restores
either version. Ordinary save, portable project and export workflows use the new
source. Timing/grid positions and key labels are inherited; content/section
observations are cleared. A stem estimate never automatically marks a passage
as drums only. Whole-song loudness matching still applies to the new copy.

The copy retains its treatment at the displayed source times: later cue, order
or overlap edits do not move it. Undo before trying another treatment to avoid
stacking reductions. Check the complete song and both neighboring blends before
export. The F1 guide and tooltips explain these choices and limitations.

Inference runs in an isolated, cancellable local worker using the pinned Spleeter four-stem
model. It processes the overlap with surrounding context, and reuses the same
parts when only mode or reduction changes. Supported limits: 30-minute source
songs and four-minute source overlaps. No audio is uploaded. Prepare is disabled
with an explanation when the optional model/runtime is absent.

Build with `python build_desktop.py --with-separation` from a dedicated build
environment containing `requirements-desktop.txt` and `requirements-separation.txt`.
Run `download_separation_model.py` first at build time; public packages bundle the licensed converted weights. No end-user download or TensorFlow installation is needed. `--with-sections` can also be supplied
when `requirements-sections.txt` and its model pack are installed. The preparer
and standalone listener below remain available for separate experiments.

Focused checks: `QT_QPA_PLATFORM=offscreen python -m unittest tests.test_separation
tests.test_separation_ui tests.test_separation_trial tests.test_help -v`.

### Separate vocal/drum listening trial

`prototype_separation.py` compares a prepared excerpt in a native Qt window:
**Original**, **Without vocals**, **Drums only**, **Vocals only**, and
**Recombined**. Press 1–5 or click a version while it keeps playing. Every version
shares one sample clock, a five-millisecond switching fade, the same waveform
scale, and one common gain. Isolated parts are deliberately not boosted to match
the original's loudness. Space plays/pauses; the slider and waveform seek every
version together. The trial follows the system output and pauses when it changes.

Separation is an estimate. **Without vocals** adds the model's drums, bass and
other accompaniment; it can contain voice bleed or lose instruments. **Recombined**
adds all four estimated parts, exposing the difference from the original. Listen
for lost drum attacks, watery/hollow sound, and unwanted sounds in isolated parts.
The standalone trial applies no tempo change or pitch shift and does not edit
the DJ project. Use **Reduce competing parts…** for the in-app blend workflow.

This historical private scientific comparison uses Demucs 4.1.0; its weights are excluded from the production app. The optional preparer uses Demucs 4.1.0 with the official HTDemucs checkpoint,
pinned by revision and SHA-256. Inference is offline, uses an explicitly loaded
safetensors architecture, and needs no uploaded music. Install the optional
dependencies in a separate environment rather than changing the desktop's:

```sh
python3 -m venv outputs/stem-separation-trial/.venv
outputs/stem-separation-trial/.venv/bin/python -m pip install -r requirements.txt -r requirements-demucs-research.txt
outputs/stem-separation-trial/.venv/bin/python download_demucs_research_model.py
outputs/stem-separation-trial/.venv/bin/python prepare_separation_trial.py /path/to/song.mp3 --start 155 --duration 30
.venv/bin/python prototype_separation.py outputs/stem-separation-trial/example-report.json
```

The preparer accepts 8–60 seconds, analyzes up to eight extra seconds on each side,
then crops all versions to the exact same 48 kHz stereo frame count. Original and
separated audio undergo the same resampling. A shared attenuation leaves two dB
of sample-peak headroom; this is not a final-export true-peak guarantee. Raw float
stems retain their estimated levels. The cache uses source content, passage,
model and preparation settings; it verifies listening-file hashes before reuse.
No partial manifest is published on a handled failure or cancellation. These
standalone trial files are outside the main app's managed cache.

`build_separation_trial.py` packages the prepared example as **DJ Separation
Trial** without the inference runtime or weights. It includes the user's selected
music excerpt for local testing; do not distribute that build as a public demo.
Only the local macOS build has been exercised; other platforms need native builds
and validation. Checks: `QT_QPA_PLATFORM=offscreen python -m unittest
tests.test_separation_trial -v`, plus a real-song render and native listening UI.

### Experimental builds and drops in the native app

The section-analysis edition adds **Song cues… → Builds and drops → Find builds and
drops**. It uses the pinned local EDMFormer model to suggest intros, builds, drops,
breakdowns and outros. A narrow colored strip annotates the real waveform.
Select a section and listen around its boundaries. **Review protection…** fills
the existing protection form; only **Protect this passage → Apply changes** saves
it to the mix. Analysis itself changes no audio settings.

Intro/outro cue suggestions use the detected or manual bar grid, account for both
neighbors and protected passages, and may shorten the affected blend. They report
omitted audio and clear any old drums-only label. An internal outro before another
predicted drop is not offered as an ending. Labels are tentative: listen to the
actual blend after applying, and use Undo or adjust the draft whenever needed.

Completed results are cached by source identity, model/configuration hashes and
analysis version, and saved in project version 11. Old projects still load. BPM,
key, cues and order do not invalidate section observations. Replaced source files
do. A separate cancellable worker releases model memory when finished. All
inference is offline. Individual songs from 2 seconds to 30 minutes are supported;
other cue tools remain available for longer recordings or without this model pack.

The optional pack contains approximately 2.6 GiB of weights. **MuQ weights use
CC BY-NC 4.0**, so this local experimental edition is not a commercial-distribution
clearance, including an automatic clearance for a free app with optional creator support. The exact MusicFM
MSD checkpoint matches the publisher's MIT-labeled model repository; its author
does not confirm all third-party rights for a particular commercial use. Source
revisions, attribution and license details are documented in
[vendor/sections](vendor/sections/README.md).
The default build remains separate from this optional pack.

For a development environment, install `requirements-sections.txt`, then fetch
the pinned assets and build on the target OS:

```sh
python download_section_models.py
python build_desktop.py --with-sections
```

The installer uses immutable EDM-98, MuQ and architecture-config revisions and
requires the exact hashes in `section-model-manifest.json`. Verified existing
files are reused. Downloads are streamed to temporary files; failed or oversized
downloads cannot replace an existing file. Successfully installed earlier files
survive a later failure and are reused on retry. Staging reserves 1 GiB of free
space. The larger training checkpoint is loaded with `weights_only=True`, using
inert placeholders for unused training configuration, then converted to the exact
104 MB inference-only safetensors file already used by the app. Its temporary
418 MB source checkpoint is deleted after verification. No model ID or inference
weights change. A clean installation downloads about 3.1 GB and retains about
2.6 GiB of assets; running it on this development machine reused all seven files.

Alternatively, `python prepare_section_models.py --research outputs/edmformer-research`
copies previously verified local research assets without downloading. The packaged edition
includes inference code, configuration and weights and does not need this research
directory, Python, or a network connection at runtime. Automated Windows/Linux
worker checks passed; hands-on validation is deferred under the current release
acceptance described below. Focused checks:

```sh
python -m unittest tests.test_song_sections tests.test_cue_review -v
```

The integrated CPU run on the 229-second “The Universe Is Ours” took 38.3 seconds;
the completed-result cache took 0.04 seconds including the source probe. It returned
the same section-label sequence as the earlier trial, with two late boundaries
moving by one model frame (about 0.12 seconds) after using the bundled FFmpeg decoder.
These engineering checks do not establish musical accuracy.

### Separate build/drop listening trial

The separate native trial evaluates the published
[EDM-98 / EDMFormer](https://github.com/25ohms/EDM-98) detector independently of the
integrated workflow. Double-click **Launch Section Trial.command** on this development Mac
to review the most recently analyzed song, or pass a particular `sections.json`
to `prototype_section_review.py`. It shows real source waveforms, suggested
sections, original-audio playback and explicit listening ratings. Hear beginning
or ending starts four seconds before that boundary and keeps playing until paused.
The output follows the system device, with a separate volume control. Ratings are
saved beside that report in `listening-review.json`; source music and DJ projects
are not edited. Selecting a row never starts playback or records a rating.

The model trial uses an isolated environment and pinned public model assets under
`outputs/edmformer-research`, **not** the packaged app's dependencies. Re-run it with:

```sh
outputs/edmformer-research/.venv/bin/python prototype_edm_sections.py /path/to/song.mp3 --device cpu
# Apple GPU, where available:
outputs/edmformer-research/.venv/bin/python prototype_edm_sections.py /path/to/song.mp3 --device mps
```

It runs offline after setup. Features are cached by source identity, model revision,
runtime and processing mode. `--attention original --frontend original` retains
the released inference operations for comparison. The optimized path chunks the
convolution with overlapping context and uses efficient attention; on MPS it also
chunks queries while keeping all keys and values. It never shortens the musical
context seen by the transformer. This checkout's research environment is an experiment, not a
self-contained distribution or a claim of Windows/Linux validation.

On the 229-second “The Universe Is Ours” trial, the original CPU run used 19.3 GiB
peak resident memory and took 42.5 seconds. The optimized CPU run used 6.3 GiB and
took 37.5 seconds. The optimized Mac GPU run took 15.0 seconds; its largest sampled
GPU-driver allocation was 4.9 GiB, versus 18.3 GiB before query chunking. GPU samples
are stage observations, not continuous peak measurements, and are not directly
comparable to CPU resident memory. All these runs returned identical section labels
and boundaries. A repeat CPU run reused both encoder feature caches, finished in
1.75 seconds and returned the same predictions. These are single-song engineering checks, **not evidence of musical
accuracy**. Broader listening validation and long-song memory measurements remain.

Focused checks:

```sh
outputs/edmformer-research/.venv/bin/python -m unittest tests.test_section_attention -v
.venv/bin/python -m unittest tests.test_section_review -v
```

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

## Try the bundled desktop demo

Choose **File → Try a demo mix**, or **Try a demo mix** in an empty arrangement.
It opens an editable three-song arrangement and the offline walkthrough. The app
includes original synthesized dance recordings with drum intros/outros, musical
sections, breakdowns and real waveforms. No download, external recording or account
is needed. BPM/key/reference grids are authored values, not analyzer results.

Night Drive (124 BPM, A minor), Signal Bloom (126 BPM, E minor) and First Light
(128 BPM, A minor) mix at 126 BPM with eight-bar overlaps: about 2 minutes 32 seconds.
Playback, blend editing, seamless comparison, Undo and export use the normal engine.
Nothing plays automatically. Save creates your own project; unsaved work from before
the demo remains in Projects → Recovery copies. Demo sources are copied to a stable
app-data folder and are not disposable cache. Save As never edits the bundled template.

`build_demo_assets.py` regenerates the deterministic original audio and portable
template in `assets/demo`. The desktop builder verifies and includes these assets;
`assets/demo/ABOUT.txt` describes their origin and arrangement. This is a practice
mix, not a substitute for listening tests on the user's music.

## Listen to the generated engine demonstration

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

### Measuring key-detection confidence

`benchmark_key_confidence.py` evaluates the current detector against the original
[GiantSteps EDM key annotations](https://www.cp.jku.at/datasets/giantsteps/), pinned
to revision `6bcd492c825ac9b8597bc650a5f6fd18b6c43d2b`. It analyzes the first minute
with the same `analyze_audio.analyze` call used for imported song key estimates.
It verifies downloaded previews against the authors' checksums and stores research
audio only in the chosen directory; use an ignored directory under `outputs`.
The audio is not included in app packages.

```sh
.venv/bin/python benchmark_key_confidence.py outputs/key-benchmark \
  --split calibration --limit 32 --download --workers 2
```

Identical audio hashes stay together in a deterministic, approximately 60/20/20 split for
calibration, validation and test. The shuffled pilot order is fixed before
predictions are inspected. Unknown related recordings/remixes are not identified
by this byte-hash grouping. Leave validation and test untouched while developing
calibration. Omit `--limit` to evaluate the entire selected partition. Omit
`--download` when the verified files are already available. Predictions are reused
only for the same annotations, detector code and recorded dependencies.

Reports retain every attempted record, failures, abstentions, exact-key accuracy,
top-three inclusion and observed accuracy by raw correlation range. Wilson
intervals show sampling uncertainty. The first successful excerpt proves the
experiment runs; a small pilot cannot establish representative accuracy.

This is evaluation infrastructure, not a shipped confidence model. GiantSteps
labels describe the whole preview, so they cannot certify every eight-second
overlap. These measurements also do not establish timing accuracy, voice-detection
accuracy, perceived blend quality, or performance on AI-generated music. Keep
those judgments separate. App correlations remain explicitly uncalibrated until
appropriate held-out evidence supports a scoped calibration.

The fixed 32-recording calibration pilot now compares the same first-minute
excerpts with four detector implementations. None had processing failures:

| Detector | Exact reference keys | Fixed baseline misses | Introduced baseline misses |
| --- | ---: | ---: | ---: |
| Current librosa/profile detector | 13/32 | — | — |
| [libKeyFinder 2.2.8](https://mixxxdj.github.io/libkeyfinder/) | 13/32 | 4 | 4 |
| [madmom key CNN](https://madmom.readthedocs.io/en/v0.16.1/modules/features/key.html) | 20/32 | 9 | 2 |
| [Essentia KeyExtractor](https://essentia.upf.edu/reference/std_KeyExtractor.html), edma profile | 14/32 | 5 | 4 |
| Essentia KeyExtractor, default bgate profile | 15/32 | 7 | 5 |

This is a small development sample, not a held-out accuracy claim or evidence
about the user's songs. Madmom is the stronger candidate for a larger comparison;
it also includes the reference key among its top three outputs on 30/32 excerpts.
However, some wrong predictions have very high softmax scores (one above 0.97).
Neither these scores nor profile correlations are calibrated correctness
probabilities. Both held-out partitions stayed unused during detector selection.
No production key detector has been replaced by these experiments.

The full **359-recording development partition** subsequently completed with no
failures or abstentions. The current detector matched **152/359 (42.3%)** reference
keys; madmom matched **234/359 (65.2%)**, correcting 103 baseline misses and
introducing 21. Its top three candidates contained the reference key on 308/359
recordings. The earlier 32 predictions were reused unchanged; all 359 recordings
have distinct audio checksums. The complete reports are
`outputs/key-benchmark/calibration-359-81dd0400743aaa5b.json` and
`outputs/key-benchmark/madmom-calibration-359.json`. This supports selecting madmom
for independent validation, not a held-out performance claim. At the end of this
development run, all 116 validation and 129 test recordings were still untouched.

The comparison uses `prototype_keyfinder.cpp` and `prototype_keyfinder.py`; it is
not called by the app. The local experiment keeps its pinned libKeyFinder and
FFTW sources, licenses, archive hashes and build artifacts under
`outputs/keyfinder-research`. Neither library is added to the app by this
prototype. Raw per-recording baseline and comparison results are retained under
`outputs/key-benchmark`.

`prototype_madmom_key.py` and `prototype_essentia_key.py` provide the other
comparisons. They accept only completed calibration reports, preserve existing
result files, verify audio checksums, and retain failed/abstained recordings in
the denominator. Optional `--limit 1` makes an initial smoke check. The installed
research dependencies stay outside the app environment; these commands assume
the corresponding isolated packages have been prepared:

```sh
PYTHONPATH=outputs/madmom-research/packages .venv/bin/python prototype_madmom_key.py \
  outputs/key-benchmark/calibration-32-f40e1acf4160016a.json \
  --out outputs/key-benchmark/madmom-comparison.json
PYTHONPATH=outputs/essentia-key-research/packages .venv/bin/python prototype_essentia_key.py \
  outputs/key-benchmark/calibration-32-f40e1acf4160016a.json \
  --out outputs/key-benchmark/essentia-comparison.json
```

For a larger development run, `benchmark_madmom_incremental.py` can follow a
running baseline report and analyze completed batches while the baseline
continues. `--reuse` accepts a previous completed comparison only when its model,
runner, dependencies, recording identities, excerpt durations and baseline
outcomes match. Failed cached estimates are tried again. New inputs and raw
predictions are retained per batch, and the combined report stays `running`
until both detectors cover every pinned development recording. A stalled producer
causes an explicit incomplete result after 20 minutes without new records; it
never becomes a completed subset or silently restarts the baseline.

```sh
PYTHONPATH=outputs/madmom-research/packages .venv/bin/python benchmark_madmom_incremental.py \
  outputs/key-benchmark/calibration-359-81dd0400743aaa5b.json \
  --reuse outputs/key-benchmark/madmom-pilot-32.json \
  --out outputs/key-benchmark/madmom-calibration-359.json
```

Madmom uses official source revision `27f032e8947204902c675e5e341a3faf5dc86dae`
and its pinned model submodule `7e3dc1b0cad499792767074d03c38b194b9b0a79`.
The key model SHA-256 is
`c58ba553be1048877662a663a2670c0051b3c2c66d109b6042ba722ed0bfc7a6`.
Its authors' two key recognition tests passed against the published reference
activations. The [2018 paper](https://arxiv.org/html/1808.05340) describes separate
GiantSteps-MTG training and GiantSteps testing data; this experiment does not
independently audit a per-recording training manifest for the released weights.
The model is CC BY-NC-SA 4.0, and source/model licenses and hashes are retained
in `outputs/madmom-research`.

Essentia uses its official PyPI `2.1b6.dev1389` CPython 3.13/macOS ARM wheel,
verified against published SHA-256
`84e5167b95d9e74b2ddd928555d5a1e11997a458dae25e653544a953bc3068b9`.
The fixed edma and bgate runs use default KeyExtractor settings apart from the
profile selection and explicit 44.1 kHz sample rate; all effective parameters
are saved in the report. Both profiles passed C major/minor synthetic progression
checks. Profiles are published by the library, not fitted on these 32 labels;
their complete training recordings have not been independently audited here.
Keep wheel metadata and license in `outputs/essentia-key-research`. Startup was
unusually slow on this Mac; a live sample showed time spent in native parameter
configuration and locale/time-zone initialization. This needs resolution before
considering this wheel for an interactive app. Neither prototype establishes
Windows/Linux packaging or short-overlap accuracy.

A later [OpenKeyScan](https://github.com/rekordcloud/openkeyscan-analyzer)
comparison uses the same fixed 32 development recordings and first-minute
excerpts. Its official default `openkeyscan3.pt` checkpoint matched **22/32
(68.8%)**, versus madmom's **20/32 (62.5%)**. It corrected four madmom misses and
introduced two; the two detectors agreed on 23/32 keys. All 32 estimates
completed, with no failures or abstentions. Its top three candidates contained
the reference key on 28/32 excerpts, versus madmom's 30/32. This small pilot
supports further comparison, not a conclusive accuracy advantage or replacing
the then-installed madmom detector.

The pinned OpenKeyScan revision is
`d5cac64bc7195826b6eb10c55b9b0c97fb3287c0`; checkpoint SHA-256 is
`5610691be2b3aa21764c5978ca0c2524fcdbf600f09dcbda6c2da937745db600`.
Official source, repository MIT license, checkpoint and download manifest are
retained in `outputs/openkeyscan-research`. The prototype calls the unmodified
official preprocessing and model loader. It verifies source/audio hashes,
all 24 key-class mappings, and sample-identical float WAV round-trips. A
one-recording smoke check passed before the full pilot, with identical scores
when repeated in the pilot. No dependency installation or app changes were
required; inference used the existing ARM64 Python 3.13/PyTorch 2.11 research
environment, not the project's Python 3.12/PyTorch 2.2.2 packaging configuration.

Median processing time was **0.214 seconds per excerpt**, compared with
**1.554 seconds** in the earlier madmom run. These times include decoding and
inference but exclude process startup; different runs and library warm-up mean
they are indicative, not a controlled speed benchmark. Raw results, scores,
disagreements, runtime identity and limitations are retained in
`outputs/key-benchmark/openkeyscan-pilot-32.json`. The official project itself
uses GiantSteps for evaluation; we have not audited the checkpoint's complete
training/model-selection history. This is not an unseen-data accuracy claim,
nor validation on the user's AI-generated songs or short transition passages.

Reproduce the comparison with a new output filename:

```sh
outputs/edmformer-research/.venv/bin/python prototype_openkeyscan_key.py \
  outputs/key-benchmark/calibration-32-f40e1acf4160016a.json \
  --madmom outputs/key-benchmark/madmom-pilot-32.json \
  --out outputs/key-benchmark/openkeyscan-comparison.json
```

The subsequent **complete 359-recording development comparison** matched
**255/359 (71.0%)** keys with OpenKeyScan versus **234/359 (65.2%)** with madmom.
OpenKeyScan corrected 34 madmom misses and introduced 13. All recordings
completed without failures or abstentions; top-three coverage was 310/359
versus 308/359. The detectors agreed on 293 recordings, but only 221 of those
agreements matched the reference key. Agreement therefore cannot be presented
as certainty. This remains development evidence, not a new held-out result.

`benchmark_openkeyscan_incremental.py` reused all 32 pilot results unchanged and
ran only the remaining 327 recordings through the frozen prototype. It checks
the full pinned annotation partition, unique identities, matching excerpts,
madmom reference predictions, source/model/runtime hashes and complete output
coverage before assembling the result. Focused checks verified valid reuse and
rejection of changed models, durations, annotations, reference predictions,
frame counts and duplicate records. The final report is
`outputs/key-benchmark/openkeyscan-calibration-359.json`; the original pilot,
remaining-input snapshots and raw predictions are preserved alongside it.
Median processing time was 0.219 seconds per excerpt, versus 1.582 seconds in
the earlier madmom development run, with the same timing limitations as above.
These development results preceded the app integration described above.

```sh
outputs/edmformer-research/.venv/bin/python benchmark_openkeyscan_incremental.py \
  outputs/key-benchmark/calibration-359-81dd0400743aaa5b.json \
  --madmom outputs/key-benchmark/madmom-calibration-359.json \
  --reuse outputs/key-benchmark/openkeyscan-pilot-32.json \
  --out outputs/key-benchmark/openkeyscan-development-comparison.json
```

The frozen comparison on the remaining partitions subsequently completed:

| Partition | OpenKeyScan exact | madmom exact | Madmom misses corrected | New misses |
| --- | ---: | ---: | ---: | ---: |
| Development | 255/359 (71.0%) | 234/359 (65.2%) | 34 | 13 |
| Validation | 85/116 (73.3%) | 84/116 (72.4%) | 4 | 3 |
| Test | 90/129 (69.8%) | 77/129 (59.7%) | 19 | 6 |
| All recordings, descriptive total | 430/604 (71.2%) | 395/604 (65.4%) | 57 | 22 |

All 245 additional recordings completed without failures or abstentions.
`evaluate_openkeyscan.py` reproduced all 24 scores and decoded audio hashes
exactly on three fixed development recordings before freezing the plan for
both remaining partitions. No model, excerpt, threshold or parameter was
selected using these new results. The checks enforce complete pinned
partitions, no shared audio hashes between partitions, identical excerpts,
valid scores/correctness and preservation of failures in the denominator.
Focused negative checks rejected missing/duplicate recordings, altered
annotations/excerpts/correctness, overlapping audio and changed parity scores.

Results and the pre-inference plan are retained in
`outputs/key-benchmark/openkeyscan-evaluation-245.json` and its `.plan.json`;
`outputs/key-benchmark/openkeyscan-comparison-604.json` links the full comparison
to its hashed evidence. Both validation and test partitions had already been
used to evaluate madmom, so these results are not presented as a newly unseen
test. The full-dataset total includes development data and is descriptive.
Checkpoint training/model-selection provenance, related mixes and relevance
to the user's generated music remain limitations.

OpenKeyScan is now the recommended default candidate: higher exact accuracy
on each partition and shorter observed processing time. It still missed
174/604 reference keys, so the app must continue to label keys as estimates,
preserve saved/manual choices and let the user override them. App-worker score
parity, cache behavior and packaged execution are separate integration checks.
This evaluation does not establish calibrated confidence.

```sh
outputs/edmformer-research/.venv/bin/python evaluate_openkeyscan.py \
  outputs/key-benchmark \
  --development outputs/key-benchmark/openkeyscan-calibration-359.json \
  --madmom-validation outputs/key-benchmark/madmom-validation-116.json \
  --madmom-test outputs/key-benchmark/madmom-final-test-129.json \
  --out outputs/key-benchmark/openkeyscan-remaining-comparison.json
```

`calibrate_key_confidence.py` prepares a source-key confidence experiment using
[temperature scaling](https://proceedings.mlr.press/v70/guo17a.html). It requires
successful 24-key predictions for every recording in the pinned calibration
partition. It rejects pilot subsets, duplicated/mismatched annotations, failed
predictions and validation/test reports. The one-parameter transform preserves
key rankings; it changes the score distribution, not the chosen key.

The resulting JSON is explicitly `fitted_not_validated`. It records the exact
detector, input/annotation hashes, development IDs, fitted temperature, NLL, Brier
scores and ten-bin reliability counts with Wilson intervals. Those fit-set
metrics are not independent evidence of calibration. Freeze the detector and
transform, then evaluate disjoint recordings before using any scores in the app.
This tool neither changes the running app nor claims calibrated overlap harmony,
timing, vocals or generated-music performance.

After completing a full madmom development report, run:

```sh
.venv/bin/python calibrate_key_confidence.py \
  outputs/key-benchmark/madmom-calibration-359.json \
  --annotations outputs/key-benchmark/annotations.zip \
  --out outputs/key-benchmark/madmom-temperature-development.json
```

The existing pilot contains only 32 of the 359 development recordings and is
intentionally refused by this command. Targeted checks in
`tests/test_key_calibration.py` verify a known overconfidence case, unchanged
key decisions, invalid-input rejection and the full-development-data gate.

The completed development fit is retained at
`outputs/key-benchmark/madmom-temperature-development.json`. Its temperature is
**1.3331680769**. On the same data used for fitting, negative log likelihood fell
from 1.411166 to 1.333705 and top-label Brier score from 0.204239 to 0.196930;
all 234 correct key decisions stayed unchanged. These are fit-set measurements,
so the artifact remains `fitted_not_validated`. Source snapshots matching the
reported inference, baseline and fitting hashes are retained under
`outputs/key-benchmark/source-snapshots` for reproducing these results after later
code changes.

`validate_key_confidence.py` runs the next, independent stage. Before processing
validation audio, it reproduces all 24 saved scores on three fixed development
recordings (first, middle and last by ID) and writes a frozen `.plan.json` with
the detector/dependency identities, evaluation source hash, fitted temperature,
recording list and measurement protocol. It accepts only the complete validation
partition, verifies that no audio hashes overlap the fit set, and never refits the
temperature or inspects test recordings. The new wrapper's hash and its reference
detector identity are recorded separately.

The baseline can run in parallel. The final report waits for all baseline
recordings, verifies the same excerpts, retains failures in accuracy denominators,
and compares raw/frozen-temperature log loss, Brier scores and reliability bins.
Incomplete model predictions do not produce a complete confidence measurement.
Choose fresh output paths when repeating an experiment; prior evidence is kept.

```sh
.venv/bin/python benchmark_key_confidence.py outputs/key-benchmark \
  --split validation --workers 2
PYTHONPATH=outputs/madmom-research/packages .venv/bin/python validate_key_confidence.py \
  outputs/key-benchmark \
  --baseline outputs/key-benchmark/validation-116-4987971ccc3f4c45.json \
  --fit outputs/key-benchmark/madmom-temperature-development.json \
  --development-report outputs/key-benchmark/madmom-calibration-359.json \
  --out outputs/key-benchmark/madmom-validation-116.json
```

These commands assume the validation previews have already been downloaded and
checksum verified. A validation improvement is evidence about this protocol and
dataset, not a guarantee for every confidence range, short overlap or generated
song. The separately reserved test partition remains the final check after
decisions based on validation are frozen.

The frozen 116-recording validation run completed without failures or
abstentions. Madmom matched **84/116 (72.4%)** reference keys, versus
**59/116 (50.9%)** for the current detector: 31 mistakes corrected and six
introduced. All 24 scores on the three reference check recordings reproduced
exactly. The paired report and its frozen plan are retained at
`outputs/key-benchmark/madmom-validation-116.json` and the adjacent `.plan.json`.

The confidence result is mixed. With the development temperature unchanged,
validation log loss improved from **1.507045 to 1.413405**, but top-label Brier
score worsened from **0.202336 to 0.211757** (lower is better for both). Multiclass
Brier also worsened, from 0.478886 to 0.486995. This supports the more accurate key
detector, but does **not** support treating the fitted adjustment as ready for
confidence in the selected key. Keep those decisions separate; do not refit on
validation labels or present the adjusted scores as validated app confidence.
The 129 final-test recordings remained undownloaded at that checkpoint.

`select_key_confidence.py` addresses the narrower question the interface needs:
whether the **selected key** is correct. It fits two monotone binary correctness
maps using the 359 development predictions: a two-parameter sigmoid of the raw
top score's log odds, and a three-parameter
[beta map](https://proceedings.mlr.press/v54/kull17a.html). Neither changes the key
decision or pretends to return a new distribution over 24 keys. The models,
input/source hashes and selection rule are saved before loading validation
scores. All previous inference results are reused; no songs are processed again.

The fixed selection rule chooses the lowest validation Brier score among maps
that improve **both binary Brier and binary log loss**, relative to both the raw
score and a constant equal to development accuracy. Temperature scaling remains
a comparison candidate. If none qualifies, the script explicitly selects no
adjustment. Full partition, model identity, sample/excerpt, score and annotation
checks reject incomplete or mismatched evidence; test predictions are refused.

```sh
.venv/bin/python select_key_confidence.py \
  --development outputs/key-benchmark/madmom-calibration-359.json \
  --validation outputs/key-benchmark/madmom-validation-116.json \
  --temperature outputs/key-benchmark/madmom-temperature-development.json \
  --annotations outputs/key-benchmark/annotations.zip \
  --out outputs/key-benchmark/madmom-correctness-selection.json
```

The two-parameter logit map was selected: `sigmoid(0.473458932 * logit(p) -
0.142636721)`. Validation Brier was **0.196411**, versus raw **0.202336**, and
binary log loss was **0.582986**, versus **0.667503**. The beta map's Brier was
0.197492. This is a modest selection-set improvement: the paired bootstrap
interval for the selected-minus-raw Brier difference is **[-0.027318, 0.014372]**,
including zero. The artifact is `selected_pending_final_test`, not a claim of
reliable confidence on unseen songs. Validation is now explicitly used for
method selection; final-test performance must be reported separately.

`evaluate_key_final.py` freezes the selected map and unchanged inference wrapper
before any final-test inference. It requires every one of the 129 reserved test
recordings, verifies no audio hashes overlap either earlier partition, and
compares against the current detector on matching excerpts. Failures remain in
accuracy denominators and prevent a complete confidence measurement. It reports
binary scores, reliability bins and paired uncertainty against raw confidence
and the development constant. There is no automatic app promotion or refitting.

After downloading and checksum-verifying the public test previews, run the
baseline and final evaluator (these can run concurrently):

```sh
.venv/bin/python benchmark_key_confidence.py outputs/key-benchmark --split test --workers 2
PYTHONPATH=outputs/madmom-research/packages .venv/bin/python evaluate_key_final.py \
  outputs/key-benchmark \
  --selection outputs/key-benchmark/madmom-correctness-selection.json \
  --baseline outputs/key-benchmark/test-129-e1d3fc73a713b729.json \
  --out outputs/key-benchmark/madmom-final-test-129.json
```

The baseline filename depends on source and dependency identity; use the path
printed by its command. Keep fresh result paths when repeating an experiment;
repeating or tuning on test results does not create another unseen test. These
source-key measurements still do not calibrate local overlap harmony, timing,
vocal detection or the quality of generated music.

The frozen final test completed on all **129 recordings**, with no failures or
abstentions. Madmom matched **77/129 (59.7%)**, versus **52/129 (40.3%)** for the
current detector: **34 corrected mistakes and nine introduced mistakes**. The
reference key was in madmom's first three choices for 107/129 recordings. Its
exact-accuracy Wilson 95% interval is [51.1%, 67.8%]. The lower result than the
72.4% validation accuracy is retained, not hidden by pooling the partitions.

For the frozen correctness map, final-test binary Brier decreased from
**0.219883 to 0.199691**, and binary log loss from **0.698114 to 0.585519**.
Mean predicted correctness was 64.0%, versus observed 59.7%. The paired
selected-minus-raw Brier interval is **[-0.041933, 0.000483]**; it still includes
zero. The adjustment beats the development-accuracy constant on both metrics,
but the evidence does not establish a conclusive Brier gain over raw scores or
precise reliability in sparsely populated score ranges. Reliability bins retain
their sample counts and Wilson intervals. No further model or calibration
selection was made using these test outcomes.

The complete paired result is
`outputs/key-benchmark/madmom-final-test-129.json`; its adjacent `.plan.json`
contains the frozen formula, original detector identity and exact evaluation
source hash. Both detectors used identical first-minute excerpts, and every
download's published checksum was verified. The final test has now been used;
future tuning requires new independent evidence. This supports integrating the
stronger source-key detector with reviewable estimates, while calibration of
short overlaps, timing, vocals and generated songs remains separate work.
The production app has not been changed by these benchmark scripts.

Dataset citation: Peter Knees, Ángel Faraldo, Perfecto Herrera, Richard Vogl,
Sebastian Böck, Florian Hörschläger and Mickael Le Goff, *Two Data Sets for Tempo
Estimation and Key Detection in Electronic Dance Music Annotated from User
Corrections*, ISMIR 2015. The pinned repository supplies annotations, source links
and checksums; this project does not redistribute its benchmark recordings.

### Measuring BPM detection

`benchmark_tempo.py` measures the actual `estimate_track` BPM path: a first-minute
estimate followed by timing refinement across the complete preview. Only the
unrelated source-key step is stubbed. It records the initial and final BPM,
timing result, application status, source checksum, runtime and code identity.
The frozen source snapshot is retained under
`outputs/tempo-benchmark/source-snapshots/77c301413baf5826`.

The protocol uses the revised labels from the
[GiantSteps tempo dataset](https://github.com/GiantSteps/giantsteps-tempo-dataset),
revision `d51ab2422e76abacfaa86616a57054bc222ec9fd`, with annotation archive SHA-256
`05b908c799be0e616e0632d7019228fc0e424c106c1873c822fa12957d7f0a87`.
Audio checksums determine fixed development/validation/test partitions of
405/120/136 labeled recordings. Three recordings without a tempo label are
listed separately and excluded from BPM accuracy. The 32 development-pilot
recordings were frozen before the first smoke inference, using a deterministic
order unrelated to the estimates. All 32 have distinct audio checksums.

Results retain failed and abstained recordings in the accuracy denominator.
The report includes the standard ±4% Accuracy1 metric and Accuracy2, which
additionally accepts half, double, third or triple tempo. Those multiples are
identified separately because they can still mislead a DJ arrangement. Initial
and final scores show the effect of the existing whole-preview refinement.

The predeclared ±0.1/±0.5 BPM columns describe closeness to the published labels;
they are not precision pass/fail gates. The
[annotation study](https://www.tagtraum.com/download/2018_schreiber_tempo_giantsteps.pdf)
derives labels from human-tap histograms with 1 BPM bins and reports tempo
ambiguity. Its taps also allow phase offsets. This makes the data useful for
global tempo comparisons, but insufficient to validate sub-BPM precision,
exact beat placement or drift-correction quality. No calibrated timing
confidence follows from this pilot.

The completed first pilot is `outputs/tempo-benchmark/app-pilot-32.json`:

| Measurement | Opening estimate | After whole-preview refinement |
| --- | ---: | ---: |
| Within 4% of the labeled BPM | 25/32 | 26/32 |
| Within 4%, allowing half/double/third/triple tempo | 27/32 | 28/32 |
| Within 0.5 BPM of the label (descriptive only) | 5/32 | 5/32 |
| Median absolute difference from the label | 1.0013 BPM | 1 BPM |

All 32 produced an estimate; there were no processing failures or abstentions.
Timing refinement was available for 11 tracks; the other 21 retained the opening
estimate with the app's existing timing-uncertain warning. All six final misses
under the 4% metric were in that uncertain group, including one half-tempo and
one double-tempo estimate. This small development sample does not establish a
reliable confidence threshold or prove that every steady result is correct.

`prototype_beat_this_tempo.py` compares the bundled Beat This `final0` model on
the same fixed recordings. It uses the app's existing audio frontend and minimal
beat postprocessor, then fits 16 consecutive observed beat intervals at a time
to reduce 20 ms frame quantization. It rejects windows with missing/extra beats
or excessive fit residual, takes the median accepted period, and separately
records the effect of the unchanged timing refinement. Neither source labels
nor the earlier app BPM guide the candidate estimate. Half/double tempo is not
silently folded into a preferred range. This is a development prototype; it
does not change imports, saved mixes or the installed application.

The runner verifies audio and model checksums, freezes its input and code
identity before inference, retains reusable beat observations and per-recording
results, and reports corrections and regressions against the existing app.
Failed or abstained candidates count as regressions when the app was correct.
The [model authors](https://github.com/CPJKU/beat_this#available-models) caution
against evaluation on training recordings. Recording-level overlap has not been
audited here, so this comparison makes no unseen-data claim.

A dataset-level provenance check is recorded in
`outputs/tempo-benchmark/model-provenance.json`. The bundled checkpoint identifies
GTZAN as its excluded test dataset, consistent with the published `final0`
description. GiantSteps is not named among the paper's training collections, but
different collections can share recordings; a recording-level overlap check is
still missing. The [training discussion](https://arxiv.org/html/2407.21658v1#S4.SS2)
also explains that the model deliberately produces sharp, overconfident outputs.
Its raw beat strengths are therefore not correctness percentages. Tempo-label
accuracy, exact beat alignment, continuity and downbeat accuracy remain separate
questions.

The paired prototype result is
`outputs/tempo-benchmark/beat-this-pilot-32.json`: 28/32 at the labeled tempo
within 4%, versus the app's 26/32. It corrected all six app misses but introduced
four half-tempo errors on other tracks. All 32 matched if half/double/third/triple
tempo was allowed. Neither runner failed or abstained. Refinement did not change
the candidate's accuracy counts. Those pilot results alone supported a second
opinion; the larger development and validation results below informed the later
import-default decision. None of these results establishes perfect beat matching.
The original candidate code is preserved in
`outputs/tempo-benchmark/source-snapshots/8b001c334449fdc4`.

The same beat-to-tempo calculation now lives in `studio_beat_tempo.py`, with exact
output parity checked against all 32 saved observations. **Analyze song** adds
the independent estimate to cached musical analysis. **Check beats and BPM**
shows the draft and beat-based BPM side by side, explains half/double counting
or disagreement, and offers **Try … BPM**. This changes only the draft reference
grid. Apply is one undoable source edit; Discard preserves the mix. A fresh
source-identity check rejects stale suggestions even if their button was already
visible. Newly analyzed imports also retain an opening-passage estimate. Both
estimates are shown when they disagree, including when the beat estimate is the
current draft BPM. Separate **Try beat** and **Try opening** actions let the user
compare them without applying changes. Existing beat observations are reused; older cue results remain usable
and gain this optional estimate when analyzed again.
At the dialog's minimum size, the beat-controls tab scrolls instead of squeezing
its explanations and number fields. The minimum-size regression checks wrapped
text, control heights and access to the lower controls; it complements native
workflow verification rather than replacing it.

The UI's 2% disagreement boundary is an explanation rule, not a calibrated
confidence threshold or a beat-alignment tolerance. Native visual validation and
installation of this review addition are separate from the already installed
OpenKeyScan update.

```sh
.venv/bin/python -m unittest tests.test_tempo_benchmark
.venv/bin/python benchmark_tempo.py outputs/tempo-benchmark \
  --limit 32 --download --out outputs/tempo-benchmark/app-pilot-repeat.json
.venv/bin/python -m unittest tests.test_beat_this_tempo
outputs/edmformer-research/.venv/bin/python prototype_beat_this_tempo.py \
  outputs/tempo-benchmark --baseline outputs/tempo-benchmark/app-pilot-32.json \
  --out outputs/tempo-benchmark/beat-this-pilot-repeat.json
```

The full 405-track development baseline is
`outputs/tempo-benchmark/app-development-405.json`.
`benchmark_beat_tempo_incremental.py` can process an atomic snapshot of the
completed prefix and resume later with the same output path:

```sh
.venv/bin/python -m unittest tests.test_beat_tempo_incremental
outputs/edmformer-research/.venv/bin/python benchmark_beat_tempo_incremental.py \
  outputs/tempo-benchmark \
  --baseline outputs/tempo-benchmark/app-development-405.json \
  --out outputs/tempo-benchmark/beat-this-development-405.json
```

Its plan fixes all 405 recordings and both detector identities before new
candidate inference. A resume rejects changed code, reordered or missing
records, changed baseline evidence and scores inconsistent with the estimates.
Every attempted outcome remains in the denominator. Reports stay `partial`
until all planned recordings are accounted for; their summaries apply only to
the completed prefix. Existing candidate results are retained on resume.

The pilot's 32 observation caches were reused after verifying identical model,
runtime, preprocessing and inference functions, and the exact extraction of
the beat-to-tempo function into the app module. Candidate results are recomputed
from those observations under the current code identity; old results are not
relabeled as new runs. That reuse is recorded in
`outputs/tempo-benchmark/pilot-observation-reuse.json`.

The full development comparison is now complete. Its checked assessment is
`outputs/tempo-benchmark/development-assessment-405.json`; the original per-track
results remain in the two 405-track reports.

| Measurement (all 405 recordings) | Current importer | Raw beat estimate shown in the review UI | Beat estimate after timing refinement |
| --- | ---: | ---: | ---: |
| Labeled BPM within 4% | 250 (61.7%) | 342 (84.4%) | 343 (84.7%) |
| Within 4%, allowing tempo multiples | 271 (66.9%) | 383 (94.6%) | 384 (94.8%) |
| Half / double tempo instead of the labeled BPM | 10 / 11 | 29 / 12 | 29 / 12 |
| Other mismatches | 134 | 13 | 12 |
| No estimate | 0 | 9 | 9 |
| Processing failures | 0 | 0 | 0 |

The raw beat estimate corrected 123 importer misses but introduced 31 misses;
timing refinement added one correction. This larger development comparison
supports evaluating Beat This as a replacement import detector, with fallback
and explicit correction controls, rather than concluding from the small pilot
that it should only ever be a second opinion.
Steadiness alone cannot certify the BPM: nine importer results and six refined
candidate results marked steady still missed the reference tempo. These are
global BPM comparisons, not precise-grid or confidence-calibration results.

`validate_tempo_detectors.py` then compares the same two detectors on all 120
recordings in the separately selected validation partition. It requires both
full development reports to be finished and their detector identities to match
the current runtime. Before inference, it freezes the validation selection,
development report hashes and runner identity. There is no test-partition option
and no fitting or tempo-range correction in this runner.

```sh
.venv/bin/python -m unittest tests.test_tempo_validation
outputs/edmformer-research/.venv/bin/python validate_tempo_detectors.py \
  outputs/tempo-benchmark \
  --development-baseline outputs/tempo-benchmark/app-development-405.json \
  --development-candidate outputs/tempo-benchmark/beat-this-development-405.json \
  --download --out outputs/tempo-benchmark/validation-120.json
```

The same command resumes after interruption. Both detector outcomes are saved
as a pair; completed failures and abstentions remain in the report, and all
attempted recordings count in accuracy. Input failures are identified separately
from detector failures. A retry of failed recordings requires a new report path
so the previous evidence is preserved; valid per-recording caches are reused.
The report distinguishes the raw beat estimate used by the review button from
the separately refined estimate. Validation results can inform detector
selection, but do not establish exact grid timing, drift-correction quality,
confidence calibration or performance on previously unseen training data.

The completed validation result is `outputs/tempo-benchmark/validation-120.json`,
with its checked assessment in `validation-assessment-120.json` beside it:

| Validation measurement (120 recordings) | Previous importer | Beat-based estimate |
| --- | ---: | ---: |
| Labeled BPM within 4% | 66 (55.0%) | 112 (93.3%) |
| Within 4%, allowing tempo multiples | 75 (62.5%) | 118 (98.3%) |
| Half / double tempo instead of labeled BPM | 2 / 7 | 4 / 2 |
| Other mismatches | 45 | 2 |

Both detectors produced estimates for every validation recording, with no
processing failures. The beat-based candidate corrected 49 previous misses and
introduced three. Timing refinement did not change its accuracy counts. These
results select Beat This as the new import default. An inconclusive
or unavailable beat estimate falls back to the existing opening-passage method;
half/double tempo is never silently folded into a preferred range. Cached beat
observations also supply musical cues without a second model run. Existing
saved BPM and key choices remain unchanged when opening or re-importing a mix.
Project version 14 adds only the source-checked opening-passage observation.
The macOS update is installed. Its 12 frozen-package checks include actual BPM
inference and saved alternatives; native checks exercised both estimate buttons,
Discard, Apply and one-step Undo on a separate mix. The existing four-song mix
reopened with its music settings preserved. Evidence is in
`outputs/tempo-import-integration/installation.json` and its linked reports.

The original benchmark code is retained in
`outputs/tempo-benchmark/source-snapshots/validation-e3b110baaf95c8fb`.
Run that snapshot's scripts to reproduce or resume the old comparison: the
current import implementation intentionally has a different detector identity.
The 136-recording final test partition has not been evaluated by this BPM
experiment. Confidence calibration and exact beat alignment remain unverified.

The focused tests separate octave matches from correct source BPM, check the
tolerance boundaries, and ensure an uncertain timing result cannot hide an
incorrect fallback BPM. Each recording's result is cached against its checksum,
annotation and frozen detector identity; unchanged estimates are reused, while
failed attempts can be retried. Existing reports and plans are preserved.

### Runtime and engine checks

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

### Broad BPM confidence experiment (not installed)

`select_tempo_confidence.py` fits three small monotone logistic candidates on the
405-track development partition, conditional on the 396 beat-based estimates.
It uses accepted-beat coverage, tempo consistency and agreement with the opening
estimate. Reference BPM labels never enter those features. The fixed target is
Accuracy1 within 4%; this is not exact beat, bar-start or correction confidence.

On the separate 120-track validation partition, the three-feature candidate had
Brier 0.06216 versus 0.06673 for the frozen development-accuracy constant, and
binary log loss 0.23141 versus 0.26818. The paired Brier difference's descriptive
95% bootstrap interval was [-0.02065, 0.00968], so an improvement is not established.
The candidate and selection inputs are frozen in
`outputs/tempo-benchmark/confidence-selection{,.plan}.json`.

`evaluate_tempo_confidence.py` runs the reserved 136-track partition using the
unchanged detector source snapshot and matching runtime, saving each paired
outcome. It verifies detector, input, source and plan identities before resuming.
Its acceptance rule is frozen before test inference; no fitting occurs on test
outcomes. Results are written to `outputs/tempo-benchmark/confidence-final.json`;
only `status: finished` accounts for the complete test. Check that report for the
current result rather than interpreting a running prefix. The runner checks free
space before every recording and reuses checksum-verified audio where available.

The frozen final test is now complete: **136 recordings**, **134 beat-based
estimates**, two abstentions and no processing failures. The beat-based route was
within the reference BPM's 4% tolerance for **121/136 (89.0%)**, versus **78/136
(57.4%)** for the earlier opening/refinement route. Only 50/136 beat-based results
were within 0.5 BPM; these labels and this tolerance do not validate precise grids.
Among the 134 estimates, the frozen confidence candidate had Brier **0.068174**
versus the development constant's **0.088959**, and binary log loss **0.244667**
versus **0.324853**. The paired Brier-difference bootstrap interval was
**[-0.035293, -0.007868]**, meeting the pre-registered acceptance rule.

This supports further validation, not immediate numeric confidence in the UI.
For example, the 17 estimates in the 0.50–0.75 band averaged 68.8% predicted
correctness but were correct only 9/17 times (52.9%, Wilson interval 31.0–73.8%).
The 0.90–1.00 band was correct 71/72 times, with a 92.5–99.8% interval. Band sizes
and disagreement remain visible in the report. The test partition has now been
used; no parameters or thresholds were changed using its outcomes. A subsequent
refit requires fresh independent evidence.

Even a passing result needs validation on full songs and AI-generated music;
GiantSteps consists of short EDM previews and model training overlap is unaudited.
Opening-only fallback and user-entered BPMs are outside this confidence model.
The app continues to show qualified estimates and user overrides, without these
experimental numeric confidence scores. Seven focused tests cover feature-label
separation, half/double disagreement, fitting, partition guards, reserved-test
exclusion from selection, resume integrity and final acceptance.

### Windows/Linux package verification workflow

`.github/workflows/desktop-packages.yml` builds the desktop package on Ubuntu 24.04
and Windows Server 2022 runners with Python 3.13.5. The optional-packs input selects
whether to include and exercise build/drop and vocal-separation models; it defaults
on, including for validation-branch pushes. Turn it off in a manual dispatch for
a core-only build. It runs focused packaging, key-runtime, help and provisioning
checks, then builds and exercises the frozen app with `build_desktop.py --check`.
With optional packs included, the installed executable also runs both isolated
workers against generated audio. Jobs have a 35-minute limit and retain small
verification reports for three days; executable bundles are not uploaded by this
workflow. It can be dispatched manually once present on the default branch, or
run from the isolated `codex/package-validation` branch.

The workflow downloads the pinned core models with checksum verification and
regenerates the original demo/listening audio. It does not upload a user's songs,
projects, render cache, benchmark audio or local research environments. Physical
output and native-window checks need separate validation; neither core checks nor
worker execution establish musical quality. Only a report that actually includes
both optional workers validates their execution in the corresponding package.

Windows audio tools are staged with `ci/install_windows_audio.py` from pinned
[FFmpeg 9.0.2 builds](https://github.com/GyanD/codexffmpeg/releases/tag/9.0.2) and
[Rubber Band 4.0.0](https://www.breakfastquay.com/files/releases/), using fixed
archive sizes and SHA-256 values. Only named executables, Rubber Band's
`sndfile.dll`, and license notices are extracted. Existing destinations are
refused and unsuccessful staging is removed. The packager places the Windows
DLL beside the audio executable and includes the downloaded notices.

Use the official [PyTorch CPU installation command](https://pytorch.org/get-started/previous-versions/)
for these runners. The key model accepts both `2.11.0` and the official
`2.11.0+cpu` spelling, while rejecting other releases and unknown local variants.
This compatibility check does not itself prove cross-platform inference equality;
the frozen-app check must actually run on the target platform.

[Run 38213737811](https://github.com/TheMarco/dj-studio/actions/runs/38213737811)
passed on both runners at source commit
`8a2d90146635bb6e10557d4d5ec9132a6543724b`: **26 focused tests and 12 frozen-app
checks per OS**. Both packages ran bundled audio tools, independent pitch/tempo
processing, offline OpenKeyScan and beat-based BPM inference, a gradual-tempo demo
transition, FLAC/MP3 exports and Qt decoding with the developer PATH empty.
Downloaded reports are retained under `outputs/cross-platform-check/run-38213737811`.
The Windows build required the Rubber Band sidecar DLL, CPU-wheel version handling,
portable manifest paths, and `.gitattributes` preserving exact vendored source bytes;
the upstream checksum guard was retained. These results cover the named runners,
not every Windows/Linux release, architecture or physical audio device.

The subsequent full build, [run 38215948485](https://github.com/TheMarco/dj-studio/actions/runs/38215948485),
passed at commit `46128d5a9ac328e9e68b4890c4b7774095f168d9` on both runners:
**36 focused tests, 12 frozen-app checks and both actual optional workers per OS**.
The clean runners downloaded and verified the complete section and separation
packs before building; the installed workers then ran with model hubs offline
and developer paths removed. The section worker returned valid bounded regions;
separation returned four finite stereo stems of the expected length. Linux took
58.3 seconds for sections and 17.2 for separation; Windows took 46.7 and 21.7.
These timings describe the short generated check, not full-song performance.
Reports are under `outputs/cross-platform-check/run-38215948485`, with their hashes
and executable identities in `outputs/cross-platform-check/optional-run.json`.
No application binaries or user audio were uploaded. Passing these workers does
not establish detection accuracy or the sound quality of separated music.

The earlier runtime validation, [run 38273656203](https://github.com/TheMarco/dj-studio/actions/runs/38273656203),
passed on both runners at commit `3d3e452eb2e5ecde2902503300da96eb0a52a429`:
**101 focused tests, 12 frozen-app checks and both optional workers per OS**.
The additional 65 tests cover timing freshness, timing/preview navigation,
transition audio, listening, recommendations, passage content and demo assets.
The preceding run exposed Windows-only test cleanup failures: background waveform
work and Qt players still held temporary fixture files. The fixtures now keep
unrelated waveform work disabled and release their players before deleting files;
this fix changes no production behavior. The corrected local group passed 29
tests, and both complete platform jobs passed without cleanup tracebacks.

Reports, job logs and the exact run metadata are retained under
`outputs/cross-platform-check/run-38273656203`; the verification record is
`outputs/cross-platform-check/pending-dc16e180.json`. All 101 compared runtime,
build/dependency, workflow and corrected-test files matched the local source.
The installed Mac app was unchanged. Only a Mac is available for hands-on checks,
so physical Windows/Linux audio output and native-window usability remain
unverified. The user subsequently accepted this limitation for the current
release on 2026-10-12, deferring physical checks unless a problem is reported.

The subsequent guide-recovery update passed
[run 38283340201](https://github.com/TheMarco/dj-studio/actions/runs/38283340201)
at commit `f453142de38fc81e6edc6071ce709816950807d3`: **112 focused tests,
12 frozen-app checks and both optional workers on each OS**. This includes the
guide's missing-file explanations, restored-source refresh, and protection against
sources disappearing before either Next or Apply. The guide tests now remain in
the platform workflow. Both logs were checked for Python and Qt cleanup tracebacks;
none were found. Linux's headless runner has no PulseAudio output, so these results
do not establish physical playback or device switching. Reports and job logs are
under `outputs/cross-platform-check/run-38283340201`, with source identity and
evidence hashes in `outputs/cross-platform-check/pending-f453142.json`. The tested
guide modules match the installed Mac update; this run changed no installed app.

### Checking optional workers in an existing package

`ci/check_optional_workers.py` runs the actual bundled section and separation
workers from a temporary directory with developer paths removed and model hubs
set offline. It generates its own 12-second source, validates section boundaries
and all four stereo stems, and preserves a small report and diagnostic logs.
Generated audio and temporary model caches are removed afterward. Each worker has
a ten-minute limit with process cleanup; existing report directories are refused.

```sh
python -m ci.check_optional_workers \
  --executable 'dist/BeatStitch.app/Contents/MacOS/BeatStitch' \
  --resources 'dist/BeatStitch.app/Contents/Frameworks' \
  --output outputs/optional-package-check --sections --separation
```

On Windows/Linux, point `--executable` at the built executable and `--resources`
at its neighboring `_internal` directory. Select only the packs included in that
build. This developer-side checker needs NumPy and SoundFile; the application
workers themselves must use their bundled dependencies and weights.

The installed macOS arm64 package passed both workers on the generated example:
11.8 seconds for section analysis on CPU and 6.7 seconds for separation. Evidence:
`outputs/cross-platform-check/mac-optional-workers/report.json`, tied to the
executable's SHA-256. This establishes execution and output shape for that package,
not musical accuracy, vocal-removal quality, long-file behavior or another OS.
Five focused checks cover invalid outputs, real child timeouts, preserved existing
files, recorded failures and isolation from developer paths.

### Measuring the original voice prompts

`benchmark_content.py` measures the original 0.2 voice-prompt policy against human
voice/no-voice annotations in the [Jamendo singing-voice corpus](https://zenodo.org/records/2585988).
It uses only the official 61-song training partition for this development baseline;
the 16 validation and 16 test songs remain reserved. Download the published
`jamando.zip` archive into `outputs/content-benchmark/` before running:

```sh
python benchmark_content.py outputs/content-benchmark/jamando.zip \
  --out outputs/content-benchmark/baseline.json --max-new 1
# Continue the same frozen selection, reusing the measured first song:
python benchmark_content.py outputs/content-benchmark/jamando.zip \
  --out outputs/content-benchmark/baseline.json
```

The runner verifies the published archive size and checksum and freezes its SHA-256,
partition, annotation hashes, detector source, weights, runtime and scoring protocol
before inference. It calls the app's actual analysis without supplying labels or
label-derived audio duration. Only one song is extracted at a time; temporary audio
and caches are removed after each recording. Saved observations and scores are
validated on resume. Interrupted or failed experiments cannot appear complete.
The archive occupies 327 MB; no full expanded dataset is retained or bundled.

The unchanged threshold completed all **61 songs / 238.7 annotated minutes / 2,832
overlapping windows**, with no failed recordings. On the question “does this window
contain any annotated voice?”, there were 641 correct prompts, one unnecessary
prompt, 1,605 missed voice-containing windows and 585 correct negatives. Window
precision was **99.84%**, but recall only **28.54%**. Positive prompts can be useful;
absence of a prompt is very weak evidence that no voice is present.

The app merges positive ten-second windows into listening ranges, which extend
before or after actual words. Evaluating those ranges by annotated time gives
**70.14% precision and 39.39% recall**. This is a different question from window
presence; the ranges must not be presented as exact vocal boundaries. Annotation
gaps remain unknown, never automatic negative labels. A check of one actual vocal
excerpt against the model's original DFT-convolution frontend found a maximum
class-score difference below `1e-7`, so the observed misses are not explained by
that frontend substitution on the checked example.

Evidence is in `outputs/content-benchmark/baseline{,.plan}.json` and
`outputs/content-benchmark/frontend-parity.json`. Six focused tests cover merged
ranges, missing annotations, boundary semantics, invalid inputs, reserved-partition
exclusion and resume integrity. These are development results, not calibration or
proof of generalization to AI-generated EDM. Training-data overlap is unaudited.
The labels do not judge competing vocals, melodies or the musical quality of a
transition. This baseline did not change the app; the subsequent policy evaluation
and update are described below.

### More sensitive possible-vocal suggestions

The app now uses a **0.08 detector-score cutoff** for possible-vocal listening
ranges, chosen on the development partition above. This is a decision threshold,
**not an 8% probability**. It also matches the existing voice veto for drum-led
passages, so a window cannot be recommended as drum-led while being flagged for
possible voice. The original cached classification and raw observations remain
valid: opening an existing project uses the improved suggestions without analyzing
its songs again. Cues, protection choices, rendered audio and saved annotations
are not rewritten.

`validate_voice_threshold.py` froze this single candidate and acceptance criteria
before processing either reserved partition. It required at least 95% window
precision, 80% recall, at most 5% false prompts on non-vocal windows, a recall gain
of at least 25 percentage points, and a positive lower bound on the paired
song-bootstrap recall-gain interval. The 16-song validation partition passed:
recall improved from **33.16% to 93.65%**, precision was **99.62%**, and the false
prompt rate remained **1.41%**. No thresholds were refitted afterward.

The first final-test run correctly refused one inconsistent annotation. In
`03 - Si Dieu.mp3`, both metadata and decoded audio end at about 182.507 seconds,
but the last *no singing* label extends to 272.199 seconds. All earlier boundaries
are inside the file. `repair_voice_reference.py` documented an amendment that
ends only this unavailable non-vocal tail at the audio end, preserving every
earlier label, the detector, the thresholds, and the acceptance criteria. The
original failed report is retained; the other 15 predictions were reused.

After that disclosed data correction, all **16 final-test songs / 763 windows**
were scored. Vocal-window recall rose from **24.03% to 86.40%**, with **99.39%**
precision. There were **3 false prompts among 197 non-vocal windows**, in two
songs. The recall gain was **62.37 percentage points**, with a paired bootstrap
95% interval of **52.48–72.59 points**, resampling entire songs. The fixed criteria
passed. This is a small, non-EDM-specific corpus, not proof that voices in generated
music will be detected reliably or a calibration for user-facing percentages.

The merged listening ranges remain approximate. By annotated time, they covered
**92.00%** of vocal audio but only **67.14%** of the highlighted time contained
voice. Ten-second observations include pauses and neighboring instrumental sound.
Listen and adjust boundaries; overlapping suggestions do not prove that two people
are singing simultaneously. No prompt still does not prove a vocal-free passage.

Reports and frozen plans are `outputs/content-benchmark/voice-validation{,.plan}.json`,
`voice-test{,.plan}.json` (original failure), and `voice-test-corrected{,.plan}.json`.
The original evaluation sources are preserved in
`outputs/content-benchmark/source-snapshots/before-voice-policy`; source and
runtime identity guards intentionally refuse to resume those experiments under
changed detector code. Focused tests cover the data amendment, scoring, test gate,
song bootstrap, cached-observation reuse and downstream overlap advice. This update
adds no model weights and leaves musical decisions with the user.

### Exploratory voice-confidence calibration (not installed)

`calibrate_voice_confidence.py` reuses the raw passage scores and human singing
annotations from the completed Jamendo experiments. It downloads nothing,
extracts no audio, and performs no model inference. It verifies the archive,
official partitions, saved measurements, per-song audio hashes, and the single
documented missing-tail correction. Incomplete results, changed labels or
detectors, and duplicate audio across partitions are rejected. Annotation gaps
stay unknown and are excluded explicitly; they are never negative labels.

The target is **any annotated singing in a fully annotated ten-second source
window**. This is not confidence in exact vocal boundaries, simultaneous vocals,
melody clashes, or a good mix. The single monotone map uses only the saved voice
score as its input. It is fitted to the 61 training songs and written to a frozen
plan before either held-out score report is loaded. There is no method selection
or refitting on validation/test. Both partitions were already inspected for the
earlier voice-threshold experiment, so this is exploratory reuse, **not a new
unseen test**. Neither the existing 0.08 prompt cutoff nor the application changes.

```sh
.venv/bin/python -m unittest tests.test_voice_confidence tests.test_content_benchmark tests.test_voice_threshold
.venv/bin/python calibrate_voice_confidence.py outputs/content-benchmark/jamando.zip \
  --development outputs/content-benchmark/baseline.json \
  --validation outputs/content-benchmark/voice-validation.json \
  --test outputs/content-benchmark/voice-test-corrected.json \
  --out outputs/content-benchmark/voice-confidence-repeat.json
```

Use a fresh output path; earlier evidence cannot be overwritten. A failure after
the plan is frozen is saved as `failed`, including completed measurements, rather
than leaving a misleading running result. The completed experiment and frozen
map are `outputs/content-benchmark/voice-confidence-exploration{,.plan}.json`.

The fitted map is `sigmoid(3.8869527753 * logit(p) + 12.0046087381)`, fitted on
2,832 windows from 61 songs. Inputs are clipped to `[1e-12, 1 - 1e-12]` before
the logit. Its substantial correction shows why raw class scores should not be
displayed as percentages of voice presence.

| Partition | Fully labelled windows | Excluded windows | Raw-score Brier | Mapped Brier | Training-prevalence constant Brier |
| --- | ---: | ---: | ---: | ---: | ---: |
| Validation, 16 songs | 709 | 13 | 0.537890 | 0.031807 | 0.160213 |
| Previously inspected test, 16 songs | 763 | 80 | 0.530807 | 0.058645 | 0.194157 |

Lower Brier is better. The mapping also improves binary log loss over both
baselines on both partitions. Resampling entire songs, with all their overlapping
windows together, gives mapped-minus-constant Brier 95% intervals of
`[-0.165820, -0.090412]` on validation and `[-0.187910, -0.088103]` on test.
Both satisfy the predeclared comparison rule. The report also includes per-song
mean Brier, reliability bins, contributing song counts, and paired comparisons
with the raw scores. These intervals do not include uncertainty from fitting
the map and cannot resolve dataset or training-overlap bias.

Overall improvement does not establish reliable percentages everywhere. In the
test 0.60–0.70 band, the map averaged 64.3%, while only 5/14 windows contained
annotated singing, across eight songs. The 0.90–1.00 band was much stronger:
499 windows across all 16 songs averaged 99.0% and were positive 99.2% of the
time. Sparse and pure bins can have zero-width empirical bootstrap intervals;
that does not prove certainty on future songs. The midrange needs more data.
Fresh independent evaluation on full songs and generated EDM is still needed,
as is validation of actual two-song overlap judgments. The artifact explicitly
sets `ui_promotion_allowed: false`; numeric confidence is not installed.

### OpenKeyScan correctness-confidence exploration (not installed)

`calibrate_openkeyscan_confidence.py` checks the detector currently used by the
app using the existing 604 cached GiantSteps predictions. It fits one monotone
logit map on the 359 development recordings, freezes the model and protocol,
then scores the separate 116- and 129-recording partitions without refitting or
choosing among candidates. Those partitions were previously used to compare
detectors, so this is **exploratory reuse, not fresh independent validation**.
The target is exact agreement with a first-minute source-key label, not local
overlap harmony, musical quality, a full song's changing key or AI-generated
music. Predictions and audio are unchanged; no model inference or download runs.

The fitted map is `sigmoid(0.7591527044 * logit(p) + 0.0967821853)`, with a
fixed probability floor of `1e-12`. Validation Brier changed from **0.183360 to
0.179255**, and test Brier from **0.183311 to 0.180182**. Their paired mapped-minus-raw
95% bootstrap intervals were **[-0.010031, 0.001401]** and
**[-0.009309, 0.002771]**: neither establishes improvement over raw scores. Both
log-loss measurements improved, but the frozen rule requires improvements in
both metrics against raw scores and the development-accuracy constant, with
paired Brier upper bounds below zero on both partitions. **That rule failed.**

Band uncertainty also matters: the validation 90–100% band averaged 93.5% but
matched only 23/27 labels (85.2%, Wilson interval 67.5–94.1%). The corresponding
test band matched 18/18, whose interval is still 82.4–100%. These small samples
cannot justify precise percentages. The app continues to show editable key
estimates; this candidate is not installed or retuned using these outcomes.
Fresh labeled evidence is needed for further confidence claims.

The reproducible report and frozen plan are
`outputs/key-benchmark/openkeyscan-confidence-exploration{,.plan}.json`.
The runner verifies the annotation checksum, exact partition membership,
distinct audio hashes, all 24 scores, saved predictions, reference labels,
excerpt duration and unchanged model identity. It rejects missing/failed rows
and changed inputs instead of publishing a partial success. Nine focused checks
passed across its new integrity tests and the existing fitting tests:

```sh
.venv/bin/python -m unittest tests.test_openkeyscan_confidence tests.test_key_confidence_selection
.venv/bin/python calibrate_openkeyscan_confidence.py \
  --development outputs/key-benchmark/openkeyscan-calibration-359.json \
  --evaluation outputs/key-benchmark/openkeyscan-evaluation-245.json \
  --annotations outputs/key-benchmark/annotations.zip \
  --out outputs/key-benchmark/openkeyscan-confidence-repeat.json
```

### Exact timing and confidence experiment (not installed)

The research folders `outputs/gtzan-timing-feasibility` and
`outputs/gtzan-timing-confidence` contain verified audio, human beat/bar-start
labels, frozen protocols, incremental predictions and a completed 100-excerpt
experiment. This closes the immediate lack of accessible labeled **timing**
audio; it does not complete confidence validation for full generated EDM,
local harmony or simultaneous vocals.

Audio comes from the [ISMIR tutorial GTZAN mini subset](https://github.com/TempoBeatDownbeat/gtzan_mini)
at `a61439e86c13037011fde8e0f0743ec55c50bce3`. Labels are from
[Beat This annotations](https://github.com/CPJKU/beat_this_annotations)
at `890407d158078527ab396b49fea3c8a83e5734ee` (v1.1). Every file was verified
against its pinned Git blob. All 100 WAVs and all 100 label files also match
the checksums in the [official mirdata full-dataset index](https://mirdata.readthedocs.io/en/stable/_modules/mirdata/datasets/gtzan_genre.html).
Audio totals 132,438,456 bytes and stays in the local research folder, outside
the app distribution. No model downloads or installed-app changes were needed.

The unchanged application frontend, Beat This final0 checkpoint, postprocessor,
BPM estimator and coherent-grid filter were evaluated on complete excerpts.
The detector authors [exclude GTZAN from final0 training](https://github.com/CPJKU/beat_this#available-models);
this does not rule out recording overlap with other corpora or upstream model
selection using GTZAN. `mir_eval==0.8.2`, installed only in the research folder,
supplies one-to-one event matching and F1. The primary evaluation includes the
intro; a separate five-seconds-onward result is retained. No label-derived BPM,
phase correction or time offset enters the detector. Seventy milliseconds is
the conventional matching tolerance; 30 ms is a stricter diagnostic, not a
validated threshold for audible quality.

Across all 100 excerpts, including omitted events and abstentions:

| Events | Within 70 ms: precision / recall | Within 30 ms: precision / recall |
| --- | --- | --- |
| Raw beats | 86.9% / 88.8% | 77.0% / 78.7% |
| Raw bar starts | 61.9% / 76.7% | 53.3% / 66.0% |
| Beats retained by the app | 92.2% / 72.2% | 84.1% / 65.9% |
| Bar starts retained by the app | 76.4% / 64.1% | 67.6% / 56.7% |

Precision measures how many predicted events matched a label; recall measures
how many labeled events were recovered. The app retained fewer events, with
higher precision: 78 grids were `supported`, 11 `partial`, and 11 `uncertain`,
with mean source coverage 76.4%. **`supported` means enough internally coherent
observations, not verified bar placement.** Even the supported subset's retained
bar-start precision was 78.1% at 70 ms. This reinforces the need for editable
beat 1, audible grid checking and reviewable cue suggestions. These mixed-genre,
30-second recordings are not a quality rating for the user's EDM mixes; the
ten disco excerpts, for example, had 96.3% retained-beat precision at 30 ms.

For confidence, a monotone sigmoid of raw strength was fitted separately for
beats and bar starts, targeting one-to-one correctness within 30 ms. This target
is conditional event precision, never correctness of an entire grid or blend.
The published [artist/title index](https://github.com/boblsturm/GTZAN) at
`fedc781026cf1a20034830f8071c98d93a422016` defines fixed artist groups; all
classical recordings share one group because performers are unknown. The
already-inspected pilot and the unknown-artist excerpt are descriptive only.
The frozen split contains 78 fit recordings (26 groups), ten validation
recordings (eight groups), and ten test recordings (nine groups). Genre coverage
is uneven, aliases are not comprehensively audited, and the held-out samples
are small. No exact audio-file duplicate crosses partitions.

Both maps were frozen before either held-out partition was inferred. They had
to beat raw scores and the frozen fit-prevalence constant in Brier score and
log loss on **both** partitions, with paired 4,000-resample artist-group
bootstrap Brier upper bounds below zero. **Neither event type passed.** On the
test partition, beat Brier improved from 0.1835 to 0.1314 and bar-start Brier
from 0.3079 to 0.1867, but their mapped-minus-raw intervals were
[-0.1737, 0.0263] and [-0.3326, 0.0802]. Validation also failed the rule.
Raw strengths were plainly not probabilities: test beats averaged 99.0%
strength with 80.3% matching within 30 ms; bar starts averaged 95.4% strength
with 63.3% matching. The app does not display these as accuracy percentages,
and neither experimental map is installed. These partitions are now observed
evidence and must not be relabeled as a fresh future test after further tuning.

One reference boundary error was found before map fitting or held-out
inference: `gtzan_reggae_00002` has a label at 30.117 s beyond its 30.013333 s
audio. Both files match the official checksums. `reference-amendment.json`
documents excluding only that terminal event from the in-audio denominator;
the original label and failed report remain intact. Sensitivity scores retain
it as a missed event, and an assertion verifies that this changes no conditional
30 ms correctness label. The other 77 completed fit results were reused
unchanged; no track was dropped. All 100 rows ultimately completed.

`report.json` records aggregate/per-genre results, coverage, both timing
tolerances, calibration bins and uncertainty, and hashes of every result.
`frozen-maps.json` identifies the fitted maps and unchanged source/model inputs.
Three existing focused calibration checks passed, as did targeted checks for
one-to-one matching, omitted/extra beats, timing tolerances, invalid labels,
the exact documented boundary correction, group-level metric arithmetic and
rejection of a map identical to its baseline. The original mismatch is preserved
under `before-tail-correction`. `ui_promotion_allowed` remains false.

### Short-loop local-key evidence

[Freesound Loop Dataset](https://doi.org/10.5281/zenodo.3967852) provides a closer
reference for short musical passages than whole-song labels. The annotation ZIP
matched its published MD5 (`3920ee437802cf047a990b2968fa066c`). The local audit found
2,821 loops with completed, non-discarded human annotations, including 1,312
with multiple annotators; 269 have unanimous major/minor key labels from at
least two annotators. This is a consensus-selected subset, not a representative
accuracy sample. The [paper](https://arxiv.org/abs/2008.11507) explains that
annotators could start from automatic key suggestions and uploader metadata,
so their judgments are external human review, not blind ground truth.

The pilot selected the first numeric sound ID with a complete 4–8.1 second loop,
before inspecting predictions. Sound 2624 was too long; sound **4049**
(`funkloop113bpm.wav`, RealRhodesSounds, [CC BY 3.0](http://creativecommons.org/licenses/by/3.0/))
lasts 4.247778 seconds and has two D-minor labels. The unchanged local analyzer's
top candidate was D minor, but its correlation was only 0.3368 and the app
returned **uncertain**, offering no retained key. This is a matching raw
candidate and an abstention, not a successfully offered key estimate or a
confidence-calibration result.

`outputs/fsld-feasibility` retains the exact source, labels, frozen protocol,
result, attribution and verification record. Only selected members of the
8.8 GB audio ZIP were retrieved by bounded HTTP ranges; member CRCs and saved
SHA256 hashes are checked. The complete ZIP was neither downloaded nor verified
against its published whole-archive checksum. The reader rejects a server that
ignores Range before consuming a full response. A prefetched ZIP fixture also
verified that standard-library decompression and CRC checks remain in use.

The completed broader experiment is recorded in `outputs/fsld-local-key`.
Its frozen plan fixed the 269 candidates, uploader-separated partitions,
duration exclusions, scoring target and acceptance rule before inference.
The observed pilot's uploader belongs only to fit. All 269 source members were
accounted for; 63 complete loops met the 4–8.1 second rule and all 63 analyses
completed. The other 206 were excluded by duration. Only eligible audio was
retained; no duplicate decoded audio crossed partitions. This required about
318 MB of ranged audio transfer, not an 8.8 GB archive download.

The unchanged local checker returned **uncertain for 60 of 63 loops**, retaining
key estimates for three. One of those three retained sets included the exact
human reference. The other two were neighboring keys on the circle of fifths;
an exact-label mismatch does not establish an audible clash. Where a raw
candidate existed, its top key matched 31 of 62 references. These raw candidates
are not the same as key estimates offered by the application.

| Partition | Eligible loops | Candidate-producing uploaders | Exact raw top-key matches | Raw / mapped / constant Brier |
| --- | ---: | ---: | ---: | --- |
| Fit | 49 | 26 | 24 / 48 | 0.3098 / 0.2497 / 0.2500 |
| Validation | 9 | 8 | 4 / 9 | 0.3334 / 0.2502 / 0.2500 |
| Test | 5 | 5 | 3 / 5 | 0.2605 / 0.2504 / 0.2500 |

A monotone sigmoid fitted only on fit data targeted exact agreement of the
**raw top candidate**, conditional on producing a candidate. Its input was
correlation rescaled to [0, 1], an uncalibrated score. The frozen map became
nearly constant and **failed the acceptance rule**: neither held-out partition
beat the fit-prevalence constant in Brier score or log loss, and the paired
uploader-bootstrap intervals did not establish improvement. The test partition
also had only five contributing uploaders, below the frozen minimum of eight.
`report.json`, `frozen-map.json` and the individual results retain the full
metrics, abstentions, source hashes and uncertainty. Neither this map nor a
new confidence percentage is installed. These partitions are now observed;
further tuning cannot turn them into a fresh test. Short-loop key agreement
also does not measure the quality of an actual DJ blend.

An additional **development-only** comparison in
`outputs/fsld-openkeyscan-development` ran the existing OpenKeyScan app worker
on five fixed fit-partition loops: 4049, 11826, 11828, 19046 and 25769 (four
uploaders). Selection was frozen before that model's inference, but labels and
profile results had already been seen. Each entire loop passed through the
unchanged application entry point, without external padding or repetition.
All five completed. OpenKeyScan matched the exact reference as its first choice
on **1/5**, versus **2/5** raw first choices for the profile checker; the profile
checker abstained from offering a key on all five. OpenKeyScan included the
reference among its top three on **5/5**. Three first-choice errors confused
major with minor at the same root; the fourth was the relative minor. This
tiny, observed development sample neither validates a replacement nor
calibrates its scores. The global song-key detector and local checker are
unchanged; manual correction remains necessary.

A separate label count found 23 unanimously voice-positive loops from 20
uploaders, 1,271 unanimously negative loops, and 18 disagreements among the
multiply annotated recordings. These labels describe whole-loop voice presence;
they provide no exact voice-activity boundaries or judgments of simultaneous
vocal clashes. See `outputs/fsld-feasibility/voice-label-availability.json`.
