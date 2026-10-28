const chat = document.getElementById("chat");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const sendBtn = document.getElementById("send-btn");
const attachBtn = document.getElementById("attach-btn");

const history = []; // chat context - follow-ups backend ko bhejne ke liye

const fileInput = document.getElementById("file-input");
const fileLabelText = document.getElementById("file-label-text");
const fileClear = document.getElementById("file-clear");
const uploadBtn = document.getElementById("upload-btn");
const uploadStatus = document.getElementById("upload-status");
const dropzone = document.getElementById("dropzone");

const side = document.getElementById("side");
const filesToggle = document.getElementById("files-toggle");
const sideClose = document.getElementById("side-close");
const fileList = document.getElementById("file-list");
const fileEmpty = document.getElementById("file-empty");
const fileCount = document.getElementById("file-count");
const filesTopCount = document.getElementById("files-top-count");
const sheetSub = document.getElementById("sheet-sub");
const clearChatBtn = document.getElementById("clear-chat");
const viewer = document.getElementById("viewer");
const viewerTitle = document.getElementById("viewer-title");
const viewerMeta = document.getElementById("viewer-meta");
const viewerBody = document.getElementById("viewer-body");
const emptyStateTemplate = chat.querySelector(".empty-state").cloneNode(true);

// Session ab server ka signed HttpOnly cookie (kb_sid) hai - same-origin fetch use khud bhejta hai.
// Refresh par wahi session milta hai, isliye uploaded files wapas aati hain (restoreFiles).

// Backend ka "answer nahi mila" jumla (main.py prompt) - isse yellow callout dikhta hai
const NOT_FOUND_TEXT = "I don't know from uploaded PDF.";
const NOT_FOUND_COPY =
  "I could not find that in your documents. Try adding another file, or ask about something the files cover.";

const SVG = {
  info:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 8v4.5M12 16h.01"/></svg>',
  doc:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/></svg>',
};

// ---------- messages ----------

function isNotFound(text) {
  return text.trim().replace(/[.\s]+$/, "").toLowerCase() === NOT_FOUND_TEXT.slice(0, -1).toLowerCase();
}

// Streaming ke dauran jawab "I don't know..." ka shuruaati hissa ho to card hold karte hain,
// taaki callout ki jagah pehle ek jhalak card na dikhe.
function mightBeNotFound(text) {
  return NOT_FOUND_TEXT.toLowerCase().startsWith(text.trim().toLowerCase());
}

// Bot answers are Markdown. marked does not sanitize, so DOMPurify (tag/attr allow-list) cleans
// its HTML before it touches the DOM; links are forced to open safely in a new tab.
const MD_TAGS = [
  "p", "br", "strong", "em", "del", "code", "pre", "blockquote", "hr", "a",
  "ul", "ol", "li", "h1", "h2", "h3", "h4", "table", "thead", "tbody", "tr", "th", "td",
];
DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A") {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  }
});

function renderMarkdown(el, text) {
  el.innerHTML = DOMPurify.sanitize(marked.parse(text, { gfm: true, breaks: true }), {
    ALLOWED_TAGS: MD_TAGS,
    ALLOWED_ATTR: ["href", "title"],
  });
}

function addCallout(text, kind) {
  removeEmptyState();
  const row = document.createElement("div");
  row.className = `msg-row ${kind}`;
  const box = document.createElement("div");
  box.className = `callout ${kind === "error" ? "error" : ""}`;
  box.innerHTML = SVG.info;
  const body = document.createElement("div");
  body.className = "callout-text";
  body.textContent = text;
  box.appendChild(body);
  row.appendChild(box);
  chat.appendChild(row);
  chat.scrollTop = chat.scrollHeight;
  return row;
}

function removeEmptyState() {
  const empty = chat.querySelector(".empty-state");
  if (empty) empty.remove();
}

