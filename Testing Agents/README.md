# Flight Booking AI Agent (Demo)

A **demo-only** flight booking assistant built with **Python, LangChain, and Groq**
(free-tier LLM hosting — no Gemini/paid Google API required),
designed for testing an external AI Agent Evaluator. It uses a mock, in-memory flight
database and mock bookings — there is no real flight API and no real payment
processing anywhere in this project.

A small FastAPI backend exposes the agent over HTTP, and a lightweight chat UI (plain
HTML/CSS/JS, no build step) lets you talk to it in a browser while also showing a live
**Agent Trace** panel with every tool call, its arguments, and its structured result —
useful for manually inspecting or feeding an external evaluator.

## Project structure

```
.
├── app/
│   ├── main.py        # FastAPI app: /api/chat, /api/sessions/{id}/reset, static UI
│   ├── agent.py        # LangChain tool-calling agent + per-session conversation memory
│   ├── tools.py        # search_flights, get_flight_details, book_flight, get_booking,
│   │                    # cancel_booking, recommend_flights
│   ├── models.py       # Pydantic input schemas for every tool
│   ├── database.py     # Mock in-memory flight inventory + bookings store
│   └── config.py       # Environment variable loading
├── static/
│   ├── index.html      # Chat UI + Agent Trace panel
│   ├── style.css
│   └── script.js
├── sample_conversations.md
├── requirements.txt
├── .env.example
└── README.md
```

## 1. Setup

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

Copy `.env.example` to `.env` and add a free Groq API key from
https://console.groq.com/keys:

```bash
cp .env.example .env
# then edit .env and set GROQ_API_KEY=...
```

Groq's free tier is generous and fast (Llama models served on their LPU inference
hardware), so no billing setup is needed to run this demo.

## 2. Run

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000** in a browser. Type a message such as:

> "Find flights from Indore to Delhi on 2026-10-15 for 2 people."

The right-hand **Agent Trace** panel (toggle with the "Agent Trace" button) shows the
exact tool name, arguments, and structured JSON result for every tool call the agent
makes — this is the structured data an external Agent Evaluator would inspect.

## 3. API (for programmatic / evaluator access)

`POST /api/chat`

```json
{ "session_id": "optional-existing-id", "message": "Search flights from Indore to Delhi on 2026-10-15" }
```

Response:

```json
{
  "session_id": "...",
  "reply": "Here are the available flights...",
  "trace": [
    { "tool": "search_flights", "tool_input": {"...": "..."}, "output": {"success": true, "flights": [...]} }
  ]
}
```

`POST /api/sessions/{session_id}/reset` — clears conversation memory for a session.

`GET /api/health` — liveness check.

## 4. Mock data

`app/database.py` contains thousands of fictional flights across Indore, Delhi, Mumbai,
Bengaluru, Hyderabad, Chennai, and Kolkata — both directions, roughly 2-3 flights per
route per day for the next 60 days from a fixed RNG seed (stable across restarts) — each
flagged `is_demo_data: true`. Bookings created via the agent are stored in memory only
(they reset when the server restarts) and are flagged `is_demo_booking: true`.

## 5. Conversation memory

Each browser session gets a `session_id` (persisted in `localStorage`), and the backend
keeps a `langchain_core.chat_history.InMemoryChatMessageHistory` per session via
`RunnableWithMessageHistory`, so the agent remembers source, destination, date, and
passenger count across turns without re-asking. Click **New Chat** to start a fresh
session with empty memory.

## 6. Safety behaviors built into the system prompt

- Never fabricates flight availability, prices, or booking IDs — always calls a tool.
- Always asks for explicit confirmation before `book_flight` or `cancel_booking`, and
  those tools themselves refuse to act unless `user_confirmation: true` is passed.
- Asks follow-up questions on incomplete input instead of guessing.
- Explains and offers alternatives when a requested flight is unavailable or sold out.
- Every tool returns `{"success": false, "error": "..."}` on invalid input (bad dates,
  unknown flight/booking IDs, insufficient seats, already-cancelled bookings) instead of
  raising, so the agent can explain the problem in plain language rather than crashing.

## 7. Sample conversations

See [`sample_conversations.md`](./sample_conversations.md) for transcripts covering
context retention, confirmation flow, refusals, booking retrieval/cancellation,
unavailable flights, recommendations, missing information, ambiguous requests, and
invalid IDs — useful as a starting checklist for an Agent Evaluator.
