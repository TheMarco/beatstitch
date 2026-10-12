"""An ordered beginner course, kept separate from the detailed control reference."""
from textwrap import dedent


def lesson(identity, title, keywords, body):
    return {'id': identity, 'title': title, 'keywords': keywords, 'body': dedent(body).strip()}


LESSONS = (
    lesson('learn-first-listen', '1 · Hear what a mix does',
           'beginner tutorial first lesson demo practice start listen', '''
    **Your aim:** hear one song hand over to another, and learn where to listen.
    You do not need a controller, piano knowledge or experience with a DAW.

    ## Start with music that is already prepared

    If you already have a mix you want to keep, choose **File → Save** first.
    Then choose **File → Try a demo mix**. This opens three short, included songs
    with an editable arrangement. Your previous unsaved work is also kept under
    **Projects… → Recovery copies**, marked **Before trying the demo mix**.
    Opening the course itself never replaces a mix or starts playback.

    The demo is a practice space. Its BPM, keys and beat grid are known from its
    composition. Imported music needs checking; the demo's accuracy does not
    show how well detection will work on your songs.

    1. Check **Audio output** at the top of the main window. Choose the headphones
       or speakers you intend to use and set a comfortable listening volume.
    2. Press **Home**, then **Play mix**. The first playback may prepare audio.
       Let that finish; you do not have to run a separate rendering command.
    3. Listen for the moment the first song gives way to the second. During the
       overlap, both play. The aim is an intentional handover, not necessarily
       an inaudible edit.
    4. Stop playback and select the second song in the arrangement. Check that
       the transition panel says **1 → 2** and names the expected songs.
    5. Choose **Listen to blend**. This plays the join with music on both sides.
       Turn on **Loop blend** if you want to hear it repeat.

    ## Listen for three things

    **Pulse:** do the kicks feel like one rhythm, or do you hear a stumbling
    double hit? **Notes:** do the musical parts fit, or does the overlap sound
    sour? **Attention:** can you follow the main musical idea, or are two voices
    or leads demanding attention at once?

    You do not have to name a key or calculate a BPM to notice these differences.
    Open **Listening examples…** at the top of this guide for short A/B lessons
    on drifting beats, clashing notes and competing basslines. Switch A and B
    while the example plays, listening to the same moment at matched levels.

    **Checkpoint:** you can play the whole mix, select a specific pair, and hear
    that pair on its own. Next, learn which parts of the screen affect which music.

    [Demo details and returning to your own project](help:demo) ·
    [Listening examples explained](help:listening-lessons) · [Playback help](help:playback)
    '''),
    lesson('learn-workspace', '2 · Know what each control changes',
           'beginner tutorial workspace library arrangement source clip transition scope undo', '''
    **Your aim:** know whether you are changing an original song, one use of it,
    one transition, or the entire mix.

    ## Four places to look

    **Library** is your collection of source recordings. Selecting a library song
    lets you hear the original with **Listen**, **Start** or **Ending**. That sound
    has no mix tempo change, transposition or transition processing. Double-clicking
    a row, dragging it to the arrangement, or using **Add selected songs** inserts it.

    **Arrangement** is the running order of your mix. A block is a *clip*: one use
    of a library song. The alternating Deck A and Deck B lanes help show overlapping
    clips; you do not have to operate two live turntables. Select a clip to see its
    song details and real source waveform. Click or drag the overview to move the
    playhead, the marker showing where main playback will start.

    **Song details** describe the selected clip. **Start** and **End** choose which
    part of the recording to keep. **Keeping the original key** reports its pitch
    choice. **More settings…** reveals exact values and advanced controls.

    **Transition** describes two neighboring clips. Check its pair number and both
    names before editing. The incoming clip owns the overlap with the previous one:
    select song 3 to work on **2 → 3**. Song 1 has no incoming transition.

    ## The scope of a change

    - **Source BPM and Source key:** shared by every use of that recording in this
      project. Changing the BPM changes how those clips are stretched. Changing
      the key label changes advice; it does not move the notes.
    - **Clip Start/End, pitch shift and Stabilize:** affect that particular use of
      the song. A second copy of the same song can have different clip settings.
    - **Overlap and bass handoff:** affect the selected incoming transition.
    - **Project BPM or tempo curve:** affect the mix as a whole.
    - **Listening volume and Audio output:** affect monitoring, not export loudness.

    The original audio file is kept intact. Some advanced operations create a new
    recording and replace a clip with that copy; they say so before you apply them.

    ## Practice without losing your place

    Select the second demo clip, then the third. Watch the pair change from **1 → 2**
    to **2 → 3**. Switch **Advanced controls** on and off: the music settings and
    real waveforms remain. Hidden advanced settings still apply; the Simple view
    summarizes active pitch and correction choices.

    Use **Undo** ({MOD}+Z) after an applied mix edit you dislike. **Discard draft**
    closes a review window without applying its choices. Opening a project and
    importing songs have different recovery behavior; Undo is not a substitute
    for saving a project you care about.

    **Checkpoint:** you can point to the selected song, the selected pair and the
    mix playhead, and explain which one your next edit will affect.

    [Workspace reference](help:workspace) · [Arrangement controls](help:arrangement) ·
    [Keyboard operation](help:shortcuts)
    '''),
    lesson('learn-choose-songs', '3 · Choose and import your songs',
           'beginner tutorial import choose songs files folder library selection duplicates', '''
    **Your aim:** start with a small set that gives you a fair chance of an easy mix.

    ## Choose two or three songs you know

    Similar styles and nearby tempos usually make a useful first exercise. Listen
    to the endings and beginnings: a reasonably clear drum passage gives a blend
    room to develop. A sung line starting immediately over another song's final
    chorus takes more care. Avoid solving every difficult pairing in your first mix.

    Pick the musical journey too. Would you like to build energy, hold a steady
    groove, or finish gently? Loudness and BPM do not fully describe energy: a
    sparse fast song can feel calmer than a dense slower one.

    ## Import and audition

    1. In **Library**, choose **Add files…** for individual recordings or
       **Add folder…** for a folder and its subfolders. File and folder drops also work.
    2. Let the background analysis finish. The status area explains which work is
       being done and when existing results are reused. Your source files stay intact.
    3. Select a library song and choose **Listen**. Try **Start**, **Ending** and the
       seek slider. These audition the original; they do not add it to the arrangement.
    4. Note its BPM and key, but treat them as estimates. If a value looks surprising,
       check the rhythm or notes before basing a whole mix on it.

    You can position the source slider before listening; seeking keeps paused audio
    paused. With the slider focused, **Left/Right** moves one second, **Page Up/Down**
    moves ten seconds, and **Home/End** goes to the beginning/end. Choose **Listen**
    to start from that position. This is separate from moving a song's Start/End cues.

    The key detector, **OpenKeyScan**, estimates from the first minute, or the whole
    source if shorter. Later musical passages can differ. **Beat This** supplies the
    beat-based BPM estimate; an opening-passage estimate is also retained when one
    is available. If beat estimation is inconclusive, the opening method can supply
    the BPM. Neither method guarantees the right rhythmic interpretation.

    ## Keep your choices in control

    To correct a key, reveal **Advanced controls** and choose **Source key**, including
    **Unknown** if you cannot tell. **Detect key** obtains an estimate; **Restore detected**
    explicitly accepts it. Your manual and previously saved key choices are preserved.
    A key label is information, not an audio effect.

    Saved songs keep their BPM when a project is reopened or the same source is
    imported again. Adding a third song should reuse valid work for unchanged songs.
    Explicit reanalysis and edits can require work again. A library source can be used
    more than once in an arrangement deliberately; that is different from importing
    another duplicate library entry.

    **Practice:** audition the ending of your proposed first song and the opening
    of the second. Identify a passage where drums are clear and words are absent.
    Check for pitched bass and lingering notes too; "no singing" does not mean drums only.

    **Checkpoint:** you have a few playable sources, a rough idea of their shape,
    and no unexplained BPM value. You can still change the order later.

    [Import, collections and filters](help:library) · [Missing files](help:projects) ·
    [Correct a tempo estimate](help:learn-check-bpm)
    '''),
    lesson('learn-build-mix', '4 · Build your first arrangement',
           'beginner tutorial mix guide first arrangement order energy tempo create draft', '''
    **Your aim:** create a short, reviewable arrangement while keeping the important choices understandable.

    ## Use the Mix guide

    Choose **Mix guide** above the arrangement, or in **File**. If you already have
    an arrangement, save it first. The guide creates a new arrangement; its review
    says **Replace arrangement** when it will replace the current one.

    1. **Choose songs:** check at least two different library songs. Audition them
       if needed. A source with no usable BPM must have one entered before inclusion.
       Filtering the list does not uncheck songs hidden by that filter.
    2. **Mix settings:** inspect the suggested common BPM and each song's speed
       change. A 124 BPM source played in a 128 BPM mix is about 3.2% faster.
       A 100 BPM source at 128 BPM is 28% faster and is a much more demanding change.
       Those percentages describe speed, not sound quality ratings.
    3. Choose a **Starting overlap**. Eight bars is a useful experiment, not a rule.
       The guide shows the duration and reduces overlaps where available audio is
       insufficient. Read its notes about any shortened pairs.
    4. **Review the draft:** inspect the proposed order and audition its blends.
       You can choose an opener, closer, fixed positions and energy ratings in the
       order review. A suggestion considers labels and measurements; it does not
       know the story you want the set to tell.
    5. Choose **Create mix** or **Replace arrangement** only when you want that draft.
       **Cancel** leaves the arrangement alone; songs you imported stay in the library.

    The guide starts with original keys and a bass handoff. It does not automatically
    transplant all the pitch, correction or cue edits from an older arrangement.
    One Undo restores the previous arrangement after Create/Replace.

    ## Or arrange directly

    Double-click a library row or drag it onto the arrangement. Add the next song,
    then select its incoming transition. Multi-select with {MOD}-click, or Shift for
    a range, to add several in the library's displayed order. You can move selected
    clips earlier or later and ask **Suggest order** for another reviewable proposal.

    Keep your first arrangement short enough to hear all of it. Start by accepting
    reasonable small speed changes and preserving pitch. A dramatic tempo change
    or pitch shift is a creative choice to test, not something to turn on simply
    because an algorithm offers it.

    **Listen:** the proposed handover should make musical sense on both sides.
    A technically related key pair can still contain competing singers, an abrupt
    energy drop, or a drop that arrives at the wrong moment.

    **Checkpoint:** your chosen songs appear once each in the intended order,
    the project tempo is understandable, and you know which pair needs attention first.

    [Guided setup details](help:first-mix) · [Order and energy controls](help:planning) ·
    [Mix review](help:mix-review)
    '''),
    lesson('learn-check-bpm', '5 · Check tempo and the beat grid',
           'beginner tutorial wrong BPM half double beat grid downbeat tap tempo click estimates', '''
    **Your aim:** distinguish a wrong BPM number, a misplaced grid and actual tempo drift.

    **Beat** is the pulse you tap to. **BPM** counts those beats per minute.
    This app assumes **4/4**: count "ONE, two, three, four" to make a bar.
    The first beat of a bar is the *downbeat*. A phrase is a larger musical thought,
    often several bars; EDM commonly changes over 8, 16 or 32 bars, but exceptions matter.

    ## Source tempo and mix tempo are different

    **Source BPM** tells DJ Studio how fast the recording originally plays.
    **Project BPM** tells it how fast the mix should play. The app changes duration
    while preserving pitch unless you also enable transposition. Typing a different
    source BPM is a correction of the source description, not the same decision as
    choosing a faster project tempo. It affects every clip using that source here.

    A 140 BPM song can be estimated at 70 BPM if a detector counts every other beat.
    Both can sound rhythmically plausible, but choosing the wrong interpretation
    changes stretching and the meaning of an eight-bar overlap. Do not automatically
    force every EDM track into a preferred BPM range.

    ## Check the estimate in a draft

    1. Select the clip, open **Song cues…**, then **Check beats and BPM**.
    2. If needed, use **Analyze song** to obtain detected beat observations. This
       action supplies observations and suggestions; it does not secretly accept
       a new BPM for an existing clip.
    3. Read the current draft BPM and the available **beat-based** and
       **opening-passage** estimates. The opening estimate comes from an early
       passage; the beat estimate uses detected beats across the source.
    4. **Try beat: … BPM** or **Try opening: … BPM** changes this draft and uses a
       regular reference grid. A button is disabled when that estimate is already
       selected. An absent or inconclusive estimate cannot supply a choice.
    5. Use **Listen**, turn on **Click**, and check the start, middle and ending.
       The song still plays at original speed and pitch here. The click changes
       to help you evaluate the reference; it is never included in exports.

    **If the number is wrong:** try **÷2**, **×2**, or **Tap tempo** for several beats,
    then **Use tapped BPM**. Tap along with the main pulse consistently. A short
    tapping exercise is a starting point, not a precise long-song measurement.

    **If the pulse fits but beat 1 is misplaced:** pause, zoom into the waveform,
    choose a known first beat of a bar and use **Mark playhead as beat 1**. The manual
    grid is a reference; it does not move drum hits or straighten the recording.
    Do not mistake every loud peak or every kick for the first beat of a phrase.

    **If two songs have the same BPM but their kicks land apart in a blend:** choose
    **Align beats…** in the Transition box. It suggests a timing draft and shows
    measured kick hits for both songs. Before / Adjusted switches while it keeps
    playing; the B earlier/later buttons and dragging update the beat check live.
    Then listen to the actual blend and Apply only if you prefer it. The
    [beat-alignment guide](help:beat-alignment) explains bar accents and uncertainty.

    **If the click progressively pulls away:** first recheck the BPM and anchor.
    A tiny wrong BPM accumulates an error over time. Irregular changes may be drift;
    the [tempo-correction lesson](help:learn-drift) explains the separate check.

    **Apply changes** commits one undoable edit. **Discard draft** keeps the mix's
    previous settings. Matching estimates are encouraging evidence, not proof.
    **Checkpoint:** the pulse and bar counting make sense in several parts of the song.

    [Full beat controls](help:musical-cues) · [Tempo reference](help:tempo)
    '''),
    lesson('learn-phrases', '6 · Choose musical starts and endings',
           'beginner tutorial phrase bars cue start end downbeat waveform protect vocals drop build', '''
    **Your aim:** bring a song in and let it leave at moments that make musical sense.

    ## Listen to the shape of the music

    A *build* increases tension; a *drop* is a major arrival or release. A *breakdown*
    may remove drums or reduce intensity. A *phrase* is a musical unit that often
    ends where the texture, melody or drum pattern changes. These are listening
    descriptions, not labels every song follows perfectly.

    Count bars during a clear section and notice the changes. A cymbal, new bassline
    or returning vocal may mark a phrase boundary. The waveform shows loud and quiet
    sections, but its shape alone cannot tell you the key, words or exact phrase start.
    AI-generated music may have irregular phrase lengths or a beat that shifts.

    ## Work on one clip's kept passage

    **Start** is where this use of the source begins. **End** is where its kept audio
    finishes, including its outgoing overlap. The next song starts before that End,
    by the chosen overlap length. Moving a cue changes what music survives and how
    the clips can overlap; it is more than moving a visual marker.

    1. Select a song and open **Song cues…**. **Start cue** and **End cue** zoom to
       their respective markers. **Fit** shows the whole source.
    2. Read a cue suggestion, then choose **Listen here** before trying it.
       Suggestions identify supported beats or sound changes, not certain musical intent.
    3. Use **Try start** or **Try end**, or adjust the draft cue controls. Keep enough
       material around a musical arrival for it to feel complete.
    4. **Apply changes**, then **Listen to blend** in the main window. Cue audition
       plays the original song; the actual blend reveals how both songs meet.

    You can also drag Start/End on the main real waveform. These direct edits take
    effect in the arrangement and support Undo. The waveform's keyboard controls
    provide the same precise choices; see the linked reference.

    ## Protect moments you care about

    A final lyric, rising build or favorite drop may deserve to remain unobscured.
    Use **Protect a passage of this song** to mark a range you want kept complete
    and outside blends. Protection is an arrangement constraint, not an audio effect.
    If an edit is rejected because it would cover a protected passage, move the cue,
    shorten the overlap, or deliberately review that protection.

    **Practice:** in the demo, hear the drum-only intro, musical middle and drum-only
    ending. In your own music, find equivalent useful passages by listening. A gap
    in singing may still contain a pitched bassline or a reverb tail.

    **Checkpoint:** no important line is cut off, the incoming section arrives at an
    intentional moment, and the outgoing song has enough room to finish.

    [Cue controls and detection limits](help:musical-cues) ·
    [Protected passages](help:protection) · [Keyboard waveform controls](help:shortcuts)
    '''),
    lesson('learn-overlap', '7 · Make a blend longer or shorter',
           'beginner tutorial overlap length longer shorter stepper bars transition 2 3 available audio', '''
    **Your aim:** choose how long two songs share the foreground.

    Yes, overlap length is adjustable. It is one of the most useful controls in the
    app. A longer overlap gives a gradual introduction room to unfold; a shorter
    overlap can avoid competing vocals or make a handover more direct. Longer does
    not automatically mean smoother or more professional.

    ## Find the right pair first

    Select the incoming song in the arrangement, or its numbered transition. For
    **2 → 3**, select song 3 and confirm both song names in the transition panel.
    **Previous transition** and **Next transition**, or [ and ], move between pairs.
    **Listen to blend** always uses the selected pair, not necessarily the first two songs.

    ## Try a small, controlled change

    1. Listen to the current overlap once. Note what you want to improve.
    2. Use the **− / +** buttons beside **Overlap** to change it by one bar. The
       number can also be typed; arrow keys nudge it. Presets offer 4, 8, 16 or 32 bars
       when enough audio is available. The overlap edge can also be dragged on the timeline.
    3. Read the duration in seconds under the stepper. At a fixed 128 BPM, 4 bars
       take 7.5 seconds, 8 take 15 seconds, 16 take 30 seconds, and 32 take 60 seconds.
       With a tempo curve, the displayed duration follows the actual curve.
    4. Replay **Listen to blend**. Audio affected by the edit is prepared before
       playback. Listen from before the overlap through the outgoing song's departure.
    5. Keep the change, or use Undo to return to the previous length.

    For a draft comparison without immediately changing the arrangement, use
    **Open blend editor**, change the overlap there and compare Current with Draft.

    ## Why a longer value may be unavailable

    Each clip has a finite kept passage between Start and End. Its other neighboring
    overlap also needs room. The app shows the maximum that fits and explains which
    clip limits it. You can shorten the blend or widen a clip's kept range if the
    source has suitable extra material. The normal control supports up to 32 bars.
    Forcing a larger number cannot create missing audio.

    Changing the overlap keeps cue choices but changes how the songs line up in the
    arrangement. Check their phrase relationship again; the numbers alone cannot
    ensure that a new drop lands where you want it.

    **Practice:** compare 8 bars with 4 bars on the demo's first transition. Listen
    for how quickly attention passes to the incoming song. If both work, choose the
    one that feels right for the set's pacing.

    **Checkpoint:** the overlap fits both sources, serves a musical purpose and
    sounds coherent all the way through—not only at its midpoint.

    [Transition reference](help:transitions) · [Longer drum passages](help:drum-loops)
    '''),
    lesson('learn-keys', '8 · Make musical parts fit together',
           'beginner tutorial key pitch transpose semitone Camelot harmony musical drums only override', '''
    **Your aim:** use key advice without letting it overrule what you hear.

    A **key** is a musical home for a set of notes, such as A minor. **Pitch** is how
    high or low those notes sound. A **semitone** is one adjacent piano-key step,
    counting black keys too. Tempo and pitch are separate choices here: playing
    faster at the project BPM normally keeps the recording's original pitch.

    ## Labels and audio are different

    Changing **Source key** corrects information used for advice. It does not retune
    the recording. **Play in key** changes the notes in the selected clip by a number
    of semitones. Its destinations retain the source mode: minor stays minor and
    major stays major. **Restore detected** restores a manually corrected source
    label to the saved estimate; **Detect key** obtains an estimate if none is saved.
    Pitch changes affect the clip's kept music, so listen to the whole clip and both
    neighboring transitions when you change it.

    The Camelot code is a shortcut for comparing keys. **8A** means A minor. Common
    candidates for 8A are 8A, 7A, 9A and 8B. A means minor; B means major. Numbers wrap
    from 12 to 1. You do not have to memorize the wheel: use the app's labels as
    suggestions and listen to the actual overlap.

    ## Try a pitch choice carefully

    1. Listen with the original key first. If the notes already fit, leave it alone.
    2. Check the detected key. A wrong label gives misleading advice. **Detect key**
       obtains an estimate; **Restore detected** accepts it. Choose a known key or
       **Unknown** yourself when appropriate.
    3. Choose a destination in **Play in key**, then click its **Listen to blend**
       button (or **Listen in mix** for one song). This prepares the changed audio;
       it does not retune playback already in progress. The library Listen button
       still plays the original recording. An available **Try … semitone(s)**
       suggestion considers both neighboring key labels, but cannot hear every
       musical conflict in those overlaps.
    4. In Simple view, **Use original key** bypasses an active shift, and
       **Restore … shift** restores it. Advanced semitone controls allow a
       manual amount and an on/off choice. Bypass remembers the amount.
    5. Replay the actual blend after edits, or use the blend editor's Current/Draft
       comparison. Check vocal character, bass and both transitions around the clip.

    A related-key pair can still have two dominant melodies or singers competing.
    A shorter overlap or different cues may solve that more naturally than a pitch
    change. Larger shifts can make voices or instruments sound altered even when
    the new key relationship is theoretically useful.

    ## When drums let you change key freely

    If the entire overlap truly has no pitched bass, voices, melody, tonal percussion
    or lingering pitched tails, there may be no competing musical key to match.
    But "the singer stopped" or "the last few hits are kicks" is not enough evidence.
    Listen to the whole overlap and its tails.

    **Drum intro / Drum outro** labels tell the advice that you have identified such
    a passage. They do not remove any notes, separate stems or make a clash disappear.
    Review them again if you move cues or change overlap length.

    **Checkpoint:** you understand whether you edited a label or changed the sound,
    and any pitch change earns its place in both neighboring blends.

    [Key controls](help:keys) · [Check actual overlap harmony](help:transitions) ·
    [Hear clashing notes](help:listening-lessons)
    '''),
    lesson('learn-listen-blends', '9 · Improve a transition by listening',
           'beginner tutorial A/B current draft bass handoff loudness vocals melodies filter echo blend editor', '''
    **Your aim:** make one useful improvement at a time, with a fair comparison.

    Select the pair and choose **Open blend editor**. Its two real waveforms show
    the outgoing and incoming music together. Draft edits stay in this window until
    **Apply changes**. **Discard draft** leaves the current arrangement unchanged.

    ## A repeatable listening method

    1. Hear the current blend, including several beats before and after the overlap.
       Say what is wrong in plain language: "the kicks stumble," "the singer gets
       covered," "the bass suddenly booms," or "the energy disappears."
    2. Change one relevant setting in the draft. Avoid changing pitch, overlap,
       cues and effects together; you would not know which change helped.
    3. Prepare **Compare current / draft**. Switch between the versions while they
       keep playing at the same position. Listening levels are matched so simple
       loudness does not decide the winner. Preparing again is needed after edits.
    4. Solo the outgoing or incoming side when you need to identify the offending
       note or sound. Solo listening helps diagnose; judge the result with both sides.
    5. Apply only if the draft helps. If the difference is subtle and both sound good,
       keeping the simpler choice is a valid result.

    ## Match the control to the problem

    **Two basslines compete:** try **Bass handoff**. It passes low frequencies from
    one song to the next over one bar near the blend midpoint, rather than letting
    both low ends crossfade together. Compare it on and off. It cannot separate
    instruments perfectly and will not resolve two clashing lead melodies.

    **One side overwhelms the other:** try the blend's level-balancing choice and
    listen for a jump before or after the transition. Listening volume is separate;
    turning the main volume down does not fix a balance problem in the export.

    **Two singers or leads compete:** try a different passage or shorter overlap
    before more processing. Local content or harmony checks can offer evidence, but
    a favorable label does not establish that the musical combination sounds good.

    **The departure feels awkward:** an optional restrained filter or echo may help.
    Start with **No extra effect**, compare the alternative, and listen to the tail.
    An effect should support the handover rather than hide an unresolved beat error.

    **Practice:** open **Listening examples… → Hear competing basslines**. Listen
    around the middle of the example before trying Bass handoff in your own blend.

    **Checkpoint:** you can name the improvement and hear it at the same moment in
    both versions. After applying, check the adjacent transition too if the edit
    changes the clip's pitch, cues or available audio.

    [Blend editor, checks and alternatives](help:transitions) ·
    [Review the whole set](help:mix-review)
    '''),
    lesson('learn-drift', '10 · Deal with wandering tempo',
           'beginner tutorial drift AI generated unstable timing stabilize disabled unavailable correction A/B', '''
    **Your aim:** understand what Stabilize can correct and why it may be unavailable.

    **Tempo matching** changes a song's overall speed to the project BPM. **Tempo
    stabilization** makes local timing adjustments where supported measurements
    provide a correction. If a recording speeds up and slows down internally, one
    constant speed change cannot remove that wandering. AI-generated music can have
    this problem, as can recordings with human timing or intentional tempo changes.

    ## Check the simple explanations first

    Listen to the original with a reference click at several points using
    **Song cues… → Check beats and BPM**. A consistently wrong BPM, a half/double
    interpretation or the wrong bar-start anchor needs a grid/BPM check. A manual
    grid merely draws and plays a reference; it does not warp the source audio.

    Select the clip, reveal **More settings…**, and use **Analyze** beside the timing
    controls to measure drift. This preserves saved BPM and cue choices. An offered
    **Use …** value requires an explicit choice; analysis is not permission to replace
    your settings.

    ## Read the reason, not just the disabled checkbox

    - **Timing not analyzed:** obtain a timing measurement first.
    - **No significant drift detected:** measured passages did not show substantial
      drift. There is no correction map to turn on. This does not prove every part
      of the song is steady or that the BPM is correct.
    - **Timing inconclusive:** there was not enough consistent evidence to make a
      safe correction map. The song might be steady or drifty; this result cannot
      decide. Repeating unchanged analysis may reuse the same result.
    - **Correction available:** a supported correction exists for part of the source.
      It is available to audition, not automatically better.
    - **Processing in progress:** wait for the current task; controls are temporarily
      unavailable while work is running.

    ## Audition before enabling

    Use the timing **A/B** comparison where a correction is available. The comparison
    isolates tempo correction against the ordinary processing at its reference
    tempo; it is not the complete two-song blend. Switch while it plays and listen
    for steadier kicks, but also softened attacks, warbling, phasing or broken tails.
    If both versions sound almost identical, that is evidence that obvious damage
    is absent in that passage—not proof that every beat has been corrected.

    Enable **Stabilize** for that clip if the correction helps, then listen to the
    actual blend and the whole kept passage. Turn it off if the result is worse.
    The **coverage** percentage describes how much source audio is corrected. It is
    not an accuracy percentage; unsupported passages retain fixed timing.

    If no useful correction is available, try a shorter overlap in a steadier passage,
    different cues, or another pair. There is no obligation to force a long blend
    through a difficult recording. Intentionally expressive timing may be worth keeping.

    **Checkpoint:** you can explain the current status and have chosen correction
    for an audible reason, not simply because a checkbox exists.

    [Timing details](help:timing) · [Beat-grid checks](help:learn-check-bpm) ·
    [Hear a drift example](help:listening-lessons)
    '''),
    lesson('learn-troubleshoot', '11 · Fix the problem you actually hear',
           'beginner tutorial troubleshoot no sound stale preview wrong output flamming vocals muddy hollow processing', '''
    **Your aim:** diagnose the symptom before adding more processing.

    ## Start with the right playback

    **Library Listen** and the cue window play original source audio. **Play mix**
    and **Listen to blend** play prepared arrangement audio. An A/B window compares
    the specific versions named there. If an edit seems to do nothing, confirm
    which one you are hearing. Main playback starts at the playhead; use Home to
    hear the beginning. After changing settings, request playback again so the
    affected audio is prepared with those choices.

    **No sound or the wrong speakers:** check Audio output, the selected device's
    connection, and listening volume. Choose your headphones explicitly or use
    System output to follow the operating system. A disconnected selected device
    can pause or mute playback until it returns or another output is selected.

    ## Musical symptoms and first actions

    **Stumbling or doubled kicks:** solo each side. Check source BPM, half/double
    counting and bar-start placement. Then consider drift. Key transposition does
    not repair a timing error.

    **Sour or uncomfortable notes:** compare original pitch with a small alternative,
    check the source-key labels, and listen for a key change or a pitched tail.
    Different cues or a shorter overlap may work better than transposition.

    **Words or melodies fight:** move the incoming start past a vocal, let the outgoing
    phrase finish earlier, shorten the overlap, or choose another order. Matching
    keys alone cannot make two simultaneous stories intelligible.

    **Bass booms or thins out:** compare Bass handoff and blend level balancing.
    Check the midpoint and the moments on either side. Do not judge bass only on
    speakers that cannot reproduce it clearly.

    **Energy suddenly disappears:** listen to the section choices and ordering.
    You may be overlapping two breakdowns or cutting away the expected arrival.
    A louder export will not restore the missing musical build.

    **Watery, hollow or unnatural sound:** bypass optional separation or stabilization,
    reduce the pitch shift or speed change, and compare again. This helps isolate
    processing artifacts from sounds already present in the original.

    ## When controls refuse a change

    Read the nearby reason or tooltip. A longer overlap may need more kept audio;
    a protected passage may prevent a proposed cue; a feature may need its optional
    model pack; a source file may be missing. These require different remedies.
    Use **Mix review** to visit relevant settings rather than changing unrelated ones.

    Background work reports progress and reuse. Unchanged analysis or processed
    audio can be reused; a changed cue, pitch, timing choice or source may invalidate
    affected results. Cancel stops supported work safely, sometimes after the current
    small step. Repeatedly starting the same task does not make it more reliable.

    **Practice:** choose one real issue, write a one-sentence description, try one
    remedy and compare. Keep the old version if you cannot hear an improvement.

    [Troubleshooting reference](help:troubleshooting) · [Playback](help:playback) ·
    [Missing files, storage and recovery](help:projects)
    '''),
    lesson('learn-finish-export', '12 · Finish, save and export',
           'beginner tutorial finish fade tail save export WAV FLAC MP3 portable project recovery', '''
    **Your aim:** keep an editable project and produce a finished audio file you can trust.

    ## Check the beginning and ending

    Listen from the very start. Does the first kick arrive cleanly, or does the mix
    begin in the middle of a word? A fade-in may suit an ambient opening but soften
    a strong first beat. Choose it for the sound you want.

    Use **Edit → Start & ending…** for a reviewable fade choice and listen to the final tail.
    A source **End** cue cuts off everything after it; a fade cannot restore audio
    beyond that cue. Widen End when the actual recording has a reverb or delay tail
    you want to preserve. A sudden ending can be intentional too.

    ## Save the project before exporting

    **File → Save** ({MOD}+S) stores an editable **.djmix** project: the arrangement,
    cues, tempo and other choices. An ordinary project refers to source files in
    their existing locations. It is not a self-contained audio mix or automatically
    a backup of your music collection.

    Give useful versions distinct names with **Save As…** before a large experiment.
    Autosave and **Projects… → Recovery copies** help recover work, but keeping a
    named project is the clearest way to know which version you intended to retain.

    For another computer or drive, use **Create portable project…** to copy the
    required media with the project. Move the whole portable folder. If files are
    missing, use the project's relink workflow and review matches; merely importing
    a file from a new location does not reconnect every old clip to it.

    ## Export playable audio

    1. Choose **Export…** in the main window, or **Export audio…** in the Mix guide.
    2. Choose **WAV** for an uncompressed lossless master, **FLAC** for a smaller
       lossless file, or **MP3** for a convenient smaller sharing copy. Converting
       a lossy original to WAV cannot restore detail that was already missing.
    3. Check the destination, estimated size and optional timestamped tracklist.
       Pick a filename that tells you which version it is.
    4. Let export complete. Use its playback or file-reveal controls to check the
       finished file. Export is separate from saving your editable project.

    Listening volume does not set export loudness. Bluetooth delay affects what
    you hear during monitoring, not the placement of samples in the exported file.
    Changing an edit afterward does not update an already exported file; export
    another version when you want those changes included.

    **Final listening pass:** hear the full set from Home, including every transition,
    the first sound and the last tail. Avoid making unheard edits immediately before
    declaring a version finished. If possible, check the exported file on another
    playback system as well as the one used while editing.

    **Checkpoint:** you have both a named .djmix project and a playable export,
    know where each is saved, and understand which source files the project needs.

    [Start and ending controls](help:finishing) · [Saving, relinking and export](help:projects)
    '''),
    lesson('learn-optional-tools', '13 · Know when advanced tools help',
           'beginner tutorial advanced optional stems separation drums loop extension tempo curve builds drops', '''
    **Your aim:** recognize useful extra tools without feeling obliged to use all of them.

    A good short mix may need only reasonable cues, a common tempo and a suitable
    overlap. Add another process when you can name a problem it solves, then compare
    it with your simpler version. Some optional tools need a model pack supplied
    with the app edition; an unavailable action should explain that requirement.

    ## Not enough intro or outro

    **Extend drums…** repeats complete bars from a passage you choose near the
    beginning or ending. It cannot invent a natural intro from unrelated material,
    and it does not remove a melody from that passage. Listen for a repeated vocal,
    cymbal, fill or reverb tail that gives the loop away. A subtly smoother join is
    useful only if you prefer it; repetition can remain obvious even with a clean join.

    Compare the proposed copy and both neighboring blends. Applying creates a new
    recording for the selected clip while the original remains available. Follow
    [the loop-extension reference](help:drum-loops) for supported ranges and cue handling.

    ## Too much voice or melody in the overlap

    **Reduce competing parts…** offers **Reduce vocals** or **Keep mostly drums**
    for the selected side of one blend. Start with a partial reduction. A 75% setting
    describes the blend with estimated separated parts; it does not guarantee that
    75% of every word disappears.

    Compare the actual two-song blend. Listen for leftover words, hollow texture,
    damaged attacks or missing musical parts. Apply only if that tradeoff helps.
    Separation can leave pitched content in the estimated drums.

    This creates a treated copy at the chosen source times. Later cue, order or
    overlap changes do not move the treatment to a new passage. Review it again,
    and use Undo before preparing another treatment so changes do not stack by accident.
    [Separation reference](help:separation).

    ## A set that gradually changes speed

    **Tempo curve…** sets a project BPM at musical bars. For example, 124 BPM at bar
    1 and 128 BPM at bar 65 makes a gradual rise. Both overlapping songs follow the
    curve. It is a creative set-level change, separate from fixing drift inside a source.
    Reordering the mix does not move your curve points automatically; review their
    musical placement afterward. [Tempo-curve reference](help:tempo-curve).

    ## Tentative musical-section labels

    **Builds and drops** can help you find candidate moments to audition when the
    optional analysis is available. A label is not a command to cut there. Listen to
    the actual passage, keep protected moments in mind, and use your own cue if the
    suggestion misunderstands the song.

    **Checkpoint:** you can explain what the chosen tool changes, what it leaves
    alone, and how you will return to the earlier version if it sounds worse.

    [Musical cues and section analysis](help:musical-cues) · [Protect important music](help:protection)
    '''),
    lesson('learn-final-check', '14 · Develop your own mixing routine',
           'beginner tutorial final checklist practice independent listening review excellent mix', '''
    **Your aim:** finish a mix by musical judgment, with the app supplying useful assistance.

    ## A repeatable routine

    **Choose:** start with songs whose journey interests you. Check their original
    beginnings, endings and important moments. Nearby tempos make the first attempts
    easier; unusual combinations can come later.

    **Arrange:** make a short draft, read the speed changes, decide the order and
    keep original pitch as your starting point. Save a named project version.

    **Check:** verify surprising BPM or key estimates. Check the pulse in multiple
    passages, not only where the first kicks happen. A stable-looking waveform or
    agreeing algorithms cannot guarantee a correct grid.

    **Refine:** work on one pair at a time. Set musical cues, choose an overlap,
    then address bass, levels or pitch only as needed. Use A/B to compare the same
    moment, and change one setting at a time so the result is understandable.

    **Review:** choose **Review all** to visit the joins. **Keep and next** records
    your listening choice; **Next** leaves that join unchecked. **Change** opens its
    editor. A kept marker records a decision, not an objective guarantee of quality.
    After relevant edits, listen again.

    **Finish:** play the entire mix, check its start and ending, save the project,
    export, and listen to the exported file. A string of good local joins can still
    have uneven pacing across the full set.

    ## Five questions for every transition

    1. **Does the pulse stay together?** No accidental stumbling kicks or steadily
       growing timing error across the overlap.
    2. **Does the musical arrival make sense?** Phrases feel intentional; a lyric,
       build or drop is not interrupted by accident.
    3. **Do the notes and important parts coexist?** Key advice is checked against
       the actual music, including bass, voices and tails.
    4. **Does the level and low end stay controlled?** No distracting boom, hollow
       gap or unexplained loudness jump.
    5. **Does the processing earn its place?** Pitch changes, stabilization and
       optional treatments help more than their artifacts distract.

    ## If both choices sound good

    You do not need to manufacture a dramatic difference. A subtle improvement,
    a stylistic preference or keeping the original can all be sensible choices.
    Take a listening break when you start making changes you cannot explain, then
    return at a comfortable volume.

    For your next practice mix, change one challenge: try a different energy arc,
    one more song, or a transition with less convenient intro material. Keep your
    earlier project so you can compare what you learned. The aim is a mix you enjoy
    hearing from beginning to end, not turning every suggestion green.

    **You have finished the course when:** you can choose songs, make a draft,
    identify a problem by listening, try a relevant change, undo it if needed,
    and keep both an editable project and an audio export. You can revisit any
    lesson; the lesson number shows your location, not a test score or saved completion record.

    [Course contents](help:beginner-course) · [Fast control reference](help:quick-start) ·
    [Glossary](help:glossary) · [Troubleshooting](help:troubleshooting)
    '''),
)

