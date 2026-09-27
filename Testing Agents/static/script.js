(function () {
  const STORAGE_KEY = "flight-agent-session-id";

  const messagesEl = document.getElementById("messages");
  const formEl = document.getElementById("chat-form");
  const inputEl = document.getElementById("chat-input");
  const sendBtn = document.getElementById("send-btn");
  const newChatBtn = document.getElementById("new-chat-btn");
  const toggleTraceBtn = document.getElementById("toggle-trace-btn");
  const tracePanel = document.getElementById("trace-panel");
  const traceLog = document.getElementById("trace-log");

  let sessionId = localStorage.getItem(STORAGE_KEY) || null;
  let turnCounter = 0;

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  function renderInline(text) {
    // Minimal, safe markdown-lite: escape first, then re-enable **bold**.
    return escapeHtml(text).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  }

  function addMessage(role, text) {
    const wrap = document.createElement("div");
    wrap.className = `message ${role}`;
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.innerHTML = renderInline(text);
    wrap.appendChild(bubble);
    messagesEl.appendChild(wrap);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return wrap;
  }

  function addTypingIndicator() {
    const wrap = document.createElement("div");
    wrap.className = "message agent typing";
    wrap.innerHTML = `<div class="bubble">
      <span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span>
    </div>`;
    messagesEl.appendChild(wrap);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return wrap;
  }

  function renderTrace(trace) {
    if (!trace || trace.length === 0) return;

    const emptyHint = traceLog.querySelector(".trace-empty");
    if (emptyHint) emptyHint.remove();

    turnCounter += 1;
    const turn = document.createElement("div");
    turn.className = "trace-turn";

    const label = document.createElement("div");
    label.className = "trace-turn-label";
    label.textContent = `Turn ${turnCounter} · ${trace.length} tool call(s)`;
    turn.appendChild(label);

    trace.forEach((call) => {
      const failed = call.output && call.output.success === false;
      const item = document.createElement("div");
      item.className = "trace-call";

      const name = document.createElement("div");
      name.className = `trace-call-name${failed ? " failed" : ""}`;
      name.innerHTML = `<span class="dot"></span>${escapeHtml(call.tool)}`;
      item.appendChild(name);

      const argsLabel = document.createElement("div");
      argsLabel.className = "trace-call-section";
      argsLabel.textContent = "Arguments";
      item.appendChild(argsLabel);

      const argsPre = document.createElement("pre");
      argsPre.textContent = JSON.stringify(call.tool_input, null, 2);
      item.appendChild(argsPre);

      const outLabel = document.createElement("div");
      outLabel.className = "trace-call-section";
      outLabel.textContent = "Result";
      item.appendChild(outLabel);

      const outPre = document.createElement("pre");
      outPre.textContent = JSON.stringify(call.output, null, 2);
      item.appendChild(outPre);

      turn.appendChild(item);
    });

    traceLog.appendChild(turn);
    traceLog.scrollTop = traceLog.scrollHeight;
  }

  async function sendMessage(text) {
    addMessage("user", text);
    inputEl.value = "";
    sendBtn.disabled = true;

    const typing = addTypingIndicator();

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, message: text }),
      });

      const data = await res.json();
      typing.remove();

      if (!res.ok) {
        addMessage("error", data.detail || "Something went wrong talking to the agent.");
        return;
      }

      sessionId = data.session_id;
      localStorage.setItem(STORAGE_KEY, sessionId);

      addMessage("agent", data.reply || "(no response)");
      renderTrace(data.trace);
    } catch (err) {
      typing.remove();
      addMessage("error", "Network error: could not reach the agent backend.");
    } finally {
      sendBtn.disabled = false;
      inputEl.focus();
    }
  }

  formEl.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = inputEl.value.trim();
    if (!text) return;
    sendMessage(text);
  });

  newChatBtn.addEventListener("click", async () => {
    if (sessionId) {
      try {
        await fetch(`/api/sessions/${sessionId}/reset`, { method: "POST" });
      } catch (_) {
        /* ignore */
      }
    }
    sessionId = null;
    localStorage.removeItem(STORAGE_KEY);
    turnCounter = 0;
    messagesEl.innerHTML = "";
    traceLog.innerHTML = '<p class="trace-empty">No tool calls yet. Ask the agent something to see its tool selection, arguments, and results here.</p>';
    addMessage(
      "agent",
      "New demo session started. Ask me about flights, bookings, or recommendations."
    );
    inputEl.focus();
  });

  toggleTraceBtn.addEventListener("click", () => {
    tracePanel.classList.toggle("collapsed");
  });

  inputEl.focus();
})();
