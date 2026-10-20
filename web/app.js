(() => {
  "use strict";

  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => [...root.querySelectorAll(s)];

  const state = {
    view: "home",
    sessionId: kstore.get("kora_session_id") || null,
    sending: false,
    health: null,
    settings: null,
    docs: [],
    sessions: [],
    progress: { active: false, stage: "idle", message: "Ready." },
    indexTimer: null,
    healthTimer: null,
  };

  const icons = {
    bot: `<svg viewBox="0 0 24 24"><path d="M12 3v3M8 7h8a3 3 0 0 1 3 3v6a3 3 0 0 1-3 3H8a3 3 0 0 1-3-3v-6a3 3 0 0 1 3-3Z" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="M8.5 12h.01M15.5 12h.01M9 16c1.7 1.2 4.3 1.2 6 0" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>`,
    mic: `<svg viewBox="0 0 24 24"><rect x="9" y="3" width="6" height="11" rx="3" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>`,
    user: `<svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="3.2" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="M5 20c1.4-4 4-6 7-6s5.6 2 7 6" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>`
  };

  const escapeHTML = value => String(value ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");

  const docName = doc => String(doc?.source || doc?.path || doc?.document_id || "Document").split(/[\\/]/).pop();
  const docType = doc => String(doc?.file_type || extClass(docName(doc)) || "file").replace(/^\./, "").toUpperCase();
  const extClass = name => String(name || "").split(".").pop().toLowerCase();
  const formatCount = value => Number.isFinite(Number(value)) ? Number(value).toLocaleString() : "—";

  function toast(message, kind = "info") {
    const stack = $("#toastStack");
    if (!stack) return;
    if ([...stack.children].some(t => t.textContent === message && !t.classList.contains("leaving"))) return;
    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    el.textContent = message;
    stack.appendChild(el);
    setTimeout(() => { el.classList.add("leaving"); setTimeout(() => el.remove(), 320); }, 2800);
  }

  async function api(url, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), options.timeout || 20000);
    try {
      const response = await fetch(url, { cache: "no-store", ...options, signal: controller.signal });
      let data = {};
      try { data = await response.json(); } catch (_) {}
      if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
      return data;
    } catch (error) {
      if (error?.name === "AbortError") throw new Error("KORA took too long to respond. Please try again.");
      if (error instanceof TypeError) throw new Error("Can't reach the KORA server. Make sure KORA is running, then reload.");
      throw error;
    } finally {
      clearTimeout(timer);
    }
  }

  function profileName() {
    return (kstore.get("kora_profile_name") || "Local user").trim() || "Local user";
  }

  function setProfileName(name) {
    const clean = String(name || "").trim().slice(0, 80) || "Local user";
    kstore.set("kora_profile_name", clean);
    const initial = clean.charAt(0).toUpperCase() || "L";
    ["#topProfileName", "#sidebarProfileName"].forEach(id => { const el = $(id); if (el) el.textContent = clean; });
    ["#topAvatar", "#profileAvatar"].forEach(id => { const el = $(id); if (el) el.textContent = initial; });
  }

  function showView(name) {
    const target = $(`#view-${name}`);
    if (!target) return;
    state.view = name;
    const labels = { home: "Home", chat: "Chat", documents: "Documents", knowledge: "Knowledge Base", library: "Library", settings: "Settings" };
    const title = $("#topTitle");
    if (title) title.textContent = labels[name] || "KORA";
    $$(".nav-item[data-view]").forEach(btn => btn.classList.toggle("active", btn.dataset.view === name));
    $$(".view").forEach(view => view.classList.toggle("active", view === target));
    $("#contentScroll")?.scrollTo({ top: 0, behavior: "smooth" });
    if (name === "home") loadHome();
    if (name === "chat") loadChatList();
    if (name === "documents") loadDocuments();
    if (name === "knowledge") loadKnowledge();
    if (name === "library") loadLibrary();
    if (name === "settings") loadSettings();
  }

  // ---- Export conversation (PDF / Markdown) ----
  function updateExportBtn() {
    const btn = $("#exportBtn");
    if (btn) btn.disabled = !(state.sessionId && $("#messages")?.querySelector(".msg:not(.error)"));
  }

  function closeExportMenu() {
    const menu = $("#exportMenu");
    if (menu) menu.hidden = true;
    $("#exportBtn")?.setAttribute("aria-expanded", "false");
  }

  async function exportChat(fmt) {
    closeExportMenu();
    if (!state.sessionId) return;
    const sid = state.sessionId;
    const label = fmt === "md" ? "Markdown" : "PDF";
    try {
      if (window.pywebview?.api?.save_export) {
        // Desktop window: native "Save as" dialog.
        toast(`Preparing ${label}…`);
        const r = await window.pywebview.api.save_export(sid, fmt);
        if (r?.ok) toast(`Saved to ${r.path}`);
        else if (!r?.cancelled) toast(r?.error || "Export failed", "error");
        return;
      }
      const resp = await fetch(`/api/library/${encodeURIComponent(sid)}/export.${fmt}`);
      if (!resp.ok) {
        let msg = `Export failed (${resp.status})`;
        try { msg = (await resp.json()).error || msg; } catch (_) {}
        throw new Error(msg);
      }
      const blob = await resp.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = resp.headers.get("X-Export-Filename") || `KORA_conversation.${fmt}`;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 4000);
      toast(`${label} downloaded`);
    } catch (error) {
      toast(error.message || "Export failed", "error");
    }
  }

  function bindExport() {
    const btn = $("#exportBtn"), menu = $("#exportMenu");
    if (!btn || !menu) return;
    btn.addEventListener("click", e => {
      e.stopPropagation();
      const open = menu.hidden;
      menu.hidden = !open;
      btn.setAttribute("aria-expanded", String(open));
    });
    menu.querySelectorAll("[data-export]").forEach(b => b.addEventListener("click", e => { e.stopPropagation(); exportChat(b.dataset.export); }));
    document.addEventListener("click", closeExportMenu);
    document.addEventListener("keydown", e => { if (e.key === "Escape") closeExportMenu(); });
    new MutationObserver(updateExportBtn).observe($("#messages"), { childList: true });
    updateExportBtn();
  }

  function newChat() {
    // Bump the token so any in-flight loadSession()/sendQuestion() response
    // that resolves after this point is discarded instead of repopulating
    // the chat with the previous conversation.
    state.chatToken = (state.chatToken || 0) + 1;
    state.streamAbort?.abort();
    state.sessionId = null;
    kstore.del("kora_session_id");
    $("#messages").innerHTML = "";
    $("#chatHero").hidden = false;
    $("#chatTitle").textContent = "New conversation";
    $("#composerInput").value = "";
    $("#composerInput").style.height = "auto";
    $("#homeAskInput").value = "";
    $$(".chat-item.active").forEach(el => el.classList.remove("active"));
    showView("chat");
    $("#composerInput")?.focus();
  }

  function normalizeSources(data) {
    const sources = Array.isArray(data?.sources) ? data.sources : [];
    return sources.map((source, index) => ({
      record_id: source?.record_id || source?.id || `source-${index + 1}`,
      source: source?.source || source?.filename || source?.file || "Unknown source",
      path: source?.path || "",
      page: source?.page ?? null,
      sheet: source?.sheet ?? null,
      row: source?.row ?? null,
      slide: source?.slide ?? null,
      section: source?.section || "",
    }));
  }

  function sourceLocationLabel(source) {
    const parts = [];
    if (source.page != null) parts.push(`Page ${source.page}`);
    if (source.sheet) parts.push(`Sheet ${source.sheet}`);
    if (source.row != null) parts.push(`Row ${source.row}`);
    if (source.slide != null) parts.push(`Slide ${source.slide}`);
    if (source.section) parts.push(source.section);
    return parts.join(" · ");
  }

  function humanizeProgress(progress) {
    if (!progress?.active) return { title: "Ready", message: "KORA is ready.", pct: 100 };
    const raw = String(progress.message || "Working…");
    const match = raw.match(/(\d+)\s*\/\s*(\d+)/);
    const pct = match ? Math.min(100, Math.round((Number(match[1]) / Math.max(1, Number(match[2]))) * 100)) : null;
    if (progress.stage === "indexing") return { title: "Indexing your workspace", message: raw, pct };
    if (progress.stage === "tool") return { title: "Using a KORA tool", message: raw, pct: null };
    if (progress.stage === "thinking") return { title: "Thinking", message: raw, pct: null };
    return { title: "Working", message: raw, pct: null };
  }

  function setStatus(healthy, label) {
    const dot = $("#statusDot");
    const text = $("#statusLabel");
    const home = $("#homeStatus");
    const settingsDot = $("#settingsStatusDot");
    const settingsLabel = $("#settingsStatusLabel");
    [dot, settingsDot].forEach(el => {
      if (!el) return;
      el.classList.remove("online", "offline", "starting");
      el.classList.add(healthy ? "online" : "starting");
    });
    if (text) text.textContent = label;
    if (settingsLabel) settingsLabel.textContent = label;
    if (home) { home.classList.toggle("online", healthy); home.classList.toggle("starting", !healthy); }
    if (home) $("span", home).textContent = label;
  }

  function renderRich(text) {
    const value = String(text ?? "").replace(/\r\n?/g, "\n");
    const lines = value.split("\n");
    let html = "", listType = null;
    const closeList = () => { if (listType) html += `</${listType}>`; listType = null; };
    for (const raw of lines) {
      const bullet = /^\s*[-•]\s+/.test(raw);
      const numbered = /^\s*\d+[.)]\s+/.test(raw);
      if (bullet || numbered) {
        const target = bullet ? "ul" : "ol";
        if (listType !== target) { closeList(); html += `<${target}>`; listType = target; }
        let line = escapeHTML(raw.replace(/^\s*(?:[-•]|\d+[.)])\s+/, ""));
        line = line.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/`(.+?)`/g, "<code>$1</code>");
        html += `<li>${line}</li>`;
      } else {
        closeList();
        let line = escapeHTML(raw).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/`(.+?)`/g, "<code>$1</code>");
        html += `<p>${line || "&nbsp;"}</p>`;
      }
    }
    closeList();
    return html;
  }

  function cleanAnswerForUI(text) {
    let value = String(text ?? "").trim();
    if (!value) return "I couldn't produce an answer.";
    value = value.replace(/<\|[^>]+\|>/g, "");
    value = value.replace(/<tool_call>[\s\S]*?<\/tool_call>/gi, "");
    value = value.replace(/^```(?:json)?\s*([\s\S]*?)\s*```$/i, "$1").trim();
    if (value.includes("\\n") || value.includes("\\t")) value = value.replace(/\\n/g, "\n").replace(/\\t/g, "\t");
    value = value.replace(/\\"/g, '"');

    try {
      const parsed = JSON.parse(value);
      if (parsed && typeof parsed === "object" && parsed.result !== undefined) {
        return `The result is ${formatCalculationValue(parsed.result)}.`;
      }
    } catch (_) {}

    // Some local model builds have emitted pseudo-fields such as `time\\ 12:30`
    // in otherwise normal answers. Normalize those into readable text rather than
    // exposing the internal separator syntax.
    value = value.replace(/\\+(time|date_time|datetime|date|calculate|calculator|result)\b\s*/gi, "$1: ");
    value = value.replace(/\b(time|date_time|datetime|date|calculate|calculator|result)\\+\s*/gi, "$1: ");
    return value.trim();
  }

  function formatCalculationValue(value) {
    const n = Number(value);
    if (Number.isFinite(n)) {
      if (Number.isInteger(n)) return n.toLocaleString();
      return n.toLocaleString(undefined, { maximumFractionDigits: 10 });
    }
    return String(value ?? "");
  }

  function buildAnswerMetaHtml(meta) {
    if (!meta) return "";
    const sources = normalizeSources(meta);
    if (!sources.length) return "";
    let html = `<div class="answer-meta"><div class="answer-sources"><div class="sources-label">Sources</div><div class="source-chips">`;
    sources.slice(0, 8).forEach((src, i) => {
      const location = sourceLocationLabel(src);
      html += `<button class="source-chip" type="button" data-record-id="${escapeHTML(src.record_id)}"><span class="source-chip-icon">${i + 1}</span><span>${escapeHTML(src.source)}${location ? ` · ${escapeHTML(location)}` : ""}</span></button>`;
    });
    html += `</div></div></div>`;
    return html;
  }

  function addMessage(role, text, meta = null) {
    $("#chatHero").hidden = true;
    const wrap = document.createElement("div");
    wrap.className = `msg ${role}`;
    const safeText = role === "assistant" ? cleanAnswerForUI(text) : String(text ?? "");
    const metaHtml = role === "assistant" ? buildAnswerMetaHtml(meta) : "";
    const userMediaHtml = role === "user" ? `${meta?.voice ? `<div class="voice-tag">${icons.mic} Voice command</div>` : ""}${window.KoraMedia?.renderAttachments(meta?.attachments) || ""}` : "";
    wrap.innerHTML = `<div class="msg-avatar">${role === "user" ? icons.user : icons.bot}</div><div class="msg-col"><div class="msg-bubble">${userMediaHtml}<div class="msg-rich" ${role === "user" && !safeText ? "hidden" : ""}>${role === "assistant" ? renderRich(safeText) : escapeHTML(safeText)}</div></div>${metaHtml}</div>`;
    $("#messages").appendChild(wrap);
    window.KoraMedia?.bindMessage(wrap);
    wrap.querySelectorAll(".source-chip").forEach(btn => btn.addEventListener("click", () => openSourceContext(btn.dataset.recordId)));
    requestAnimationFrame(scrollChatBottom);
    return wrap;
  }

  // Creates an empty assistant bubble to grow live as stream deltas arrive.
  function startStreamingMessage() {
    $("#chatHero").hidden = true;
    const wrap = document.createElement("div");
    wrap.className = "msg assistant streaming";
    wrap.innerHTML = `<div class="msg-avatar">${icons.bot}</div><div class="msg-col"><div class="msg-bubble"><div class="msg-rich"></div></div></div>`;
    $("#messages").appendChild(wrap);
    requestAnimationFrame(scrollChatBottom);
    return wrap;
  }

  // Replaces the live (raw, unclean) streamed text with the final cleaned
  // answer + source chips once the stream's "final" event arrives.
  function finalizeStreamingMessage(wrap, text, meta) {
    wrap.classList.remove("streaming");
    const safeText = cleanAnswerForUI(text);
    const col = wrap.querySelector(".msg-col");
    wrap.querySelector(".msg-rich").innerHTML = renderRich(safeText);
    const metaHtml = buildAnswerMetaHtml(meta);
    if (metaHtml) {
      col.insertAdjacentHTML("beforeend", metaHtml);
      col.querySelectorAll(".source-chip").forEach(btn => btn.addEventListener("click", () => openSourceContext(btn.dataset.recordId)));
    }
    requestAnimationFrame(scrollChatBottom);
  }

  function addThinking() {
    $("#typingIndicator")?.remove();
    const wrap = document.createElement("div");
    wrap.className = "msg assistant";
    wrap.id = "typingIndicator";
    wrap.innerHTML = `<div class="msg-avatar">${icons.bot}</div><div class="msg-col"><div class="thinking-card"><span class="typing-dots"><i></i><i></i><i></i></span><div class="thinking-copy"><strong id="thinkingStage">Thinking</strong><span id="thinkingStatus">Preparing your answer…</span></div></div></div>`;
    $("#messages").appendChild(wrap);
    scrollChatBottom();
  }

  async function pollThinking() {
    const stage = $("#thinkingStage"), status = $("#thinkingStatus");
    if (!stage || !status) return;
    try {
      const data = await api("/api/activity", { timeout: 5000 });
      state.progress = data || state.progress;
      const view = humanizeProgress(state.progress);
      stage.textContent = view.title;
      status.textContent = view.message;
    } catch (_) {}
  }

  function scrollChatBottom() {
    const el = $("#chatScroll");
    if (el) el.scrollTop = el.scrollHeight;
  }

  function setSendingUI(sending) {
    const btn = $("#sendBtn");
    if (!btn) return;
    if (sending) {
      btn.dataset.prevHtml = btn.innerHTML;
      btn.innerHTML = "◼";
      btn.classList.add("stop-mode");
      btn.title = "Stop generating";
    } else {
      if (btn.dataset.prevHtml) btn.innerHTML = btn.dataset.prevHtml;
      btn.classList.remove("stop-mode");
      btn.title = "";
    }
  }

  // Reads a fetch() body as Server-Sent Events, calling onEvent(parsedJSON)
  // for each "data: {...}" line. SSE events can be split across chunk
  // boundaries, so partial lines are buffered until a full one arrives.
  async function readSSE(response, onEvent) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed.startsWith("data:")) continue;
        const raw = trimmed.slice(5).trim();
        if (!raw || raw === "[DONE]") continue;
        try { onEvent(JSON.parse(raw)); } catch (_) {}
      }
    }
  }

  async function sendQuestion(question, opts = {}) {
    const value = String(question || $("#composerInput")?.value || "").trim();
    if ((!value && !window.KoraMedia?.has()) || state.sending) return;
    if (window.KoraMedia?.busy()) { toast("Hang on — your attachment is still uploading."); return; }
    const attachments = window.KoraMedia?.take() || [];
    const voice = !!opts.voice;
    const myToken = (state.chatToken = (state.chatToken || 0) + 1);
    state.sending = true;
    $("#composerInput").value = "";
    $("#composerInput").style.height = "auto";
    $("#homeAskInput").value = "";
    showView("chat");
    addMessage("user", value, { attachments, voice });
    addThinking();
    setSendingUI(true);
    const timer = setInterval(pollThinking, 900);
    const controller = new AbortController();
    state.streamAbort = controller;
    $("#sendBtn").onclick = e => { if (state.sending) { e.preventDefault(); controller.abort(); } };

    let streamWrap = null;
    let answerText = "";
    let streamedOk = false;

    try {
      const resp = await fetch("/api/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: value, session_id: state.sessionId, attachments, voice }),
        signal: controller.signal,
      });
      if (!resp.ok || !resp.body) throw new Error(`Request failed (${resp.status})`);

      await readSSE(resp, event => {
        if (myToken !== state.chatToken) return;
        if (event.type === "delta") {
          if (!streamWrap) { $("#typingIndicator")?.remove(); streamWrap = startStreamingMessage(); }
          answerText += event.text;
          streamWrap.querySelector(".msg-rich").innerHTML = renderRich(answerText);
          scrollChatBottom();
        } else if (event.type === "final") {
          streamedOk = true;
          if (myToken !== state.chatToken) return;
          if (event.session_id) {
            state.sessionId = event.session_id;
            kstore.set("kora_session_id", state.sessionId);
          }
          $("#typingIndicator")?.remove();
          if (!streamWrap) streamWrap = startStreamingMessage();
          finalizeStreamingMessage(streamWrap, event.answer || answerText || "I couldn't produce an answer.", event);
        } else if (event.type === "error") {
          throw new Error(event.error || "Streaming failed");
        }
      });

      if (myToken !== state.chatToken) return;
      if (!streamedOk) throw new Error("Stream ended unexpectedly");
      await Promise.all([loadChatList(), loadLibrary(), loadHome()]);
    } catch (error) {
      if (myToken !== state.chatToken) return;
      if (error?.name === "AbortError") {
        // User hit stop mid-stream: keep whatever text arrived, just close it out.
        $("#typingIndicator")?.remove();
        if (streamWrap && answerText) finalizeStreamingMessage(streamWrap, answerText, null);
        else if (streamWrap) streamWrap.remove();
        return;
      }
      // Streaming endpoint unavailable/failed before we got anything useful --
      // fall back to the original non-streaming request rather than fail outright.
      if (streamWrap) streamWrap.remove();
      try {
        const data = await api("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question: value, session_id: state.sessionId, attachments, voice }),
          timeout: 310000,
        });
        if (myToken !== state.chatToken) return;
        if (data.session_id) {
          state.sessionId = data.session_id;
          kstore.set("kora_session_id", data.session_id);
        }
        $("#typingIndicator")?.remove();
        addMessage("assistant", data.answer || "I couldn't produce an answer.", data);
        await Promise.all([loadChatList(), loadLibrary(), loadHome()]);
      } catch (fallbackError) {
        if (myToken !== state.chatToken) return;
        $("#typingIndicator")?.remove();
        addMessage("error", fallbackError.message || "Something went wrong.");
        toast(fallbackError.message || "Chat failed", "error");
      }
    } finally {
      clearInterval(timer);
      state.sending = false;
      state.streamAbort = null;
      $("#sendBtn").onclick = null;
      setSendingUI(false);
      updateExportBtn();
      refreshIndexProgress();
    }
  }

  async function loadSession(sessionId) {
    const myToken = (state.chatToken = (state.chatToken || 0) + 1);
    state.streamAbort?.abort();
    try {
      const data = await api(`/api/library/${encodeURIComponent(sessionId)}`);
      if (myToken !== state.chatToken) return; // superseded by a newer newChat()/loadSession()
      state.sessionId = data.id || sessionId;
      kstore.set("kora_session_id", state.sessionId);
      $("#messages").innerHTML = "";
      $("#chatHero").hidden = true;
      $("#chatTitle").textContent = data.title || "Conversation";
      for (const message of (data.messages || [])) {
        if (!message || !message.role || message.role === "system" || message.role === "tool") continue;
        addMessage(message.role === "user" ? "user" : "assistant", message.content || "", message);
      }
      showView("chat");
      updateExportBtn();
      renderChatList(state.sessions);
    } catch (error) {
      toast(error.message || "Could not open conversation", "error");
    }
  }

  function sessionTitle(session) {
    return session?.title || (session?.messages || []).find(m => m.role === "user")?.content?.slice(0, 80) || "Conversation";
  }

  function renderChatList(sessions) {
    const list = $("#chatList");
    if (!list) return;
    const q = String($("#chatFilter")?.value || "").trim().toLowerCase();
    const rows = (sessions || []).filter(s => !q || sessionTitle(s).toLowerCase().includes(q));
    list.innerHTML = rows.map(s => `<div class="row-wrap"><button class="chat-item ${s.id === state.sessionId ? "active" : ""}" data-session="${escapeHTML(s.id)}"><strong>${escapeHTML(sessionTitle(s))}</strong><span>${formatCount((s.messages || []).filter(m => m.role === "user").length)} question${(s.messages || []).filter(m => m.role === "user").length === 1 ? "" : "s"}</span></button><button class="row-del" data-delete-session="${escapeHTML(s.id)}" title="Delete conversation" aria-label="Delete conversation">×</button></div>`).join("") || `<div class="empty-state compact">No conversations yet.</div>`;
    list.querySelectorAll("[data-session]").forEach(btn => btn.addEventListener("click", () => loadSession(btn.dataset.session)));
    list.querySelectorAll("[data-delete-session]").forEach(btn => btn.addEventListener("click", e => { e.stopPropagation(); deleteSession(btn.dataset.deleteSession); }));
  }

  async function loadChatList() {
    try {
      const data = await api("/api/library");
      state.sessions = Array.isArray(data.sessions) ? data.sessions : [];
      renderChatList(state.sessions);
    } catch (error) {
      $("#chatList").innerHTML = `<div class="empty-state">${escapeHTML(error.message)}</div>`;
    }
  }

  function renderRecentConversations() {
    const target = $("#recentConversations");
    if (!target) return;
    const sessions = state.sessions.slice(0, 4);
    target.innerHTML = sessions.map(s => {
      const title = sessionTitle(s);
      const questions = (s.messages || []).filter(m => m.role === "user").length;
      return `<button class="list-row" data-session="${escapeHTML(s.id)}"><span class="file-icon">◌</span><span class="row-copy"><strong>${escapeHTML(title)}</strong><span>${formatCount(questions)} question${questions === 1 ? "" : "s"}</span></span><span class="row-meta">${formatCount((s.messages || []).length)} msgs</span></button>`;
    }).join("") || `<div class="empty-state compact">No conversations yet.</div>`;
    target.querySelectorAll("[data-session]").forEach(btn => btn.addEventListener("click", () => loadSession(btn.dataset.session)));
  }

  async function loadHome() {
    try {
      const [overview, docsData, library, progress] = await Promise.all([
        api("/api/overview"), api("/api/documents"), api("/api/library"), api("/api/progress")
      ]);
      state.docs = Array.isArray(docsData.documents) ? docsData.documents : [];
      state.sessions = Array.isArray(library.sessions) ? library.sessions : [];
      state.progress = progress || state.progress;
      $("#metricDocuments").textContent = formatCount(overview.documents);
      $("#metricRecords").textContent = formatCount(overview.records);
      $("#metricConversations").textContent = formatCount(state.sessions.length);
      const indexStatusEl = $("#metricIndexStatus");
      if (indexStatusEl) {
        indexStatusEl.textContent = state.progress.active ? "Busy" : "Ready";
        indexStatusEl.classList.add("is-word");
        indexStatusEl.classList.toggle("busy", Boolean(state.progress.active));
      }
      renderRecentConversations();
      renderRecentDocuments();
      updateIndexStrip(state.progress);
    } catch (error) {
      toast(error.message || "Couldn't load the workspace", "error");
    }
  }

  function renderRecentDocuments() {
    const target = $("#recentDocuments");
    if (!target) return;
    const docs = state.docs.slice(0, 5);
    target.innerHTML = docs.map(doc => `<button class="list-row" data-doc-path="${escapeHTML(doc.path || doc.source || "")}"><span class="file-icon ${escapeHTML(extClass(docName(doc)))}">${escapeHTML(docType(doc))}</span><span class="row-copy"><strong>${escapeHTML(docName(doc))}</strong><span>${escapeHTML(doc.path || "")}</span></span><span class="row-meta">${formatCount(doc.record_count)} records</span></button>`).join("") || `<div class="empty-state compact">No indexed documents yet.</div>`;
    target.querySelectorAll("[data-doc-path]").forEach(btn => btn.addEventListener("click", () => openDocument(btn.dataset.docPath)));
  }

  const DOC_PAGE_SIZE = 100;

  function renderDocuments() {
    const list = $("#documentsList");
    if (!list) return;
    const q = String($("#documentSearch")?.value || "").trim().toLowerCase();
    const type = String($("#documentTypeFilter")?.value || "").toLowerCase();
    const sort = String($("#documentSort")?.value || "name");
    const docs = state.docs.filter(doc => {
      const name = docName(doc).toLowerCase();
      const path = String(doc.path || "").toLowerCase();
      const matchQ = !q || name.includes(q) || path.includes(q) || String(doc.document_id || "").toLowerCase().includes(q);
      const matchType = !type || String(doc.file_type || extClass(name)).toLowerCase() === type;
      return matchQ && matchType;
    }).sort((a,b) => {
      if (sort === "records") return Number(b.record_count || 0) - Number(a.record_count || 0);
      if (sort === "type") return docType(a).localeCompare(docType(b)) || docName(a).localeCompare(docName(b));
      return docName(a).localeCompare(docName(b));
    });

    // Reset pagination whenever the filtered set changes (new search/sort/type,
    // or the underlying doc count changed) rather than always rendering everything ---
    // with thousands of records that would otherwise mean thousands of DOM rows.
    const signature = `${q}|${type}|${sort}|${docs.length}`;
    if (state.docListSignature !== signature) {
      state.docListSignature = signature;
      state.docPageCount = 1;
    }
    const visibleCount = Math.min(docs.length, (state.docPageCount || 1) * DOC_PAGE_SIZE);
    const visible = docs.slice(0, visibleCount);

    list.innerHTML = visible.map(doc => {
      const name = docName(doc);
      const typeName = docType(doc);
      const checkKey = doc.path || doc.document_id || name;
      return `<div class="document-row"><label class="doc-check" title="Select"><input type="checkbox" data-doc-check="${escapeHTML(checkKey)}" aria-label="Select ${escapeHTML(name)}"><span></span></label><span class="file-icon ${escapeHTML(extClass(name))}">${escapeHTML(typeName)}</span><div class="doc-copy"><strong title="${escapeHTML(name)}">${escapeHTML(name)}</strong><span title="${escapeHTML(doc.path || "")}">${escapeHTML(doc.path || "")}</span></div><span class="doc-col doc-col-type">${escapeHTML(typeName)}</span><span class="doc-col doc-col-records">${formatCount(doc.record_count)} records</span><span class="doc-col doc-col-id" title="${escapeHTML(doc.document_id || "")}">${escapeHTML(doc.document_id || "—")}</span><button class="doc-menu" data-doc-menu="${escapeHTML(doc.path || name)}" aria-label="Open document">⋯</button></div>`;
    }).join("") || `<div class="empty-state">No documents match your search.</div>`;

    const headLabel = $("#docListHeadLabel");
    if (headLabel) headLabel.textContent = docs.length ? `${formatCount(docs.length)} document${docs.length === 1 ? "" : "s"}` : "No documents";

    if (docs.length) {
      const hint = document.createElement("div");
      hint.className = "list-count-hint";
      hint.textContent = `Showing ${formatCount(visible.length)} of ${formatCount(docs.length)} documents`;
      list.appendChild(hint);
    }
    if (visibleCount < docs.length) {
      const row = document.createElement("div");
      row.className = "load-more-row";
      row.innerHTML = `<button class="load-more-btn" type="button" id="docsLoadMoreBtn">Load ${formatCount(Math.min(DOC_PAGE_SIZE, docs.length - visibleCount))} more</button>`;
      list.appendChild(row);
      $("#docsLoadMoreBtn", row).addEventListener("click", () => { state.docPageCount = (state.docPageCount || 1) + 1; renderDocuments(); });
    }

    list.querySelectorAll("[data-doc-menu]").forEach(btn => btn.addEventListener("click", () => openDocument(btn.dataset.docMenu)));
    list.querySelectorAll("[data-doc-check]").forEach(cb => cb.addEventListener("change", updateBulkSelection));
    applyBulkSelection();
  }

  function populateDocTypeFilter() {
    const select = $("#documentTypeFilter");
    if (!select) return;
    const current = select.value;
    const types = [...new Set(state.docs.map(docType))].sort();
    select.innerHTML = `<option value="">All types</option>${types.map(t => `<option value="${escapeHTML(t.toLowerCase())}">${escapeHTML(t)}</option>`).join("")}`;
    select.value = current && types.includes(current.toUpperCase()) ? current : "";
  }

  async function loadDocuments() {
    try {
      const data = await api("/api/documents");
      state.docs = Array.isArray(data.documents) ? data.documents : [];
      populateDocTypeFilter();
      renderDocuments();
    } catch (error) {
      $("#documentsList").innerHTML = `<div class="empty-state">${escapeHTML(error.message)}</div>`;
    }
  }

  function updateBulkSelection() {
    const boxes = $$("[data-doc-check]");
    const selected = boxes.filter(el => el.checked).map(el => el.dataset.docCheck);
    const bar = $("#bulkBar");
    if (bar) {
      $("#bulkCount").textContent = `${selected.length} selected`;
      bar.hidden = selected.length === 0;
    }
    const headAll = $("#docSelectAllInput");
    if (headAll) {
      headAll.checked = boxes.length > 0 && selected.length === boxes.length;
      headAll.indeterminate = selected.length > 0 && selected.length < boxes.length;
    }
  }

  function setAllDocChecks(checked) {
    $$("[data-doc-check]").forEach(cb => { cb.checked = checked; });
    updateBulkSelection();
  }

  function applyBulkSelection() {
    const bar = $("#bulkBar");
    if (!bar) return;
    updateBulkSelection();
  }

  async function removeSelectedDocuments() {
    const names = $$("[data-doc-check]:checked").map(el => el.dataset.docCheck);
    if (!names.length) return;
    if (!(await kconfirm(`Remove ${names.length} document${names.length === 1 ? "" : "s"}?`, { title: "Remove documents", confirmText: "Remove" }))) return;
    let removed = 0;
    for (const name of names) {
      try { await api(`/api/sources/${encodeURIComponent(name)}`, { method: "DELETE" }); removed += 1; }
      catch (_) {}
    }
    toast(`${removed} document${removed === 1 ? "" : "s"} removed`, removed ? "success" : "error");
    await Promise.all([loadDocuments(), loadHome(), loadKnowledge()]);
  }

  async function openDocument(locator) {
    const modal = $("#sourceModal");
    if (!modal) return;
    modal.hidden = false;
    const visibleName = String(locator || "").split(/[\\/]/).pop() || "Document";
    $("#sourceModalTitle").textContent = visibleName;
    $("#sourceModalLocation").textContent = "Loading…";
    $("#sourceModalContent").textContent = "";
    $("#sourceModalDelete").hidden = false;
    $("#sourceModalDelete").dataset.docName = locator || "";
    try {
      const [doc, preview] = await Promise.all([
        api(`/api/documents/${encodeURIComponent(locator)}`),
        api(`/api/preview/${encodeURIComponent(docName({ source: visibleName }))}`).catch(() => null)
      ]);
      $("#sourceModalTitle").textContent = docName(doc);
      $("#sourceModalLocation").textContent = doc.path || doc.file_type || "Indexed document";
      $("#sourceModalContent").textContent = preview?.text || `Document ID: ${doc.document_id || "—"}\nRecords: ${doc.record_count ?? "—"}\nType: ${docType(doc)}\nHash: ${doc.file_hash || "—"}`;
    } catch (error) {
      $("#sourceModalLocation").textContent = "";
      $("#sourceModalContent").textContent = error.message || "Document unavailable.";
    }
  }

  async function openSourceContext(recordId) {
    if (!recordId) return;
    const modal = $("#sourceModal");
    modal.hidden = false;
    $("#sourceModalDelete").hidden = true;
    $("#sourceModalTitle").textContent = "Source";
    $("#sourceModalLocation").textContent = "Loading…";
    $("#sourceModalContent").textContent = "";
    try {
      const data = await api(`/api/source-context/${encodeURIComponent(recordId)}`);
      $("#sourceModalTitle").textContent = data.source || "Source";
      $("#sourceModalLocation").textContent = data.location || "Workspace source";
      $("#sourceModalContent").textContent = data.content || "No readable source content.";
    } catch (error) {
      $("#sourceModalLocation").textContent = "";
      $("#sourceModalContent").textContent = error.message || "Source unavailable.";
    }
  }

  function kconfirm(message, { title = "Are you sure?", confirmText = "Delete", danger = true } = {}) {
    return new Promise(resolve => {
      const backdrop = document.createElement("div");
      backdrop.className = "modal-backdrop kconfirm";
      backdrop.innerHTML = `<div class="modal confirm-modal" role="alertdialog" aria-modal="true"><div class="confirm-icon ${danger ? "danger" : ""}">${svg("M12 8v5M12 16.5h.01M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z")}</div><h3>${escapeHTML(title)}</h3><p>${escapeHTML(message)}</p><div class="confirm-actions"><button class="secondary-btn" data-confirm="cancel">Cancel</button><button class="${danger ? "danger-btn" : "primary-btn"}" data-confirm="ok">${escapeHTML(confirmText)}</button></div></div>`;
      const done = value => { document.removeEventListener("keydown", onKey, true); backdrop.classList.add("leaving"); setTimeout(() => backdrop.remove(), 220); resolve(value); };
      const onKey = event => { if (event.key === "Escape") { event.preventDefault(); done(false); } else if (event.key === "Enter") { event.preventDefault(); done(true); } };
      backdrop.addEventListener("click", event => { if (event.target === backdrop) return done(false); const button = event.target.closest("[data-confirm]"); if (button) done(button.dataset.confirm === "ok"); });
      document.addEventListener("keydown", onKey, true);
      document.body.appendChild(backdrop);
      $("[data-confirm='cancel']", backdrop)?.focus();
    });
  }

  async function deleteDocument(name) {
    if (!name) return;
    if (!(await kconfirm(`Remove ${String(name).split(/[\\/]/).pop()} from the KORA workspace?`, { title: "Remove document", confirmText: "Remove" }))) return;
    try {
      await api(`/api/sources/${encodeURIComponent(name)}`, { method: "DELETE" });
      $("#sourceModal").hidden = true;
      toast(`${name} removed`, "success");
      await Promise.all([loadDocuments(), loadHome(), loadKnowledge()]);
    } catch (error) {
      toast(error.message || "Could not remove document", "error");
    }
  }

  function renderIndexDetails(progress = state.progress) {
    const target = $("#indexDetails");
    if (!target) return;
    const view = humanizeProgress(progress);
    $("#indexBadge").textContent = progress.active ? "Working" : "Up to date";
    $("#indexBadge").classList.toggle("neutral", !progress.active);
    const overview = { documents: state.docs.length, records: state.docs.reduce((n,d) => n + Number(d.record_count || 0), 0) };
    target.innerHTML = `<div class="index-file"><span class="status-dot ${progress.active ? "starting" : "online"}"></span><strong>${escapeHTML(view.title)}</strong><span>${escapeHTML(view.message)}</span></div><div class="index-file"><span>▤</span><strong>Documents</strong><span>${formatCount(overview.documents)}</span></div><div class="index-file"><span>#</span><strong>Indexed records</strong><span>${formatCount(overview.records)}</span></div>`;
  }

  function updateIndexStrip(progress) {
    const strip = $("#indexStrip");
    if (!strip) return;
    const active = Boolean(progress?.active);
    strip.hidden = !active;
    if (!active) return;
    const view = humanizeProgress(progress);
    $("#indexStripTitle").textContent = view.title;
    $("#indexStripMessage").textContent = view.message;
    $("#indexStripBar").style.width = `${view.pct == null ? 38 : view.pct}%`;
  }

  async function refreshIndexProgress() {
    try {
      const data = await api("/api/progress", { timeout: 5000 });
      state.progress = data || state.progress;
      updateIndexStrip(state.progress);
      if (state.view === "knowledge") renderIndexDetails(state.progress);
      const indexMetric = $("#metricIndexStatus");
      if (indexMetric) indexMetric.textContent = state.progress.active ? "Busy" : "Ready";
    } catch (_) {}
  }

  function startIndexPolling() {
    refreshIndexProgress();
    state.indexTimer = setInterval(refreshIndexProgress, 1200);
  }

  async function loadKnowledge() {
    try {
      const [overview, docs, progress, health] = await Promise.all([
        api("/api/overview"), api("/api/documents"), api("/api/progress"), api("/api/health")
      ]);
      state.docs = Array.isArray(docs.documents) ? docs.documents : state.docs;
      state.progress = progress || state.progress;
      renderIndexDetails(state.progress);
      $("#knowledgeSummary").innerHTML = `<div class="hardware-info"><div><strong>${formatCount(overview.documents)}</strong><span>Documents</span></div><div><strong>${formatCount(overview.records)}</strong><span>Records</span></div><div><strong>${escapeHTML(health.ready ? "Ready" : "Starting")}</strong><span>Runtime</span></div></div>`;
      $("#knowledgeRuntime").innerHTML = `<div class="hardware-info"><div><strong>${escapeHTML(overview.model || health.model || "—")}</strong><span>Chat model</span></div><div><strong>${escapeHTML(overview.embedding_model || health.embedding_model || "—")}</strong><span>Embedding model</span></div><div><strong>${escapeHTML(state.progress.active ? "Indexing" : "Idle")}</strong><span>Index state</span></div></div>`;
    } catch (error) {
      $("#indexDetails").innerHTML = `<div class="empty-state compact">${escapeHTML(error.message)}</div>`;
    }
  }

  async function reindex() {
    try {
      const data = await api("/api/reindex", { method: "POST", timeout: 120000 });
      toast(data.status === "already_running" ? "Indexing is already running" : "Indexing started", "success");
      refreshIndexProgress();
    } catch (error) {
      toast(error.message || "Could not rebuild the index", "error");
    }
  }

  function renderLibrary() {
    const grid = $("#libraryGrid");
    if (!grid) return;
    const q = String($("#librarySearch")?.value || "").trim().toLowerCase();
    const rows = state.sessions.filter(s => !q || sessionTitle(s).toLowerCase().includes(q));
    grid.innerHTML = rows.map(s => `<div class="row-wrap lib"><button class="library-card panel-card" data-library-session="${escapeHTML(s.id)}"><strong>${escapeHTML(sessionTitle(s))}</strong><span>Local conversation</span><span class="count">${formatCount((s.messages || []).filter(m => m.role === "user").length)} questions · ${formatCount((s.messages || []).length)} messages</span></button><button class="row-del" data-delete-library="${escapeHTML(s.id)}" title="Delete conversation" aria-label="Delete conversation">×</button></div>`).join("") || `<div class="empty-state">No saved conversations yet.</div>`;
    grid.querySelectorAll("[data-library-session]").forEach(btn => btn.addEventListener("click", () => loadSession(btn.dataset.librarySession)));
    grid.querySelectorAll("[data-delete-library]").forEach(btn => btn.addEventListener("click", e => { e.stopPropagation(); deleteSession(btn.dataset.deleteLibrary); }));
  }

  async function loadLibrary() {
    try {
      const data = await api("/api/library");
      state.sessions = Array.isArray(data.sessions) ? data.sessions : [];
      renderLibrary();
    } catch (error) {
      $("#libraryGrid").innerHTML = `<div class="empty-state">${escapeHTML(error.message)}</div>`;
    }
  }

  async function deleteSession(id) {
    const session = state.sessions.find(s => s.id === id);
    if (!(await kconfirm(`Delete “${sessionTitle(session)}”?`, { title: "Delete conversation", confirmText: "Delete" }))) return;
    try {
      await api(`/api/library/${encodeURIComponent(id)}`, { method: "DELETE" });
      if (state.sessionId === id) newChat();
      await Promise.all([loadChatList(), loadLibrary(), loadHome()]);
      toast("Conversation deleted", "success");
    } catch (error) {
      toast(error.message || "Could not delete conversation", "error");
    }
  }

  function openUpload() {
    $("#uploadModal").hidden = false;
    $("#uploadQueue").innerHTML = "";
    $("#fileInput").value = "";
  }

  async function uploadFiles(files) {
    const queue = $("#uploadQueue");
    for (const file of files) {
      const row = document.createElement("div");
      row.className = "upload-item";
      row.innerHTML = `<span class="file-icon">↥</span><strong>${escapeHTML(file.name)}</strong><span class="upload-state">Uploading…</span>`;
      queue.appendChild(row);
      try {
        const form = new FormData();
        form.append("file", file);
        const result = await api("/api/sources", { method: "POST", body: form, timeout: 180000 });
        row.querySelector(".upload-state").textContent = result.status === "indexed" ? "Indexed" : "Saved";
        toast(`${file.name} indexed`, "success");
      } catch (error) {
        row.querySelector(".upload-state").textContent = "Failed";
        toast(`${file.name}: ${error.message}`, "error");
      }
    }
    await Promise.all([loadHome(), loadDocuments(), loadKnowledge()]);
  }

  async function loadSettings() {
    loadSystem();
    try {
      const [settings, overview, health] = await Promise.all([api("/api/settings"), api("/api/overview"), api("/api/health")]);
      state.settings = settings;
      $("#modelName").textContent = settings.model || overview.model || "—";
      $("#embeddingModelName").textContent = settings.embedding_model || overview.embedding_model || "—";
      $("#settingsRecordCount").textContent = `${formatCount(overview.records)} indexed records`;
      $("#workspacePath").textContent = "Local KORA workspace";
      $("#aiControlSubtitle").textContent = health.ready ? `Ready — ${settings.model || overview.model || "local model"}` : "Local model services are starting…";
      $("#aiStatusBadge").textContent = health.ready ? "Ready" : "Starting";
      $("#hardwareInfo").innerHTML = `<div><strong>${escapeHTML(health.chat_runtime ? "Online" : "Starting")}</strong><span>Chat runtime</span></div><div><strong>${escapeHTML(health.embedding_runtime ? "Online" : "Starting")}</strong><span>Embedding runtime</span></div><div><strong>${escapeHTML(overview.documents)}</strong><span>Indexed documents</span></div>`;
    } catch (error) {
      $("#aiControlSubtitle").textContent = error.message || "Could not read runtime status";
    }
  }

  async function loadSystem() {
    const specs = $("#sysSpecs");
    if (!specs) return;
    try {
      const d = await api("/api/system", { timeout: 15000 });
      const hw = d.hardware || {}, pr = d.profile || {}, run = d.running;
      const fmt = n => `${Number(n || 0).toFixed(Number(n) >= 10 ? 0 : 1)} GB`;
      const gpus = (hw.gpus || []);
      const gpuHtml = gpus.length
        ? gpus.map(g => `<div><strong>${escapeHTML(g.name)}</strong><span>${escapeHTML(g.integrated ? "Integrated" : "Dedicated")} · ${escapeHTML((g.vendor || "").toUpperCase())}${g.vram_gb ? " · " + fmt(g.vram_gb) + " VRAM" : ""}</span></div>`).join("")
        : `<div><strong>No graphics card detected</strong><span>Running on the CPU</span></div>`;
      specs.innerHTML = `<div><strong>${hw.cpu_cores || "?"} cores / ${hw.cpu_logical || "?"} threads</strong><span>Processor</span></div>`
        + `<div><strong>${fmt(hw.ram_total_gb)}</strong><span>Memory · ${fmt(hw.ram_avail_gb)} free at startup</span></div>` + gpuHtml;
      const modeName = { gpu: "GPU", hybrid: "GPU + CPU", cpu: "CPU" }[pr.mode] || pr.mode || "—";
      $("#sysTier").textContent = (run ? run.tier : pr.tier) || "—";
      const via = run ? run.backend : (pr.backends || [])[0];
      const ai = [
        [modeName, "Running on" + (pr.gpu_name && pr.mode !== "cpu" ? ` · ${pr.gpu_name}` : "")],
        [(run ? run.ctx : pr.chat_ctx).toLocaleString(), "Context size (tokens)"],
        [run ? run.threads : pr.chat_threads, "CPU threads for the AI"],
        [(run ? run.gpu_layers : pr.chat_gpu_layers) >= 999 ? "All" : (run ? run.gpu_layers : pr.chat_gpu_layers), "Model layers on GPU"],
        [(run ? run.vision_audio : pr.use_mmproj) ? "On" : "Off", "Image & voice input"],
        [via || "—", run ? "Runtime in use" : "Runtime (starting…)"],
      ];
      $("#sysAi").innerHTML = ai.map(([v, l]) => `<div><strong>${escapeHTML(v)}</strong><span>${escapeHTML(l)}</span></div>`).join("");
      const warn = $("#sysWarnings");
      warn.hidden = !(pr.warnings || []).length;
      warn.innerHTML = (pr.warnings || []).map(w => `<div class="access-note">${escapeHTML(w)}</div>`).join("");
      $("#sysProfile").value = d.prefs?.profile || "auto";
      $("#sysForceGpu").checked = !!d.prefs?.force_gpu;
      const env = d.env_overrides || [];
      $("#sysEnvNote").hidden = !env.length;
      $("#sysEnvList").textContent = env.join(", ") + " — these environment variables take priority over the options below.";
    } catch (error) {
      specs.innerHTML = `<div><strong>Unavailable</strong><span>${escapeHTML(error.message || "Could not read system info")}</span></div>`;
    }
  }

  async function saveSystemPrefs() {
    try {
      await api("/api/system/performance", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ profile: $("#sysProfile").value, force_gpu: $("#sysForceGpu").checked }) });
      $("#sysNote").textContent = "Saved. Close and reopen KORA to apply the new performance settings.";
      toast("Performance saved — restart KORA to apply", "success");
    } catch (error) { toast(error.message || "Could not save", "error"); }
  }

  async function openLogs() {
    try { await api("/api/open-logs", { method: "POST" }); toast("Opened logs folder", "success"); }
    catch (error) { toast(error.message || "Could not open logs", "error"); }
  }

  async function checkHealth() {
    try {
      const data = await api("/api/health", { timeout: 5000 });
      state.health = data;
      setStatus(Boolean(data.ready), data.ready ? "KORA ready" : "Starting KORA…");
      const homeStatus = $("#homeStatus");
      if (homeStatus) $("span", homeStatus).textContent = data.ready ? "KORA ready" : "Starting KORA…";
    } catch (_) {
      state.health = null;
      setStatus(false, "KORA unavailable");
    }
  }

  function openCommandPalette() {
    const modal = $("#commandPalette");
    modal.hidden = false;
    const input = $("#paletteInput");
    input.value = "";
    renderPalette("");
    requestAnimationFrame(() => input.focus());
  }

  function renderPalette(query) {
    const q = String(query || "").toLowerCase();
    const results = [
      ["New Chat", "Start a fresh conversation", "new"],
      ["Home", "Overview and workspace status", "home"],
      ["Chat", "Ask KORA anything", "chat"],
      ["Documents", "Browse and manage indexed files", "documents"],
      ["Knowledge Base", "Check the index and rebuild it", "knowledge"],
      ["Library", "Open previous conversations", "library"],
      ["Settings", "Runtime and appearance", "settings"],
      ["Upload documents", "Add files to the workspace", "upload"],
    ].filter(x => !q || x[0].toLowerCase().includes(q) || x[1].toLowerCase().includes(q));
    $("#paletteResults").innerHTML = results.map(r => `<button class="palette-result" data-action="${r[2]}"><strong>${escapeHTML(r[0])}</strong><span>${escapeHTML(r[1])}</span></button>`).join("") || `<div class="empty-state compact">No commands found.</div>`;
    $("#paletteResults").querySelectorAll("[data-action]").forEach(btn => btn.addEventListener("click", () => { const action = btn.dataset.action; $("#commandPalette").hidden = true; if (action === "new") newChat(); else if (action === "upload") openUpload(); else showView(action); }));
  }

  async function openWorkspace() {
    try { await api("/api/open-workspace", { method: "POST" }); toast("Opened workspace folder", "success"); }
    catch (error) { toast(error.message || "Could not open workspace", "error"); }
  }

  function bindAppearance() {
    const AP = window.koraAppearance;
    let ap = AP.load();
    let fadeT;
    const syncAP = () => {
      $$('[data-theme-pick]').forEach(b => b.classList.toggle("active", b.dataset.themePick === ap.theme));
      $$('[data-accent]').forEach(b => b.classList.toggle("active", b.dataset.accent === ap.accent));
      const custom = $(".swatch.custom");
      custom?.classList.toggle("active", ap.accent === "custom");
      if (custom) custom.style.setProperty("--sw", ap.custom);
      $("#customAccent").value = ap.custom;
      ["glass", "navglass", "glow", "motion"].forEach(k => { const el = $("#" + k + "Toggle"); if (el) el.checked = k === "navglass" ? ap[k] !== false : Boolean(ap[k]); });
      [["blur", "px"], ["alpha", "%"], ["size", "%"], ["round", "%"]].forEach(([k,u]) => { const el = $("#" + k + "Range"); const out = $("#" + k + "Val"); if (el) el.value = ap[k]; if (out) out.textContent = ap[k] + u; });
      [["font", "font"], ["bg", "bg"], ["bubble", "bubble"], ["width", "width"], ["density", "density"]].forEach(([id,k]) => { const el = $("#" + id + "Select"); if (el) el.value = ap[k]; });
      const on = ap.glass || ap.navglass !== false;
      $("#blurCard")?.classList.toggle("off", !on);
      $("#alphaCard")?.classList.toggle("off", !on);
    };
    const setAP = patch => {
      if ("theme" in patch || "accent" in patch) {
        document.documentElement.classList.add("theme-fade");
        clearTimeout(fadeT);
        fadeT = setTimeout(() => document.documentElement.classList.remove("theme-fade"), 500);
      }
      ap = { ...ap, ...patch };
      AP.save(ap);
      AP.apply(ap);
      syncAP();
    };
    $$('[data-theme-pick]').forEach(b => b.addEventListener("click", () => setAP({ theme: b.dataset.themePick })));
    $$('[data-accent]').forEach(b => b.addEventListener("click", () => setAP({ accent: b.dataset.accent })));
    $("#customAccent")?.addEventListener("input", e => setAP({ accent: "custom", custom: e.target.value }));
    $("#glassToggle")?.addEventListener("change", e => setAP(e.target.checked ? { glass: true, glow: true } : { glass: false }));
    $("#navglassToggle")?.addEventListener("change", e => setAP({ navglass: e.target.checked }));
    $("#glowToggle")?.addEventListener("change", e => setAP({ glow: e.target.checked }));
    $("#motionToggle")?.addEventListener("change", e => setAP({ motion: e.target.checked }));
    [["#blurRange","blur"],["#alphaRange","alpha"],["#sizeRange","size"],["#roundRange","round"]].forEach(([id,k]) => $(id)?.addEventListener("input", e => setAP({ [k]: Number(e.target.value) })));
    [["#fontSelect","font"],["#bgSelect","bg"],["#bubbleSelect","bubble"],["#widthSelect","width"],["#densitySelect","density"]].forEach(([id,k]) => $(id)?.addEventListener("change", e => setAP({ [k]: e.target.value })));
    $("#surpriseBtn")?.addEventListener("click", () => {
      const pick = arr => arr[Math.floor(Math.random() * arr.length)];
      setAP({ theme: pick($$('[data-theme-pick]').map(b => b.dataset.themePick).filter(x => x !== "system")), accent: pick(["auto","auto","violet","blue","teal","green","amber","rose"]), bg: pick(["plain","grid","dots","mesh"]), glow: Math.random() > .5, round: pick([60,100,140,160]) });
      toast("Fresh look applied", "success");
    });
    $("#resetAppearance")?.addEventListener("click", () => { setAP({ ...AP.def }); toast("Appearance reset", "success"); });
    syncAP();
  }

  // Icons / sidebar polish preserved from the previous visual system.
  const P = {
    "✦":"M12 3l2.2 6.8L21 12l-6.8 2.2L12 21l-2.2-6.8L3 12l6.8-2.2L12 3",
    "＋":"M12 5v14M5 12h14",
    "⌂":"M3 11l9-8 9 8M5 9.5V20h5v-6h4v6h5V9.5",
    "◌":"M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z",
    "▤":"M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5M9 13h6M9 17h6",
    "◇":"M12 3l9 9-9 9-9-9z",
    "▥":"M4 5h4v14H4zM10 5h4v14h-4zM16 6.5l4 1-3 11.5-4-1",
    "⚙":"M4 7h9M17 7h3M4 17h3M11 17h9M15 5v4M9 15v4",
    "⌕":"M11 4a7 7 0 1 0 0 14 7 7 0 0 0-0-14zM20 20l-4-4",
    "➤":"M5 12h14M13 6l6 6-6 6",
    "↥":"M12 16V4M6 10l6-6 6 6M5 20h14",
    "⌄":"M6 9l6 6 6-6",
    "→":"M5 12h14M13 6l6 6-6 6",
    "×":"M6 6l12 12M18 6L6 18",
    "▦":"M4 4h16v16H4zM4 10h16M4 15h16M10 4v16",
    "⋯":"M5 12h.01M12 12h.01M19 12h.01",
    "↻":"M20 12a8 8 0 1 1-2.5-5.8M20 4v5h-5"
  };
  const svg = d => `<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="${d}"/></svg>`;

  function bindInterfacePolish() {
    $$(".nav-icon,.icon,.search-icon,.send-btn,.attach-btn,.icon-btn,.metric-icon,.metric-arrow,.ac-icon,.ac-arrow,.chevron,.drop-icon,.inline-search-wrap>span,.library-search>span,.palette-input>span,.primary-btn>span").forEach(el => { const g = el.textContent.trim(); if (P[g]) el.innerHTML = svg(P[g]); });
    $("#homeAttachBtn") && ($("#homeAttachBtn").innerHTML = svg("M21 12l-8.5 8.5a5 5 0 0 1-7-7L14 5a3.5 3.5 0 0 1 5 5l-8.5 8.5a2 2 0 0 1-3-3L15 8"));
    const iconize = root => {
      if (!root?.querySelectorAll) return;
      const nodes = [...root.querySelectorAll(".file-icon,.doc-menu,.metric-arrow,.primary-btn>span")];
      if (root.matches?.(".file-icon,.doc-menu")) nodes.push(root);
      nodes.forEach(el => { if (el.children.length) return; const glyph = el.textContent.trim(); if (P[glyph]) el.innerHTML = svg(P[glyph]); });
    };
    iconize(document.body);
    new MutationObserver(records => records.forEach(record => record.addedNodes.forEach(node => { if (node.nodeType === 1) iconize(node); }))).observe(document.body, { childList: true, subtree: true });
    const sb = $("#sidebar"), menuBtn = $("#menuBtn");
    if (sb) {
      const collapseSidebarAfterNav = () => { sb.classList.remove("open"); sb.dataset.clickCollapsed = "true"; menuBtn?.classList.remove("on"); };
      const clearClickCollapse = () => { if (sb.dataset.clickCollapsed === "true") delete sb.dataset.clickCollapsed; };
      $$(".nav-item[data-view]").forEach(btn => btn.addEventListener("click", collapseSidebarAfterNav));
      sb.addEventListener("mouseenter", clearClickCollapse);
      menuBtn?.addEventListener("click", () => { sb.classList.toggle("open"); menuBtn.classList.toggle("on", sb.classList.contains("open")); });
      document.addEventListener("click", e => { if (window.innerWidth <= 980 && !sb.contains(e.target) && e.target !== menuBtn) { sb.classList.remove("open"); menuBtn?.classList.remove("on"); } });
    }
  }

  function bindCustomDropdowns() {
    const selectProto = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value");
    const arrow = svg("M6 9l6 6 6-6");
    const check = svg("M5 12.5l4.5 4.5L19 7.5");
    const closeMenus = except => $$(".dd.open").forEach(dd => { if (dd !== except) { dd.classList.remove("open"); $(".dd-menu", dd).hidden = true; $(".dd-btn", dd).setAttribute("aria-expanded", "false"); } });
    $$("select:not(.dd-native)").forEach(select => {
      const wrap = document.createElement("div");
      wrap.className = "dd";
      select.parentNode.insertBefore(wrap, select);
      wrap.appendChild(select);
      select.classList.add("dd-native");
      select.tabIndex = -1;
      select.setAttribute("aria-hidden", "true");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "dd-btn";
      button.setAttribute("aria-haspopup", "listbox");
      button.setAttribute("aria-expanded", "false");
      const menu = document.createElement("div");
      menu.className = "dd-menu";
      menu.setAttribute("role", "listbox");
      menu.hidden = true;
      wrap.append(button, menu);
      const sync = () => {
        const current = select.options[select.selectedIndex];
        button.innerHTML = `<span class="dd-label${current ? "" : " dd-empty"}">${current ? escapeHTML(current.textContent) : "Not available"}</span>${arrow}`;
        menu.innerHTML = [...select.options].map((option, index) => `<button type="button" role="option" class="dd-opt${index === select.selectedIndex ? " sel" : ""}" data-i="${index}" aria-selected="${index === select.selectedIndex}"><span>${escapeHTML(option.textContent)}</span>${index === select.selectedIndex ? check : ""}</button>`).join("");
      };
      Object.defineProperty(select, "value", { configurable: true, get() { return selectProto.get.call(this); }, set(value) { selectProto.set.call(this, value); sync(); } });
      new MutationObserver(sync).observe(select, { childList: true, subtree: true });
      sync();
      const open = () => { closeMenus(wrap); wrap.classList.add("open"); menu.hidden = false; button.setAttribute("aria-expanded", "true"); wrap.classList.toggle("up", button.getBoundingClientRect().bottom + menu.offsetHeight + 16 > innerHeight); $(".dd-opt.sel", menu)?.focus(); };
      const close = focus => { wrap.classList.remove("open"); menu.hidden = true; button.setAttribute("aria-expanded", "false"); if (focus) button.focus(); };
      button.addEventListener("click", e => { e.stopPropagation(); wrap.classList.contains("open") ? close() : open(); });
      button.addEventListener("keydown", e => { if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); open(); } });
      menu.addEventListener("click", e => { const option = e.target.closest(".dd-opt"); if (!option) return; e.stopPropagation(); select.selectedIndex = Number(option.dataset.i); sync(); select.dispatchEvent(new Event("change", { bubbles: true })); select.dispatchEvent(new Event("input", { bubbles: true })); close(true); });
      menu.addEventListener("keydown", e => { const options = $$(".dd-opt", menu), index = options.indexOf(document.activeElement); if (e.key === "ArrowDown") { e.preventDefault(); options[Math.min(index + 1, options.length - 1)]?.focus(); } else if (e.key === "ArrowUp") { e.preventDefault(); options[Math.max(index - 1, 0)]?.focus(); } else if (e.key === "Escape" || e.key === "Tab") { e.stopPropagation(); close(e.key === "Escape"); } });
    });
    document.addEventListener("click", () => closeMenus());
  }

  function bindGlobalInteractions() {
    bindExport();
    $$(".nav-item[data-view]").forEach(btn => btn.addEventListener("click", () => showView(btn.dataset.view)));
    $$('[data-view-target]').forEach(btn => btn.addEventListener("click", () => showView(btn.dataset.viewTarget)));
    $("#newChatBtn")?.addEventListener("click", newChat);
    $("#libraryNewChatBtn")?.addEventListener("click", newChat);
    $("#chatNewBtn")?.addEventListener("click", newChat);
    $("#homeSendBtn")?.addEventListener("click", () => sendQuestion($("#homeAskInput").value));
    $("#homeAskInput")?.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); sendQuestion(e.currentTarget.value); } });
    $("#composerForm")?.addEventListener("submit", e => { e.preventDefault(); sendQuestion($("#composerInput").value); });
    $("#composerInput")?.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); sendQuestion(e.currentTarget.value); } });
    $("#composerInput")?.addEventListener("input", e => { e.target.style.height = "auto"; e.target.style.height = Math.min(e.target.scrollHeight, 160) + "px"; });
    $$(".prompt-chip,.prompt-card").forEach(btn => btn.addEventListener("click", () => sendQuestion(btn.dataset.prompt)));
    $("#homeUploadBtn")?.addEventListener("click", openUpload);
    $("#documentsUploadBtn")?.addEventListener("click", openUpload);
    $("#homeAttachBtn")?.addEventListener("click", openUpload);
    $("#uploadClose")?.addEventListener("click", () => { $("#uploadModal").hidden = true; });
    $("#sourceModalClose")?.addEventListener("click", () => { $("#sourceModal").hidden = true; });
    $("#sourceModal")?.addEventListener("click", e => { if (e.target.id === "sourceModal") e.currentTarget.hidden = true; });
    $("#uploadModal")?.addEventListener("click", e => { if (e.target.id === "uploadModal") e.currentTarget.hidden = true; });
    $("#sourceModalDelete")?.addEventListener("click", e => deleteDocument(e.currentTarget.dataset.docName));
    $("#browseFilesBtn")?.addEventListener("click", () => $("#fileInput").click());
    $("#fileInput")?.addEventListener("change", e => uploadFiles([...e.target.files]));
    const drop = $("#dropZone");
    if (drop) {
      ["dragenter","dragover"].forEach(type => drop.addEventListener(type, e => { e.preventDefault(); drop.classList.add("drag"); }));
      ["dragleave","drop"].forEach(type => drop.addEventListener(type, e => { e.preventDefault(); drop.classList.remove("drag"); }));
      drop.addEventListener("drop", e => uploadFiles([...e.dataTransfer.files]));
    }
    $("#documentSearch")?.addEventListener("input", renderDocuments);
    $("#documentTypeFilter")?.addEventListener("change", renderDocuments);
    $("#documentSort")?.addEventListener("change", renderDocuments);
    $("#chatFilter")?.addEventListener("input", () => renderChatList(state.sessions));
    $("#librarySearch")?.addEventListener("input", renderLibrary);
    $("#rebuildBtn")?.addEventListener("click", reindex);
    $("#rebuildBtn2")?.addEventListener("click", reindex);
    $("#openWorkspaceBtn")?.addEventListener("click", openWorkspace);
    $("#openWorkspaceSettingsBtn")?.addEventListener("click", openWorkspace);
    $("#sysSaveBtn")?.addEventListener("click", saveSystemPrefs);
    $("#sysRefreshBtn")?.addEventListener("click", loadSystem);
    $("#sysLogsBtn")?.addEventListener("click", openLogs);
    $("#saveProfileBtn")?.addEventListener("click", () => { setProfileName($("#profileNameInput").value); toast("Name saved", "success"); });
    $("#profileButton")?.addEventListener("click", () => showView("settings"));
    $("#sidebarProfileButton")?.addEventListener("click", () => showView("settings"));
    $("#chatSearchBtn")?.addEventListener("click", () => $("#chatFilter")?.focus());
    $("#bulkRemoveBtn")?.addEventListener("click", removeSelectedDocuments);
    $("#bulkSelectAllBtn")?.addEventListener("click", () => setAllDocChecks(true));
    $("#bulkCancelBtn")?.addEventListener("click", () => setAllDocChecks(false));
    $("#docSelectAllInput")?.addEventListener("change", e => setAllDocChecks(e.currentTarget.checked));
    $$(".settings-tab").forEach(btn => btn.addEventListener("click", () => { const name = btn.dataset.settings; $$(".settings-tab").forEach(x => x.classList.toggle("active", x === btn)); $$(".settings-section").forEach(x => x.classList.toggle("active", x.dataset.settingsPanel === name)); }));
    document.addEventListener("keydown", e => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openCommandPalette(); }
      else if (e.key === "/" && !e.ctrlKey && !e.metaKey && !["INPUT","TEXTAREA","SELECT"].includes(document.activeElement?.tagName)) { e.preventDefault(); $("#composerInput")?.focus(); }
      else if (e.key === "Escape") { $("#commandPalette").hidden = true; $("#sourceModal").hidden = true; $("#uploadModal").hidden = true; }
    });
    $("#commandPalette")?.addEventListener("click", e => { if (e.target.id === "commandPalette") e.currentTarget.hidden = true; });
    $("#paletteInput")?.addEventListener("input", e => renderPalette(e.target.value));
  }

  function bindJumpToLatest() {
    const chat = $("#chatScroll");
    if (!chat) return;
    const button = document.createElement("button");
    button.className = "jump-btn";
    button.type = "button";
    button.hidden = true;
    button.innerHTML = `${svg("M12 5v14M6 13l6 6 6-6")}<span>Latest</span>`;
    chat.parentElement?.appendChild(button);
    const sync = () => { button.hidden = chat.scrollHeight - chat.scrollTop - chat.clientHeight < 160; };
    chat.addEventListener("scroll", sync, { passive: true });
    button.addEventListener("click", () => chat.scrollTo({ top: chat.scrollHeight, behavior: "smooth" }));
    sync();
  }

  function bindDocumentExtras() {
    const dv = $("#view-documents");
    if (!dv) return;
    const overlay = document.createElement("div");
    overlay.className = "drop-overlay";
    overlay.hidden = true;
    overlay.innerHTML = `<div>${svg("M12 16V4M6 10l6-6 6 6M5 20h14")}<strong>Drop files to upload</strong><span>They'll be indexed by KORA</span></div>`;
    document.body.appendChild(overlay);
    ["dragenter","dragover"].forEach(ev => dv.addEventListener(ev, e => { if (![...(e.dataTransfer?.types || [])].includes("Files")) return; e.preventDefault(); overlay.hidden = false; }));
    overlay.addEventListener("dragleave", () => { overlay.hidden = true; });
    overlay.addEventListener("dragover", e => e.preventDefault());
    overlay.addEventListener("drop", e => { e.preventDefault(); overlay.hidden = true; if (e.dataTransfer?.files?.length) { openUpload(); uploadFiles([...e.dataTransfer.files]); } });
    const tg = document.createElement("div");
    tg.className = "view-toggle";
    tg.innerHTML = `<button type="button" data-l="list" aria-label="List view" title="List">${svg("M4 6h16M4 12h16M4 18h16")}</button><button type="button" data-l="grid" aria-label="Grid view" title="Grid">${svg("M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z")}</button>`;
    $(".toolbar", dv)?.appendChild(tg);
    const setLayout = layout => { $("#documentsList")?.classList.toggle("as-grid", layout === "grid"); $$('button', tg).forEach(b => b.classList.toggle("active", b.dataset.l === layout)); kstore.set("kora_doc_layout", layout); };
    tg.addEventListener("click", e => { const b = e.target.closest("[data-l]"); if (b) setLayout(b.dataset.l); });
    setLayout(kstore.get("kora_doc_layout") || "list");
  }

  function bindAppearanceRangeFeedback() {
    const inputProto = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value");
    $$('input[type="range"]').forEach(r => {
      const update = () => { const pct = ((Number(r.value) - Number(r.min)) / ((Number(r.max) - Number(r.min)) || 1)) * 100; r.style.setProperty("--pct", pct + "%"); };
      Object.defineProperty(r, "value", { configurable: true, get() { return inputProto.get.call(this); }, set(v) { inputProto.set.call(this, v); update(); } });
      r.addEventListener("input", update);
      update();
    });
  }

  function bindWindowControls() {
    const api = () => window.pywebview && window.pywebview.api;
    const enable = () => {
      document.documentElement.classList.add("in-app-window");
      $("#winMinBtn")?.addEventListener("click", () => api()?.minimize());
      $("#winMaxBtn")?.addEventListener("click", () => api()?.toggle_maximize());
      $("#winCloseBtn")?.addEventListener("click", () => api()?.close());
    };
    // pywebview injects window.pywebview asynchronously and fires this event
    // once it's ready; it may also already be present if this script runs late.
    if (window.pywebview) enable();
    else window.addEventListener("pywebviewready", enable, { once: true });
  }

  function bindPointerGlow() {
    const targets = ".ask-card,.action-card,.stat-strip,.nav-item,.new-chat,.panel-card,.setting-card,.hardware-card,.document-row,.list-row,.chat-item,.library-card,.settings-tab,.theme-card,.swatch,.primary-btn,.secondary-btn,.ghost-btn,.icon-btn,.profile-chip,.prompt-chip,.prompt-card,.stat,.index-strip,.composer,.chat-main";
    const update = e => { const el = e.currentTarget, r = el.getBoundingClientRect(); el.style.setProperty("--mx", (e.clientX - r.left) + "px"); el.style.setProperty("--my", (e.clientY - r.top) + "px"); };
    $$(targets).forEach(el => { el.classList.add("pointer-glow"); el.addEventListener("pointermove", update); });
  }

  // Ambient cursor glow: follows the pointer anywhere in the UI. Controlled by
  // the "Background glow" toggle in Settings > Appearance (html.glow class);
  // this keeps --cursor-x/--cursor-y in sync with the real pointer so the
  // glow (html.glow .app-shell:before in style.css) tracks the mouse instead
  // of sitting frozen in the middle of the screen.
  function bindCursorGlow() {
    const root = document.documentElement;
    let raf = null, lastX = window.innerWidth / 2, lastY = window.innerHeight / 2;
    const apply = () => { raf = null; root.style.setProperty("--cursor-x", lastX + "px"); root.style.setProperty("--cursor-y", lastY + "px"); };
    const onMove = e => { lastX = e.clientX; lastY = e.clientY; if (raf == null) raf = requestAnimationFrame(apply); };
    window.addEventListener("pointermove", onMove, { passive: true });
    window.addEventListener("pointerdown", onMove, { passive: true });
  }

  async function boot() {
    setProfileName(profileName());
    $("#profileNameInput").value = profileName();
    bindGlobalInteractions();
    bindInterfacePolish();
    bindCustomDropdowns();
    bindDocumentExtras();
    bindJumpToLatest();
    bindAppearanceRangeFeedback();
    bindAppearance();
    bindPointerGlow();
    bindCursorGlow();
    bindWindowControls();
    const bootScreen = $("#bootScreen");
    const bootBar = $("#bootBar"), bootPct = $("#bootPct"), bootMsg = $("#bootMsg"), bootStep = $("#bootStep"), bootSkip = $("#bootSkip");
    let finished = false;
    const finishBoot = () => {
      if (finished) return;
      finished = true;
      if (bootBar) bootBar.style.width = "100%";
      if (bootPct) bootPct.textContent = "100%";
      if (bootMsg) bootMsg.textContent = "KORA is ready.";
      if (bootStep) bootStep.textContent = "Ready";
      setTimeout(() => { bootScreen?.classList.add("leaving"); setTimeout(() => bootScreen?.remove(), 700); }, 350);
    };
    bootSkip?.addEventListener("click", finishBoot);

    const pollBoot = async () => {
      try {
        const h = await api("/api/health", { timeout: 4000 });
        const b = h.boot || {};
        const pct = Math.max(0, Math.min(100, Number(b.percent || 0)));
        if (bootBar) bootBar.style.width = `${pct}%`;
        if (bootPct) bootPct.textContent = `${Math.round(pct)}%`;
        if (bootMsg && b.message) bootMsg.textContent = b.message;
        if (bootStep) bootStep.textContent = b.label || "Starting";
        if (b.step === "ready" || h.ready || pct >= 100) { finishBoot(); return; }
      } catch (_) {
        if (bootMsg) bootMsg.textContent = "Waiting for the KORA server…";
        if (bootSkip) bootSkip.hidden = false;
      }
      if (!finished) setTimeout(pollBoot, 700);
    };
    if (bootScreen) pollBoot();

    await Promise.allSettled([checkHealth(), loadHome(), loadChatList(), loadDocuments(), loadSettings()]);
    await refreshIndexProgress();
    startIndexPolling();
    state.healthTimer = setInterval(checkHealth, 5000);
    $("#composerInput")?.focus();
  }

  boot();
  window.KoraApp = { sendQuestion, toast };
})();
