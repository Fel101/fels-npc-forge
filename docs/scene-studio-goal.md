# Scene Studio expansion goal

User authorized all eight additions on October 14, 2026. This is one complete goal; milestones do not narrow its scope.

1. Visual stage editing: draggable actor positions, rotation, walk marks and a top-down view.
2. Editing safety: undo/redo, recoverable drafts and scene duplication.
3. Acting: facing, look targets, gestures, listening reactions and facial expressions on compatible rigs.
4. Voice takes: retained alternatives per line, comparison, explicit selection/approval and matching mouth timing.
5. Moving cameras: push-ins, pans, tracking and adjustable transitions, identical in preview and video capture.
6. Sound: separate ambience/music/effect tracks, timing, volume and dialogue ducking in preview and export.
7. Props and ensemble scenes: Items-tab models placed as props/scenery; more than two cast members.
8. Writing assistant: editable suggestions using chosen character profiles, setting and objective, with script/actions/cameras.

## Implementation sequence

- Extend validated scene records while preserving legacy defaults, speech identities and durable media.
- Add history/recovery, scene copies, cast management and visual staging.
- Share deterministic acting and moving-camera calculations between preview and export.
- Retain voice takes and introduce scene-owned audio tracks, with matching export mixing and package metadata.
- Wire Items props and profile-based writing suggestions into the editor.
- Complete meaningful automated checks and native UI/real audio/video/package QA in copied libraries.
- Commit only owned paths; verify activation on port 8765 after the final production restart.

## Invariants

Existing scenes remain readable and retain their original staging, camera defaults and speech until edited. Undo and browser recovery never manufacture or replace server-owned voice, media or render metadata. Saved revisions use conflict checks. No generated voice take is silently described as human-approved. Existing Body Designer changes, including `web/stage3d.js`, are outside this goal and remain untouched. Local QA uses copied records; production scenes are not test fixtures.

All eight implementations are present. Native copied-library QA has verified drag/rotation/walk marks, undo/redo/recovery, scene copies and cast changes, profile-based draft apply/undo, three real voice alternatives, track import/mixing and a 215-frame MP4/package. The remaining completion gates are final staged-source regression, a local owned-files commit and verified activation on port 8765. See [Scene Studio](scene-studio.md) for behavior and evidence.

Status: active until those final gates pass. Baseline and later evidence are retained in `local/scene-studio-audit-20261014`.