COURSE_IDS = tuple(topic['id'] for topic in LESSONS)

OVERVIEW = lesson('beginner-course', 'Beginner course · start here',
                  'beginner tutorial lessons learn walkthrough first mix getting started', '''
    ## From choosing songs to a mix you want to keep

    DJ Studio helps you arrange songs, match their speed and prepare blends.
    You choose the music, listen to the suggestions and decide what stays.
    A good mix has a clear pulse, a musical handover and a sense of direction.
    The longest overlap or the most processing is not automatically the best choice.

    **[Start lesson 1: hear what a mix does](help:learn-first-listen)**

    This course teaches the decisions as well as the buttons. Each lesson has
    an aim, things to try and a listening checkpoint. Use **Previous lesson** and
    **Next lesson** below an article, or pick any lesson from the list.
    You can keep the guide open beside the app and return to it as needed.

    **For a first session:** try the included demo, then make a short mix with
    two or three songs you know. Keep Advanced controls off until a lesson needs
    them. Change one thing at a time and compare before keeping it.

    **Looking for one answer?** Search a symptom or a control, such as
    **wrong BPM**, **overlap**, **no sound** or **export**. The detailed reference
    pages are in the same list. Hovering over controls explains their purpose;
    F1 opens help for the focused control where available.

    Reading this guide does not edit your mix. Practice actions in the main app
    can change it; save a project you care about first. Applied mix edits have
    Undo, and review windows let you discard their drafts. Saving a project and
    exporting a playable audio file are separate actions.

    ## Choose a lesson
    ''')
OVERVIEW['body'] += '\n\n' + '\n'.join(f"- [{item['title']}](help:{item['id']})" for item in LESSONS)
COURSE_TOPICS = (OVERVIEW, *LESSONS)