function addMessage(text, cls) {
  if (cls === "error") return addCallout(text, "error");
  removeEmptyState();

  const row = document.createElement("div");
  row.className = `msg-row ${cls}`;

  const col = document.createElement("div");
  col.className = "msg-col";

  const bubble = document.createElement("div");
  bubble.className = `bubble ${cls}`;

  if (cls.includes("typing")) {
    bubble.setAttribute("aria-label", "Thinking");
    bubble.innerHTML = '<span class="tdot"></span><span class="tdot"></span><span class="tdot"></span>';
  } else if (cls === "bot") {
    bubble.classList.add("md");
    renderMarkdown(bubble, text);
  } else {
    bubble.textContent = text;
  }
  col.appendChild(bubble);

  row.appendChild(col);
  chat.appendChild(row);
  chat.scrollTop = chat.scrollHeight;
  return row;
}

function addSources(row, sources) {
  if (!row || !Array.isArray(sources) || !sources.length) return;
  const wrap = document.createElement("div");
  wrap.className = "sources";
  const label = document.createElement("span");
  label.textContent = "From";
  wrap.appendChild(label);
  sources.forEach((name) => {
    const chip = document.createElement("span");
    chip.className = "source-chip";
    chip.innerHTML = SVG.doc;
    const text = document.createElement("span");
    text.textContent = name;
    chip.appendChild(text);
    wrap.appendChild(chip);
  });
  row.querySelector(".msg-col").appendChild(wrap);
  chat.scrollTop = chat.scrollHeight;
}

// ---------- files in this session (sidebar) ----------

const sessionFiles = new Map(); // name -> { chunks, status }

const STATUS_BADGE = {
  ingested: ["Added", "added"],
  overwritten: ["Updated", "updated"],
  skipped: ["Already added", "dup"],
};

function fileKind(name) {
  const ext = name.split(".").pop().toLowerCase();
  if (ext === "pdf") return ["PDF", "pdf"];
  if (ext === "txt") return ["TXT", "txt"];
  return ["MD", "md"];
}

function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

function renderFiles(latest) {
  fileList.querySelectorAll(".file-row").forEach((el) => el.remove());
  sessionFiles.forEach((info, name) => {
    const [label, kind] = fileKind(name);
    const badgeInfo = STATUS_BADGE[info.status]; // restored files (after refresh) have no badge

    const row = document.createElement("button");
    row.type = "button";
    row.className = `file-row${name === latest ? " latest" : ""}`;
    row.setAttribute("aria-label", `Open ${name}`);
    row.addEventListener("click", () => openViewer(name));

    const type = document.createElement("span");
    type.className = `file-type ${kind}`;
    type.textContent = label;

    const infoEl = document.createElement("div");
    infoEl.className = "file-info";
    const nameEl = document.createElement("div");
    nameEl.className = "file-name";
    nameEl.textContent = name;
    nameEl.title = name;
    const meta = document.createElement("div");
    meta.className = "file-meta";
    const passages = document.createElement("span");
    passages.textContent = plural(info.chunks, "passage");
    meta.append(passages);
    if (badgeInfo) {
      const badge = document.createElement("span");
      badge.className = `badge ${badgeInfo[1]}`;
      badge.textContent = badgeInfo[0];
      meta.append(badge);
    }
    infoEl.append(nameEl, meta);

    row.append(type, infoEl);
    fileList.appendChild(row);
  });

  const files = sessionFiles.size;
  const passages = [...sessionFiles.values()].reduce((sum, f) => sum + f.chunks, 0);
  fileEmpty.hidden = files > 0;
  fileCount.textContent = plural(files, "file");
  filesTopCount.textContent = files;
  sheetSub.textContent = files
    ? `Ask about the ${plural(files, "file")} in this session, ${plural(passages, "passage")} in all.`
    : "Add a file first, then ask about it in plain words.";
}

// ---------- file viewer ----------

let viewerRequest = 0; // purana jawab naye click ko overwrite na kare

