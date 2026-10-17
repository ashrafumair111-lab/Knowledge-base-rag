const chat = document.getElementById("chat");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const sendBtn = document.getElementById("send-btn");

const fileInput = document.getElementById("file-input");
const fileLabelText = document.getElementById("file-label-text");
const fileClear = document.getElementById("file-clear");
const uploadBtn = document.getElementById("upload-btn");
const uploadStatus = document.getElementById("upload-status");
const dropzone = document.getElementById("dropzone");

const BOT_SVG =
  '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l2.2 5.8L20 10l-5.8 2.2L12 18l-2.2-5.8L4 10l5.8-2.2z"/></svg>';

// ---------- messages ----------

function now() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function addMessage(text, cls) {
  const empty = chat.querySelector(".empty-state");
  if (empty) empty.remove();

  const row = document.createElement("div");
  row.className = `msg-row ${cls}`;

  const isUser = cls === "user";
  const isBot = cls === "bot" || cls.includes("typing");

  // avatar (only for chat messages)
  if (isUser || isBot) {
    const avatar = document.createElement("div");
    avatar.className = `avatar ${isUser ? "avatar-user" : "avatar-bot"}`;
    if (isUser) {
      avatar.textContent = "U";
    } else {
      avatar.innerHTML = BOT_SVG;
    }
    row.appendChild(avatar);
  }

  const col = document.createElement("div");
  col.className = "msg-col";

  const bubble = document.createElement("div");
  bubble.className = `bubble ${cls}`;

  if (cls.includes("typing")) {
    bubble.innerHTML = '<span class="tdot"></span><span class="tdot"></span><span class="tdot"></span>';
  } else {
    bubble.textContent = text;
  }
  col.appendChild(bubble);

  if (isUser || isBot) {
    const time = document.createElement("div");
    time.className = "time";
    time.textContent = now();
    col.appendChild(time);
  }

  row.appendChild(col);
  chat.appendChild(row);
  chat.scrollTop = chat.scrollHeight;
  return row;
}

// ---------- upload ----------

function setUploadStatus(text, cls) {
  uploadStatus.textContent = text;
  uploadStatus.className = `upload-status show ${cls || ""}`;
}

function onFileSelected(file) {
  uploadBtn.disabled = !file;
  fileLabelText.textContent = file ? file.name : "Choose file\u2026";
  fileClear.hidden = !file;
  uploadStatus.className = "upload-status";
  uploadStatus.textContent = "";
}

async function uploadFile(file) {
  uploadBtn.disabled = true;
  setUploadStatus("Uploading\u2026", "busy");

  const form = new FormData();
  form.append("file", file);

  try {
    const res = await fetch("/upload", { method: "POST", body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Upload failed");

    const labels = {
      ingested: ["\u2713 ingested", "ok"],
      overwritten: ["\u21bb overwritten", "ok"],
      skipped: ["\u29d6 skipped \u2014 duplicate", "warn"],
    };
    const [label, cls] = labels[data.status] || [data.status, ""];

    setUploadStatus(`${label} \u00b7 ${data.chunks} chunks`, cls);
    addMessage(`File "${data.source}" \u2014 ${data.status} (${data.chunks} chunks)`, "system");
  } catch (err) {
    setUploadStatus(err.message, "err");
  } finally {
    uploadBtn.disabled = false;
  }
}

fileInput.addEventListener("change", () => onFileSelected(fileInput.files[0]));
uploadBtn.addEventListener("click", () => {
  const file = fileInput.files[0];
  if (file) uploadFile(file);
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

chatForm.addEventListener("submit", async (e) => {
  e.preventDefault();

  const question = chatInput.value.trim();
  if (!question) return;

  addMessage(question, "user");
  chatInput.value = "";

  sendBtn.disabled = true;
  const typing = addMessage("", "bot typing");

  try {
    const res = await fetch("/api", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ input: question }),
    });
    const data = await res.json();

    typing.remove();
    if (!res.ok) throw new Error(data.detail || "Request failed");
    addMessage(data.content, "bot");
  } catch (err) {
    typing.remove();
    addMessage(err.message, "error");
  } finally {
    sendBtn.disabled = false;
    chatInput.focus();
  }
});
