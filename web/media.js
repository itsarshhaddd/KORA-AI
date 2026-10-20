/* KORA multimodal input: image attachments, microphone voice commands, audio files.
 *
 * Talks to the backend through:
 *   GET  /api/capabilities      -> {vision, audio, audio_chunk_seconds}
 *   POST /api/media             -> stores an image / short audio clip, returns {id, kind, name, url}
 *   POST /api/transcribe        -> {text} for one <=30 s wav clip (used for voice commands)
 * and with app.js through window.KoraApp (sendQuestion, toast) / window.KoraMedia.
 */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const esc = v => String(v ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  const toast = (m, k = "info") => (window.KoraApp?.toast ? window.KoraApp.toast(m, k) : console.log(m));
  const store = { get: k => { try { return localStorage.getItem(k); } catch (_) { return null; } }, set: (k, v) => { try { localStorage.setItem(k, v); } catch (_) {} } };

  const MAX_ATTACH = 4;
  const MAX_REC_SECONDS = 60;
  const SAMPLE_RATE = 16000;

  const caps = { vision: false, audio: false, chunk: 30 };
  let tray = [];            // {key, kind, name, url, id, status}
  let seq = 0;
  let autoSend = store.get("kora_voice_autosend") !== "0";

  const ICON = {
    plus: `<svg viewBox="0 0 24 24" class="i"><path d="M12 5v14M5 12h14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>`,
    mic: `<svg viewBox="0 0 24 24" class="i"><rect x="9" y="3" width="6" height="11" rx="3" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>`,
    x: `<svg viewBox="0 0 24 24" class="i"><path d="M6 6l12 12M18 6L6 18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>`,
    check: `<svg viewBox="0 0 24 24" class="i"><path d="M5 12.5l4.5 4.5L19 7.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
    wave: `<svg viewBox="0 0 24 24" class="i"><path d="M4 10v4M8 6v12M12 3v18M16 7v10M20 10v4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>`,
  };

  // ---------------------------------------------------------------- audio utils
  async function decodeToMono16k(blobOrBuffer) {
    const data = blobOrBuffer.arrayBuffer ? await blobOrBuffer.arrayBuffer() : blobOrBuffer;
    const AC = window.AudioContext || window.webkitAudioContext;
    const ac = new AC();
    let decoded;
    try { decoded = await ac.decodeAudioData(data.slice(0)); } finally { ac.close?.(); }
    const off = new OfflineAudioContext(1, Math.max(1, Math.ceil(decoded.duration * SAMPLE_RATE)), SAMPLE_RATE);
    const src = off.createBufferSource();
    src.buffer = decoded; src.connect(off.destination); src.start();
    const rendered = await off.startRendering();
    return rendered.getChannelData(0);
  }

  function encodeWav(samples) {
    const buf = new ArrayBuffer(44 + samples.length * 2);
    const v = new DataView(buf);
    const w = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
    w(0, "RIFF"); v.setUint32(4, 36 + samples.length * 2, true); w(8, "WAVE"); w(12, "fmt ");
    v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
    v.setUint32(24, SAMPLE_RATE, true); v.setUint32(28, SAMPLE_RATE * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
    w(36, "data"); v.setUint32(40, samples.length * 2, true);
    for (let i = 0; i < samples.length; i++) {
      const s = Math.max(-1, Math.min(1, samples[i]));
      v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
    return new Blob([buf], { type: "audio/wav" });
  }

  function peak(samples) { let p = 0; for (let i = 0; i < samples.length; i += 16) p = Math.max(p, Math.abs(samples[i])); return p; }

  function splitChunks(samples) {
    const size = caps.chunk * SAMPLE_RATE, out = [];
    for (let i = 0; i < samples.length; i += size) out.push(samples.subarray(i, Math.min(samples.length, i + size)));
    return out;
  }

  async function postForm(url, form) {
    const resp = await fetch(url, { method: "POST", body: form });
    let data = {};
    try { data = await resp.json(); } catch (_) {}
    if (!resp.ok) throw new Error(data.error || `Request failed (${resp.status})`);
    return data;
  }

  async function transcribeSamples(samples, setStatus) {
    const chunks = splitChunks(samples), parts = [];
    for (let i = 0; i < chunks.length; i++) {
      if (chunks.length > 1) setStatus?.(`Transcribing… ${i + 1}/${chunks.length}`);
      if (peak(chunks[i]) < 0.008) continue; // silence
      const form = new FormData();
      form.append("file", encodeWav(chunks[i]), "voice.wav");
      const { text } = await postForm("/api/transcribe", form);
      if (text) parts.push(text.trim());
    }
    return parts.join(" ").trim();
  }

  // ---------------------------------------------------------------- tray (attachments)
  function explainUnavailable() {
    toast("Image & voice input need the Gemma multimodal file. Put mmproj-*.gguf in the models folder and restart KORA.", "error");
  }

  function renderTray() {
    const el = $("#attachTray");
    if (!el) return;
    el.hidden = tray.length === 0;
    el.innerHTML = tray.map(a => {
      const body = a.kind === "image"
        ? `<img src="${esc(a.url)}" alt="${esc(a.name)}">`
        : `<span class="att-audio">${ICON.wave}<em>${esc(a.name)}</em></span>`;
      return `<div class="att-chip ${a.kind} ${a.status}" data-key="${a.key}">${body}${a.status === "uploading" ? `<span class="att-spin"></span>` : ""}<button type="button" class="att-x" aria-label="Remove" data-remove="${a.key}">${ICON.x}</button></div>`;
    }).join("");
    el.querySelectorAll("[data-remove]").forEach(b => b.addEventListener("click", () => { tray = tray.filter(a => String(a.key) !== b.dataset.remove); renderTray(); }));
    el.querySelectorAll(".att-chip.image img").forEach(img => img.addEventListener("click", () => openLightbox(img.src)));
    updateSendState();
  }

  function updateSendState() {
    const busy = tray.some(a => a.status === "uploading");
    const btn = $("#sendBtn");
    if (btn && !btn.classList.contains("stop-mode")) btn.disabled = busy;
  }

  async function addFiles(fileList) {
    const files = [...fileList];
    if (!files.length) return;
    if (!caps.vision && !caps.audio) { explainUnavailable(); return; }
    for (const file of files) {
      const isImg = file.type.startsWith("image/");
      const isAud = file.type.startsWith("audio/") || /\.(wav|mp3|m4a|ogg|oga|flac|aac|webm)$/i.test(file.name);
      if (!isImg && !isAud) { toast(`"${file.name}" isn't an image or audio file.`, "error"); continue; }
      if (tray.length >= MAX_ATTACH) { toast(`You can attach up to ${MAX_ATTACH} files per message.`); break; }
      if (isImg) await attachImage(file); else await attachAudio(file);
    }
  }

  async function attachImage(file) {
    if (!caps.vision) { explainUnavailable(); return; }
    const item = { key: ++seq, kind: "image", name: file.name || "image.png", url: URL.createObjectURL(file), status: "uploading" };
    tray.push(item); renderTray();
    try {
      const form = new FormData(); form.append("file", file, item.name);
      const info = await postForm("/api/media", form);
      Object.assign(item, { id: info.id, status: "ready", url: info.url });
    } catch (e) {
      tray = tray.filter(a => a !== item); toast(e.message, "error");
    }
    renderTray();
  }

  async function attachAudio(file) {
    if (!caps.audio) { explainUnavailable(); return; }
    let samples;
    try { samples = await decodeToMono16k(file); } catch (_) { toast(`Couldn't read the audio in "${file.name}".`, "error"); return; }
    if (samples.length / SAMPLE_RATE > caps.chunk) {
      // Too long for the model to hear in one go: turn it into text instead.
      toast(`That clip is longer than ${caps.chunk}s — transcribing it into the message box instead.`);
      setBarState("busy", "Transcribing…");
      try {
        const text = await transcribeSamples(samples, t => setBarState("busy", t));
        if (text) insertText(text); else toast("No speech found in that audio.");
      } catch (e) { toast(e.message, "error"); }
      setBarState("idle");
      return;
    }
    const item = { key: ++seq, kind: "audio", name: file.name || "audio.wav", url: "", status: "uploading" };
    tray.push(item); renderTray();
    try {
      const form = new FormData(); form.append("file", encodeWav(samples), item.name.replace(/\.[^.]+$/, "") + ".wav");
      const info = await postForm("/api/media", form);
      Object.assign(item, { id: info.id, status: "ready", url: info.url });
    } catch (e) { tray = tray.filter(a => a !== item); toast(e.message, "error"); }
    renderTray();
  }

  function insertText(text) {
    const ta = $("#composerInput");
    if (!ta) return;
    ta.value = (ta.value ? ta.value.replace(/\s*$/, " ") : "") + text;
    ta.dispatchEvent(new Event("input"));
    ta.focus();
  }

  // ---------------------------------------------------------------- recording (voice commands)
  const rec = { active: false, mr: null, stream: null, chunks: [], cancelled: false, t0: 0, timer: null, raf: 0, ctx: null, levels: [] };

  function setBarState(mode, label) {
    const bar = $("#recBar"), form = $("#composerForm");
    if (!bar || !form) return;
    bar.hidden = mode === "idle";
    form.classList.toggle("recording", mode === "rec");
    form.classList.toggle("transcribing", mode === "busy");
    $("#micBtn")?.classList.toggle("on", mode === "rec");
    if (mode === "busy") $("#recLabel").textContent = label || "Transcribing…";
  }

  async function startRecording() {
    if (rec.active) return;
    if (!caps.audio) { explainUnavailable(); return; }
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") { toast("This window can't access a microphone.", "error"); return; }
    try {
      rec.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 } });
    } catch (e) {
      toast(e.name === "NotAllowedError" ? "Microphone permission was denied." : "No microphone found.", "error");
      return;
    }
    const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"].find(t => MediaRecorder.isTypeSupported?.(t)) || "";
    rec.mr = new MediaRecorder(rec.stream, mime ? { mimeType: mime } : {});
    rec.chunks = []; rec.cancelled = false; rec.levels = [];
    rec.mr.ondataavailable = e => { if (e.data && e.data.size) rec.chunks.push(e.data); };
    rec.mr.onstop = onRecordingStopped;
    rec.mr.start(250);
    rec.active = true; rec.t0 = Date.now();

    const AC = window.AudioContext || window.webkitAudioContext;
    rec.ctx = new AC();
    const analyser = rec.ctx.createAnalyser(); analyser.fftSize = 512;
    rec.ctx.createMediaStreamSource(rec.stream).connect(analyser);
    const buf = new Uint8Array(analyser.fftSize);
    const canvas = $("#recWave"), g = canvas.getContext("2d");
    let last = 0;
    const draw = now => {
      if (!rec.active) return;
      rec.raf = requestAnimationFrame(draw);
      if (now - last < 45) return; last = now;
      analyser.getByteTimeDomainData(buf);
      let sum = 0; for (const b of buf) sum += ((b - 128) / 128) ** 2;
      rec.levels.push(Math.min(1, Math.sqrt(sum / buf.length) * 4));
      const dpr = window.devicePixelRatio || 1, w = canvas.clientWidth, h = canvas.clientHeight;
      if (canvas.width !== w * dpr) { canvas.width = w * dpr; canvas.height = h * dpr; }
      g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
      g.fillStyle = getComputedStyle(canvas).color;
      const barW = 3, gap = 3, n = Math.floor(w / (barW + gap)), vis = rec.levels.slice(-n);
      vis.forEach((lv, i) => { const bh = Math.max(3, lv * h); g.fillRect(w - (vis.length - i) * (barW + gap), (h - bh) / 2, barW, bh); });
    };
    rec.raf = requestAnimationFrame(draw);
    rec.timer = setInterval(() => {
      const s = Math.floor((Date.now() - rec.t0) / 1000);
      $("#recTime").textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
      if (s >= MAX_REC_SECONDS) stopRecording(true);
    }, 250);
    $("#recTime").textContent = "0:00";
    $("#recLabel").textContent = "Listening…";
    setBarState("rec");
  }

  function stopRecording(send) {
    if (!rec.active) return;
    rec.cancelled = !send; rec.active = false;
    clearInterval(rec.timer); cancelAnimationFrame(rec.raf);
    try { rec.mr.stop(); } catch (_) {}
    rec.stream?.getTracks().forEach(t => t.stop());
    rec.ctx?.close?.(); rec.ctx = null;
    if (!send) setBarState("idle");
  }

  async function onRecordingStopped() {
    if (rec.cancelled) return;
    const duration = (Date.now() - rec.t0) / 1000;
    if (duration < 0.5) { setBarState("idle"); toast("Too short — hold on a bit longer."); return; }
    setBarState("busy", "Transcribing…");
    try {
      const samples = await decodeToMono16k(new Blob(rec.chunks, { type: rec.mr.mimeType || "audio/webm" }));
      const text = await transcribeSamples(samples, t => setBarState("busy", t));
      setBarState("idle");
      if (!text) { toast("Didn't catch that — try again a little closer to the mic."); return; }
      if (autoSend && window.KoraApp?.sendQuestion && !tray.some(a => a.status === "uploading")) {
        const typed = ($("#composerInput")?.value || "").trim();
        window.KoraApp.sendQuestion(typed ? `${typed} ${text}` : text, { voice: true });
      } else insertText(text);
    } catch (e) {
      setBarState("idle"); toast(e.message || "Transcription failed", "error");
    }
  }

  function toggleRecording() { rec.active ? stopRecording(true) : startRecording(); }

  function refreshAutoSendLabel() {
    const b = $("#voiceModeBtn");
    if (b) { b.textContent = autoSend ? "Voice: auto-send" : "Voice: review first"; b.classList.toggle("on", autoSend); }
  }

  // ---------------------------------------------------------------- lightbox + message rendering
  function openLightbox(src) {
    const box = document.createElement("div");
    box.className = "lightbox";
    box.innerHTML = `<img src="${esc(src)}" alt=""><button type="button" class="lightbox-x" aria-label="Close">${ICON.x}</button>`;
    const close = () => { box.remove(); document.removeEventListener("keydown", onKey); };
    const onKey = e => { if (e.key === "Escape") close(); };
    box.addEventListener("click", e => { if (e.target !== box.querySelector("img")) close(); });
    document.addEventListener("keydown", onKey);
    document.body.appendChild(box);
  }

  function renderAttachments(list) {
    const items = (list || []).filter(a => a && a.id);
    if (!items.length) return "";
    return `<div class="msg-atts">${items.map(a => {
      const url = `/api/media/${encodeURIComponent(a.id)}`;
      return a.kind === "image"
        ? `<img class="msg-img" src="${url}" alt="${esc(a.name)}" loading="lazy" data-lightbox="${url}">`
        : `<div class="msg-audio">${ICON.wave}<audio controls preload="none" src="${url}"></audio></div>`;
    }).join("")}</div>`;
  }

  function bindMessage(root) {
    root.querySelectorAll("[data-lightbox]").forEach(img => img.addEventListener("click", () => openLightbox(img.dataset.lightbox)));
  }

  // ---------------------------------------------------------------- init
  async function init() {
    const form = $("#composerForm");
    if (!form) return;
    try {
      const c = await (await fetch("/api/capabilities")).json();
      caps.vision = !!c.vision; caps.audio = !!c.audio; caps.chunk = c.audio_chunk_seconds || 30;
    } catch (_) {}
    form.classList.toggle("no-media", !caps.vision && !caps.audio);

    $("#attachMediaBtn")?.addEventListener("click", () => (caps.vision || caps.audio) ? $("#mediaFileInput").click() : explainUnavailable());
    $("#mediaFileInput")?.addEventListener("change", e => { addFiles(e.target.files); e.target.value = ""; });
    $("#micBtn")?.addEventListener("click", toggleRecording);
    $("#recCancel")?.addEventListener("click", () => stopRecording(false));
    $("#recDone")?.addEventListener("click", () => stopRecording(true));
    $("#voiceModeBtn")?.addEventListener("click", () => { autoSend = !autoSend; store.set("kora_voice_autosend", autoSend ? "1" : "0"); refreshAutoSendLabel(); });
    refreshAutoSendLabel();

    // paste images straight into the composer
    $("#composerInput")?.addEventListener("paste", e => {
      const files = [...(e.clipboardData?.files || [])].filter(f => f.type.startsWith("image/"));
      if (files.length) { e.preventDefault(); addFiles(files); }
    });
    // drag & drop anywhere over the chat
    const zone = $(".chat-main") || document.body;
    let depth = 0;
    const over = on => form.classList.toggle("dragover", on);
    zone.addEventListener("dragenter", e => { if (e.dataTransfer?.types?.includes("Files")) { depth++; over(true); } });
    zone.addEventListener("dragleave", () => { depth = Math.max(0, depth - 1); if (!depth) over(false); });
    zone.addEventListener("dragover", e => { if (e.dataTransfer?.types?.includes("Files")) e.preventDefault(); });
    zone.addEventListener("drop", e => { if (e.dataTransfer?.files?.length) { e.preventDefault(); depth = 0; over(false); addFiles(e.dataTransfer.files); } });
    // Ctrl/Cmd + M toggles the microphone
    document.addEventListener("keydown", e => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "m") { e.preventDefault(); toggleRecording(); } if (e.key === "Escape" && rec.active) stopRecording(false); });
  }

  window.KoraMedia = {
    has: () => tray.length > 0,
    busy: () => tray.some(a => a.status === "uploading"),
    take() { const out = tray.filter(a => a.status === "ready").map(({ id, kind, name }) => ({ id, kind, name })); tray = []; renderTray(); return out; },
    renderAttachments, bindMessage, openLightbox,
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