async function openViewer(name) {
  const mine = ++viewerRequest;
  viewerTitle.textContent = name;
  viewerMeta.textContent = "";
  viewerBody.textContent = "Loading\u2026";
  if (!viewer.open) viewer.showModal();
  try {
    const res = await fetch(`/session/passages?source=${encodeURIComponent(name)}`);
    const data = await res.json();
    if (mine !== viewerRequest) return;
    if (!res.ok) throw new Error(data.detail || "Could not open this file");
    viewerMeta.textContent = plural(data.passages.length, "passage");
    const list = document.createElement("ol");
    list.className = "passages";
    data.passages.forEach((p) => {
      const item = document.createElement("li");
      const num = document.createElement("span");
      num.className = "passage-n";
      num.textContent = p.n;
      const text = document.createElement("p");
      text.textContent = p.text; // textContent: file ka text kabhi HTML nahi banta
      item.append(num, text);
      list.appendChild(item);
    });
    viewerBody.replaceChildren(list);
  } catch (err) {
    if (mine === viewerRequest) viewerBody.textContent = err.message;
  }
}

document.getElementById("viewer-close").addEventListener("click", () => viewer.close());
viewer.addEventListener("click", (e) => {
  if (e.target === viewer) viewer.close(); // backdrop click
});

// ---------- chat that survives a refresh ----------
// Chat browser me (localStorage) rehti hai, session ke hash wali key par: files server par hain,
// chat sirf is browser ki hai. Key tab milti hai jab /session/files jawab de.

const CHAT_KEEP = 40; // itne aakhri messages
const CHAT_ROLES = ["user", "bot", "notfound"];
const transcript = []; // { role, text, sources? }
let chatKey = null;

function saveChat() {
  clearChatBtn.hidden = !transcript.length;
  if (!chatKey) return;
  try {
    if (transcript.length) localStorage.setItem(chatKey, JSON.stringify(transcript.slice(-CHAT_KEEP)));
    else localStorage.removeItem(chatKey);
  } catch (err) {
    /* storage full / blocked: chat bas is baar refresh par nahi bachegi */
  }
}

function loadChat() {
  try {
    const list = JSON.parse(localStorage.getItem(chatKey) || "[]");
    return Array.isArray(list)
      ? list.filter((m) => m && CHAT_ROLES.includes(m.role) && typeof m.text === "string")
      : [];
  } catch (err) {
    return []; // kharab JSON: khali chat se shuru
  }
}

function showEntry(entry) {
  transcript.push(entry);
  if (entry.role === "user") {
    addMessage(entry.text, "user");
    history.push({ role: "user", content: entry.text });
  } else if (entry.role === "notfound") {
    addCallout(NOT_FOUND_COPY, "notfound");
    history.push({ role: "assistant", content: NOT_FOUND_TEXT });
  } else {
    addSources(addMessage(entry.text, "bot"), Array.isArray(entry.sources) ? entry.sources : []);
    history.push({ role: "assistant", content: entry.text });
  }
}

clearChatBtn.addEventListener("click", () => {
  transcript.length = 0;
  history.length = 0;
  chat.replaceChildren(emptyStateTemplate.cloneNode(true));
  saveChat();
  chatInput.focus();
});

// Refresh ke baad is session ki purani files wapas dikhao (server cookie se session pehchanta hai).
// Fail ho to chupchap khali list - app phir bhi naye uploads ke saath chalti hai.
async function restoreFiles() {
  try {
    const res = await fetch("/session/files");
    if (!res.ok) return;
    const { files, session } = await res.json();
    files.forEach((f) => sessionFiles.set(f.source, { chunks: f.chunks, status: "restored" }));
    if (files.length) renderFiles(null);
    chatKey = `kb-chat:${session}`;
    if (transcript.length) saveChat(); // user ne restore se pehle hi likh diya
    else loadChat().forEach(showEntry);
    clearChatBtn.hidden = !transcript.length;
  } catch (err) {
    /* offline / backend down: nothing to restore */
  }
}

