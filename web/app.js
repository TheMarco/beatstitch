(() => {
  'use strict';

  const MEDIA = window.BEATSTITCH_MEDIA;
  if (!MEDIA) return;

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
  const lerp = (a, b, t) => a + (b - a) * t;
  const easeOut = t => 1 - Math.pow(1 - t, 3);
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const token = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const COLORS = {
    amber: token('--amber'), text: token('--text'), text2: token('--text-2'), text3: token('--text-3'),
    good: token('--good'), bad: token('--bad'), panel: token('--panel'), field: token('--field'),
  };
  const FONT = '"Barlow", ui-sans-serif, system-ui, sans-serif';
  const MINOR = ['C', 'C♯', 'D', 'E♭', 'E', 'F', 'F♯', 'G', 'G♯', 'A', 'B♭', 'B'];
  const MAJOR = ['C', 'D♭', 'D', 'E♭', 'E', 'F', 'F♯', 'G', 'A♭', 'A', 'B♭', 'B'];

  function clock(seconds, tenths = false) {
    seconds = Math.max(0, seconds);
    if (tenths) {
      const total = Math.floor(seconds * 10);
      const minutes = Math.floor(total / 600);
      const rest = total - minutes * 600;
      return `${minutes}:${String(Math.floor(rest / 10)).padStart(2, '0')}.${rest % 10}`;
    }
    const whole = Math.floor(seconds);
    return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
  }

  function hexAlpha(hex, alpha) {
    const n = parseInt(hex.replace('#', ''), 16);
    return `rgba(${n >> 16}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
  }

  function setText(element, text) {
    if (element && element.textContent !== text) element.textContent = text;
  }

  /* ---------------------------------------------------------------- Audio */

  let context = null;
  let analyser = null;
  let analyserData = null;

  function audioContext() {
    if (!context) {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      context = new AudioContextClass();
      analyser = context.createAnalyser();
      analyser.fftSize = 1024;
      analyser.connect(context.destination);
      analyserData = new Float32Array(analyser.fftSize);
    }
    if (context.state === 'suspended') context.resume();
    return context;
  }

  function decode(ac, data) {
    return new Promise((resolve, reject) => {
      const result = ac.decodeAudioData(data, resolve, reject);
      if (result && typeof result.then === 'function') result.then(resolve, reject);
    });
  }

  async function download(url, onProgress) {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${url} returned ${response.status}`);
    const size = Number(response.headers.get('content-length')) || 0;
    if (!response.body || !size) {
      const buffer = await response.arrayBuffer();
      onProgress(1);
      return buffer;
    }
    const reader = response.body.getReader();
    const chunks = [];
    let received = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      received += value.length;
      onProgress(Math.min(1, received / size));
    }
    const bytes = new Uint8Array(received);
    let at = 0;
    for (const chunk of chunks) { bytes.set(chunk, at); at += chunk.length; }
    return bytes.buffer;
  }

  const players = [];

  /** Several versions of the same audio on one clock. Switching changes only which one you hear. */
  class SyncPlayer {
    constructor(versions, { loop = false, nominal = 0 } = {}) {
      this.versions = versions;
      this.loop = loop;
      this.nominal = nominal;
      this.active = versions[0].id;
      this.buffers = null;
      this.loading = null;
      this.sources = [];
      this.offset = 0;
      this.startedAt = 0;
      this.playing = false;
      this.pending = false;
      this.duration = 0;
      this.handlers = [];
      players.push(this);
    }

    on(handler) { this.handlers.push(handler); return this; }
    emit(type, value) { for (const handler of this.handlers) handler(type, value); }
    get length() { return this.duration || this.nominal; }
    get busy() { return this.playing || this.pending; }

    load() {
      if (this.loading) return this.loading;
      const ac = audioContext();
      const parts = this.versions.map(() => 0);
      this.emit('progress', 0);
      this.loading = Promise.all(this.versions.map((version, index) =>
        download(version.src, value => {
          parts[index] = value;
          this.emit('progress', parts.reduce((a, b) => a + b, 0) / parts.length);
        }).then(data => decode(ac, data))
      )).then(buffers => {
        this.buffers = buffers;
        this.duration = Math.min(...buffers.map(buffer => buffer.duration));
        this.output = ac.createGain();
        this.output.connect(analyser);
        this.gains = buffers.map((_, index) => {
          const gain = ac.createGain();
          gain.gain.value = this.versions[index].id === this.active ? 1 : 0;
          gain.connect(this.output);
          return gain;
        });
        this.emit('loaded');
      }, error => {
        this.loading = null;
        throw error;
      });
      return this.loading;
    }

    get position() {
      if (!this.playing) return this.offset;
      const elapsed = this.offset + context.currentTime - this.startedAt;
      return this.loop ? elapsed % this.duration : Math.min(elapsed, this.duration);
    }

    async play() {
      audioContext();
      for (const player of players) if (player !== this) player.pause();
      if (this.playing) return;
      this.pending = true;
      this.emit('state');
      try {
        await this.load();
      } catch (error) {
        this.pending = false;
        this.emit('state');
        this.emit('error', error);
        return;
      }
      if (!this.pending) return; // paused while loading
      this.pending = false;
      this.start();
    }

    start() {
      const ac = audioContext();
      this.stopSources();
      if (this.offset >= this.duration - 0.02) this.offset = 0;
      const when = ac.currentTime + 0.02;
      const run = {};
      this.run = run;
      this.sources = this.buffers.map((buffer, index) => {
        const source = ac.createBufferSource();
        source.buffer = buffer;
        source.connect(this.gains[index]);
        if (this.loop) {
          source.loop = true;
          source.loopStart = 0;
          source.loopEnd = this.duration;
          source.start(when, this.offset);
        } else {
          source.start(when, this.offset, this.duration - this.offset);
        }
        return source;
      });
      this.sources[0].onended = () => {
        if (this.run !== run || !this.playing) return;
        this.playing = false;
        this.offset = this.duration;
        this.stopSources();
        this.emit('state');
      };
      this.startedAt = when;
      this.playing = true;
      this.emit('state');
    }

    stopSources() {
      this.run = null;
      for (const source of this.sources) {
        source.onended = null;
        try { source.stop(); } catch (error) { /* already stopped */ }
        source.disconnect();
      }
      this.sources = [];
    }

    pause() {
      if (this.pending) {
        this.pending = false;
        this.emit('state');
      }
      if (!this.playing) return;
      this.offset = this.position;
      this.stopSources();
      this.playing = false;
      this.emit('state');
    }

    toggle() { if (this.busy) this.pause(); else this.play(); }

    seek(seconds) {
      this.offset = clamp(seconds, 0, Math.max(0, this.length - 0.01));
      if (this.playing) this.start();
      this.emit('seek');
    }

    setLoop(loop) {
      if (this.loop === loop) return;
      const position = this.position;
      this.loop = loop;
      if (this.playing) { this.offset = position; this.start(); }
    }

    setVersion(id) {
      this.active = id;
      if (this.gains) {
        const now = context.currentTime;
        this.gains.forEach((gain, index) => {
          gain.gain.cancelScheduledValues(now);
          gain.gain.setValueAtTime(gain.gain.value, now);
          gain.gain.linearRampToValueAtTime(this.versions[index].id === id ? 1 : 0, now + 0.03);
        });
      }
      this.emit('version', id);
    }
  }

  function audioError(error) {
    console.error(error);
    if (location.protocol === 'file:') {
      return 'Browsers block audio on pages opened from disk. Serve the web folder instead, for example with "python3 -m http.server" inside it.';
    }
    return 'The audio could not load. Check your connection and try again.';
  }

  /* ------------------------------------------------------ Animation loop */

  let frame = 0;
  const tickers = new Set();
  function requestTick() { if (!frame) frame = requestAnimationFrame(tick); }
  function tick(now) {
    frame = 0;
    let again = false;
    for (const ticker of tickers) if (ticker(now)) again = true;
    if (again) requestTick();
  }

  function fitCanvas(canvas) {
    const rect = canvas.getBoundingClientRect();
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.max(1, Math.round(rect.width));
    const h = Math.max(1, Math.round(rect.height));
    if (canvas.width !== Math.round(w * ratio) || canvas.height !== Math.round(h * ratio)) {
      canvas.width = Math.round(w * ratio);
      canvas.height = Math.round(h * ratio);
    }
    return { w, h, ratio };
  }

  function makeLayer(w, h, ratio) {
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(w * ratio);
    canvas.height = Math.round(h * ratio);
    const g = canvas.getContext('2d');
    g.setTransform(ratio, 0, 0, ratio, 0, 0);
    return [canvas, g];
  }

  function roundRect(g, x, y, w, h, r) {
    r = Math.min(r, w / 2, h / 2);
    g.beginPath();
    g.moveTo(x + r, y);
    g.arcTo(x + w, y, x + w, y + h, r);
    g.arcTo(x + w, y + h, x, y + h, r);
    g.arcTo(x, y + h, x, y, r);
    g.arcTo(x, y, x + w, y, r);
    g.closePath();
  }

  function fitText(g, text, width) {
    if (g.measureText(text).width <= width) return text;
    let cut = text;
    while (cut.length > 1 && g.measureText(cut + '…').width > width) cut = cut.slice(0, -1);
    return cut.length > 1 ? cut + '…' : '';
  }

  const visible = new Map();
  const observer = 'IntersectionObserver' in window ? new IntersectionObserver(entries => {
    for (const entry of entries) visible.set(entry.target, entry.isIntersecting ? entry.intersectionRatio : 0);
    requestTick();
  }, { threshold: [0, 0.1, 0.3, 0.6, 1] }) : null;
  const watch = element => { visible.set(element, 1); if (observer) observer.observe(element); };
  const isVisible = element => (visible.get(element) || 0) > 0;

  /* --------------------------------------------------- Demo arrangement */

  const demo = MEDIA.demo;
  const BAR = 240 / demo.bpm;
  const WAVE_RATE = demo.waveRate || 16;
  const DETAIL_RATE = demo.detailRate || 150;
  const clips = demo.clips;
  const mix = new SyncPlayer(demo.versions, { nominal: demo.duration });

  const ui = {
    deck: $('#deck'), disc: $('#deck-disc'), deckPlay: $('#deck-play'), hint: $('#deck-hint'),
    ring: $('#deck-play .ring circle'), heroPlay: $('#hero-play'), play: $('#mix-play'), restart: $('#mix-restart'),
    time: $('#mix-time'), total: $('#mix-total'), bar: $('#mix-bar'), timeline: $('#timeline'),
    canvas: $('#timeline-canvas'), scope: $('#scope-canvas'), blendTitle: $('#blend-title'), blendDetail: $('#blend-detail'),
    phase: $('#phase'), phaseOut: $('#phase-out'), phaseIn: $('#phase-in'), phaseValue: $('#phase-value'),
    error: $('#mix-error'), versions: $$('.player [data-version]'), player: $('#listen'),
  };
  watch(ui.player);
  watch(ui.deck);
  setText(ui.total, `/ ${clock(demo.duration)}`);

  let mode = 'beatstitch';
  const morph = { value: 0, from: 0, to: 0, start: 0 };
  let started = false;
  let level = 0;
  let hoverX = null;
  let staticDirty = true;
  let layers = null;

  function shortKey(clip) { return (clip.mode === 'minor' ? MINOR : MAJOR)[clip.tonic] + (clip.mode === 'minor' ? 'm' : ''); }
  function percent(clip) {
    const change = (demo.bpm / clip.bpm - 1) * 100;
    if (Math.abs(change) < 0.05) return '';
    return `${change > 0 ? '+' : '-'}${Math.abs(change).toFixed(1)}%`;
  }

  /** Where a clip sits and which source seconds it plays, blending the two versions. */
  function clipSpan(clip, m) {
    return {
      start: lerp(clip.start, clip.plain.start, m),
      end: lerp(clip.end, clip.plain.end, m),
      srcStart: lerp(0, clip.plain.sourceStart, m),
      srcEnd: lerp(clip.sourceDuration, clip.plain.sourceEnd, m),
    };
  }

  function geometry(w, h) {
    const small = w < 640;
    const gut = small ? 20 : 28;
    const right = 8;
    const ruler = 24;
    const timeH = 18;
    const mixH = small ? 28 : 34;
    const gap = 2;
    const laneH = Math.floor((h - ruler - timeH - mixH - gap * 3) / 2);
    const lanes = [ruler + gap, ruler + gap * 2 + laneH];
    const mixY = lanes[1] + laneH + gap;
    const span = w - gut - right;
    const X = t => gut + (t / demo.duration) * span;
    const T = x => ((x - gut) / span) * demo.duration;
    return { w, h, small, gut, right, ruler, timeH, mixH, laneH, lanes, mixY, X, T };
  }

  // Engine curves (studio_engine._weights): equal-power highs, one-bar raised-cosine bass swap.
  function blendGains(progressBars, lengthBars, incoming, handoff) {
    const p = clamp(progressBars / lengthBars, 0, 1);
    const high = incoming ? Math.sin(p * Math.PI / 2) : Math.cos(p * Math.PI / 2);
    const width = Math.min(lengthBars, 1);
    const q = clamp((progressBars - (lengthBars - width) / 2) / width, 0, 1);
    const swap = (1 - Math.cos(q * Math.PI)) / 2;
    const low = lerp(high, incoming ? swap : 1 - swap, handoff);
    return { high, low };
  }

  function drawWave(g, peaks, rms, x0, x1, top, height, color, sourceAt, alpha = 1) {
    const mid = top + height / 2;
    const amp = height / 2 * 0.94;
    const left = Math.max(Math.ceil(x0), 0);
    const right = Math.floor(x1);
    const count = peaks.length;
    for (let x = left; x < right; x++) {
      const a = sourceAt(x) * WAVE_RATE;
      const b = sourceAt(x + 1) * WAVE_RATE;
      let i0 = Math.floor(Math.min(a, b));
      let i1 = Math.max(i0 + 1, Math.floor(Math.max(a, b)));
      i0 = clamp(i0, 0, count - 1);
      i1 = clamp(i1, i0 + 1, count);
      let peak = 0;
      let body = 0;
      for (let i = i0; i < i1; i++) {
        if (peaks[i] > peak) peak = peaks[i];
        if (rms && rms[i] > body) body = rms[i];
      }
      const ph = Math.max(0.5, peak / 100 * amp);
      g.globalAlpha = (rms ? 0.42 : 0.9) * alpha;
      g.fillStyle = color;
      g.fillRect(x, mid - ph, 1, ph * 2);
      if (rms) {
        const bh = Math.max(0.5, body / 100 * amp * 1.35);
        g.globalAlpha = 0.95 * alpha;
        g.fillRect(x, mid - Math.min(bh, ph), 1, Math.min(bh, ph) * 2);
      }
    }
    g.globalAlpha = 1;
  }

  function buildTimeline(size) {
    const { w, h, ratio } = size;
    const G = geometry(w, h);
    const m = morph.value;
    const [base, g] = makeLayer(w, h, ratio);
    const [played, p] = makeLayer(w, h, ratio);

    g.fillStyle = '#101013';
    g.fillRect(0, 0, w, h);
    G.lanes.forEach((y, i) => { g.fillStyle = i ? '#131317' : '#141419'; g.fillRect(G.gut, y, w - G.gut, G.laneH); });
    g.fillStyle = COLORS.field;
    g.fillRect(G.gut, G.mixY, w - G.gut, G.mixH);

    // Bar grid
    const bars = Math.ceil(demo.duration / BAR);
    for (let bar = 0; bar <= bars; bar++) {
      const x = Math.round(G.X(bar * BAR)) + 0.5;
      if (x > w - G.right) break;
      const major = bar % 8 === 0;
      g.fillStyle = major ? 'rgba(255,255,255,0.08)' : 'rgba(255,255,255,0.03)';
      g.fillRect(x - 0.5, G.lanes[0], 1, G.mixY + G.mixH - G.lanes[0]);
      g.fillStyle = major ? 'rgba(255,255,255,0.4)' : 'rgba(255,255,255,0.16)';
      g.fillRect(x - 0.5, G.ruler - (major ? 9 : 5), 1, major ? 9 : 5);
    }
    g.font = `500 11px ${FONT}`;
    g.textBaseline = 'alphabetic';
    const flags = clips.slice(1).map(clip => G.X(clip.start));
    const labelEvery = G.small ? 16 : 8;
    for (let bar = 0; bar <= bars; bar += labelEvery) {
      const x = G.X(bar * BAR);
      if (x > w - 20 || flags.some(f => Math.abs(f - x) < 34)) continue;
      g.fillStyle = COLORS.text3;
      g.fillText(String(bar + 1), x + 3, 12);
    }
    // Blend flags
    g.font = `600 11px ${FONT}`;
    flags.forEach((x, index) => {
      g.fillStyle = COLORS.amber;
      g.fillRect(Math.round(x) - 1, 3, 2, G.ruler - 3);
      g.beginPath();
      g.moveTo(Math.round(x) + 1, 3);
      g.lineTo(Math.round(x) + 8, 7.5);
      g.lineTo(Math.round(x) + 1, 12);
      g.fill();
      g.fillText(`${index + 1}→${index + 2}`, x + 11, 13);
    });

    // Clips
    clips.forEach((clip, index) => {
      const span = clipSpan(clip, m);
      const top = G.lanes[index % 2] + 3;
      const height = G.laneH - 6;
      const x0 = G.X(span.start);
      const x1 = G.X(span.end);
      const header = height > 60 ? 18 : 15;
      roundRect(g, x0 + 0.5, top + 0.5, x1 - x0 - 1, height - 1, 4);
      g.fillStyle = hexAlpha(clip.color, 0.13);
      g.fill();
      g.strokeStyle = hexAlpha(clip.color, 0.55);
      g.lineWidth = 1;
      g.stroke();
      g.save();
      roundRect(g, x0 + 0.5, top + 0.5, x1 - x0 - 1, height - 1, 4);
      g.clip();
      g.fillStyle = hexAlpha(clip.color, 0.92);
      g.fillRect(x0, top, x1 - x0, header);
      g.restore();

      g.font = `600 ${header > 15 ? 12 : 11}px ${FONT}`;
      g.fillStyle = '#141414';
      g.textBaseline = 'middle';
      const meta = `${shortKey(clip)}  ${clip.bpm.toFixed(1)}${m < 0.5 && percent(clip) ? '  ' + percent(clip) : ''}`;
      const room = x1 - x0 - 14;
      const metaWidth = g.measureText(meta).width;
      const showMeta = !G.small && room > metaWidth + g.measureText(clip.name).width + 16;
      g.fillText(fitText(g, clip.name, showMeta ? room - metaWidth - 12 : room), x0 + 7, top + header / 2 + 0.5);
      if (showMeta) {
        g.textAlign = 'right';
        g.font = `500 ${header > 15 ? 12 : 11}px ${FONT}`;
        g.fillText(meta, x1 - 7, top + header / 2 + 0.5);
        g.textAlign = 'left';
      }
      g.textBaseline = 'alphabetic';

      const waveTop = top + header + 3;
      const waveHeight = height - header - 6;
      const scale = (span.srcEnd - span.srcStart) / (span.end - span.start);
      const sourceAt = x => span.srcStart + (G.T(x) - span.start) * scale;
      g.save();
      g.beginPath();
      g.rect(x0 + 1, waveTop, x1 - x0 - 2, waveHeight);
      g.clip();
      drawWave(g, clip.wave.peak, clip.wave.rms, x0 + 1, x1 - 1, waveTop, waveHeight, clip.color, sourceAt);

      // Fade curves: solid for the band above 180 Hz, dashed for the bass.
      const curve = (blendStart, blendLength, incoming, band) => {
        const bars = blendLength / BAR;
        g.beginPath();
        for (let x = Math.floor(G.X(blendStart)); x <= Math.ceil(G.X(blendStart + blendLength)); x++) {
          const progress = (G.T(x) - blendStart) / BAR;
          const gains = blendGains(progress, bars, incoming, 1 - m);
          const y = waveTop + waveHeight - gains[band] * (waveHeight - 2);
          if (x === Math.floor(G.X(blendStart))) g.moveTo(x, y); else g.lineTo(x, y);
        }
        g.stroke();
      };
      g.lineWidth = 1.25;
      const blends = [];
      if (index) blends.push([clip.start, clip.incoming, true]);
      if (index + 1 < clips.length) blends.push([clips[index + 1].start, clips[index + 1].incoming, false]);
      for (const [blendStart, blendLength, incoming] of blends) {
        g.setLineDash([]);
        g.strokeStyle = 'rgba(255,255,255,0.88)';
        curve(blendStart, blendLength, incoming, 'high');
        if (m < 0.999) {
          g.setLineDash([4, 3]);
          g.strokeStyle = `rgba(255,255,255,${0.75 * (1 - m)})`;
          curve(blendStart, blendLength, incoming, 'low');
        }
      }
      g.setLineDash([]);
      g.restore();
    });

    // Rendered mix lane: dim on the base layer, bright on the played layer.
    const mixAt = x => G.T(x);
    for (const [layer, color] of [[g, '#5b616c'], [p, '#dfe4ec']]) {
      layer.save();
      layer.beginPath();
      layer.rect(G.gut, G.mixY + 2, w - G.gut - G.right, G.mixH - 4);
      layer.clip();
      drawWave(layer, demo.mix.beatstitch.peak, demo.mix.beatstitch.rms, G.gut, w - G.right, G.mixY + 2, G.mixH - 4, color, mixAt, 1 - m);
      if (m > 0.001) drawWave(layer, demo.mix.without.peak, demo.mix.without.rms, G.gut, w - G.right, G.mixY + 2, G.mixH - 4, color, mixAt, m);
      layer.restore();
    }

    // Time ruler and lane letters
    g.font = `500 11px ${FONT}`;
    const every = G.small ? 30 : 10;
    for (let t = 0; t <= demo.duration; t += every) {
      const x = G.X(t);
      if (x > w - 26) break;
      g.fillStyle = 'rgba(255,255,255,0.14)';
      g.fillRect(Math.round(x), G.h - G.timeH, 1, 4);
      g.fillStyle = COLORS.text3;
      g.fillText(clock(t), x + 3, G.h - 5);
    }
    g.font = `600 11px ${FONT}`;
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    g.fillStyle = COLORS.text3;
    g.fillText('A', G.gut / 2, G.lanes[0] + G.laneH / 2);
    g.fillText('B', G.gut / 2, G.lanes[1] + G.laneH / 2);
    g.fillText('M', G.gut / 2, G.mixY + G.mixH / 2);
    g.textAlign = 'left';
    g.textBaseline = 'alphabetic';

    return { G, base, played, size };
  }

  function drawTimeline() {
    const size = fitCanvas(ui.canvas);
    if (staticDirty || !layers || layers.size.w !== size.w || layers.size.h !== size.h || layers.size.ratio !== size.ratio) {
      layers = buildTimeline(size);
      staticDirty = false;
    }
    const { G, base, played } = layers;
    const g = ui.canvas.getContext('2d');
    g.setTransform(size.ratio, 0, 0, size.ratio, 0, 0);
    g.clearRect(0, 0, size.w, size.h);
    g.drawImage(base, 0, 0, size.w, size.h);
    const x = G.X(mix.position);
    if (x > G.gut) {
      g.save();
      g.beginPath();
      g.rect(G.gut, G.mixY, x - G.gut, G.mixH);
      g.clip();
      g.drawImage(played, 0, 0, size.w, size.h);
      g.restore();
    }
    if (hoverX !== null && hoverX > G.gut) {
      g.fillStyle = 'rgba(255,255,255,0.28)';
      g.fillRect(Math.round(hoverX), G.ruler, 1, size.h - G.ruler - G.timeH);
    }
    g.fillStyle = COLORS.amber;
    g.fillRect(Math.round(x) - 0.75, G.ruler - 2, 1.5, size.h - G.ruler - G.timeH + 2);
    g.beginPath();
    g.moveTo(x - 5, G.ruler - 8);
    g.lineTo(x + 5, G.ruler - 8);
    g.lineTo(x, G.ruler - 1);
    g.closePath();
    g.fill();
  }

  /**
   * Zoomed deck view: each lane's song scrolls past a fixed playhead, the way DJ software
   * stacks two decks. Kicks that line up sit on the same grid line; drifting kicks separate.
   */
  function drawScope() {
    const { w, h, ratio } = fitCanvas(ui.scope);
    const g = ui.scope.getContext('2d');
    g.setTransform(ratio, 0, 0, ratio, 0, 0);
    g.clearRect(0, 0, w, h);
    const span = w < 640 ? 2.4 : 4;
    const pps = w / span;
    const now = mix.position;
    const t0 = now - span / 2;
    const center = Math.round(w / 2);
    const gap = 3;
    const rowH = (h - gap * 3) / 2;
    const beat = 60 / demo.bpm;

    for (let k = Math.ceil(t0 / beat); k * beat <= t0 + span; k++) {
      if (k < 0) continue;
      const x = Math.round((k * beat - t0) * pps);
      g.fillStyle = k % 4 === 0 ? 'rgba(255,255,255,0.13)' : 'rgba(255,255,255,0.05)';
      g.fillRect(x, 0, 1, h);
    }

    const m = morph.value;
    clips.forEach((clip, index) => {
      const lane = index % 2;
      const top = gap + lane * (rowH + gap);
      const mid = top + rowH / 2;
      const s = clipSpan(clip, m);
      const from = Math.max(0, Math.floor((Math.max(t0, s.start) - t0) * pps));
      const to = Math.min(w, Math.ceil((Math.min(t0 + span, s.end) - t0) * pps));
      if (to <= from) return;
      const scale = (s.srcEnd - s.srcStart) / (s.end - s.start);
      const detail = clip.detail;
      const sourceAt = x => (s.srcStart + (t0 + x / pps - s.start) * scale) * DETAIL_RATE;
      g.fillStyle = clip.color;
      for (let x = from; x < to; x++) {
        const i0 = Math.floor(sourceAt(x));
        const i1 = Math.max(i0 + 1, Math.floor(sourceAt(x + 1)));
        let peak = 0;
        for (let i = Math.max(0, i0); i < Math.min(detail.length, i1); i++) if (detail[i] > peak) peak = detail[i];
        const ph = Math.max(0.5, (peak / 100) * (rowH / 2 - 2));
        g.globalAlpha = x < center ? 0.45 : 0.95;
        g.fillRect(x, mid - ph, 1, ph * 2);
      }
      g.globalAlpha = 1;
      g.font = `600 11px ${FONT}`;
      const label = `${lane ? 'B' : 'A'}  ${clip.name}`;
      const lx = Math.max(8, from + 8);
      if (lx + g.measureText(label).width + 12 < to) {
        g.fillStyle = 'rgba(13,13,16,0.82)';
        g.fillRect(lx - 5, top + 4, g.measureText(label).width + 10, 17);
        g.fillStyle = clip.color;
        g.fillText(label, lx, top + 16);
      }
    });

    g.fillStyle = COLORS.amber;
    g.fillRect(center - 1, 0, 2, h);
  }

  /** Which kick is ahead and by how much, from each song's beat grid. */
  function beatOffset(blendIndex, t, m) {
    const outgoing = clips[blendIndex - 1];
    const incoming = clips[blendIndex];
    const phase = clip => {
      const span = clipSpan(clip, m);
      const source = span.srcStart + (t - span.start) * (span.srcEnd - span.srcStart) / (span.end - span.start);
      return source * clip.bpm / 60;
    };
    let beats = (phase(outgoing) - phase(incoming)) % 1;
    if (beats >= 0.5) beats -= 1;
    if (beats < -0.5) beats += 1;
    return { beats, ms: Math.abs(beats) * 60 / incoming.bpm * 1000 };
  }

  function songText(clip) {
    if (mode === 'beatstitch' && Math.abs(clip.bpm - demo.bpm) > 0.01) return `${clip.name} ${clip.bpm} → ${demo.bpm} BPM`;
    return `${clip.name} ${clip.bpm} BPM`;
  }

  let lastPhaseKey = '';
  function updateReadouts() {
    const t = mix.position;
    setText(ui.time, clock(t, true));
    const bar = Math.floor(t / BAR);
    setText(ui.bar, `${bar + 1}.${Math.floor((t - bar * BAR) / (BAR / 4)) + 1}`);
    const seconds = String(Math.floor(t));
    if (ui.timeline.getAttribute('aria-valuenow') !== seconds) {
      ui.timeline.setAttribute('aria-valuenow', seconds);
      ui.timeline.setAttribute('aria-valuetext', `${clock(t)} of ${clock(demo.duration)}`);
    }

    const blend = clips.findIndex((clip, index) => index && t >= clip.start && t < clip.start + clip.incoming);
    if (!started && !mix.busy && t === 0) {
      setText(ui.blendTitle, 'Ready');
      setText(ui.blendDetail, `${clips.map(c => c.name).join(', ')} at ${demo.bpm} BPM with 8-bar blends.`);
    } else if (blend > 0) {
      setText(ui.blendTitle, `Blend ${blend} → ${blend + 1}`);
      setText(ui.blendDetail, `${songText(clips[blend - 1])} into ${songText(clips[blend])}`);
    } else {
      const index = Math.max(0, clips.findIndex((clip, i) => t < (clips[i + 1] ? clips[i + 1].start : Infinity)));
      const clip = clips[index];
      const next = clips[index + 1];
      setText(ui.blendTitle, clip.name);
      setText(ui.blendDetail, `${clip.key}, ${clip.camelot}. ${mode === 'beatstitch' ? `${clip.bpm} → ${demo.bpm} BPM` : `${clip.bpm} BPM, original tempo`}${next ? `. Next blend in ${clock(next.start - t)}` : ''}`);
    }

    const key = blend > 0 ? `${blend}` : 'idle';
    if (blend > 0) {
      const { beats, ms } = beatOffset(blend, t, mode === 'beatstitch' ? 0 : 1);
      ui.phaseIn.style.left = `${50 + beats * 100}%`;
      const value = `${Math.round(ms)} ms`;
      setText(ui.phaseValue, value);
      ui.phaseValue.classList.toggle('good', ms < 12);
      ui.phaseValue.classList.toggle('bad', ms >= 12);
    }
    if (key !== lastPhaseKey) {
      lastPhaseKey = key;
      ui.phase.classList.toggle('idle', blend <= 0);
      if (blend > 0) {
        ui.phase.style.setProperty('--out', clips[blend - 1].color);
        ui.phase.style.setProperty('--in', clips[blend].color);
      } else {
        ui.phaseIn.style.left = '50%';
        ui.phaseValue.classList.remove('good', 'bad');
        setText(ui.phaseValue, 'Between blends');
      }
    }
  }

  function updateDeck() {
    const t = mix.position;
    if (!reducedMotion.matches) {
      ui.disc.style.transform = `rotate(${((t / BAR) * 360) % 360}deg)`;
    }
    let target = 0;
    if (mix.playing && analyser && !reducedMotion.matches) {
      analyser.getFloatTimeDomainData(analyserData);
      let sum = 0;
      for (let i = 0; i < analyserData.length; i++) sum += analyserData[i] * analyserData[i];
      target = clamp((Math.sqrt(sum / analyserData.length) - 0.03) * 4.2, 0, 1);
    }
    level = target > level ? lerp(level, target, 0.6) : level * 0.9;
    if (level < 0.002) level = 0;
    ui.deck.style.setProperty('--level', level.toFixed(3));
    return level > 0;
  }

  function updateHint() {
    let text = 'Press the record';
    if (mix.pending) text = 'Loading the mix';
    else if (mix.playing) text = mode === 'beatstitch' ? `One turn per bar at ${demo.bpm} BPM` : 'Unstitched: original tempos, plain crossfade';
    else if (started) text = `Paused at ${clock(mix.position)}`;
    setText(ui.hint, text);
  }

  function setMode(next) {
    if (next === mode) return;
    mode = next;
    mix.setVersion(next);
    ui.versions.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.version === next)));
    ui.deck.classList.toggle('apart', next === 'without');
    morph.from = morph.value;
    morph.to = next === 'without' ? 1 : 0;
    morph.start = performance.now();
    if (reducedMotion.matches) morph.value = morph.to;
    staticDirty = true;
    lastPhaseKey = '';
    updateHint();
    requestTick();
  }

  tickers.add(now => {
    let again = false;
    if (morph.value !== morph.to) {
      const t = clamp((now - morph.start) / 240, 0, 1);
      morph.value = t >= 1 ? morph.to : lerp(morph.from, morph.to, easeOut(t));
      staticDirty = true;
      again = true;
    }
    const active = mix.busy;
    if (isVisible(ui.player) || staticDirty) {
      drawTimeline();
      drawScope();
    }
    updateReadouts();
    if (updateDeck()) again = true;
    if (active) again = true;
    return again;
  });

  function syncMixButtons() {
    const busy = String(mix.busy);
    for (const button of [ui.play, ui.deckPlay, ui.heroPlay]) button.dataset.playing = busy;
    ui.play.setAttribute('aria-label', mix.busy ? 'Pause' : 'Play');
    ui.deckPlay.setAttribute('aria-label', mix.busy ? 'Pause the demo mix' : 'Play the demo mix');
    setText($('span', ui.heroPlay), mix.busy ? 'Pause the demo mix' : 'Play the demo mix');
    ui.deck.classList.toggle('loading', mix.pending);
    updateHint();
  }

  mix.on((type, value) => {
    if (type === 'state') {
      if (mix.busy) started = true;
      if (mix.busy) ui.error.hidden = true;
      syncMixButtons();
      requestTick();
    } else if (type === 'progress') {
      ui.ring.setAttribute('stroke-dasharray', `${(value * 100).toFixed(1)} 100`);
      setText(ui.hint, `Loading the mix, ${Math.round(value * 100)}%`);
    } else if (type === 'error') {
      ui.error.textContent = audioError(value);
      ui.error.hidden = false;
    } else {
      requestTick();
    }
  });

  ui.play.addEventListener('click', () => mix.toggle());
  ui.deckPlay.addEventListener('click', () => mix.toggle());
  ui.heroPlay.addEventListener('click', () => {
    const rect = ui.player.getBoundingClientRect();
    if (!mix.busy && rect.top > window.innerHeight - 120) {
      ui.player.scrollIntoView({ behavior: reducedMotion.matches ? 'auto' : 'smooth', block: 'center' });
    }
    mix.toggle();
  });
  ui.restart.addEventListener('click', () => { mix.seek(0); requestTick(); });
  ui.versions.forEach(button => button.addEventListener('click', () => setMode(button.dataset.version)));

  // Seek and scrub on the timeline
  let scrubbing = false;
  const timelineTime = event => {
    const rect = ui.timeline.getBoundingClientRect();
    const G = geometry(rect.width, rect.height);
    return clamp(G.T(event.clientX - rect.left), 0, demo.duration);
  };
  ui.timeline.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    scrubbing = true;
    try { ui.timeline.setPointerCapture(event.pointerId); } catch (error) { /* synthetic events */ }
    started = true;
    mix.seek(timelineTime(event));
    requestTick();
  });
  ui.timeline.addEventListener('pointermove', event => {
    const rect = ui.timeline.getBoundingClientRect();
    hoverX = event.pointerType === 'mouse' ? event.clientX - rect.left : null;
    if (scrubbing) mix.seek(timelineTime(event));
    requestTick();
  });
  const endScrub = () => { scrubbing = false; };
  ui.timeline.addEventListener('pointerup', endScrub);
  ui.timeline.addEventListener('pointercancel', endScrub);
  ui.timeline.addEventListener('pointerleave', () => { hoverX = null; requestTick(); });
  ui.timeline.addEventListener('keydown', event => {
    const step = event.shiftKey ? BAR * 8 : BAR;
    const actions = {
      ArrowLeft: () => mix.seek(mix.position - step),
      ArrowRight: () => mix.seek(mix.position + step),
      Home: () => mix.seek(0),
      End: () => mix.seek(demo.duration),
      ' ': () => mix.toggle(),
      Enter: () => mix.toggle(),
    };
    if (!actions[event.key]) return;
    event.preventDefault();
    started = true;
    actions[event.key]();
    requestTick();
  });

  /* ------------------------------------------------------ Ear training */

  const lessons = MEDIA.lessons;
  const lessonUi = {
    root: $('#lesson'), tabs: $('#lesson-tabs'), listen: $('#lesson-listen'), change: $('#lesson-change'),
    ab: $('#lesson-ab'), wave: $('#lesson-wave'), canvas: $('#lesson-canvas'), play: $('#lesson-play'),
    restart: $('#lesson-restart'), repeat: $('#lesson-repeat'), time: $('#lesson-time'), error: $('#lesson-error'),
  };
  watch(lessonUi.root);
  const lessonPlayers = lessons.map(lesson => new SyncPlayer(lesson.versions, { loop: true, nominal: lesson.duration }));
  let lessonIndex = 0;
  let lessonVersion = 'a';
  let lessonLayer = null;
  const lessonPlayer = () => lessonPlayers[lessonIndex];

  function tabLabel(lesson) {
    const text = lesson.title.replace(/^Hear\s+/i, '');
    return text.charAt(0).toUpperCase() + text.slice(1);
  }

  lessons.forEach((lesson, index) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.setAttribute('role', 'tab');
    button.id = `lesson-tab-${lesson.id}`;
    button.setAttribute('aria-controls', 'lesson-panel');
    button.textContent = tabLabel(lesson);
    button.addEventListener('click', () => selectLesson(index));
    button.addEventListener('keydown', event => {
      const move = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
      if (!move) return;
      event.preventDefault();
      const next = (index + move + lessons.length) % lessons.length;
      selectLesson(next);
      lessonUi.tabs.children[next].focus();
    });
    lessonUi.tabs.appendChild(button);
  });

  function selectLesson(index) {
    lessonPlayer().pause();
    lessonIndex = index;
    const lesson = lessons[index];
    Array.from(lessonUi.tabs.children).forEach((tab, i) => {
      tab.setAttribute('aria-selected', String(i === index));
      tab.tabIndex = i === index ? 0 : -1;
    });
    $('#lesson-panel').setAttribute('aria-labelledby', `lesson-tab-${lesson.id}`);
    setText(lessonUi.listen, lesson.listen);
    setText(lessonUi.change, lesson.change);
    lessonUi.ab.replaceChildren(...lesson.versions.map((version, i) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.dataset.version = version.id;
      button.append(version.label);
      const kbd = document.createElement('kbd');
      kbd.textContent = String(i + 1);
      button.append(kbd);
      button.addEventListener('click', () => setLessonVersion(version.id));
      return button;
    }));
    lessonUi.error.hidden = true;
    const player = lessonPlayer();
    player.setLoop(lessonUi.repeat.checked);
    setLessonVersion('a');
    syncLessonButtons();
    lessonLayer = null;
    requestTick();
  }

  function setLessonVersion(id) {
    lessonVersion = id;
    lessonPlayer().setVersion(id);
    $$('button', lessonUi.ab).forEach(button => button.setAttribute('aria-pressed', String(button.dataset.version === id)));
    lessonLayer = null;
    requestTick();
  }

  function buildLesson(size) {
    const { w, h, ratio } = size;
    const [layer, g] = makeLayer(w, h, ratio);
    const lesson = lessons[lessonIndex];
    const gut = 26;
    const right = 10;
    const bottom = 20;
    const rowH = (h - bottom - 12) / 2;
    lesson.versions.forEach((version, i) => {
      const top = 6 + i * (rowH + 4);
      const active = version.id === lessonVersion;
      g.font = `600 12px ${FONT}`;
      g.textBaseline = 'middle';
      g.fillStyle = active ? COLORS.amber : COLORS.text3;
      g.fillText(version.id.toUpperCase(), 9, top + rowH / 2);
      const count = version.wave.length;
      const at = x => ((x - gut) / (w - gut - right)) * lesson.duration;
      drawWave(g, version.wave, null, gut, w - right, top, rowH, active ? COLORS.amber : '#9aa0aa', x => at(x) * (count / lesson.duration) / WAVE_RATE, active ? 1 : 0.55);
    });
    g.textBaseline = 'alphabetic';
    g.font = `500 11px ${FONT}`;
    g.fillStyle = COLORS.text3;
    g.fillText('0:00', gut, h - 6);
    g.textAlign = 'right';
    g.fillText(clock(lesson.duration), w - right, h - 6);
    g.textAlign = 'left';
    return { layer, size, gut, right, bottom };
  }

  function drawLesson() {
    const size = fitCanvas(lessonUi.canvas);
    if (!lessonLayer || lessonLayer.size.w !== size.w || lessonLayer.size.h !== size.h || lessonLayer.size.ratio !== size.ratio) {
      lessonLayer = buildLesson(size);
    }
    const g = lessonUi.canvas.getContext('2d');
    g.setTransform(size.ratio, 0, 0, size.ratio, 0, 0);
    g.clearRect(0, 0, size.w, size.h);
    g.drawImage(lessonLayer.layer, 0, 0, size.w, size.h);
    const lesson = lessons[lessonIndex];
    const player = lessonPlayer();
    const x = lessonLayer.gut + (player.position / lesson.duration) * (size.w - lessonLayer.gut - lessonLayer.right);
    g.fillStyle = COLORS.text;
    g.fillRect(Math.round(x) - 0.75, 4, 1.5, size.h - lessonLayer.bottom - 6);
    setText(lessonUi.time, `${clock(player.position)} / ${clock(lesson.duration)}`);
    const seconds = String(Math.floor(player.position));
    if (lessonUi.wave.getAttribute('aria-valuenow') !== seconds) lessonUi.wave.setAttribute('aria-valuenow', seconds);
  }

  function syncLessonButtons() {
    const player = lessonPlayer();
    lessonUi.play.dataset.playing = String(player.busy);
    setText($('span', lessonUi.play), player.pending ? 'Loading' : player.playing ? 'Pause' : 'Listen');
  }

  lessonPlayers.forEach(player => player.on((type, value) => {
    if (player !== lessonPlayer()) return;
    if (type === 'state') { syncLessonButtons(); requestTick(); }
    if (type === 'error') { lessonUi.error.textContent = audioError(value); lessonUi.error.hidden = false; }
    if (type === 'seek') requestTick();
  }));

  tickers.add(() => {
    const player = lessonPlayer();
    if (isVisible(lessonUi.root) || !lessonLayer) drawLesson();
    return player.busy;
  });

  lessonUi.play.addEventListener('click', () => lessonPlayer().toggle());
  lessonUi.restart.addEventListener('click', () => { lessonPlayer().seek(0); requestTick(); });
  lessonUi.repeat.addEventListener('change', () => lessonPlayer().setLoop(lessonUi.repeat.checked));
  let lessonScrub = false;
  const lessonTime = event => {
    const rect = lessonUi.wave.getBoundingClientRect();
    return clamp((event.clientX - rect.left - 26) / (rect.width - 36), 0, 1) * lessons[lessonIndex].duration;
  };
  lessonUi.wave.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    lessonScrub = true;
    try { lessonUi.wave.setPointerCapture(event.pointerId); } catch (error) { /* synthetic events */ }
    lessonPlayer().seek(lessonTime(event));
    requestTick();
  });
  lessonUi.wave.addEventListener('pointermove', event => { if (lessonScrub) { lessonPlayer().seek(lessonTime(event)); requestTick(); } });
  lessonUi.wave.addEventListener('pointerup', () => { lessonScrub = false; });
  lessonUi.wave.addEventListener('pointercancel', () => { lessonScrub = false; });
  lessonUi.wave.addEventListener('keydown', event => {
    const player = lessonPlayer();
    const actions = {
      ArrowLeft: () => player.seek(player.position - 1),
      ArrowRight: () => player.seek(player.position + 1),
      Home: () => player.seek(0),
      ' ': () => player.toggle(),
      Enter: () => player.toggle(),
    };
    if (!actions[event.key]) return;
    event.preventDefault();
    actions[event.key]();
    requestTick();
  });

  /* ------------------------------------------- Keyboard: 1 and 2 switch */

  document.addEventListener('keydown', event => {
    if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.key !== '1' && event.key !== '2') return;
    const target = event.target;
    if (target && (/^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName) || target.isContentEditable)) return;
    const lessonFirst = lessonPlayer().busy || (!mix.busy && (visible.get(lessonUi.root) || 0) > (visible.get(ui.player) || 0));
    if (lessonFirst) setLessonVersion(event.key === '1' ? 'a' : 'b');
    else setMode(event.key === '1' ? 'beatstitch' : 'without');
  });

  /* ------------------------------------------------- Tempo table + grid */

  const tableBody = $('#tempo-table tbody');
  clips.forEach(clip => {
    const row = document.createElement('tr');
    const name = document.createElement('td');
    const swatch = document.createElement('span');
    swatch.className = 'swatch';
    swatch.style.background = clip.color;
    name.append(swatch, clip.name);
    const cells = [clip.bpm.toFixed(1), demo.bpm.toFixed(1), percent(clip) || '0.0%'].map(text => {
      const cell = document.createElement('td');
      cell.textContent = text;
      return cell;
    });
    row.append(name, ...cells);
    tableBody.appendChild(row);
  });

  const SVG = 'http://www.w3.org/2000/svg';
  const svgEl = (name, attributes = {}, parent) => {
    const element = document.createElementNS(SVG, name);
    for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
    if (parent) parent.appendChild(element);
    return element;
  };

  (function beatGrid() {
    const figure = $('#grid-figure');
    const toggle = $('#grid-match');
    const stat = $('#grid-stat');
    const outgoing = clips[0];
    const incoming = clips[1];
    const seconds = 8 * BAR;
    const left = 8;
    const width = 624;
    const X = t => left + (t / seconds) * width;
    const rows = { out: 66, in: 146 };
    const beats = Math.round(seconds / (60 / demo.bpm));

    for (let k = 0; k < beats; k++) {
      svgEl('line', { x1: X(k * 60 / demo.bpm), x2: X(k * 60 / demo.bpm), y1: rows.out - 24, y2: rows.in + 18, stroke: 'rgba(255,255,255,0.06)' }, figure);
    }
    const label = (y, clip) => {
      const text = svgEl('text', { x: left, y: y - 30, class: 'beat-row-label' }, figure);
      text.textContent = clip.name;
      const detail = svgEl('tspan', { dx: 8 }, text);
      return detail;
    };
    const outDetail = label(rows.out, outgoing);
    const inDetail = label(rows.in, incoming);
    inDetail.textContent = `${incoming.bpm} BPM`;
    const tick = (k, y, color, group) => {
      const tall = k % 4 === 0;
      return svgEl('rect', { x: -1.5, y: y - (tall ? 17 : 10), width: 3, height: tall ? 34 : 20, rx: 1.5, fill: color, class: 'beat-tick', style: `transform: translateX(${X(k * 60 / demo.bpm)}px)` }, group);
    };
    const outTicks = [];
    for (let k = 0; k < beats; k++) {
      tick(k, rows.in, incoming.color, figure);
      outTicks.push(tick(k, rows.out, outgoing.color, figure));
    }
    for (let bar = 0; bar < 8; bar++) {
      const text = svgEl('text', { x: X(bar * BAR), y: 196, class: 'axis-label' }, figure);
      text.textContent = bar === 0 ? 'Bar 1' : String(bar + 1);
    }

    const render = () => {
      const matched = toggle.checked;
      const bpm = matched ? demo.bpm : outgoing.bpm;
      outTicks.forEach((rect, k) => { rect.style.transform = `translateX(${X(k * 60 / bpm)}px)`; });
      outDetail.textContent = matched ? `${outgoing.bpm} → ${demo.bpm} BPM` : `${outgoing.bpm} BPM`;
      const apart = matched ? 0 : (beats * 60 / outgoing.bpm - beats * 60 / demo.bpm) * 1000;
      stat.textContent = `${Math.round(apart)} ms`;
      stat.style.color = matched ? COLORS.good : COLORS.bad;
    };
    toggle.addEventListener('change', render);
    render();
  })();

  /* ---------------------------------------------------- Camelot wheel */

  (function camelotWheel() {
    const figure = $('#camelot');
    const info = $('#wheel-info');
    const minorByNumber = { 1: 8, 2: 3, 3: 10, 4: 5, 5: 0, 6: 7, 7: 2, 8: 9, 9: 4, 10: 11, 11: 6, 12: 1 };
    const majorByNumber = { 1: 11, 2: 6, 3: 1, 4: 8, 5: 3, 6: 10, 7: 5, 8: 0, 9: 7, 10: 2, 11: 9, 12: 4 };
    const keyOf = code => {
      const number = parseInt(code, 10);
      const minor = code.endsWith('A');
      const tonic = (minor ? minorByNumber : majorByNumber)[number];
      return { number, minor, short: (minor ? MINOR : MAJOR)[tonic] + (minor ? 'm' : ''), long: `${(minor ? MINOR : MAJOR)[tonic]} ${minor ? 'minor' : 'major'}` };
    };
    const songsIn = code => clips.filter(clip => clip.camelot === code);
    const neighbors = code => {
      const { number, minor } = keyOf(code);
      const wrap = n => ((n - 1 + 12) % 12) + 1;
      const letter = minor ? 'A' : 'B';
      return [
        [`${wrap(number - 1)}${letter}`, 'One step'],
        [`${wrap(number + 1)}${letter}`, 'One step'],
        [`${number}${minor ? 'B' : 'A'}`, minor ? 'Relative major' : 'Relative minor'],
      ];
    };
    const cx = 200;
    const cy = 200;
    const rings = { B: [128, 196], A: [64, 126] };
    const arc = (r0, r1, a0, a1) => {
      const p = (r, a) => `${(cx + r * Math.cos(a)).toFixed(2)} ${(cy + r * Math.sin(a)).toFixed(2)}`;
      return `M ${p(r1, a0)} A ${r1} ${r1} 0 0 1 ${p(r1, a1)} L ${p(r0, a1)} A ${r0} ${r0} 0 0 0 ${p(r0, a0)} Z`;
    };
    const segments = new Map();
    for (const letter of ['B', 'A']) {
      for (let number = 1; number <= 12; number++) {
        const code = `${number}${letter}`;
        const center = (-90 + (number % 12) * 30) * Math.PI / 180;
        const a0 = center - Math.PI / 12;
        const a1 = center + Math.PI / 12;
        const [r0, r1] = rings[letter];
        const group = svgEl('g', { class: 'wheel-seg', tabindex: '-1', role: 'button', 'aria-label': `${code}, ${keyOf(code).long}` }, figure);
        const path = svgEl('path', { d: arc(r0, r1, a0, a1) }, group);
        const rm = (r0 + r1) / 2;
        const tx = cx + rm * Math.cos(center);
        const ty = cy + rm * Math.sin(center);
        const codeText = svgEl('text', { x: tx, y: ty - 7, class: 'code' }, group);
        codeText.textContent = code;
        const nameText = svgEl('text', { x: tx, y: ty + 9, class: 'name' }, group);
        nameText.textContent = keyOf(code).short;
        songsIn(code).forEach((clip, i, list) => {
          const offset = (i - (list.length - 1) / 2) * 9;
          svgEl('circle', { cx: tx + offset, cy: ty + 22, r: 3.2, fill: clip.color }, group);
        });
        segments.set(code, { group, path, codeText, nameText });
        group.addEventListener('click', () => select(code, true));
        group.addEventListener('pointerenter', () => paint(code));
        group.addEventListener('pointerleave', () => paint(selected));
        // Left and right go around the wheel; up and down move between the major and minor rings.
        group.addEventListener('keydown', event => {
          const { number: n, minor } = keyOf(code);
          const wrap = v => ((v - 1 + 12) % 12) + 1;
          const ring = minor ? 'A' : 'B';
          const moves = {
            ArrowRight: `${wrap(n + 1)}${ring}`, ArrowLeft: `${wrap(n - 1)}${ring}`,
            ArrowUp: `${n}B`, ArrowDown: `${n}A`,
          };
          if (!moves[event.key]) return;
          event.preventDefault();
          select(moves[event.key], true);
        });
      }
    }
    const hub = svgEl('text', { x: cx, y: cy, class: 'axis-label', 'text-anchor': 'middle', 'dominant-baseline': 'central' }, figure);
    hub.textContent = 'Inner: minor';
    const hub2 = svgEl('text', { x: cx, y: cy + 16, class: 'axis-label', 'text-anchor': 'middle', 'dominant-baseline': 'central' }, figure);
    hub2.textContent = 'Outer: major';
    hub.setAttribute('y', cy - 8);
    hub2.setAttribute('y', cy + 10);

    let selected = '8A';
    function paint(code) {
      const near = new Set(neighbors(code).map(([c]) => c));
      for (const [key, seg] of segments) {
        const inner = key.endsWith('A');
        let fill = inner ? '#1d1d23' : '#23232a';
        let text = COLORS.text2;
        let sub = COLORS.text3;
        if (key === code) { fill = COLORS.amber; text = '#1a1206'; sub = 'rgba(26,18,6,0.72)'; }
        else if (near.has(key)) { fill = 'rgba(255,162,52,0.24)'; text = COLORS.text; sub = COLORS.text2; }
        seg.path.style.fill = fill;
        seg.codeText.style.fill = text;
        seg.nameText.style.fill = sub;
      }
    }
    function select(code, focus) {
      selected = code;
      for (const [key, seg] of segments) seg.group.setAttribute('tabindex', key === code ? '0' : '-1');
      if (focus) segments.get(code).group.focus();
      paint(code);
      const key = keyOf(code);
      const here = songsIn(code);
      const lines = neighbors(code).map(([c, why]) => {
        const songs = songsIn(c).map(clip => clip.name).join(', ');
        return `<li><span><b>${c}</b> ${keyOf(c).long}${songs ? `<br><span style="color:var(--text-3)">${songs}</span>` : ''}</span><span>${why}</span></li>`;
      }).join('');
      info.innerHTML = `<h4>${code}</h4><p>${key.long}</p><ul>${lines}</ul>` +
        `<p class="wheel-songs">${here.length ? `In the demo: ${here.map(clip => clip.name).join(' and ')}.` : 'Choose any key on the wheel.'}</p>`;
    }
    select(selected, false);
  })();

  /* ------------------------------------------------- Bass handoff chart */

  (function handoffChart() {
    const figure = $('#handoff-figure');
    const toggle = $('#handoff-on');
    const stat = $('#handoff-stat');
    const statLabel = $('#handoff-stat-label');
    const buttons = $$('#handoff-bars button');
    const plot = { x: 40, y: 14, w: 588, h: 176 };
    const state = { bars: 8, on: 1 };
    const tween = { from: { ...state }, to: { ...state }, start: 0, running: false };
    const out = clips[0].color;
    const inn = clips[1].color;

    const grid = svgEl('g', {}, figure);
    const shade = svgEl('path', { fill: 'rgba(255,123,110,0.2)' }, figure);
    const paths = {
      outHigh: svgEl('path', { fill: 'none', stroke: out, 'stroke-width': 2.25 }, figure),
      inHigh: svgEl('path', { fill: 'none', stroke: inn, 'stroke-width': 2.25 }, figure),
      outLow: svgEl('path', { fill: 'none', stroke: out, 'stroke-width': 2.25, 'stroke-dasharray': '6 5' }, figure),
      inLow: svgEl('path', { fill: 'none', stroke: inn, 'stroke-width': 2.25, 'stroke-dasharray': '6 5' }, figure),
    };
    const axis = svgEl('g', {}, figure);

    function drawGrid(bars) {
      grid.replaceChildren();
      axis.replaceChildren();
      const step = bars <= 8 ? 1 : bars <= 16 ? 2 : 4;
      for (let b = 0; b <= bars; b += step) {
        const x = plot.x + (b / bars) * plot.w;
        svgEl('line', { x1: x, x2: x, y1: plot.y, y2: plot.y + plot.h, stroke: 'rgba(255,255,255,0.06)' }, grid);
        const text = svgEl('text', { x, y: plot.y + plot.h + 20, class: 'axis-label', 'text-anchor': b === 0 ? 'start' : b === bars ? 'end' : 'middle' }, axis);
        text.textContent = b === bars ? `${b} bars` : String(b);
      }
      const mid = plot.x + plot.w / 2;
      svgEl('line', { x1: mid, x2: mid, y1: plot.y, y2: plot.y + plot.h, stroke: 'rgba(255,162,52,0.45)', 'stroke-dasharray': '2 4' }, grid);
      for (const [value, text] of [[1, '100%'], [0.5, '50%'], [0, '0']]) {
        const y = plot.y + (1 - value) * plot.h;
        svgEl('line', { x1: plot.x, x2: plot.x + plot.w, y1: y, y2: y, stroke: 'rgba(255,255,255,0.06)' }, grid);
        const label = svgEl('text', { x: plot.x - 8, y: y + 4, class: 'axis-label', 'text-anchor': 'end' }, axis);
        label.textContent = text;
      }
    }

    function render(bars, on) {
      const n = 240;
      const points = { outHigh: [], inHigh: [], outLow: [], inLow: [] };
      const both = [];
      for (let i = 0; i <= n; i++) {
        const progress = (i / n) * bars;
        const o = blendGains(progress, bars, false, on);
        const c = blendGains(progress, bars, true, on);
        const x = plot.x + (i / n) * plot.w;
        const y = v => plot.y + (1 - v) * plot.h;
        points.outHigh.push([x, y(o.high)]);
        points.inHigh.push([x, y(c.high)]);
        points.outLow.push([x, y(o.low)]);
        points.inLow.push([x, y(c.low)]);
        both.push([x, y(Math.min(o.low, c.low))]);
      }
      const line = list => list.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`).join('');
      for (const key of Object.keys(paths)) paths[key].setAttribute('d', line(points[key]));
      const floor = plot.y + plot.h;
      shade.setAttribute('d', `${line(both)}L${plot.x + plot.w} ${floor}L${plot.x} ${floor}Z`);
    }

    // Bars where both bass lines are above a quarter of full volume.
    function overlapBars(bars, on) {
      let count = 0;
      const n = 4000;
      for (let i = 0; i < n; i++) {
        const progress = ((i + 0.5) / n) * bars;
        if (Math.min(blendGains(progress, bars, false, on).low, blendGains(progress, bars, true, on).low) > 0.25) count++;
      }
      return (count / n) * bars;
    }

    function update() {
      buttons.forEach(button => button.setAttribute('aria-pressed', String(Number(button.dataset.bars) === state.bars)));
      const value = overlapBars(state.bars, state.on);
      stat.textContent = `${value < 10 ? value.toFixed(1) : Math.round(value)} bars`;
      stat.style.color = value < 1 ? COLORS.good : COLORS.bad;
      statLabel.textContent = `of ${state.bars} with both bass lines loud`;
      drawGrid(state.bars);
      tween.from = { ...tween.current || state };
      tween.to = { ...state };
      tween.start = performance.now();
      tween.running = !reducedMotion.matches;
      if (!tween.running) { tween.current = { ...state }; render(state.bars, state.on); }
      requestTick();
    }

    tickers.add(now => {
      if (!tween.running) return false;
      const t = clamp((now - tween.start) / 220, 0, 1);
      const e = easeOut(t);
      // Interpolate the handoff width as a fraction of the blend so the curves morph smoothly.
      const fraction = lerp(1 / tween.from.bars, 1 / tween.to.bars, e);
      tween.current = { bars: 1 / fraction, on: lerp(tween.from.on, tween.to.on, e) };
      render(tween.current.bars, tween.current.on);
      if (t >= 1) { tween.running = false; tween.current = { ...tween.to }; }
      return tween.running;
    });

    buttons.forEach(button => button.addEventListener('click', () => { state.bars = Number(button.dataset.bars); update(); }));
    toggle.addEventListener('change', () => { state.on = toggle.checked ? 1 : 0; update(); });
    tween.current = { ...state };
    render(state.bars, state.on);
    update();
  })();

  /* ------------------------------------------- Download for the visitor's OS */

  (function platformDownload() {
    const platform = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || '';
    const agent = navigator.userAgent || '';
    let os = null;
    if (/win/i.test(platform)) os = 'windows';
    else if (/linux|x11/i.test(platform) && !/android/i.test(agent)) os = 'linux';
    else if (/mac/i.test(platform)) os = 'mac';
    if (!os) return;
    const names = { mac: 'Mac', windows: 'Windows', linux: 'Linux' };
    const card = $(`.platform[data-os="${os}"]`);
    $$('[data-download-label]').forEach(label => {
      label.textContent = `Download for ${names[os]}`;
      const link = label.closest('a');
      if (link && card) link.href = card.href;
    });
    $$('.platform').forEach(card => card.classList.toggle('is-recommended', card.dataset.os === os));
  })();

  /* ---------------------------------------------------------- Page chrome */

  const nav = $('#nav');
  const onScroll = () => nav.classList.toggle('scrolled', window.scrollY > 8);
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();

  if ('ResizeObserver' in window) {
    new ResizeObserver(() => { staticDirty = true; lessonLayer = null; requestTick(); }).observe(document.body);
  } else {
    window.addEventListener('resize', () => { staticDirty = true; lessonLayer = null; requestTick(); });
  }
  reducedMotion.addEventListener?.('change', () => { ui.disc.style.transform = ''; requestTick(); });

  selectLesson(0);
  syncMixButtons();
  requestTick();
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => { staticDirty = true; lessonLayer = null; requestTick(); });
})();
