# Character audition suites

In Voice Designer, assign an English voice and save its settings. Under
**Character audition suite**, optionally enter names, places or difficult words,
then click **Run character audition**. The suite uses the saved voice, not
unsaved controls. It does not replace the assigned voice or dialogue recordings.

One quick-lane job captures the voice reference/preset, pronunciation rules,
default performance and runtime binding, then generates:

- A personalized greeting.
- The same sentence in neutral, angry, whispered and amused/happy delivery.
- Names and pronunciation, using existing pronunciation rules.
- A longer speech to test pacing and consistency.

Models without mood direction skip the three emotional cases with a reason.
The suite runs the existing offline CPU alignment checks for these clips only;
the character's saved lip-sync settings are preserved. Generation and case
progress appear in the normal queue, with completed clips retained as they arrive.
Cold model/runtime checksum capture can take time before queue submission returns.

## Review

The suite keeps its captured reference alongside the clips for listening.
Automatic flags report silent/invalid audio, delivered clipping, unusually short
or long duration, failed/unavailable word checks and amplitude mouth fallback.
Where available, the recognized transcript and synthesized pronunciation appear
beside the authored text. Duration flags are screening heuristics; they do not
prove missing words. Whisper and unusual names can also confuse recognition.

For each completed clip, separately review **Voice identity**, **Words &
pronunciation** and **Delivery**, and save notes. The choices are Not reviewed,
Sounds right and Needs work. Automatic flags remain visible after a listening
review; successful alignment never approves voice identity or pronunciation.
Review writes check the clip's SHA-256, preventing a review of replaced audio.

Changing voice identity, effective settings, used pronunciation rules or default
performance marks affected audition clips as old. The retained clips and judgments
remain available for comparison. Run a new suite to audition the new settings.
Up to twelve suites are retained per character, excluded from voice identity
snapshots so history does not recursively enter generated audio metadata.

**Download audition & report** exports completed WAVs, the captured reference
when applicable, and `audition.json`. The report includes scripts, measurements,
checksums, runtime/model receipts, pronunciation/performance details, lip-sync
evidence and human judgments. Partial suites can also be exported. Export refuses
audio or reference files that have changed since capture.

## Verification

`tests/test_voice_audition.py` checks identity freezing, supported-case selection,
independent script dispatch, pronunciation, measured flags, human reviews,
freshness and checksum-checked packages. API tests exercise validation and partial
packages; the controller fixture checks submission and unsaved-setting guards.

October 13 real evidence is retained under
`local/voice-audition-real-20261013-175813-165f8b/`: seven real DramaBox clips
(7.28–28.42 seconds), `audition.zip` and `report.json`. Every clip retained
transcript-mismatch and amplitude-fallback flags. Recognized transcripts included
extra fragments resembling the stage direction; the report preserves that evidence
without treating it as a successful word check or a human listening verdict.
Original source/reference hashes and saved disabled lip-sync settings were unchanged.