// ---------- phone: files sheet ----------

function setSideOpen(open) {
  document.body.classList.toggle("side-open", open);
  filesToggle.setAttribute("aria-expanded", String(open));
  (open ? sideClose : filesToggle).focus();
}

function sideVisible() {
  return getComputedStyle(side).display !== "none";
}

filesToggle.addEventListener("click", () => setSideOpen(true));
sideClose.addEventListener("click", () => setSideOpen(false));
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && document.body.classList.contains("side-open")) setSideOpen(false);
});

// ---------- upload ----------

function setUploadStatus(text, cls) {
  uploadStatus.textContent = text;
  uploadStatus.className = `upload-status show ${cls || ""}`;
}

function onFileSelected(file) {
  uploadBtn.disabled = !file;
  fileLabelText.textContent = file ? file.name : "Choose file…";
  fileClear.hidden = !file;
  uploadStatus.className = "upload-status";
  uploadStatus.textContent = "";
}

const BIG_FILE_BYTES = 4 * 1024 * 1024; // 4 MB se bada -> S3 direct (sirf deployed app par)

async function ingestViaS3(file) {
  const pre = await fetch("/presign", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename: file.name, size: file.size }),
  });
  if (!pre.ok) return null; // S3 available nahi (local dev) -> caller multipart try kare
  const preData = await pre.json();
  const put = await fetch(preData.upload_url, { method: "PUT", body: file });
  if (!put.ok) throw new Error("S3 upload failed (HTTP " + put.status + ")");
  setUploadStatus("Processing…", "busy");
  const res = await fetch("/upload-s3", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ key: preData.key }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || "Ingest failed");
  return data;
}

async function uploadFile(file) {
  uploadBtn.disabled = true;
  try {
    let data = null;
    if (file.size > BIG_FILE_BYTES) {
      setUploadStatus("Uploading directly to S3…", "busy");
      try {
        data = await ingestViaS3(file);
      } catch (err) {
        setUploadStatus("S3 failed, trying direct…", "busy");
      }
    }
    if (!data) {
      setUploadStatus("Uploading…", "busy");
      const form = new FormData();
      form.append("file", file);
      const res = await fetch("/upload", {
        method: "POST",
        body: form,
      });
      data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Upload failed");
    }

    const labels = {
      ingested: ["Added", "ok"],
      overwritten: ["Updated", "ok"],
      skipped: ["Already added — no changes", "warn"],
    };
    const [label, cls] = labels[data.status] || [data.status, ""];

    setUploadStatus(`${label} · ${plural(data.chunks, "passage")}`, cls);
    sessionFiles.set(data.source, { chunks: data.chunks, status: data.status });
    renderFiles(data.source);
  } catch (err) {
    setUploadStatus(err.message, "err");
    // Files sheet band ho (phone) to error chat me bhi dikhao, warna user ko dikhega nahi
    if (!sideVisible()) addMessage(err.message, "error");
  } finally {
    uploadBtn.disabled = false;
  }
}

let uploadOnPick = false; // "+" button se chuni file turant upload hoti hai

fileInput.addEventListener("change", () => {
  const file = fileInput.files[0];
  onFileSelected(file);
  if (file && uploadOnPick) uploadFile(file);
  uploadOnPick = false;
});
uploadBtn.addEventListener("click", () => {
  const file = fileInput.files[0];
  if (file) uploadFile(file);
});
attachBtn.addEventListener("click", () => {
  uploadOnPick = true;
  fileInput.value = "";
  fileInput.click();
});

// cross button: clear the selected file -> label shows "Choose file…" again
fileClear.addEventListener("click", () => {
  fileInput.value = "";
  onFileSelected(null);
});

