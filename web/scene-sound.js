export function duckGain(time, beats, fps) {
  let gain = 1;
  for (const beat of beats) {
    if (!beat.voice) continue;
    const start = beat.start / fps, end = start + Math.min(beat.voice.seconds, beat.frames / fps);
    if (time >= start - .08 && time < start) gain = Math.min(gain, 1 - .7 * (time - start + .08) / .08);
    else if (time >= start && time <= end) gain = Math.min(gain, .3);
    else if (time > end && time < end + .16) gain = Math.min(gain, .3 + .7 * (time - end) / .16);
  }
  return gain;
}

export class SceneSoundPlayer {
  constructor(onError = () => {}, options = {}) {
    this.tracks = new Map(); this.onError = onError; this.running = false; this.context = null;
    this.makeContext = options.context || (() => new (window.AudioContext || window.webkitAudioContext)());
    this.fetchAudio = options.fetch || (url => fetch(url));
  }
  position(row) {
    if (!row.source || this.context.currentTime < row.startAt) return row.time || row.track.offset;
    const elapsed = Math.max(0, Math.min(this.context.currentTime, row.stopAt) - row.startAt);
    const offset = row.offset + elapsed, end = row.track.media.seconds, available = end - row.track.offset;
    return row.track.loop ? row.track.offset + (offset - row.track.offset) % available : Math.min(end, offset);
  }
  pause() {
    this.running = false;
    for (const row of this.tracks.values()) {
      row.time = this.position(row);
      if (row.source) { try { row.source.stop(); } catch { /* Already ended. */ } row.source = null; }
      row.gain?.disconnect(); row.gain = null;
    }
  }
  status() {
    const now = this.context?.currentTime || 0;
    return [...this.tracks.values()].map(row => ({id: row.track.id, playing: !!row.source && now >= row.startAt && now < row.stopAt && this.context.state === 'running',
      queued: !!row.source && now < row.startAt, loading: !!row.loading, time: this.position(row), volume: row.effectiveVolume ?? row.track.volume}));
  }
  clear() { this.pause(); this.tracks.clear(); }
  sync(scene, timeline) {
    const ids = new Set();
    for (const track of timeline.soundtracks || []) {
      const url = `/api/scenes/${scene.id}/files/${track.media.file}`, id = track.id + ':' + url; ids.add(id);
      let row = this.tracks.get(id);
      if (!row) { row = {url, track, time: track.offset, source: null, buffer: null}; this.tracks.set(id, row); }
      row.track = track;
    }
    for (const [id, row] of this.tracks) if (!ids.has(id)) {
      if (row.source) { try { row.source.stop(); } catch { /* Already ended. */ } }
      row.gain?.disconnect(); this.tracks.delete(id);
    }
  }
  async prepare(scene, timeline) {
    this.sync(scene, timeline);
    if (!this.tracks.size) return;
    this.context ||= this.makeContext();
    // Resume from the Play gesture before awaiting any network or decoding work.
    await this.context.resume();
    await Promise.all([...this.tracks.values()].map(async row => {
      if (row.buffer) return;
      row.loading ||= this.fetchAudio(row.url).then(async response => {
        if (!response.ok) throw Error('A scene sound file could not be loaded.');
        row.buffer = await this.context.decodeAudioData(await response.arrayBuffer());
      }).finally(() => { row.loading = null; });
      await row.loading;
    }));
  }
  schedule(timeline, time) {
    this.pause(); this.running = true;
    if (!this.context) return;
    const now = this.context.currentTime; this.anchorTime = now; this.anchorScene = time;
    for (const row of this.tracks.values()) {
      const track = row.track, available = track.media.seconds - track.offset, elapsed = Math.max(0, time - track.start);
      const remaining = Math.min(track.duration - elapsed, timeline.frames / timeline.fps - Math.max(time, track.start), track.loop ? Infinity : available - elapsed);
      if (!row.buffer || available <= 0 || remaining <= 0) continue;
      const source = this.context.createBufferSource(), gain = this.context.createGain();
      source.buffer = row.buffer; source.loop = track.loop; source.loopStart = track.offset; source.loopEnd = track.media.seconds;
      source.connect(gain); gain.connect(this.context.destination);
      const offset = track.offset + (track.loop ? elapsed % available : elapsed), startAt = now + Math.max(0, track.start - time);
      gain.gain.setValueAtTime(track.volume * (track.duck ? duckGain(time, timeline.beats, timeline.fps) : 1), now);
      if (track.duck) for (const beat of timeline.beats) {
        if (!beat.voice) continue;
        const start = beat.start / timeline.fps, end = start + Math.min(beat.voice.seconds, beat.frames / timeline.fps);
        for (const [at, amount, ramp] of [[start - .08, 1, false], [start, .3, true], [end, .3, false], [end + .16, 1, true]]) {
          if (at >= time) gain.gain[ramp ? 'linearRampToValueAtTime' : 'setValueAtTime'](track.volume * amount, now + at - time);
        }
      }
      Object.assign(row, {source, gain, offset, startAt, stopAt: startAt + remaining, time: offset});
      source.onended = () => { if (row.source === source) { row.time = this.position(row); row.source = null; gain.disconnect(); row.gain = null; } };
      source.start(startAt, offset); source.stop(row.stopAt);
    }
  }
  update(scene, timeline, frame, playing) {
    this.sync(scene, timeline);
    const time = frame / timeline.fps;
    if (!playing) {
      this.pause();
      for (const row of this.tracks.values()) {
        const elapsed = Math.max(0, time - row.track.start), available = row.track.media.seconds - row.track.offset;
        row.time = row.track.offset + (row.track.loop ? elapsed % Math.max(.001, available) : Math.min(available, elapsed));
      }
    } else if (!this.running || (this.context && Math.abs(this.anchorScene + this.context.currentTime - this.anchorTime - time) > .15)) {
      this.schedule(timeline, time);
    }
    for (const row of this.tracks.values()) row.effectiveVolume = row.track.volume * (row.track.duck ? duckGain(time, timeline.beats, timeline.fps) : 1);
  }
}

export async function soundDataUrl(file) {
  if (!file || file.size > 50_000_000) throw Error('Choose an audio file smaller than 50 MB.');
  const context = new (window.AudioContext || window.webkitAudioContext)(); let decoded;
  try { decoded = await context.decodeAudioData(await file.arrayBuffer()); } finally { await context.close(); }
  if (decoded.duration < .1 || decoded.duration > 60) throw Error('Choose 0.1–60 seconds of audio. Longer ambience can be trimmed and looped.');
  const rate = 24000, off = new OfflineAudioContext(1, Math.ceil(decoded.duration * rate), rate), source = off.createBufferSource();
  source.buffer = decoded; source.connect(off.destination); source.start();
  const samples = (await off.startRendering()).getChannelData(0), view = new DataView(new ArrayBuffer(44 + samples.length * 2));
  const text = (offset, value) => [...value].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)));
  text(0, 'RIFF'); view.setUint32(4, 36 + samples.length * 2, true); text(8, 'WAVE'); text(12, 'fmt ');
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true); view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true); text(36, 'data'); view.setUint32(40, samples.length * 2, true);
  samples.forEach((v, i) => view.setInt16(44 + i * 2, Math.max(-1, Math.min(1, v)) * 32767, true));
  const bytes = new Uint8Array(view.buffer); let binary = '';
  for (let i = 0; i < bytes.length; i += 32768) binary += String.fromCharCode(...bytes.subarray(i, i + 32768));
  return 'data:audio/wav;base64,' + btoa(binary);
}
