// Speech uses the actual audio clock. Silence and the tail after speech use a monotonic clock.
export class SceneClock {
  constructor(fps = 25) { this.fps = fps; this.frame = 0; this.anchor = 0; this.running = false; this.tail = false; }
  seek(frame, now = 0) { this.frame = Math.max(0, frame); this.anchor = now; this.tail = false; }
  start(now) { this.anchor = now; this.running = true; }
  pause(frame = this.frame) { this.frame = frame; this.running = false; }
  attach(frame, now) { this.seek(frame, now); }
  sample(now, beat, audio) {
    if (!this.running) return Math.floor(this.frame);
    if (beat?.voice && audio && !this.tail) {
      if (audio.ended) {
        this.frame = Math.max(this.frame, beat.start + Math.min(audio.duration || beat.voice.seconds, beat.voice.seconds) * this.fps);
        this.anchor = now; this.tail = true;
      } else {
        // Keep the selected frame while loading or waiting for playback permission.
        if (audio.readyState >= 2 && !audio.paused) this.frame = beat.start + audio.currentTime * this.fps;
        this.anchor = now;
        return Math.floor(this.frame);
      }
    }
    return Math.floor(this.frame + Math.max(0, now - this.anchor) * this.fps / 1000);
  }
}
