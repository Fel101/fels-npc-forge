// Only authoring fields are undoable. Media, takes, revisions and render receipts stay server-owned.
export const AUTHORING_FIELDS = ['title', 'cast', 'aspect', 'backdrop', 'mode', 'background_prompt',
  'voice_overrides', 'voice_bindings', 'beats', 'actors', 'props', 'soundtracks'];

export function authoringCopy(scene) {
  return structuredClone(Object.fromEntries(AUTHORING_FIELDS.filter(k => scene[k] !== undefined).map(k => [k, scene[k]])));
}

export class SceneHistory {
  constructor(storage, scope = 'npcforge.scene-drafts.v1') {
    this.storage = storage; this.scope = scope; this.undoStack = []; this.redoStack = []; this.baseRevision = null;
    this.error = null;
  }
  key(scene) { return `${this.scope}:${scene.id || 'new'}`; }
  reset(scene) { this.undoStack = [authoringCopy(scene)]; this.redoStack = []; this.baseRevision = scene.revision ?? null; }
  capture(scene) {
    const value = authoringCopy(scene), last = this.undoStack.at(-1);
    if (JSON.stringify(last) === JSON.stringify(value)) return false;
    this.undoStack.push(value); if (this.undoStack.length > 100) this.undoStack.shift(); this.redoStack = [];
    this.persist(scene); return true;
  }
  apply(scene, value) {
    for (const key of AUTHORING_FIELDS) delete scene[key];
    Object.assign(scene, structuredClone(value)); this.persist(scene); return scene;
  }
  undo(scene) {
    this.capture(scene);
    if (this.undoStack.length < 2) return false;
    this.redoStack.push(this.undoStack.pop()); this.apply(scene, this.undoStack.at(-1)); return true;
  }
  redo(scene) {
    if (!this.redoStack.length) return false;
    const next = this.redoStack.pop(); this.undoStack.push(next); this.apply(scene, next); return true;
  }
  persist(scene) {
    try {
      this.storage?.setItem(this.key(scene), JSON.stringify({version: 1, id: scene.id || null,
        baseRevision: this.baseRevision, updated: new Date().toISOString(), value: authoringCopy(scene)}));
      this.error = null;
    } catch { this.error = 'Draft recovery storage is unavailable. Save the scene to keep your edits.'; }
  }
  recoverable(scene) {
    try {
      const row = JSON.parse(this.storage?.getItem(this.key(scene)) || 'null');
      if (row?.version !== 1 || row.id !== (scene.id || null) || !row.value || !Array.isArray(row.value.beats)) return null;
      if (JSON.stringify(row.value) === JSON.stringify(authoringCopy(scene))) return null;
      return {...row, conflict: row.baseRevision !== (scene.revision ?? null)};
    } catch { return null; }
  }
  clear(scene) { try { this.storage?.removeItem(this.key(scene)); } catch { /* Save still succeeds without storage. */ } }
}