// drag & drop
["dragenter", "dragover"].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  })
);
["dragleave", "dragend"].forEach((ev) =>
  dropzone.addEventListener(ev, () => dropzone.classList.remove("dragover"))
);
dropzone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropzone.classList.remove("dragover");
  const file = e.dataTransfer.files[0];
  if (!file) return;
  const dt = new DataTransfer();
  dt.items.add(file);
  fileInput.files = dt.files;
  onFileSelected(file);
  uploadFile(file);
});

// ---------- chat ----------

// POST /api/stream -> Server-Sent Events. EventSource sirf GET karta hai (headers/body nahi),
// isliye fetch + reader. onToken(text) har tukde par; return { sources }.
async function streamAnswer(payload, onToken) {
  const res = await fetch("/api/stream", payload);
  const type = res.headers.get("content-type") || "";
  if (!res.ok || !res.body || !type.includes("text/event-stream")) {
    const err = new Error("Streaming not available");
    err.noStream = true; // old/buffering deployment -> caller plain /api try karega
    throw err;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let sources = [];

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let end;
    while ((end = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      let event = "message";
      let data = "";
      block.split("\n").forEach((line) => {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      });
      const parsed = data ? JSON.parse(data) : {};
      if (event === "token") onToken(parsed.text);
      else if (event === "sources") sources = parsed.sources || [];
      else if (event === "error") throw new Error(parsed.detail || "Request failed");
    }
  }
  return { sources };
}

async function plainAnswer(payload) {
  const res = await fetch("/api", payload);
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || "Request failed");
  return data;
}

function autosize() {
  chatInput.style.height = "auto";
  chatInput.style.height = Math.min(chatInput.scrollHeight, 160) + "px";
}

chatInput.addEventListener("input", autosize);
chatInput.addEventListener("keydown", (e) => {
  // Enter = send, Shift+Enter = new line (IME composition ko mat todo)
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    chatForm.requestSubmit();
  }
});

// example questions: input bharo, bhejo nahi
chat.addEventListener("click", (e) => {
  const tip = e.target.closest(".tip");
  if (!tip) return;
  chatInput.value = tip.textContent;
  autosize();
  chatInput.focus();
});

chatForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (sendBtn.disabled) return;

  const question = chatInput.value.trim();
  if (!question) return;

  addMessage(question, "user");
  history.push({ role: "user", content: question });
  transcript.push({ role: "user", text: question });
  chatInput.value = "";
  autosize();

  sendBtn.disabled = true;
  let typing = addMessage("", "bot typing");

  const payload = {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ input: question, history: history.slice(-12) }),
  };

  let text = "";
  let card = null; // bot row, pehla visible token aane par banta hai

  const onToken = (piece) => {
    text += piece;
    if (!card && mightBeNotFound(text)) return;
    if (!card) {
      typing.remove();
      typing = null;
      card = addMessage(text, "bot");
    } else {
      renderMarkdown(card.querySelector(".bubble"), text);
      chat.scrollTop = chat.scrollHeight;
    }
  };

  try {
    let sources;
    try {
      ({ sources } = await streamAnswer(payload, onToken));
    } catch (err) {
      // Stream shuru hi nahi hui -> wahi sawal purane /api se (kuch dikha nahi, to safe)
      if (!err.noStream || text) throw err;
      const data = await plainAnswer(payload);
      text = data.content;
      sources = data.sources;
    }

    if (typing) typing.remove();
    if (isNotFound(text)) {
      if (card) card.remove();
      addCallout(NOT_FOUND_COPY, "notfound");
      transcript.push({ role: "notfound", text });
    } else if (!text.trim()) {
      throw new Error("The model returned an empty answer.");
    } else {
      if (!card) card = addMessage(text, "bot");
      addSources(card, sources);
      transcript.push({ role: "bot", text, sources: sources || [] });
    }
    history.push({ role: "assistant", content: text });
  } catch (err) {
    if (typing) typing.remove();
    addMessage(err.message, "error");
  } finally {
    saveChat();
    sendBtn.disabled = false;
    chatInput.focus();
  }
});

restoreFiles();
