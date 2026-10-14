"""Offline help content for BeatStitch.

TOPICS is a sequence of dictionaries with the exact keys ``id``, ``title``,
``keywords`` (space-delimited) and ``body`` (a Markdown string rendered with
Qt QTextBrowser.setMarkdown). Bodies contain no title heading (the UI adds the
topic title), no HTML, no images or external resources, and no tables wider
than two columns. ``{MOD}`` means Cmd on macOS and Ctrl elsewhere; ``{ALT}``
means Option on macOS and Alt elsewhere. Internal links use ``help:<topic-id>``.
"""

from studio_help_course import COURSE_TOPICS

REFERENCE_TOPICS = (
    {
        'id': 'downloads', 'title': 'Choose optional features and downloads',
        'keywords': 'download models optional features install size storage internet offline cancel retry remove missing detector key beat voices drums vocal removal build drop',
        'body': (
            '## Start small\n\n'
            'The installer contains the mixing tools, original practice music and listening lessons. '
            'You can edit the timeline, enter BPM and source keys, change tempo and pitch, prepare blends, '
            'master and export without downloading models. Basic opening-passage tempo estimates also work without a model.\n\n'
            'Open **File → Downloads & features** to choose extra help. Checkboxes select files for **Download selected**. '
            'An installed pack is marked **Ready** and stays available until you remove it. Nothing downloads just because you check a box.\n\n'
            '## What each pack adds\n\n'
            '- **Beat detection — 81 MB:** detected beats and bar starts, whole-song BPM estimates and beat-based cue suggestions. '
            'You can still use manual cues and basic tempo estimates without it.\n'
            '- **Key detection — 19 MB:** estimated source keys and suggestions based on those estimates. '
            'Manual key labels, key compatibility advice and pitch transposition still work without it.\n'
            '- **Voice and drum detection — 25 MB:** possible voices, drum-led passages and overlapping-voice warnings. '
            'These are listening prompts, not guarantees. Ordinary mixing works without it.\n'
            '- **Vocal removal and musical parts — 146 MB download, 157 MB installed:** reduce competing vocals or keep mostly drums in a blend. '
            'This is separate from voice detection and automatic mastering.\n'
            '- **Advanced build/drop detection — about 3.07 GB download, 2.76 GB installed:** experimental labels for builds, drops, '
            'breakdowns, intros and outros. It is unchecked by default. Beat-based cue suggestions do not require this large pack.\n\n'
            'The three small detectors total about **125 MB** and are recommended for a first mix. You can select fewer, '
            'close the window and keep working, or add another feature later. A feature button can offer the particular pack it needs.\n\n'
            '## During a download\n\n'
            'The same blocking progress window used for audio processing shows the feature, stage and transferred bytes. '
            'Wait for it to finish, or use **Cancel processing** to stop safely. The app stays blocked until work actually stops. '
            '**Retry** reuses complete verified files and resumes partial bytes when the server supports it. '
            'If verification fails, the bad partial file is discarded and Retry gets a fresh copy. '
            'An internet connection and extra temporary disk space are needed for installation. The app explains low-space errors.\n\n'
            '## After installation\n\n'
            'Models are stored in the app’s user-data folder, separately from the application and disposable audio caches. '
            'App updates keep them. Analysis is local and works offline after installation; your songs are not uploaded. '
            'Downloading a detector does not replace existing BPM labels, key choices, cues or listening judgments. '
            'Analyze a song again when you want the new detector to check it.\n\n'
            '**Remove** deletes only the chosen downloaded model and its saved partial bytes. Songs, projects, prepared audio and saved observations stay intact. '
            'A model cannot be removed while audio analysis is using the data folder. You can download it again later.\n\n'
            '## Licenses\n\n'
            'Separate downloads retain their original licenses. The large advanced pack includes MuQ weights licensed for noncommercial use '
            'under CC BY-NC 4.0. **Credits & licenses** shows the original notices; a separate download does not change the license terms.\n\n'
            'Continue with [the beginner course](help:beginner-course) or [the first-mix guide](help:quick-start).'
        ),
    },
    {
        'id': 'beat-alignment', 'title': 'Line up the beats in a blend',
        'keywords': 'beat alignment kicks double hits offset earlier later same BPM sync downbeat bar phase drag nudge auto',
        'body': (
            '## Same speed, different beat positions\n\n'
            'Matching BPM makes songs run at the same speed. Their kick hits can still land apart, producing '
            'double hits or a galloping rhythm. Beat alignment moves incoming song B against outgoing song A. '
            'You do not need to calculate or type a source cue time.\n\n'
            '## Try the automatic draft\n\n'
            '1. Select the incoming song or its numbered transition.\n'
            '2. Click **Align beats…** in the Transition box. You can also choose **Align beats** inside **Open blend editor**.\n'
            '3. The app checks repeated low-frequency kick attacks near this overlap. When independent passages agree, '
            'it tries a small correction in the draft and starts a looping beat check. An uncertain result keeps the timing as it was.\n'
            '4. The view shows real low-frequency waveform envelopes and estimated kick hits: A above B. '
            'Matching hits should sit vertically above each other. These markers come from measured audio, '
            'rather than a regularly drawn grid. The playhead follows the check and hits light up as it passes.\n'
            '5. Switch **Before / Adjusted** while it keeps playing. This comparison only changes what you hear and see; '
            'it never commits a change to the mix.\n\n'
            '## Fine-tune by seeing and hearing\n\n'
            'Drag B’s row left to hear it earlier or right to hear it later. **B earlier / B later** make small '
            '10-millisecond steps; the waveform and beat-check audio update immediately without rerunning pitch or tempo processing. '
            'Arrow keys do the same when the view has focus. Zoom in with **+**, or pan with **← / →**, for a closer look.\n\n'
            'If individual kicks coincide but the bar accents feel misplaced, check **By one beat** to try whole-beat steps. '
            'Shift+Left/Right also moves one beat. Equally accented kicks cannot establish which one is beat 1 of a bar; '
            'listen to the accents, phrases and musical changes. **Reset alignment** restores the timing from before this check.\n\n'
            '## Hear the final blend and keep it\n\n'
            '**Hear beats** uses even listening levels before blend fades and bass handoff, so both pulses are easy to hear. '
            '**Focus on kicks** is a listening filter, never an export effect. The check uses the project reference BPM; '
            'an enabled set tempo curve still applies to the final mix.\n\n'
            'Use **Listen** below or **Compare current / draft** to judge the actual blend with its fades, effects and bass handoff. '
            'Then choose **Apply changes** to keep the timing, or **Discard draft** to leave the mix as it was. '
            'Apply is one undoable edit. The original recordings remain untouched.\n\n'
            'The correction adjusts B’s incoming source cue while keeping its source ending fixed. This can omit or restore '
            'a small amount at B’s beginning and move later songs with the changed duration. The app explains this in the draft '
            'summary and checks source limits, neighboring blends and protected passages. A changed cue clears the drums-only intro '
            'label; resetting this alignment restores its prior value.\n\n'
            '## When automatic alignment cannot decide\n\n'
            'Sparse drums, offbeat bass, different kick shapes, half-beat ambiguity or a changing timing gap can make the check '
            'uncertain. Uncertain means no reliable correction was established, not that the songs are aligned. '
            'The pulse correction is within half a beat. When both songs have a clear, repeated strongest kick accent, '
            'the app can suggest a whole-beat move, up to half a four-beat bar, to match those accents. This still '
            'does not prove the musical first beat of a bar. '
            'If the gap grows or changes as the blend plays, recheck [beats and BPM](help:musical-cues) and '
            '[tempo drift](help:timing). One fixed offset cannot straighten a changing tempo.'
        ),
    },
    {
        'id': 'tempo-curve', 'title': 'Gradually change the mix tempo',
        'keywords': 'tempo curve ramp bpm gradual speed bar point time clock pitch undo',
        'body': (
            'Choose **Tempo curve…** next to the main BPM control, or **Edit → Set tempo curve…**. '
            'Turn on **Use gradual tempo changes**. Each row sets a BPM at a musical bar: bar 1 is the start, '
            'and one bar has four beats. For example, 124 BPM at bar 1 and 128 BPM at bar 65 creates a gradual rise over 64 bars. '
            'Equal BPM values make a steady section. After the last point, its BPM is held.\n\n'
            'The first point stays at bar 1. Use **Add point** to add a later point, or **Remove** beside a later point. '
            'Positions must increase. The graph shows the curve across the current mix; '
            'the time below it is the resulting mix length. Large or fast changes may sound unnatural—start with small changes and listen.\n\n'
            '**Apply tempo settings** makes one undoable edit. Cancel leaves the mix unchanged. Turn the checkbox off '
            'to return to the fixed main BPM; the points are remembered. While on, edit BPM in this window. '
            'The main fixed-BPM box is disabled.\n\n'
            '## What stays aligned\n\n'
            'Both songs follow the same curve inside an overlap. Cues, overlap lengths and fades stay in musical bars, '
            'while playback clocks, preview lengths and exported tracklists use elapsed seconds. The timeline is spaced '
            'by bars, so its time labels are not necessarily evenly spaced in seconds. The source files stay unchanged. '
            'Adding or reordering songs does not move the curve points: review the curve after rearranging the set.\n\n'
            'Key is preserved unless you enable a pitch shift. **Stabilize tempo** is separate: it corrects measured drift '
            'inside a song before that song follows the set curve. The standalone timing A/B comparison uses the fixed '
            'reference BPM to isolate that correction; listen to the actual blend or full mix to hear the set curve too. '
            'See [Tempo correction](help:timing), [Arrangement](help:arrangement), and [Projects and export](help:projects).'
        ),
    },
    {
        'id': 'separation',
        'title': 'Reduce competing voices or melodies',
        'keywords': 'separate separation stems vocals voice drums bleed artifacts reduce parts copy undo',
        'body': (
            '## Make room inside one blend\n\n'
            'Select a song in the mix and choose **Reduce competing parts…**, also available in Edit. '
            'Choose its previous or next blend. Only the selected song is treated; the other song is unchanged. '
            'There must be two songs in the mix. [Shortening an overlap](help:transitions) or choosing a different '
            'drum-led passage can sometimes sound more natural than separation.\n\n'
            '**Reduce vocals** keeps estimated drums, bass and other accompaniment. **Keep mostly drums** '
            'keeps estimated drums and reduces the remaining music. Start with a partial reduction, such as 50% '
            'or 75%. At 100%, only the kept estimated parts remain in the middle of the overlap. This percentage '
            'controls blending with the original; it does not promise that percentage of vocal removal.\n\n'
            'The treatment fades in and out over one source beat, shortened if the overlap is very short. '
            'The rest of the source audio is copied intact. Cues, tempo correction, pitch, song positions and '
            'blend settings stay as chosen. Normal whole-song loudness matching still applies to the new copy. '
            'A protected passage cannot be treated: review its protection or change the overlap first.\n\n'
            '## Listen before applying\n\n'
            '**Prepare blend comparison** analyzes only the relevant passage with surrounding context, locally '
            'on your computer. A/B switches continuously between the current and proposed actual mix, with '
            'both songs, project BPM, pitch and blend settings included. Listening levels are matched so louder '
            'does not automatically seem better. Listen for words left behind, missing melodies, softened kicks '
            'or a hollow, watery sound. Reduce the amount or keep the current mix if artifacts are distracting.\n\n'
            'Changing only the amount or mode reuses the separated parts. Changing the source passage prepares '
            'new parts. Closing while busy cancels the work; no change is applied. Output and listening volume '
            'follow the main window. A device change pauses this comparison.\n\n'
            '## Apply and return to the original\n\n'
            '**Apply reduced-parts copy** saves an ordinary WAV and replaces only this clip. The original remains '
            'in the library. **Undo** restores the original clip; Redo uses the prepared copy. The new recording '
            'survives disposable-cache cleanup and travels with a portable project. After reopening a project, '
            'the original recording is still in the library for reuse. No drums-only label is set automatically: '
            'a model may leave melodic or vocal material in estimated drums. Content observations need fresh analysis.\n\n'
            'This is a saved treatment at the source times shown in the window. Changing cues, song order or '
            'overlap length afterward does not move the treatment. Check the complete song and both neighboring '
            'blends before exporting. Use Undo before trying different treatment settings, so reductions do not stack.\n\n'
            '## Availability\n\n'
            'Vocal removal uses Spleeter and its verified local four-stem model. Its code and pretrained weights are MIT licensed. '
            'Install its optional 146 MB download in File → Downloads & features; afterward it works offline. An installation without '
            'those files explains why preparation is unavailable; ordinary mixing still works. The current '
            'limit is a 30-minute source song and a four-minute source overlap. The standalone listening trial '
            'is a separate demonstration; it does not apply choices to a mix.'
        ),
    },
    {
        'id': 'listening-lessons',
        'title': 'Hear common mixing problems',
        'keywords': 'listen lesson example audio teach tutorial beginner drifting beats clashing notes bass handoff',
        'body': (
            '## Learn by switching between two sounds\n\n'
            'Open **Help → Listening examples…**, or use **Listening examples…** at the top of the main '
            'BeatStitch guide. No imported songs are needed. Each example is 16 seconds of original synthesized '
            'music with two versions. Nothing is inserted into your library or arrangement.\n\n'
            'Choose **Listen**, then click **A** or **B** while it plays. The music stays at the same position '
            'on one playback clock. Whole-example loudness is matched, so the louder option should not win '
            'just because of its level. Bluetooth headphones may add a short delay when switching.\n\n'
            '- **Drifting beats:** hear a second drum pattern gradually move away from the steady beat, '
            'then hear the same pattern placed on a regular grid. This illustrates timing drift; it does '
            'not measure how accurately the analyzer will correct your songs. [Timing help](help:timing).\n'
            '- **Clashing notes:** hear a melody against a chord one semitone away, then hear it in the '
            'chord’s key. Real songs can still clash even when their key labels match. [Key help](help:keys).\n'
            '- **Competing basslines:** compare a crossfade with both bass patterns audible against the '
            'app’s one-bar bass handoff. Listen around the middle, ideally on equipment that reproduces '
            'bass. A handoff is a choice to audition, not a requirement. [Blend help](help:transitions).\n\n'
            '## Playback and next steps\n\n'
            'The two real waveforms share a listening cursor. Click either waveform or use the position '
            'slider to seek both versions. **Repeat** loops the example. **Start again** returns both to '
            'the beginning. A/B keys switch versions; Space plays/pauses unless a focused button or '
            'dropdown owns it. Home returns to the beginning. With the position slider focused, arrows '
            'move a quarter second and Page Up/Down move two seconds.\n\n'
            '**Show me the steps** pauses playback and opens help for that problem. The text below each '
            'waveform explains exactly what changed and where to try the relevant controls. These simple '
            'examples teach you what to listen for; they are not proof that a setting will improve every song.\n\n'
            'The output is the device selected in the main window. A disconnected output pauses playback; '
            'reconnect it or go back to choose another device. **Listening volume** affects only lessons. '
            '**Back to my mix** stops the example and preserves your arrangement, settings and playhead. '
            'Main mix playback remains paused. No download, analysis or audio rendering is needed to use lessons.'
        ),
    },
    {
        'id': 'drum-loops',
        'title': 'Extend a drum intro or outro',
        'keywords': 'loop repeat drums extension intro outro longer overlap smooth joins copy bars',
        'body': (
            '## Make room for a blend\n\n'
            'Select a song in the mix and choose **Extend drums…**, or **Edit → Extend selected drums…**. '
            'This adds repeats of an existing passage near the source’s beginning or ending. It does not '
            'generate new music or separate drums from melodies. Choose a steady passage without prominent '
            'voices, pitched tails or a changing melody. A drum-led label is a suggestion to listen, not a guarantee.\n\n'
            'Choose **Loop length** (1, 2, 4 or 8 bars) and **Add** (1–8 extra copies). A four-bar passage '
            'with two extra copies adds eight bars. Repeats go directly after the original passage; the '
            'remaining song follows them. **Smooth joins** blends five milliseconds before each repeat '
            'into its natural lead-in, keeping the new beat in place. Off uses direct joins. A subtle '
            'difference is normal; smoothing cannot fix a bad musical loop.\n\n'
            '## No passages available?\n\n'
            'Loops require complete bars from a current detected or manually checked grid, inside the '
            'clip’s kept audio. Choices stay within the first/last 30% of the source, capped at 90 seconds. '
            '[Protected passages](help:protection) cannot be repeated. Try a shorter loop or the other '
            'end. **Check beats and passages…** closes the unapplied draft and opens [Song cues](help:musical-cues), '
            'where you can check beat 1, analyze bars, find drum-led passages or adjust Start/End. '
            'A beat grid locates bars; it does not prove the passage is only drums or perfectly steady.\n\n'
            '## Hear the result in context\n\n'
            '**Prepare comparison** creates a draft using the actual project tempo, pitch and blend '
            'settings. Use **Hear the repeated passage** to check all added joins, or **Hear the neighboring '
            'blend** to check the handover. There is no neighboring blend before the first or after the '
            'last song. The explanation states where versions align. A/B switches continuously on one '
            'clock; later music can differ because the extension moves it. If one mix ends first, that '
            'version is silent for the remaining comparison time.\n\n'
            'The two waveforms show actual audio. Gold lines mark repeats in the extended version. '
            '**First repeat** selects B and seeks just before its first visible join. Listen for chopped '
            'words, repeated melodies, offbeat kicks and an awkward return to the song. Comparisons use '
            'matched listening levels; these temporary gains are not exported. Preview length is limited '
            'to three minutes; reduce copies or loop length for very long passages. The source song can '
            'be up to 30 minutes long. The main window’s output and volume are used.\n\n'
            '**Neighboring overlap** optionally changes that blend’s bar count. It does not automatically '
            'expand when you add loops. Both overlaps and protected passages still have to fit. The '
            'summary shows the net duration change, including overlap changes.\n\n'
            '## Apply, Undo and keep your audio\n\n'
            '**Apply extended copy** saves a full-length WAV in the app’s media folder and replaces only '
            'the selected clip. Your source stays untouched and remains in the library. Pitch, cues, '
            'protected passages and other clip choices are preserved, with later cue times shifted '
            'around the added audio. Accepted tempo correction is baked into the copy; its correction '
            'switch is then off because that correction is already included.\n\n'
            'The new copy inherits BPM and key labels. Old beat, structure, voice and drum-only annotations '
            'are cleared because file positions changed; check or analyze the new copy before relying '
            'on those. **Undo** restores the original clip and overlap. The added source stays in the '
            'library for Redo or reuse. Other clips using the original source stay unchanged.\n\n'
            'Save stores the editable project with the copy’s location. Normal playback and export use '
            'the copy automatically. Cache cleanup never removes applied audio; **Create portable '
            'project…** includes it when moving to another machine. Closing an unapplied draft discards '
            'its choices; prepared previews are disposable cache files.'
        ),
    },
    {
        'id': 'automatic-mastering',
        'title': 'Automatic mastering: an optional final finishing pass',
        'keywords': 'automatic mastering AI loudness compression limiter peaks dB LUFS match before after export finish dynamics louder',
        'body': (
            '## What it does\n\n'
            '**Mastering: On/Off…** beside Export, or **Edit → Automatic mastering…**, opens an optional '
            'finishing choice for the whole mix. It gently controls dynamics and brief peaks, then raises '
            'average loudness where a restrained peak-reduction allowance permits. Some mixes need little '
            'change. A measured amount of very-low-frequency energy may also trigger 20 Hz cleanup. '
            'It uses established audio processing, not a neural mastering model. Broad tonal EQ and stereo '
            'widening are not applied automatically.\n\n'
            'Mastering is off by default. Your source recordings, cues, BPM, keys and song order stay '
            'unchanged. It does not repair misplaced beats, musical clashes or competing vocals; review '
            'those choices before finishing the mix.\n\n'
            '## Hear a fair comparison\n\n'
            'Choose **Compare before / after** to prepare both versions and start listening. '
            '**A · Current mix** is the mix before this final pass. **B · Mastered** is after it. '
            'Switch A/B while playback continues at the same position, or choose a blend from **Passage** '
            'and enable **Loop**. The real waveform rows show these two rendered versions on one fixed '
            'amplitude scale. Library and source waveforms still show the original recording.\n\n'
            'Leave **Match listening loudness** checked first. It attenuates the louder version so extra '
            'volume does not automatically win. Listen for kick punch, clear vocals, harshness, and '
            'unwanted pumping or smeared attacks. If both sound equally good, no audible quality '
            'improvement has been demonstrated; keeping mastering off is a valid choice.\n\n'
            'Uncheck matching to hear the actual finished loudness. B will often become louder. '
            'The listening-volume slider only adjusts monitoring. Neither matching nor listening '
            'volume changes the mastered export. Hover the level summary for measurements and the '
            'processing summary for the decisions made.\n\n'
            '## Keep your choice\n\n'
            'The top **Use automatic mastering for this mix** checkbox is a draft project setting. '
            '**Apply changes** saves that on/off choice as one Undo action. Comparing A/B alone does '
            'not apply it. **Close** stops listening and discards an unapplied choice. '
            'Applying takes effect when you next prepare or play the mix or export it; it does not '
            'change the audio files you previously exported. The setting survives saving and reopening '
            'the project. Older projects open with mastering off.\n\n'
            'With mastering on, **Play mix**, the main **Listen to blend**, opening/ending previews and '
            'exports use the same finished mix. Blend excerpts come from that whole render rather '
            'than running a different mastering pass on each short clip. The detailed blend editor '
            'and timing/beat tools monitor source or deck audio before final mastering so you can '
            'inspect individual parts and their balance; return to main Listen to blend to judge '
            'the finished result. The export dialog states whether mastering is included.\n\n'
            '## What needs processing\n\n'
            'Turning mastering on reuses the assembled mix and prepared songs where available. It only '
            'adds a final finishing pass. Turning it off reuses the existing mix before mastering. '
            'A/B, matching, loop and seeking use already prepared audio without another audio-processing '
            'job. The task banner states when finishing or comparison preparation is running and lets '
            'you cancel. Editing a song or fade may change whole-mix loudness and require finishing '
            'again; unchanged pitch and tempo work still remains reusable.\n\n'
            '[Check blends](help:transitions) · [Start and ending](help:finishing) · '
            '[Save and export](help:projects)'
        ),
    },
    {
        'id': 'finishing',
        'title': 'Give the mix a clean start and ending',
        'keywords': 'opening ending fade fade-in fade-out tail reverb cut abrupt start finish bars source beginning',
        'body': (
            '## Listen before choosing a fade\n\n'
            '**Edit → Start & ending…** opens a draft of the first and last song. Choose **Opening** or '
            '**Ending**, then use the **Fade in / Fade out** stepper. Each bar is four beats at the mix '
            'tempo; the explanation also shows seconds. **Off** keeps the source level. A song that already '
            'starts or finishes naturally may sound best with fades off.\n\n'
            'The real source waveform shows the kept audio. Its gold curve shows fade volume. '
            '**Listen to opening / ending** prepares this draft automatically and plays enough context '
            'to hear the whole fade. Pause, seek or stop to inspect it. The displayed output device is '
            'the one chosen in the main window; close this dialog to change output or volume.\n\n'
            '## Keep a recorded ending or beginning\n\n'
            'A cut-off cymbal or reverb tail may still be in the original file beyond the End cue. '
            '**Include remaining source ending** extends that cue to the file’s end. The matching Opening '
            'choice extends Start to the file’s beginning. The explanation states how many source seconds '
            'are added. These choices include all remaining audio, including silence or extra music, so '
            'listen first. They cannot recreate a tail missing from the original recording.\n\n'
            'Uncheck a choice before applying to restore the current cue. If it is unavailable, the full '
            'source beginning or ending is already included. For a more precise cut, use '
            '[Song cues](help:musical-cues). Adding source audio can change the mix duration.\n\n'
            '## Why a fade may be limited\n\n'
            'Fades stay outside the first/last blend and all [protected passages](help:protection). '
            'The stepper explains the available number of whole bars. A single-song mix also cannot have '
            'overlapping opening and ending fades. Shorten a fade or adjust the relevant cues/overlap '
            'when the draft reports a conflict.\n\n'
            '**Apply changes** keeps both tabs’ choices as one Undo action. **Close** discards unapplied '
            'changes. These are project settings: they affect normal listening and export, while source '
            'files stay untouched. The arrangement also shows the gold fade curves. Changing a fade '
            'reuses stretched audio and rebuilds only affected listening previews. A fresh arrangement '
            'from Mix guide starts with fades off. [Guided setup](help:first-mix), '
            '[Saving and export](help:projects).'
        ),
    },
    {
        'id': 'demo',
        'title': 'Try the demo mix',
        'keywords': 'demo sample example first mix beginner included music no songs practice walkthrough start',
        'body': (
            '## A complete mix to explore\n\n'
            '**File → Try a demo mix** opens three original synthesized dance songs and a ready-made, editable '
            'arrangement. An empty arrangement also has a **Try a demo mix** button. No account, download or '
            'import is needed. The demo is optional; you can always start with your own songs.\n\n'
            '**Night Drive** is 124 BPM in A minor, **Signal Bloom** is 126 BPM in E minor, and **First Light** '
            'is 128 BPM in A minor. The mix plays at 126 BPM and lasts about 2 minutes 32 seconds. '
            'Pitch shifting and optional blend-level balancing start off.\n\n'
            '## 1. Listen to the whole mix\n\n'
            'Close this guide to return to the arrangement, then press **Play mix** at the top or Space. '
            'Audio is prepared automatically the first time. The visible output device and volume belong to '
            'your normal playback settings; the demo never starts playing by itself.\n\n'
            '## 2. Hear what a blend changes\n\n'
            'The second song is selected first, so **Listen to blend** previews **1 → 2**. '
            'Each overlap starts at eight bars (about 15.2 seconds). Open the **blend editor**, try a shorter '
            'overlap or different bass handling, then **Compare current / draft**. Switch A/B while it plays. '
            '**Apply changes** keeps your choice; **Discard draft** leaves the mix alone. Undo reverses applied edits.\n\n'
            'Both overlaps use authored drum-only intros/outros. Each source is 32 bars: eight bars of drums, '
            'eight with bass and melody, four of breakdown, four returning to the groove, then eight of drums. '
            'The musical layer releases before the final drum section. The real waveforms and regular beat '
            'references let you see these passages. BPM, key and grid come from the composition, so they are '
            'known demo values, not evidence of how accurately the app analyzes unfamiliar music.\n\n'
            '**Steady demo tempo** explains why drift correction is unnecessary for these particular sources. '
            'This authored status disappears if the source identity or its applied BPM changes. '
            'Your own imported songs still use the usual timing analysis and uncertainty messages.\n\n'
            '## 3. Keep or export your version\n\n'
            'Try [Start & ending](help:finishing) if you want a gentler opening or a faded ending. '
            '**Save** asks where to keep your editable project. **Export…** creates a playable audio file; try '
            'the sharing preset or keep a lossless master. The demo source songs are copied to a stable local '
            'folder, so they remain available across app updates. **Create portable project…** packages the '
            'audio with your project if you want to move it.\n\n'
            '## Return to your own music\n\n'
            'Unsaved work from before the demo is in **Projects → Recovery copies**, marked **Before trying '
            'the demo mix**. Saved projects remain under **Recent projects**. Opening the demo again makes '
            'a fresh arrangement and keeps your unsaved version in recovery first. Source songs are never '
            'removed. [Start with your own songs](help:first-mix), [Blending](help:transitions), '
            '[Saving and recovery](help:projects).'
        ),
    },
    {
        "id": "protection",
        "title": "Protect vocals, builds, drops or favorite passages",
        "keywords": "protect protected passage moment lock vocal build drop favorite trim cue overlap keep mandatory",
        "body": (
            "## Mark a passage you want heard clearly\n\n"
            "Select a song, then choose **Protect…** beneath its real waveform, or open **Song cues… → "
            "Protect passages**. Listen to the original and use **Mark start here / Mark end here** at the "
            "white listening cursor, or type the **From / Through** source times in seconds. Choose a type "
            "and optional name, then **Protect this passage**. Vocal, Build and Drop are labels you assign. "
            "Multiple passages may be protected.\n\n"
            "Blue outlines on the source waveform and blue strips on arrangement clips show protected audio. "
            "The Simple song summary reports how many passages are protected. **Listen** beside a listed "
            "passage auditions its original audio. Source files stay untouched.\n\n"
            "## Find possible vocals\n\n"
            "**Find possible vocals** checks the song locally for voice-like sound, including singing and "
            "speech. It shares cached observations with **Find drum-led passages**; checking an unchanged "
            "song again reuses that work. Scroll the protection panel to reach the suggestions.\n\n"
            "**Listen here** starts the original audio at a suggested range and continues playing; use "
            "Pause to stop. **Use this range** fills From, Through and the Vocal label. Review and adjust "
            "the times, then choose **Protect this passage** and **Apply changes**. Selecting a suggestion "
            "alone changes neither the mix nor its protections.\n\n"
            "These are approximate ten-second scan windows, sometimes joined into longer ranges. They "
            "are not precise vocal boundaries. Instruments can be mistaken for voices and vocals can "
            "be missed; a positive-check count is not an accuracy score. No flagged vocals does not "
            "prove the audio is vocal-free. If part of a range is outside Start/End, retain the whole "
            "passage or listen and review a shorter range before protecting it.\n\n"
            "## Find possible builds and drops\n\n"
            "In **Song cues… → Builds and drops**, choose **Find builds and drops**. This experimental model "
            "suggests musical sections; it can miss or mislabel them. Select a row, listen around its start "
            "and end, then choose **Review protection…** to fill the form. Adjust the range and choose "
            "**Protect this passage**, then **Apply changes**. A suggested label alone protects nothing. "
            "The model is included only in the section-analysis edition. Manual marking always works.\n\n"
            "## What protection guarantees\n\n"
            "A protected passage must remain completely between this clip’s Start and End. Neither its "
            "incoming nor outgoing overlap may cover it. These constraints use the same processed sample "
            "positions as the mix, including BPM changes and tempo stabilization. Protection follows this "
            "clip through reordering, is saved in the project, and does not apply to another copy of the song. "
            "It is not a pitch, volume or position lock.\n\n"
            "Cue edits that cut a protected passage are rejected. The overlap stepper and blend editor "
            "respect the protected range and explain the maximum. Order suggestions keep protected songs "
            "included even when a shorter set is requested, and reject positions that would cover a "
            "protected passage. You may still explicitly remove the clip, start a new mix, or replace the "
            "arrangement using Mix guide; those actions remove that clip’s protections too.\n\n"
            "## Resolve a conflict in the draft\n\n"
            "Adding a passage can reveal that an existing blend covers it. The draft remains visible, but "
            "**Apply changes** stays unavailable with the reason shown. Try **Shorten blends to fit**: it "
            "only shortens this song’s adjacent blends and lists their new bar counts for review. It never "
            "changes cues or lengthens a blend. If there is not even one bar of room, widen Start/End, "
            "choose a different song position, or remove the protection if that was your intention. "
            "An opener can protect its beginning and a closer its ending because no blend is needed there.\n\n"
            "**Apply changes** saves the protections, any adjusted adjacent overlaps, and other cue/grid "
            "edits together as one Undo action. **Discard draft** changes nothing in the arrangement. "
            "**Remove protection** only removes a draft mark; apply to keep that choice. Adding or removing "
            "marks alone does not alter the sound or force audio to be processed again.\n\n"
            "This protects the ranges you marked, not everything else a listener might consider important. "
            "Vocal suggestions have not been calibrated across your music. Listen to the actual "
            "transitions before exporting. See [Song cues](help:musical-cues), "
            "[Blends](help:transitions) and [Order planning](help:planning)."
        ),
    },
    {
        "id": "workspace",
        "title": "Simple and Advanced controls",
        "keywords": "workspace simple advanced beginner view mode hidden controls source pitch original key settings",
        "body": (
            "## Start with Simple controls\n\n"
            "BeatStitch starts with **Advanced controls** switched off. Select a song in the arrangement: "
            "the left summary shows its source tempo/key, the speed change in the mix, current pitch, "
            "tempo-correction state and kept audio range. The **real song waveform** remains beside it, "
            "with draggable Start/End markers and zoom controls. The arrangement also keeps its real waveforms.\n\n"
            "**Play in key** changes this clip's pitch: choose a destination, then use the nearby **Listen to blend** "
            "button (or **Listen in mix** for one song). Only keys with the same major/minor mode are offered. "
            "Choose **Original** to reset to zero shift. The source key label is corrected separately in More settings.\n\n"
            "Use **Song cues…** to check beats and musical sections before choosing start/end points. "
            "The arrows move the song earlier/later; **Remove song** takes this clip out of the mix, "
            "leaves the source in your library and can be undone. **Mix guide** helps build a new draft.\n\n"
            "## Adjust a blend and listen\n\n"
            "The transition panel keeps the outgoing/incoming names, key advice, **Overlap** stepper, "
            "seconds, presets and **Bass handoff**. The latter hands low frequencies from one song to "
            "the next to help avoid two bass lines competing. **Listen to blend**, **Loop blend**, "
            "**Review all** and the two-song **blend editor** are available in either view.\n\n"
            "## Original key or pitch shift\n\n"
            "**Keeping the original key** means the source pitch is preserved while BPM is matched. "
            "If a shift is active, the summary names it and offers **Use original key**. Turning it off "
            "remembers the amount; **Restore … shift** turns it back on. **Try … semitone(s)** appears "
            "only when the key-label checks suggest a different nonzero shift within your preferences. "
            "It applies to the selected song and checks both neighboring blends. Listen afterward; "
            "Undo restores your previous choice. A related key label cannot rule out clashing melodies or vocals.\n\n"
            "## Reveal exact settings\n\n"
            "Turn on **Advanced controls** above the arrangement, choose it in **View**, or press "
            "**{MOD}+Shift+A**. **More settings…** in the song summary also opens this view. It adds "
            "exact cue values, source BPM/key editing, manual pitch, timing analysis and correction, "
            "and the drum-only labels. Bars/beats and the 4/4 indicator return to the transport. "
            "Switch it off to return to Simple. This preference is remembered across launches.\n\n"
            "Changing the view does not change any song, cue, overlap, key, correction, selection, "
            "playhead or prepared audio. **Hidden settings remain active**: a pitch shift or correction "
            "is still shown in the Simple song summary. Drum-only labels still affect key advice, "
            "which identifies them as marked beats only. Undo concerns mix edits, not this view preference.\n\n"
            "See [Guided setup](help:first-mix), [Keys and pitch](help:keys), "
            "[Timing correction](help:timing), and [Transitions](help:transitions)."
        ),
    },
    {
        "id": "first-mix",
        "title": "Make a mix with the guided setup",
        "keywords": "first mix guide beginner wizard tutorial choose songs checkboxes draft cancel replace undo missing files refresh reconnect drive",
        "body": (
            "## Choose, listen, then apply\n\n"
            "Choose **Mix guide** above the arrangement or in the **File** menu. An empty arrangement also "
            "offers **Make your first mix**. The guide has four steps and keeps you in control.\n\n"
            "1. **Choose songs.** Check at least two library songs, or use **Add files… / Add folder…**. "
            "**Listen** auditions an original song; the slider, **Start** and **Ending** help you explore it. "
            "Filtering by name does not uncheck hidden songs. A song with no BPM needs a known value entered "
            "with **BPM…** before it can be selected. A missing source is explained beside its song: restore "
            "the file or reconnect its drive, then click **Refresh** and select it. If files moved, close "
            "the guide and use **File → Reconnect source audio…** to locate them.\n"
            "2. **Mix settings.** A suggested common tempo balances the largest speed changes. Read every "
            "song’s percentage change; you can type another BPM. A warning about half/double-time means the "
            "source BPM labels need checking, not that the app has fixed them. **Starting overlap** is a "
            "stepper in bars, with seconds beside it. Short clips receive smaller overlaps, listed in the "
            "review. Original keys are preserved; no pitch shift is applied by the guide.\n"
            "3. **Review the draft.** A proposed order uses key labels, source tempos and optional estimated "
            "energy. Choose an opener, closer, position locks or energy ratings and suggest again. Listen to "
            "each proposed blend before choosing **Create mix** or **Replace arrangement**. "
            "See [Planning](help:planning) for these controls.\n"
            "4. **Listen and export.** **Review each blend** walks through the transitions. "
            "[Start & ending](help:finishing) auditions fades and recorded tails. **Play the whole "
            "mix** prepares audio and plays from the playhead. **Export audio…** opens file-format and "
            "destination choices. Save in the main window keeps the editable project; Export creates audio.\n\n"
            "## What changes, and when\n\n"
            "Before Create/Replace, everything is a draft except library imports. **Cancel** preserves your "
            "arrangement; imported songs remain in the library. Source BPM values entered in the guide apply "
            "only for selected songs when you create the mix. **Stop and close** waits for the current import "
            "step to finish safely. Already imported songs remain available.\n\n"
            "If a selected source disappears while the guide is open, it stops before continuing or "
            "replacing your arrangement and names the missing songs. Restore those files and retry; "
            "your existing arrangement stays intact.\n\n"
            "The guide starts fresh clips using the library’s start/end cues, original keys and a bass handoff. "
            "It does not copy pitch, stabilization, cue or overlap edits from an existing arrangement. "
            "If one exists, the review explicitly says it will be replaced. One **Undo Create mix with guide** "
            "restores the previous arrangement, tempo and edited source BPMs. Imported songs stay in the library. "
            "Your source audio files stay untouched.\n\n"
            "The guide does not guarantee a good musical result: BPM/key labels and estimated cues can be "
            "wrong, and related keys can still have competing vocals. Real waveforms and detailed controls "
            "remain available. Use [Review all](help:transitions) and [Keys and pitch](help:keys) to refine "
            "the result by ear."
        ),
    },
    {
        "id": "planning",
        "title": "Plan song order and set length",
        "keywords": "suggest order planner draft energy gentle build peak pacing duration shorter omit opener closer locks position",
        "body": (
            "## Start with a draft\n\n"
            "Choose **Suggest order** above the arrangement. The planner shows your current songs on the left and "
            "a proposed order on the right after **Suggest draft**. Your mix stays unchanged until **Apply plan**. "
            "**Keep current mix** discards the draft, including any ratings or locks you entered.\n\n"
            "## Choose what stays in place\n\n"
            "The **Opening song** starts the set; the **Closing song** ends it. **Let the planner choose** leaves "
            "that choice open. The current opener is selected by default. **Keep position** holds a song at its "
            "current numbered position and always includes it. These locks apply to suggestions; you can still "
            "drag songs manually. Conflicting choices need a lock released or a different opener/closer.\n\n"
            "## Shape the energy\n\n"
            "Choose gentle changes, a gradual build, or a rise and fall. **Use estimate** lets the app suggest "
            "energy from the audio kept between each clip’s Start and End. After **Suggest draft**, "
            "**Estimated 1/5** means gentle activity and **Estimated 5/5** means very busy activity. "
            "It considers how often sounds start, brightness, how broadly sound fills the spectrum, and how "
            "sustained it is. Simply making a file louder does not raise its estimate. Hover over the result "
            "for details. These are rough activity estimates, not emotional certainty or quality ratings: "
            "a busy hi-hat can be less exciting than a sparse, dramatic drop.\n\n"
            "Choose your own **1 · Gentle** through **5 · Peak** rating whenever the feel differs. Your rating "
            "wins and has more influence on the order. Turn off **Estimate energy from audio when I have not "
            "rated a song** to use manual ratings only. Too little usable audio stays **Unknown**. Key labels "
            "and nearby source tempos still guide the order; current pitch shifts are included in key advice. "
            "Analysis runs locally, reports its progress and can be stopped. Unchanged clips reuse cached "
            "analysis, including when you add another song. Changing cues or replacing the source refreshes "
            "that clip’s estimate. It never changes the audio.\n\n"
            "## Aim for a shorter set\n\n"
            "By default every song is included. **Choose a shorter set; songs may be left out** explicitly allows "
            "a subset of at least two songs, near your target duration. The opener, closer and fixed-position songs "
            "remain included. The draft lists every omitted song, its exact duration and the difference from your "
            "target. It uses whole clips with your existing Start/End, overlaps and tempo; it does not trim songs "
            "to force an exact length. A fixed position near the end can require a longer set.\n\n"
            "## Hear the proposed blends\n\n"
            "Select an incoming song on the right and choose **Listen to 1 → 2** (or the displayed pair). "
            "The preview uses the actual processing and shows both real waveforms. Loop or seek while listening, "
            "then return to the plan. The first song has no incoming blend. Changing a preference clears the old "
            "proposal so you cannot accidentally apply a stale plan.\n\n"
            "## Apply and review\n\n"
            "**Apply plan** changes the arrangement, included songs, ratings, locks and planning preferences as "
            "one named Undo action. Omitted songs stay in the library. Undo restores the previous arrangement. "
            "Applied planning preferences are saved with the project. If an existing drum-only label would cover "
            "new, unverified audio after reordering, the draft explains that the label will be cleared; audio "
            "does not change because of that label.\n\n"
            "The search proposes a valid arrangement; it does not prove an optimal order or musical compatibility. "
            "Key labels do not reveal competing vocals or melodies. After applying, use "
            "[Review all](help:transitions) to decide which blends to keep."
        ),
    },
    {
        "id": "quick-start",
        "title": "Quick start",
        "keywords": "start begin first mix tutorial overview workflow steps new",
        "body": (
            "## Make your first mix\n"
            "\n"
            "For a guided setup, choose **Mix guide** above the arrangement. It walks through song selection, "
            "tempo, overlap, draft listening and export. See [Guided setup](help:first-mix). "
            "The default Simple view keeps real waveforms and everyday actions visible; **Advanced controls** "
            "reveals exact source, pitch and timing settings. See [Workspace views](help:workspace). "
            "To work directly in the arrangement instead:\n\n"
            "1. Add music with **Add folder…** or **Add files…** in the **Library**, "
            "or drop files and folders onto it. Tempo and key analysis runs in the background.\n"
            "2. Drag songs onto the **Arrangement**, or double-click a library row. "
            "Songs alternate between **Deck A** and **Deck B** and overlap automatically.\n"
            "3. Open **Mix review**, next to Library, for a tempo suggestion and settings to check. "
            "Click a clip to edit its **Start**, **End**, tempo and key in the selected song's details.\n"
            "4. Select a blend and press **Listen to blend** (P) to hear it. Use **Loop blend** to repeat it.\n"
            "5. Click **Play mix** to hear the whole arrangement, then **Export…** to save an audio file.\n"
            "\n"
            "Everything works offline, and your original audio files are never changed: "
            "edits only describe how the mix is rendered. **Undo** reverses arrangement, cue, tempo and pitch edits. "
            "Opening projects and importing songs are not part of that undo history.\n"
            "\n"
            "A sense of scale: at 128 BPM in 4/4, one bar lasts 1.875 seconds, "
            "so an 8-bar blend is 15 seconds of music. See [Tempo](help:tempo), "
            "[Library](help:library), [Arrangement](help:arrangement) and [Playback](help:playback)."
        ),
    },
    {
        "id": "mix-review",
        "title": "Mix review and suggestions",
        "keywords": "mix review advice recommend suggested tempo bpm pitch limit prefer conservative warnings fix",
        "body": (
            "## Review before you export\n\n"
            "The **Mix review** tab beside **Library** keeps a current list of settings to check. "
            "It updates when you change the mix and leaves the arrangement waveforms visible. "
            "Its count means items to review, not a rating of how good the mix sounds. "
            "Nothing changes just by opening it.\n\n"
            "## A shared tempo\n\n"
            "The tempo suggestion balances the largest percentage change between the fastest and slowest "
            "songs in your arrangement. **Song-by-song tempo changes** shows the speed change now and at the "
            "suggested tempo. **Use … BPM** applies the proposal to the whole mix and can be undone. "
            "Pitch and source cue choices stay as set. This uses the BPM labels, so check them first if "
            "the suggestion seems wrong. BPM labels about 2× apart are flagged for a half/double-time check; "
            "the app does not guess which one is wrong.\n\n"
            "## Pitch and both neighboring blends\n\n"
            "A pitch change affects the whole clip, including the blend after it. Advice checks both neighbors "
            "and rejects a shift that would turn an already compatible key pairing into a clash. "
            "It can suggest the outgoing or incoming song, and names the song that will change. "
            "**Try … st** applies the suggested shift; use **Listen** afterwards, and Undo if you prefer the original. "
            "**Show blend** selects the incoming clip and shows its overlap controls.\n\n"
            "## Possible overlapping voices\n\n"
            "Each pair also shows a separate voice result from saved passage observations, mapped into "
            "its current overlap. Opening Mix review does not run analysis. A possible vocal collision "
            "adds an item to review even when the key labels are related or you marked a passage as beats only. "
            "**Review voices…** opens that exact blend for listening and optional cue changes.\n\n"
            "Missing or outdated source observations appear as pending voice checks. **Check voices…** "
            "opens the pair and starts the missing analysis there, reusing observations that are still "
            "current. You can stop it in the blend editor. Cue, tempo and effect edits update the voice "
            "warning from those observations without processing the songs again.\n\n"
            "No flagged voices does not prove a vocal-free blend or compatible melodies. Detection can "
            "miss vocals or mistake instruments for them. Pending checks are listed separately from "
            "the flagged-item count; neither number rates the music. Listen before applying any fix.\n\n"
            "## Suggestion preferences\n\n"
            "**Preferred tempo change** starts at 6%. This is a listening preference, not a measured sound-quality "
            "threshold. If no common tempo meets it, the review explains the compromise; choose closer-tempo songs, "
            "check their BPM labels, or adjust the preference. You can always type mix BPM manually.\n\n"
            "**Largest suggested pitch shift** starts at 2 semitones. Set it to zero to turn pitch suggestions off. "
            "Changing preferences affects advice only and does not alter existing audio settings. "
            "Manual pitch controls remain available.\n\n"
            "Pitch suggestions use song key labels, not the exact melody or vocals in an overlap. "
            "Unknown keys remain unknown; a beats-only flag is your description of that passage. "
            "Related key labels do not guarantee a smooth blend. Listen to each transition before exporting. "
            "See [Keys and pitch](help:keys), [Tempo](help:tempo), and [Transitions](help:transitions)."
        ),
    },
    {
        "id": "library",
        "title": "Library",
        "keywords": "library add folder files import songs sort browser waveform analyze again collections select multiple batch duplicates filter",
        "body": (
            "## Adding songs\n"
            "\n"
            "**Add folder…** includes subfolders; **Add files…** adds individual files. "
            "You can also drop files and folders onto the **Library**, drag a song's row "
            "onto the **Arrangement**, or double-click a row to append it to the mix. "
            "Select a row and use **Listen**, **Start**, or **Ending** beneath the waveform to hear the "
            "original song without adding it. The slider seeks within the song and keeps paused audio paused. "
            "Focus it and use **Left/Right** for one second, **Page Up/Down** for ten seconds, or **Home/End** "
            "for the beginning/end. Choose **Listen** when ready. This plays original tempo "
            "and pitch; mix processing only applies when you listen to the arrangement.\n"
            "\n"
            "## Several songs at once\n\n"
            "Use **Cmd-click on Mac / Ctrl-click on Windows or Linux** to pick individual songs, "
            "**Shift-click** to select a range, or **Cmd+A / Ctrl+A** while the library has focus to select all visible songs. "
            "**Add selected to mix**, **Enter**, or dragging the selection inserts the songs in the current list order. "
            "Sort a column first if needed. Drag onto the arrangement to choose the insertion point. "
            "One **Undo** removes the entire batch. If any song needs a BPM or the cues cannot fit, no songs are added; "
            "enter missing tempos through [Mix guide](help:first-mix) or add that song alone. "
            "You can intentionally insert a library song more than once.\n\n"
            "## Collections and filters\n\n"
            "**Collections… → New collection from selection…** saves a group of songs in this project. "
            "The same menu adds selected songs to an existing collection, removes membership, renames, or deletes a collection. "
            "Deleting a collection keeps its songs and source files. Every collection change is undoable and saved with the project. "
            "Use **All collections** to see the complete library again.\n\n"
            "Search names, filenames, key names or Camelot codes (for example **8A**). Combine search with a collection and "
            "**Not yet in the mix**, **Already in the mix**, **Key fits selected mix song**, **Within 6% of mix tempo**, "
            "**Needs BPM or key**, or **Source audio missing**. Key fit uses the selected arrangement clip, including any pitch shift; "
            "it cannot guarantee that overlapping vocals or melodies fit. Filtering never removes mix clips. "
            "Songs hidden by a filter are deselected, so adding a selection only adds visible songs.\n\n"
            "## Identical imports\n\n"
            "Reimporting an unchanged file keeps the existing song and your edited BPM, key and cues. "
            "An exact file copy at another location is skipped when the original source is still available and unchanged. "
            "The import message reports the skipped count. Different recordings or encodings are kept separately; "
            "matching names alone never cause a skip. No audio file is deleted. "
            "If an existing source file has changed, use **Reconnect source audio** to review it.\n\n"
            "## Reading the list\n"
            "\n"
            "Click a column header to sort by name, BPM, key in Camelot order, or length. "
            "A colored square marks songs already in the mix; a green dot marks songs whose key "
            "fits the selected clip. Selecting a row shows its waveform in the strip below the list.\n"
            "\n"
            "## Analysis is a suggestion\n"
            "\n"
            "BPM and key are estimated locally and can be wrong. Songs with a half-time feel are "
            "sometimes detected at half or double the tempo you hear — if the grid feels twice as fast "
            "or twice as slow as the song, correct the **BPM** box in the **Song settings** panel by ear; "
            "see [Tempo](help:tempo).\n"
            "\n"
            "Select one or several songs and click **Detect keys**, or right-click and choose "
            "**Analyze BPM and key again**. If the optional key detector is missing, the app offers its 19 MB download first. "
            "Detection fills unknown labels and reuses current cached estimates. Existing key labels and an explicitly "
            "chosen Unknown are kept; **Use detected key(s)** accepts the latest estimate instead. "
            "A remaining Unknown has a reason in the song's tooltip and the analysis result. "
            "Rechecking songs already in the mix keeps their BPM and cue positions; edit those deliberately in Song settings. "
            "The separate **Analyze** button checks timing while preserving those choices.\n\n"
            "## Removing songs\n\n"
            "Select songs and click **Remove songs**, use their right-click menu, or press Delete while the library has focus. "
            "If they are used in the mix, the button says **Remove + mix** and also removes those clips. "
            "Collection memberships are removed too. Original audio files stay on disk. One Undo restores the whole batch.\n\n"
            "## Batch checks and settings\n\n"
            "Right-click a multi-selection for **More checks for selected songs**: tempo drift, beats and musical cues, "
            "voice/drum passages and optional advanced builds/drops. Current caches are reused. These observations "
            "do not choose new mix cues or turn on correction automatically. **Results** lists every song, including "
            "errors, inconclusive findings and songs not reached before cancellation; **Retry unfinished** checks just those songs.\n\n"
            "The source-label submenu assigns a common BPM or key label deliberately. The mix-settings submenu "
            "sets common pitch shifts, overlaps, supported tempo stabilization, level balance, bass swaps or transition "
            "effects for the selected songs' mix clips. Source key labels describe the recording; pitch shifts change "
            "audio when it is prepared. Pitch shifts keep major/minor character. Changes can be undone as a batch.\n\n"
            "**Reconnect source audio** also accepts a multi-selection. **Find selected originals in a folder** "
            "verifies file contents and offers all exact matches together. A matching filename is insufficient. "
            "Older songs with missing originals and no saved file identity require individual listening and confirmation.\n\n"
            "## While work is running\n\n"
            "A blocking progress window shows the current task and progress. Other app controls remain unavailable "
            "until work and result application finish. When supported, **Cancel processing** requests a safe stop. "
            "The window remains open while tasks stop and resources are released. Closing that window or pressing "
            "Escape cannot bypass running work. Completed model downloads and valid analysis caches are kept."
        ),
    },
    {
        "id": "arrangement",
        "title": "Arrangement",
        "keywords": "arrangement clips decks lanes mix ruler overview zoom reorder move remove waveform display",
        "body": (
            "## Lanes and clips\n"
            "\n"
            "Songs alternate between **Deck A** and **Deck B** so neighbors always overlap; "
            "rendered audio appears on the **Mix** lane. Each song keeps one color. A clip shows a "
            "three-band waveform in shades of that color — dark lows, mid-tone mids, pale highs — "
            "with the crossfade and bass-swap curves drawn over each overlap.\n"
            "\n"
            "Waveforms are display only: they are computed once per song and cached, so they never prove "
            "that timing correction was applied. Listen with **Listen to blend** or the **A/B** comparison instead; "
            "see [Timing](help:timing).\n"
            "\n"
            "## Moving around\n"
            "\n"
            "Numbered locators above the bar ruler mark the blends, with a green dot for a key match "
            "and a red one for a clash. The overview strip above the ruler shows the whole mix — click it "
            "to jump there. Drag the bar ruler up or down to zoom and sideways to scroll, or use "
            "{MOD}+scroll or a trackpad pinch. The minus, plus and fit buttons zoom out, zoom in, "
            "or fit the whole mix.\n"
            "\n"
            "## Reordering\n"
            "\n"
            "Drag clips to reorder them, or select a clip and use the left and right arrow buttons in the "
            "Clip box (**Move earlier in the mix**, **Move later in the mix**). **Remove** takes the clip "
            "out of the mix; the song stays in your library."
            "\n\n## Choosing what plays\n\n"
            "**Start** and **End** are times in the original song, before stretching. Start includes the incoming "
            "blend; End includes the outgoing blend. For example, Start 0:30 skips the first 30 seconds of the source. "
            "The clip's position in the mix is determined by the songs before it and their overlaps.\n\n"
            "Drag either marker on the small source waveform. Markers snap to estimated bars, Shift snaps to beats, "
            "and {ALT} places them freely. Use the **Start** or **End** button below the waveform to focus on a cue, "
            "**− / +** to zoom, and **Fit** to see the whole song. {MOD}+scroll zooms at the pointer; ordinary scroll "
            "or dragging the ruler pans. Double-click either time box to type minutes:seconds.milliseconds.\n\n"
            "## Keyboard access\n\n"
            "Tab to a waveform; its orange outline shows focus. In the arrangement, Up/Down selects a song, "
            "Left/Right seeks by a beat (Shift by a bar), and Home/End seeks the beginning/end. "
            "In the source waveform, Up selects Start and Down selects End; Left/Right moves that marker "
            "by a bar, Shift by a beat, or {ALT} by 10 milliseconds. These edits respect overlap and protected-passage "
            "limits and can be undone. +/− zooms and 0 fits the whole mix or song. "
            "Tab leaves the waveform. See [all keyboard shortcuts](help:shortcuts)."
        ),
    },
    {
        "id": "musical-cues",
        "title": "Find musical cues",
        "keywords": "musical cues beats downbeat bar phrase section structure energy intro outro beginning ending tail click tap half double manual grid drums percussion drum-led voice passages confidence build drop EDMFormer",
        "body": (
            "## Find useful places to enter or leave a song\n\n"
            "Select a song in the arrangement, then choose **Find musical cues…** in the clip panel. "
            "The app analyzes the original audio locally and caches its observations. It does not change BPM, "
            "pitch, timing correction or cues while analyzing.\n\n"
            "A **downbeat** is the first beat of a bar. The app checks the model's detected bar starts against "
            "the source BPM and four-beat bars. Orange ruler marks show supported bar starts; green dashed "
            "lines show estimated changes in energy or texture. Unsupported passages are left without detected bars. "
            "Before analysis, the ruler is only a regular guide from your current cue and BPM.\n\n"
            "**Coverage** says how much audio passed these checks, not how often the model is correct. "
            "If the pulse appears half or double the source BPM, open the **Check beats and BPM** tab. "
            "After **Analyze song**, that tab shows a separate **beat-based estimate** beside your draft BPM. "
            "It comes from the detected beat spacing, independently of your saved BPM. Newly analyzed imports also "
            "keep an **opening-passage estimate** when available. Both remain visible when they disagree, even if "
            "the beat estimate supplied the imported BPM. Either can count every other beat or follow the wrong rhythm; "
            "neither is guaranteed correct. **Try beat** and **Try opening** change only the draft and its "
            "reference click. Listen at several places, and check that four clicks make a musical bar. The original "
            "song keeps its speed during this check. **Apply changes** keeps the draft as one undoable edit for all "
            "uses of that source song; **Discard draft** keeps the mix unchanged. Agreement or coverage is not a "
            "confidence percentage. If no beat estimate is available, keep your BPM or use **Tap tempo**.\n\n"
            "An eight-bar phrase pattern is suggested only when enough section changes agree; otherwise "
            "the app says the phrase pattern is inconclusive. These beat/texture estimates do not label vocals, builds or drops; "
            "use the dedicated passage and section tabs for those suggestions.\n\n"
            "Whole-song musical analysis reads small audio blocks and needs temporary free disk space. "
            "It checks space before starting and removes its working audio afterward. Progress names the reading, "
            "sound measurement and beat-finding stages. Cancel stops at the next checkpoint. Fresh musical "
            "analysis supports songs up to four hours; longer sources can use manual beat grids and cue points.\n\n"
            "## Listen, try, then apply\n\n"
            "**Listen here** plays the original song from four beats before a suggested point. **Try start** "
            "or **Try end** puts that point in a draft. You can also type source seconds, drag Start/End on the "
            "real waveform, and zoom with Start cue, End cue, −, + and Fit. Dragging snaps to supported bar starts; "
            "Shift uses beats and {ALT} places freely. Click the waveform to listen there, or use arrow keys "
            "for a beat and Shift+arrows for a bar.\n\n"
            "**Keep original ending** retains the final audio and its tail. Earlier ending suggestions say how "
            "much audio they omit; listen for unfinished vocals or reverb before using them. All cue changes "
            "must leave room for both neighboring overlaps.\n\n"
            "**Apply changes** commits the draft as one undoable edit. Start and End affect only this clip. **Discard draft** "
            "keeps the mix as it was; the computed analysis remains cached. Finally, use **Listen to blend** "
            "or **Open blend editor** to judge the actual transition at the project tempo and pitch.\n\n"
            "## Find a drum-led passage\n\n"
            "Open **Drum-led passages** and choose **Find drum-led passages**. The local detector looks for "
            "prominent drums with weaker voice and pitched-instrument signals in overlapping ten-second windows. "
            "It proposes a passage only where those observations agree. It may find no candidates even when "
            "useful passages exist; keep your cues and listen manually in that case.\n\n"
            "**Consistent observations** describes repeated evidence, not an accuracy percentage. These "
            "estimates have not been calibrated across your music. Quiet vocals, pitched bass, melodies and "
            "tails can be missed. A drum-led candidate is not proof of drums-only audio or permission to ignore key clashes.\n\n"
            "**Listen here** plays the original song from the passage and continues beyond it; use Pause to stop. "
            "**Try Start** or **Try End** shows the exact cue and proposed overlap length. It uses the current "
            "detected or manual bar grid, and may shorten the blend to fit inside the passage. Missing or "
            "inconsistent bars, protected passages and neighboring blends can prevent a cue suggestion. "
            "Analyze song or check beats/BPM, then review the draft.\n\n"
            "The draft reports any omitted source audio. **Apply changes** keeps the cue and overlap as one "
            "Undo action. You can still edit them or discard the draft. Trying a candidate clears any old "
            "drums-only label for that passage; it does not prove notes are absent. If you deliberately confirm that a blend is only unpitched drums, "
            "the Advanced controls provide Drum intro/outro labels. Hear the actual blend before exporting.\n\n"
            "Passage observations are saved with the project and reused for unchanged source audio. Analysis "
            "alone changes no audio settings. Verified moved files keep their observations; changed files need analysis again.\n\n"
            "## Find builds, drops, intros and outros\n\n"
            "Open **Builds and drops → Find builds and drops**. A build raises tension; a drop releases it with "
            "the main musical energy; a breakdown usually reduces the intensity. The local experimental model "
            "also suggests intros, outros and quiet sections. Labels and boundaries are approximate, not verified facts. "
            "Colored strips beneath the real waveform carry a question mark to keep that uncertainty visible.\n\n"
            "Select a row without starting playback. **Listen to start / Listen to end** plays from four beats "
            "before that boundary. **Review protection…** fills the protection form, where you can adjust the range. "
            "Choose Protect this passage and Apply changes to keep it. No section is automatically protected or trimmed.\n\n"
            "Intro/outro **Try Start / Try End** choices require complete, continuous bars and enough room for "
            "both neighboring blends and all protected passages. A choice can shorten the adjacent overlap and "
            "reports omitted source audio. It clears a prior drums-only label: an intro or outro can still contain "
            "pitched instruments and voices. The normal key checks still apply. Preview the actual blend afterward.\n\n"
            "Build/drop analysis supports individual songs from two seconds to thirty minutes. It runs in a separate "
            "process; Stop and close cancels it. Completed observations are stored with the project and cached for "
            "unchanged source files. Changes to BPM, key, cue points or song order do not require running this model again. "
            "No audio is uploaded. The model pack is optional; if absent, the panel explains why Find is unavailable.\n\n"
            "## Check the beats by ear\n\n"
            "Open **Check beats and BPM** and turn on **Click**. Original audio and the clicks share one playback clock. "
            "The song keeps its original speed and pitch while you check it, even after a draft BPM edit. "
            "Higher clicks mark beat 1 of each four-beat bar. Listen at **Start**, **Middle** and **Ending**. "
            "Clicks should land on the same rhythmic beats throughout; count one-two-three-four with the music. "
            "The click is never exported, and toggling it keeps playback running at the same volume.\n\n"
            "Use **÷2** or **×2** if the BPM estimate counts every other beat or counts twice per beat. "
            "Or press **Tap tempo** on at least four beats; keep tapping steadily, then choose **Use tapped BPM**. "
            "Tap tempo is approximate: listen before applying it. A pause of over three seconds starts a new tap sequence.\n\n"
            "For a misplaced grid, pause playback, zoom in, and click a kick you recognize as the first beat of a bar. "
            "Choose **Mark playhead as beat 1**, or type/nudge the **Beat 1 at** source time. "
            "This makes a **Manual regular grid** at your draft BPM, extending both before and after that reference. "
            "It changes the displayed grid and cue snapping, without moving audio or changing cue points. "
            "Choose **Detected grid** to return to observed beats; if you changed BPM, Analyze song checks that new interpretation.\n\n"
            "**Scope:** applying source BPM or the beat grid affects all uses of this source song in the current mix. "
            "The app checks every affected overlap before accepting a BPM change. Undo restores the old settings. "
            "A manual regular grid is a reference, not a tempo-correction map. If clicks drift in and out despite a sensible "
            "BPM, check the separate tempo-correction analysis; do not assume that drawing a steady grid straightens the audio."
        ),
    },
    {
        "id": "tempo",
        "title": "Tempo",
        "keywords": "tempo bpm source project beat bar math half double time signature",
        "body": (
            "## Two BPM settings and a correction\n"
            "\n"
            "- **Source BPM** is the song's own tempo, shown in the **BPM** box in the **Song settings** panel. "
            "Drag it up or down (Shift for fine steps), or double-click to type.\n"
            "- **Project BPM** is the mix tempo in the control bar. Every song is stretched to it "
            "while keeping its original pitch unless you choose a shift under **Play in key**. Large tempo or pitch changes can cause audible artifacts.\n"
            "- **Stabilize** is optional drift correction inside one song; see [Timing](help:timing).\n"
            "\n"
            "## Fixing a wrong estimate\n"
            "\n"
            "New imports use a **Beat This** beat-based BPM estimate when available, with an opening-passage "
            "estimate as fallback. Both available estimates are retained. Previously saved BPM choices are preserved. "
            "Neither method guarantees the correct pulse: a 140 BPM recording might be counted as 70 BPM.\n\n"
            "Select the clip and open **Song cues… → Check beats and BPM**. **Try beat: … BPM** and "
            "**Try opening: … BPM** let you compare available estimates in a draft. Listen with **Click** at the "
            "start, middle and ending. These controls change a regular reference grid, not the source audio. "
            "Use **÷2**, **×2**, **Tap tempo**, or a manually entered BPM when appropriate. **Apply changes** "
            "commits the draft; **Discard draft** keeps the previous settings. See the "
            "[step-by-step tempo lesson](help:learn-check-bpm) for counting and grid placement.\n\n"
            "The separate timing **Analyze** checks for drift. Its **Use …** button may offer a measured "
            "value (for example **Use 128.003**). Clicking it explicitly accepts that suggestion; analysis "
            "does not automatically replace an existing clip's BPM. A grid that slowly pulls away may "
            "indicate a slightly wrong BPM; irregular wandering can require [stabilization](help:timing).\n"
            "\n"
            "Correcting a BPM number never rewrites your audio file; it only changes how BeatStitch "
            "stretches that song when rendering.\n"
            "\n"
            "## Bar maths\n"
            "\n"
            "BeatStitch assumes 4/4. At a steady 128 BPM each beat is 0.469 seconds, "
            "so a 4-beat bar is 1.875 seconds and 8 bars are 15 seconds. "
            "With a [tempo curve](help:tempo-curve), bars get shorter as the mix speeds up and longer as it slows down. "
            "The overlap duration shown below the stepper follows that curve."
        ),
    },
    {
        "id": "timing",
        "title": "Timing and stabilization",
        "keywords": "timing analyze stabilize drift correction map coverage steady inconclusive compare downbeat phrase",
        "body": (
            "## What Analyze does\n"
            "\n"
            "Select a clip and click **Analyze** in the **Song settings** panel to check the whole song for tempo drift. "
            "This preserves your saved BPM and cues — it only adds a measurement. If the measurement disagrees "
            "with your BPM, a **Use …** button offers the new value; click it only if you want it.\n"
            "\n"
            "## Reading the status\n"
            "\n"
            "- **Timing not analyzed** — no measurement yet. Click **Analyze**.\n"
            "- **Timing needs updating** — the saved measurement does not match the current source file. "
            "Reconnect missing audio first, then **Analyze** again. The old BPM suggestion and A/B comparison "
            "are unavailable. You can still turn an existing correction off. Changing BPM alone does not "
            "invalidate timing measured from the same source audio.\n"
            "- **No significant drift detected** — no substantial drift was detected in reliably measured passages. "
            "This does not establish that the whole song is steady, and no correction is available to enable.\n"
            "- **Timing inconclusive** — beats were too sparse or conflicting for a safe map, or drift was "
            "found but no safe map could be built. This does not mean the song is steady. Re-analyzing may reuse the same valid cached analysis; "
            "the result may stay inconclusive.\n"
            "- **Correction available** — a reliable map exists for part of the song. Turn on **Stabilize** "
            "after listening to the **A/B** comparison if you like the result.\n"
            "- **Processing in progress** — timing controls are temporarily paused during background work. "
            "Wait for the status bar's current task to finish. This is not an analysis result.\n\n"
            "Timing analysis reads the source in chunks and uses temporary disk space, approximately 11 MB per "
            "minute of song length. Free space is checked first. **Cancel** stops at the next small processing "
            "step and removes unfinished files. Reading, rhythm and beat-attack progress describe analysis only; "
            "your original song is unchanged. Fresh timing analysis supports songs up to four hours. For a longer "
            "source, use a shorter excerpt or keep ordinary fixed-tempo processing.\n"
            "\n"
            "## Using Stabilize\n"
            "\n"
            "**Stabilize** is off by default and only meaningful when a reliable correction map exists. "
            "The coverage figure is the proportion of the song that gets corrected — not a trust score. "
            "Unsupported sections keep fixed timing, joined with short smooth blends. "
            "Your original audio file is never modified; correction only affects rendered output.\n"
            "\n"
            "The detector targets regular electronic kick patterns. It does not find downbeats, so match "
            "phrases by ear: there is no automatic guarantee that bar 1 of the grid is bar 1 of the phrase."
        ),
    },
    {
        "id": "keys",
        "title": "Keys and pitch",
        "keywords": "key camelot transpose pitch semitone match shift harmony clash vocals melody drums openkeyscan detection estimate override manual unknown",
        "body": (
            "## Key and Camelot codes\n"
            "\n"
            "A key like \"A minor\" is shown with its Camelot code (for example **8A**), which makes "
            "compatible keys easy to spot. A means minor; B means major. Common candidates share a code, move one "
            "number with the same letter (12 wraps to 1), or keep the number and change letter. For example, "
            "8A can blend with 8A, 7A, 9A or 8B. Set a song's key with the **Key** (**Source key**) list in the "
            "**Song settings** panel; choose **Unknown** when you cannot tell. Editing this label changes advice, not the audio.\n"
            "\n"
            "## Estimates and your own choices\n\n"
            "New songs use the local **OpenKeyScan** detector. It listens to the first minute (or the whole song if shorter) "
            "and can get the key wrong. The song may also change key later. **Estimated key · check by ear** is a suggestion, "
            "not a guarantee that the notes in a blend will fit. Model scores are not shown as accuracy percentages.\n\n"
            "Choose any key or **Unknown** in the Key list to override detection. **Your key choice is kept** means "
            "that reanalysis, saving and reopening will preserve your choice. Older project labels are preserved too, "
            "because the app cannot know which old labels you entered yourself.\n\n"
            "**Detect key** finds an estimate without replacing the current label or changing BPM, cues or pitch. "
            "The detected key stays visible below **Source key**, even after a manual correction. **Restore detected** "
            "returns the label to that saved estimate and supports Undo. If no usable estimate is saved, detect it first. "
            "The library right-click menu also offers **Check key / Use estimate**. In Simple view, turn on Advanced controls "
            "to see the editable Source key list. Hover over "
            "the estimate or library row to see other possible keys. Repeated checks reuse the result when the song "
            "and detector are unchanged. If detection cannot finish, you can still enter the key yourself.\n\n"
            "## Shifting pitch\n"
            "\n"
            "Choose **Play in key** in either view to transpose the song to a destination key. "
            "This uses the Source key label, so correct a wrong label first. Pitch shifting preserves mode: "
            "a minor song offers only minor destinations and a major song offers only major destinations. "
            "It cannot turn a minor composition into major. Choose **Original** to restore zero pitch shift. "
            "If the source key is Unknown, set its label first or use the manual semitone control in Advanced controls.\n\n"
            "The numeric **st** control shifts pitch in semitones; +1 raises every note by one half-step. "
            "Type a value or use its arrows. **Apply shift** applies or bypasses that amount while remembering it; "
            "it is disabled at zero because there is no shift to apply. The **Try … st** button, when available, applies the suggested "
            "shift after checking both neighboring blends. It never recommends turning an already related "
            "neighboring pair into a clash. **Mix review** explains the affected blends and controls the "
            "largest suggested shift; see [Mix review](help:mix-review).\n"
            "\nChoose the nearby **Listen to blend** (or **Listen in mix** for one song) to prepare and hear the choice. "
            "Pitch changes are also used by Play mix and Export. Edits do not change audio already playing. "
            "The library Listen button always auditions the original recording, regardless of the mix's pitch choice.\n"
            "\n## Finding compatible songs\n\n"
            "**Find matches** beside Play in key opens the library's key-compatible filter and clears an old search "
            "or collection restriction. It compares library songs at their original pitch with the selected mix "
            "song's current playback key. Select a library song to see the two keys and why they fit. "
            "For example, **A minor and C major are relative keys**: they can fit without transposing either song. "
            "The filter also includes the same key and neighboring keys on the circle of fifths. "
            "It changes no pitch or arrangement settings. Choose **All songs** to clear the filter.\n"
            "\n"
            "## Guidance, not a promise\n"
            "\n"
            "Key verdicts and green dots are harmonic guidance, not a guarantee. Clashing vocals or lead "
            "melodies can still fight even when the codes agree, and a local key change inside a song can "
            "surprise you. When a whole overlap truly contains no pitched bass, vocals, melody or tonal tails, "
            "**Drum outro** or **Drum intro** relax the guidance — they never change the audio."
        ),
    },
    {
        "id": "transitions",
        "title": "Transitions",
        "keywords": "transition blend editor preview solo outgoing incoming loop bass swap overlap order chevron verdict cue end alternatives current draft comparison A/B pitch matched loudness balance levels quieter louder volume jump review all keep change next unchecked remaining filter echo outgoing finish effects listening notes checklist beats harmony voices melodies fits unsure",
        "body": (
            '**Kicks landing apart despite matching BPM?** Choose **Align beats…** in the Transition box. '
            'It checks the beats, offers a timing draft and shows the actual kick hits for live comparison. '
            'See [Line up the beats](help:beat-alignment).\n\n'
            "## Which blend am I hearing?\n"
            "\n"
            "The Transition box title reads like **Transition 2 → 3**: the arrow means the blend from song 2 "
            "into song 3. Selecting a song selects the blend leading into it, so selecting song 3 previews "
            "the 2 → 3 blend. Step between blends with the chevron arrows (**Previous transition**, "
            "**Next transition**) or **[** and **]**.\n"
            "\n"
            "## Previewing\n"
            "\n"
            "**Listen to blend** (P) prepares the selected blend with four bars of context on each side, then plays it. "
            "If you change cues, tempo, pitch or correction afterwards, preview again — the previous audio is stale.\n"
            "**Loop blend** repeats this preview. "
            "The main **Play mix** button always returns to the whole arrangement at your mix playhead.\n"
            "\n## Review all blends\n\n"
            "**Review all** starts a listening pass through the mix. Each blend waits for your decision; "
            "it does not jump to the next one when playback ends. Use **Listen to blend** to replay it, or "
            "**Loop blend** to repeat it. The strip above the arrangement identifies the pair and shows progress.\n\n"
            "**Keep and next** means you like the current sound. It records your choice and moves on without changing "
            "audio. It becomes available after the current audio has loaded. **Change…** opens the blend editor: "
            "try alternatives, compare, then Apply or discard. You return to the same pair and hear the current "
            "mix again before deciding. Apply is one undoable edit. **Next** moves on without keeping that blend.\n\n"
            "At the end, **Review remaining** visits unchecked blends. **End review** or **Stop** pauses the walk; "
            "the summary lets you continue. **Close review** hides it. **Review all** starts a new pass. "
            "Playing the full mix, auditioning a source or selecting another pair also pauses the walk.\n\n"
            "A Keep decision belongs to the sound you heard. Changing cues, tempo, pitch, correction or bass handling "
            "clears affected decisions; changing one song's pitch can affect both neighboring blends. Unchanged "
            "blends stay kept. Source-file replacements also need another listen. Keep markers are temporary "
            "choices, not quality scores, locks or proof that a mix is correct. Keep markers are not saved in the project.\n"
            "\n## Listening notes\n\n"
            "**Listening notes…** in the Transition box opens an optional checklist for the selected blend. "
            "Use **Listen to blend** first, then separately judge whether the beats stay together, the musical notes "
            "fit, and the voices or lead melodies coexist. **Not checked** means you have not judged that question; "
            "**Not sure** means you listened but cannot decide. Neither means the blend has passed a check. "
            "You can add a reminder about phrasing, bass, levels or processing. Playback can continue while you write.\n\n"
            "**Save notes** stores your opinions with the project and is undoable. It never alters audio, starts "
            "processing or creates detector confidence scores. These notes belong to the exact prepared sound. "
            "If settings or source files change, earlier notes stay in the project but the checklist asks you to "
            "check the new sound. Changing a title or source-key label alone does not make audio different. "
            "Keep markers and saved listening notes are separate: keeping a blend never fills in the checklist for you.\n"
            "\n## Blend editor\n\n"
            "**Open blend editor** shows both real song waveforms on one time axis. The grid follows the incoming cue "
            "and project BPM; it is not a verified beat or phrase analysis. With tempo correction enabled, the waveform "
            "positions follow the same timing map as the audio.\n\n"
            "**Try an alternative** offers shorter/longer overlaps where they fit, different bass handling, level balancing, filter/echo finishes and suitable "
            "incoming pitch choices. These are starting points to hear, not promises of a better mix. Pitch suggestions "
            "respect your **Mix review** shift limit and check both neighbors. The explanation tells you when later songs "
            "move or the entire incoming clip changes pitch. You can refine a choice with the draft controls.\n\n"
            "Change the **Overlap** stepper or **Swap bass at the middle** to try a draft. **Listen** prepares it, "
            "reusing unchanged audio. Switch **Outgoing**, **Blend** and **Incoming** while it plays without restarting. "
            "Solo keeps each song's mix volume and fades; it can be silent outside that song's part. **Loop** repeats "
            "the preview, with up to four bars of context on either side, stopping before neighboring blends.\n\n"
            "## One song overpowers the other\n\n"
            "Try **Balance levels in this blend**. The app measures each song inside the actual overlap after "
            "tempo and pitch processing, before applying the crossfade. It gently reduces the louder side by "
            "up to 3 dB, easing in and out within the overlap. It never boosts a quiet intro or tail, and it "
            "does not compress the music. Outside the overlap, the songs keep their usual whole-song loudness matching.\n\n"
            "**Listen** shows which side was reduced and by how much. Similar levels, very quiet material or an "
            "overlap under one second can leave the sound unchanged. The limit may leave a large difference; "
            "try different cues if needed. **Compare current / draft** lets you judge the handover while playing. "
            "Apply only if you prefer it. This setting belongs to this incoming blend, is off by default, "
            "is saved with the project, and affects the exported mix after applying.\n\n"
            "**Listen** also checks the notes in the actual overlap. Its separate local-harmony result uses each "
            "rendered side with the applied pitch, tempo, fades and bass handoff, including audible tonal tails. "
            "The surrounding listening context is excluded from this check. It may show related key estimates, "
            "a possible local clash, or uncertainty. Short passages, too few notes and changing evidence stay "
            "uncertain. A pitched tail in an area you marked as drum-only is flagged for listening. This does not "
            "identify vocals, prove a good blend, or change your source key labels and drum-only marks. Whole-song "
            "key advice elsewhere may differ because it describes the whole song rather than this passage.\n\n"
            "When the local check differs from the song labels, solo both sides and trust what you hear. Try "
            "shorter overlaps, different cues or a pitch alternative. **Listen** again after an edit to check the "
            "new overlap; unchanged prepared audio reuses its result. Profile correlations are not confidence "
            "probabilities, so no accuracy percentage is shown.\n\n"
            "## Melodies compete even when keys fit\n\n"
            "**Check musical crowding** in the blend editor is an optional experimental listening aid. "
            "It prepares your current draft if needed, then checks the actual overlap for two audible, "
            "different moving pitched patterns. It uses the processed tempo, pitch, fades, bass handoff "
            "and effects. Everything runs locally; unchanged prepared audio reuses its saved result. "
            "Checking never changes your mix, starts playback, or marks a passage as drums only.\n\n"
            "**Possible musical crowding** means something to listen to, not a bad-mix verdict. "
            "Chords, arpeggios and voices can produce this evidence too; it does not isolate or name "
            "lead instruments. Slow, quiet or masked melodies can be missed. Bass-harmonic suppression "
            "can also hide a lead that doubles the bass. No warning is not proof that a blend works. "
            "The approximate ranges come from four-to-eight-second windows, not exact melody boundaries; "
            "there is no calibrated confidence percentage. Overlaps under four seconds remain uncertain.\n\n"
            "**Listen to flagged part** starts just before the first flagged window. Switch "
            "**Outgoing / Blend / Incoming** to hear both contributions; playback continues until paused. "
            "**Try shorter overlap** puts a shorter length into the draft, keeping cues, pitch and "
            "other settings. Both neighbors and protected passages must fit. It has not been scored as "
            "better: a short handover can sound abrupt and the melodies may still compete. Use "
            "**Compare current / draft**, or check the new draft again, then **Apply changes** only if "
            "you prefer it. One Undo restores the previous mix. Key and voice checks remain separate.\n\n"
            "**Find another entrance** searches up to eight detected or manually checked bar starts, "
            "spread up to 32 bars earlier or later. It renders candidates with the same overlap length "
            "and processing, then offers up to two with less flagged duration and a substantial reduction "
            "in underlying pitch activity. Small warning-threshold changes alone do not qualify. "
            "The search preserves protected passages, both neighboring blends, and at least four clear "
            "bars of this song outside its blends (or the original clear duration if shorter). Silent "
            "entrances and worse known vocal collisions are rejected. It does not newly analyze voices "
            "or prove that the new local keys fit. The suggestions show source audio omitted/restored "
            "and the change in mix duration. **Try incoming start** clears the old drums-only intro mark "
            "and keeps everything in a draft for **Compare current / draft** and Apply/Undo.\n\n"
            "Searching can take longer than a single check. Completed audio and checks are reused on "
            "repeat searches; **Stop and close** cancels without applying partial results. If no entrance "
            "qualifies, it says so. This bounded search does not establish the best possible entrance. "
            "If no valid bar starts are available, check beats in Song cues or keep your own cue.\n\n"
            "## Voices compete in the blend\n\n"
            "**Check voices in blend** checks both original songs locally. **Listen** also checks them when "
            "preparing a blend. Existing passage observations are reused, including those from Song cues. "
            "The voice result is separate from key advice: two songs in related keys can still have "
            "competing words or melodies.\n\n"
            "Possible voice ranges follow Start/End, source BPM, project tempo and tempo stabilization "
            "into the actual overlap. Echo repeats are included. These ranges come from approximate "
            "source scan windows; instruments can be mistaken for voices and some vocals can be missed. "
            "No flagged overlap does not prove that vocals or melodies fit. This check does not detect "
            "melodic clashes, and it never sets a drums-only label. Solo both sides and listen.\n\n"
            "When a nearby supported bar start reduces the observed vocal overlap, **Try incoming start** "
            "offers it as a draft. The explanation says how many source seconds are omitted or restored "
            "and how the mix duration changes. It keeps the incoming End and overlap length, checks both "
            "neighboring blends and protected passages, and clears the incoming drums-only mark. It "
            "does not guarantee a better musical entrance. **Compare current / draft**, listen, then "
            "**Apply changes** only if you prefer it; one Undo restores the previous mix.\n\n"
            "If there is no suitable cue, check the bar grid in Song cues, try a shorter overlap, or "
            "choose another song. The search is limited to nearby bar starts; absence of a suggestion "
            "does not mean every possible cue was checked.\n\n"
            "Click the waveform to seek, or focus it and use arrow keys for one beat, Shift+arrows for one bar, "
            "and Home/End for the preview edges. **Apply changes** keeps the draft as one undoable edit. "
            "**Discard draft** closes without changing the mix. These listening buttons never affect an export.\n"
            "\n"
            "## Compare current and draft\n\n"
            "After changing a draft, choose **Compare current / draft**. The app matches the two versions' listening "
            "levels with a constant gain, so a louder version is less likely to fool you into preferring it. Switch "
            "**A · Current mix** and **B · Draft blend** while playback keeps running. Both follow the same outgoing "
            "song position. A longer overlap brings the incoming song in earlier; you will hear that timing change. "
            "The real waveforms follow the version you select.\n\n"
            "Listen for competing notes or voices, doubled kicks and a dip or jump in energy. **Loop** repeats the "
            "comparison. **Apply draft to mix** keeps it as one undoable change; **Back to editing** changes nothing "
            "in the mix. Comparison gains are for listening only and are not exported. If pitch changes, also listen "
            "to the incoming song's next blend.\n\n"
            "## Filter or echo on the outgoing song\n\n"
            "In the blend editor, **Outgoing finish** starts at **No extra effect**. **Filter out** gradually "
            "removes low frequencies from the outgoing song to make room for the next one. It can sound too thin; "
            "try it against the normal blend. **Echo out** fades the outgoing song early, then plays four "
            "increasingly quiet, filtered repeats of a short ending. Listen for distracting repeated notes or words.\n\n"
            "Echo normally repeats one beat over the final bar. In a one-bar overlap it repeats half a beat "
            "over the final half-bar. These effects stay inside the existing overlap: they do not extend the "
            "song, change the mix length or reach into protected passages. Only the outgoing song is affected. "
            "Level balancing measures the dry overlap first and never boosts the effect or its repeats.\n\n"
            "Choose **Listen**, then **Outgoing** to hear the effect alone. **Compare current / draft** switches "
            "between the current mix and your draft at matched listening levels while playing. **Apply changes** "
            "saves the choice for this blend and the exported mix as one Undo action. The main transition panel "
            "names the active effect. Real source waveforms remain visible; they show the original audio, "
            "not the filter or echo. Changing an effect reuses unchanged pitch and tempo processing.\n\n"
            "## Length and bass handling\n\n"
            "Select the incoming song, then adjust **Overlap** in the Transition box to make the blend longer or "
            "shorter. Click **−** or **+** to change it by one bar. You can also drag the number up/down, "
            "use arrow keys, or double-click to type. The maximum (up to 32 bars) accounts for both songs' "
            "cues and their other blends. The duration in seconds and available maximum appear below the stepper; "
            "hover there to see which song limits it. **Presets** offers common 4, 8, 16 and 32-bar choices. "
            "At a steady 128 BPM, 8 bars are 15 seconds; 16 bars are 30 seconds. "
            "With a [tempo curve](help:tempo-curve), the seconds shown follow its changing BPM. "
            "The shaded overlap on the timeline updates immediately; preview again to hear the change. "
            "The selected pair is labeled **Out** and **In** on the timeline. Drag the orange grip at the "
            "start of the overlap left to lengthen it or right to shorten it; it snaps to bars. "
            "The outgoing song's end stays fixed, while the incoming song and later songs move. "
            "Press Escape during a drag to cancel. **Undo** reverses the whole drag.\n\n"
            "**Reset** restores this transition to 8 bars (or the longest available shorter length), turns "
            "bass swap on, turns level balance and extra effects off, and clears both drum-only labels. It keeps song cues and pitch and can be undone.\n\n"
            "Each clip's **End** marks the end of its outgoing overlap. Both clips need enough audio for the "
            "chosen overlap; a middle clip must have room for both its incoming and outgoing blends. If a change "
            "is rejected, shorten the overlap or widen Start/End. **Bass swap** hands the low end "
            "over in one bar at the middle of the blend instead of crossfading it. **Drum outro** and "
            "**Drum intro** mark a drum-only overlap; they adjust key advice, not sound. See [Keys](help:keys).\n"
            "\n"
            "The key verdict line shows both keys with a green (match) or red (clash) dot. "
            "**Suggest order** opens a reviewable plan with optional energy ratings, opener/closer choices, "
            "position locks and set-length preferences. See [Plan song order](help:planning). "
            "Applying is optional; hear the proposed blends before deciding."
        ),
    },
    {
        "id": "playback",
        "title": "Playback and listening",
        "keywords": "playback play pause stop render seek output volume device speaker click loudness",
        "body": (
            "## Choose what to listen to\n"
            "\n"
            "**Play mix** (Space) plays the whole arrangement from the mix playhead. Click the timeline ruler "
            "to choose a starting point, even before audio is ready. The app prepares audio automatically when "
            "needed. **Listen to blend** (P) auditions only the selected transition, without replacing your "
            "mix playhead. The library's **Listen** button plays the selected original song. Only one of these "
            "listening modes plays at a time. Edits take effect on the next listen.\n"
            "\nMoving the timeline playhead never starts a new render. The app seeks in the prepared full mix "
            "when available, or within the current prepared blend. Outside prepared audio, playback pauses at "
            "your chosen mix position; press **Play mix** when you want the whole mix prepared. "
            "A cached blend stops at its preview boundary; Play mix is the full-arrangement transport.\n"
            "\nDuring work, a blocking progress window names the task and shows "
            "the current stage, such as reusing cached audio, preparing an uncached song or assembling the mix. "
            "Cancel processing requests a safe stop. Controls stay blocked until processing, cleanup and result application finish. Loading visual waveforms is identified separately from audio processing. "
            "Adding a song reuses unchanged prepared songs; first-time full-mix playback can still need passages "
            "that a short blend preview never prepared.\n"
            "\n"
            "**Stop** pauses listening and ends a transition review. Rewind returns **Back to the start** (Home); "
            "the bar counter shows bars, beats and sixteenths, "
            "next to minutes and seconds.\n"
            "\n"
            "## Output and volume\n"
            "\n"
            "The speaker button (**Audio output**) follows the system default or routes playback to one device. "
            "If that device disconnects, playback pauses and mutes until it returns or you pick another output.\n"
            "\n"
            "The meter shows the **Mix level before output volume**; the **Output volume** slider only changes "
            "how loudly you listen. It is not a master fader: exports ignore it and always use full mix level "
            "with checked headroom. See [Projects](help:projects).\n"
            "\n"
            "## Comparing tempo correction\n"
            "\n"
            "**A/B** builds an up-to-90-second comparison from a trusted drift section inside your cues, using "
            "your pitch and project tempo with loudness matched so the test is fair. Both versions share one "
            "playback clock: **A: fixed BPM** and **B: stabilized tempo** switch with a tiny fade, without pausing. "
            "The **Steady reference click** is an audition aid only and never appears in renders or exports. "
            "Listen for double hits, smeared kicks or a change in tone as well as beat alignment. Similar sound is "
            "encouraging for audio quality, but does not prove the beats are correct. A/B needs at least eight seconds "
            "of supported drift inside your cues; widen the cue range if needed. Choosing B only auditions it: close "
            "the comparison and enable **Stabilize** to use correction in the mix."
        ),
    },
    {
        "id": "projects",
        "title": "Projects, rendering and export",
        "keywords": "projects save open new export render cache storage disk space cleanup limit autosave recovery djmix wav",
        "body": (
            "## Save, render, export\n"
            "\n"
            "These do three different jobs:\n"
            "\n"
            "- **Save** ({MOD}+S) and **Save as…** write the `.djmix` project: clips, cues, tempo, keys and "
            "settings. Edits also autosave, and autosave remembers the exact project file it belongs to. "
            "**New mix** and **Open…** keep a recovery copy of unsaved work; older autosaves without a known file "
            "ask for **Save as…** once.\n"
            "- **Play mix** prepares audio automatically when needed, then plays from the mix playhead.\n"
            "- **Export…** ({MOD}+E) saves the whole mix. Choose **High-quality master** (48 kHz, 24-bit WAV), "
            "**Lossless, smaller file** (FLAC), or **Easy sharing** (320 kbps MP3). The dialog estimates file size; "
            "FLAC compression varies with the music. You can include a CSV tracklist with song start/end times. "
            "Exports ignore **Output volume**. Encoded files are checked for duration and peak headroom.\n"
            "Before exporting, audition the [opening and ending](help:finishing) to check fades and recorded tails. "
            "After export, use **Play exported mix** to hear the actual file, or **Show in Finder/folder** to locate it. "
            "The completion message includes the saved folder and any tracklist.\n"
            "\n"
            "## Find a mix or recover earlier work\n\n"
            "Open **Projects…** in the bottom bar, or **File → Projects and recovery…** ({MOD}+Shift+O). "
            "**Recent projects** remembers the last 20 successfully opened or saved project files. "
            "Each entry shows its name, date, song count, duration and location. Use **Open a file…** for "
            "another project or a file you moved. Missing audio is reported separately from a missing or unreadable project.\n\n"
            "**Recovery copies** includes the latest autosave and snapshots kept before leaving unsaved work. "
            "Choose **Recover** to open a separate unsaved copy; **Save** then asks where to keep it. "
            "This does not overwrite the original saved project or an older recovery copy. The latest autosave "
            "always follows the active mix. Your current unsaved work "
            "is kept first. If that recovery write fails, the app keeps your current mix open. "
            "Unreadable copies remain visible with a reason and a **Show folder** action. No recovery files are deleted by browsing.\n\n"
            "Recovery copies load 20 at a time; **Load older copies** includes the next page. Search covers "
            "the loaded entries. **Refresh** rereads the list. The bottom bar distinguishes **Saved**, "
            "**Edited** and **Not saved**, and shows the last autosave time or an autosave failure. "
            "Autosave helps resume work on this computer; use Save to keep a named project and keep backups elsewhere.\n\n"
            "## Your music stays where it is\n"
            "\n"
            "Projects reference your audio files by path instead of copying them, so moving a project to another "
            "machine needs those sources too. Your original files are never rewritten by analysis, correction or render.\n\n"
            "## Reconnect a moved song\n\n"
            "Choose **Reconnect audio…** in Mix Review for a missing song, right-click its library row, or use "
            "**File → Reconnect source audio…** ({MOD}+Shift+L). Choose the original recording at its new location. The dialog "
            "shows its real waveform and lets you listen at original speed and pitch through the selected output. "
            "Nothing changes until **Reconnect audio**; one Undo restores the previous source location. All clips "
            "using that source reconnect together, retaining their cues, pitch, overlaps and protected passages.\n\n"
            "New imports store a content fingerprint, which recognizes unchanged file contents even after a move. "
            "A different recording, encoding or tag edit will not match. Use an unchanged copy, or import a new version "
            "as a new song. For older projects with no saved identity and no readable original, listen and confirm "
            "the recording/version explicitly. BPM, key and clip edits stay as set, but old beat/timing analysis is "
            "cleared and tempo correction is turned off for affected clips. Check timing again afterward. "
            "A changed duration or a file changed during verification is rejected.\n\n"
            "## Take the project and audio together\n\n"
            "**File → Create portable project…** ({MOD}+Shift+P) shows the audio size and lets you choose a destination. It copies "
            "all project sources, including library songs outside the arrangement, into a new folder. Original "
            "files and the current mix stay unchanged. Free space is checked first; cancellation cleans unfinished "
            "copies. Existing destination folders are never replaced.\n\n"
            "Move the whole completed folder. Open **Mix.djmix** in BeatStitch, keeping **media/** beside it. "
            "The project uses relative paths and verifies file contents when opened. Valid timing/grid analysis "
            "is connected to the verified copies. Missing files can be reconnected; changed bundled files must be "
            "restored from an unchanged copy. Saving beside the contained media keeps the project portable. "
            "**Save as…** elsewhere saves a linked project referencing that media; use Create portable project "
            "to make another complete copy. Both computers need a compatible BeatStitch build.\n"
            "\n"
            "## The render cache\n"
            "\n"
            "Processed excerpts are cached: adding a third song "
            "can reuse the first two if their processing settings are unchanged. Transposition (off counts as zero), source and project tempo, file identity, the "
            "source start point, and — for corrected clips — the timing map and both cues govern reuse. Blend lengths, "
            "bass handoffs, order and key labels do not invalidate the source transformation. A full cached song can serve a shorter "
            "preview, but not the reverse. Prepared mixes and previews also survive restarts. Changes to names, key labels "
            "and display beat grids keep matching prepared audio usable. A change elsewhere in the mix can leave a transition "
            "ready to play, even if it moves on the timeline. Unchanged assembled sections are reused; each new mix still gets "
            "a final headroom check. The status bar reports what was reused.\n\n"
            "## Storage and free space\n\n"
            "Open **File → Storage and cache…** to see usage, change the total allowance (2 GB by default), or "
            "**Clear unused cached files**. Audio, analysis and real waveforms share this allowance. The oldest unused "
            "files are removed after jobs finish. Clearing the cache means some work must be done again next time; "
            "it does not remove songs, projects, exports or the bundled analysis model.\n\n"
            "Files in use remain protected. Cleanup waits for active processing, and loaded audio can keep usage "
            "temporarily above the limit. Working space for a render can also exceed the cache allowance. Large audio "
            "jobs check free drive space first. If space is too low, clear unused cache files or export to another drive.\n\n"
            "Real waveforms load in small chunks. Sources longer than about five and a half hours keep the source "
            "overview instead of the detailed frequency-band display. **Cancel processing** in the progress window interrupts external audio processing "
            "and cleans up its process. An analysis calculation may need a moment to reach its next stopping point; "
            "wait for the job to finish stopping before starting another. Completed cached work remains reusable."
        ),
    },
    {
        "id": "troubleshooting",
        "title": "Troubleshooting",
        "keywords": "troubleshooting problems missing files no sound slow inconclusive clash stale device help",
        "body": (
            "## Sound or files\n"
            "\n"
            "- **A song is missing or fails to load.** The path changed or the drive is offline. Reconnect the drive, "
            "restore the original path, or choose **File → Reconnect source audio…** and locate an unchanged copy. "
            "Review it before **Reconnect audio**; existing clips retain their edit choices. See "
            "[reconnecting sources](help:projects) for verification and older projects. Importing the file again "
            "does not reconnect existing clips; ordinary projects store references, not copies.\n"
            "- **No sound, or playback paused by itself.** Check the speaker (**Audio output**) menu: an explicitly "
            "chosen device that disconnected mutes playback until it returns or you select another output. "
            "The disconnected choice is remembered so reconnecting restores it.\n"
            "- **Sound comes from the wrong device, such as speakers instead of AirPods.** Connect your headphones "
            "in the operating system first, then choose them explicitly in **Audio output**, or choose **System output** "
            "to follow the OS default.\n"
            "- **A mix sounds stale.** Click **Play mix** or **Listen to blend** again. Changed audio is prepared "
            "before playback; editing does not process sound live. Check that you are not using the library's "
            "**Listen**, which plays the original song without your changes.\n"
            "\n"
            "## Timing and pitch\n"
            "\n"
            "- **Timing inconclusive.** The track may lack a steady kick or the beats may conflict; check the source "
            "BPM by ear and, if needed, correct it manually. Re-running **Analyze** reuses the same valid analysis "
            "and may stay inconclusive. A disabled Stabilize button does not establish that the track is steady.\n"
            "- **Keys clash.** Try the **Match key** suggestion, a different shift, or reorder the songs. Remember "
            "that vocals and melodies can clash even with matching codes.\n"
            "\n"
            "## Speed\n"
            "\n"
            "The first preview of a corrected clip processes the whole clip and can take a while; later previews and "
            "exports reuse it. Watch the status bar for cached versus processed songs. Display waveforms never show "
            "whether correction worked. Listen with **A/B**, then enable Stabilize and preview the actual blend.\n\n"
            "If a blend is longer than the available audio, reduce **Overlap** bars or widen Start/End. "
            "If a corrected passage sounds worse, turn Stabilize off. **Cancel** stops supported jobs after the current "
            "processing step, so it may not stop immediately."
        ),
    },
    {
        "id": "glossary",
        "title": "Glossary",
        "keywords": "glossary terms definitions words meaning vocabulary",
        "body": (
            "| Term | Meaning |\n"
            "| --- | --- |\n"
            "| Beat | The recurring pulse you tap or count to; not every audible drum hit. |\n"
            "| BPM | Beats per minute: a measure of tempo. |\n"
            "| Bar | A group of beats. This app assumes four beats per bar (4/4). |\n"
            "| Source BPM | A song's own tempo, edited in the **Song settings** panel. |\n"
            "| Project BPM | The mix tempo; every song stretches to it. |\n"
            "| Clip | One use of a library song in the arrangement, with its own kept section and processing choices. |\n"
            "| Arrangement | The sequence of clips and their overlaps on the mix timeline. |\n"
            "| Playhead | The marker showing the playback or listening position. |\n"
            "| Transition / blend | The handover between neighboring clips. An overlap lets both play together. |\n"
            "| Key | The tonal center and major/minor character of musical notes. An estimated label can be wrong or vary within a song. |\n"
            "| Transpose | Shift musical pitch up or down without choosing a new tempo. |\n"
            "| Stabilize | Optional per-clip drift correction; off by default. |\n"
            "| Correction map | The measured timing fixes for part of a song. |\n"
            "| Coverage | The corrected proportion of a song; not a trust score. |\n"
            "| Camelot code | A short key label, such as 8A; see Keys and pitch for compatible candidates. |\n"
            "| Semitone | One half-step of pitch; the **Transpose** unit. |\n"
            "| Cue Start / End | The played slice of a song; **End** finishes the outgoing overlap. |\n"
            "| Overlap bars | How long two songs play together; edit **Overlap** in the Transition box. |\n"
            "| Bass swap | A one-bar low-end handover at the blend's middle. |\n"
            "| Drift | A song's tempo wandering away from a fixed BPM. |\n"
            "| Downbeat | Beat one of a bar. Analyze song estimates these; verify by listening. |\n"
            "| Phrase | A musical thought lasting several bars. Common EDM lengths include 8, 16 or 32 bars; listen for exceptions. |\n"
            "| Build | A passage that increases tension toward an arrival. |\n"
            "| Drop | The arrival of the main groove or impact, often following a build. |\n"
            "| Breakdown | A passage with reduced drums or energy, often emphasizing atmosphere or melody. |\n"
            "| Tail | Sound that continues after a note or phrase, such as reverb or delay. It can still contain pitched notes. |\n"
            "| A/B | Compare two named versions at the same listening position. Read the labels to know what differs. |\n"
            "| Solo | Hear one side of a blend by itself while checking it. |\n"
            "| Artifact | An unwanted sound introduced by processing, such as watery notes or softened drum attacks. |\n"
            "| Clipping | Distortion when audio exceeds the available signal range. |\n"
            "| Render | Preparing audio for in-app playback and seeking. |\n"
            "| Export | Writing the finished mix to WAV, FLAC, or MP3. |\n"
            "| .djmix | A project file referencing your audio by path. |\n"
            "| Headroom | Safety margin below clipping, checked on every render. |"
        ),
    },
    {
        "id": "shortcuts",
        "title": "Keyboard shortcuts",
        "keywords": "shortcuts keys keyboard hotkeys commands undo redo zoom help",
        "body": (
            "These shortcuts use your platform's modifier keys. They apply in the main mix window unless noted.\n"
            "\n"
            "| Press | Action |\n"
            "| --- | --- |\n"
            "| Space | Activate a focused button or dropdown; otherwise play or pause the mix |\n"
            "| P | **Listen to transition** |\n"
            "| [ / ] | Previous / next transition |\n"
            "| Delete or Backspace, with arrangement focused | **Remove from mix** (the song stays in the library) |\n"
            "| {MOD}+Z | **Undo** |\n"
            "| {REDO} | **Redo** |\n"
            "| {MOD}+S | **Save** project |\n"
            "| {MOD}+O / {MOD}+N | Open a project / start a new mix |\n"
            "| {MOD}+I / {MOD}+Shift+I | Import a folder / add files |\n"
            "| {MOD}+. | Stop playback |\n"
            "| F1 | Help for the focused control |\n"
            "| {MOD}+F | Search in the guide window |\n"
            "| Esc | Close the guide or cancel typed number entry |\n"
            "| {MOD}+R | **Prepare and play full mix** |\n"
            "| {MOD}+E | **Export mix…** |\n"
            "| {MOD}+0 | **Fit the whole mix** |\n"
            "| {MOD}+Shift+A | Show or hide **Advanced controls** |\n"
            "| {MOD}+Left / {MOD}+Right | Move the selected clip earlier / later |\n"
            "\n"
            "## Waveforms without a mouse\n\n"
            "Use Tab and Shift+Tab to move between controls. An orange outline marks the focused waveform. "
            "The source waveform highlights the active Start or End marker.\n\n"
            "| Focused waveform | Keys | Action |\n"
            "| --- | --- | --- |\n"
            "| Arrangement | Up / Down | Select the previous / next song without moving it |\n"
            "| Arrangement or blend editor | Left / Right | Seek one beat; hold Shift for one bar |\n"
            "| Arrangement or blend editor | Home / End | Seek the beginning / end of the mix or blend |\n"
            "| Mix overview | Left / Right | Seek and scroll one bar; hold Shift for eight bars |\n"
            "| Mix overview | Home / End | Seek and scroll to the beginning / end of the mix |\n"
            "| Source Start/End markers | Up / Down | Select and show Start / End |\n"
            "| Source Start/End markers | Left / Right | Move that cue to the next nearby bar line, or one bar in an unmeasured passage |\n"
            "| Source Start/End markers | Shift+Left / Shift+Right | Move that cue by a beat |\n"
            "| Source Start/End markers | {ALT}+Left / {ALT}+Right | Move that cue by 10 milliseconds |\n"
            "| Arrangement or source markers | + / − / 0 | Zoom in / zoom out / fit the whole mix or song |\n\n"
            "In the **Song cues** dialog, Left/Right seeks through the original song (Shift by a bar); "
            "Home/End seeks its boundaries. Up/Down selects Start/End, and {ALT}+Left/Right adjusts that cue "
            "by 10 milliseconds. Its number boxes allow larger changes. These are draft cues until **Apply changes**.\n\n"
            "Cue edits use the same overlap and protected-passage checks as dragging and can be undone. "
            "Selecting a song or seeking does not change your mix. See [Arrangement](help:arrangement).\n\n"
            "**Preview position sliders:** in the blend editor, Current/Draft comparison, suggested-order preview, "
            "Start & ending, and the reference-click player, **Left/Right** moves one second and "
            "**Page Up/Down** moves five seconds. **Home/End** goes to either end of the preview. "
            "Seeking keeps paused audio paused; choose Listen to start it. In an A/B comparison, both versions "
            "stay on the same clock. The library's original-song slider uses ten-second page steps, and the "
            "short listening lessons use quarter-second arrows and two-second page steps.\n\n"
            "Home returns **Back to the start**. In number boxes, drag to change values (Shift for fine steps), "
            "double-click to type, and use Up and Down to nudge. In the sample display, {MOD}+scroll zooms at the "
            "pointer while plain scroll or ruler drag pans; hold Shift to snap markers to beats, or {ALT} for free placement."
        ),
    },
)

TOPICS = COURSE_TOPICS + REFERENCE_TOPICS
