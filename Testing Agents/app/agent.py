"""LangChain tool-calling agent setup with per-session conversation memory."""
from typing import Dict

from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.chat_history import BaseChatMessageHistory, InMemoryChatMessageHistory
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_groq import ChatGroq

from app.config import GROQ_API_KEY, GROQ_MODEL
from app.tools import ALL_TOOLS

SYSTEM_PROMPT = """You are a helpful flight booking assistant. Your job is to help users \
search for flights, compare options, make demo bookings, and manage existing bookings.

Always collect the required information before calling a tool. Never invent flight \
availability, prices, or booking IDs. Use the available tools to retrieve this information.

Before booking or cancelling a flight, clearly explain the action and ask the user for \
explicit confirmation.

If the user provides incomplete information, ask follow-up questions instead of making \
assumptions.

If a requested flight is unavailable, explain the situation and offer alternatives.

Keep responses clear, concise, and conversational. You are operating in a demo \
environment, so never claim that a real flight has been booked or cancelled."""

# Per-session in-memory chat history store, keyed by session_id.
_session_store: Dict[str, BaseChatMessageHistory] = {}


def _get_session_history(session_id: str) -> BaseChatMessageHistory:
    if session_id not in _session_store:
        _session_store[session_id] = InMemoryChatMessageHistory()
    return _session_store[session_id]


def clear_session(session_id: str) -> None:
    _session_store.pop(session_id, None)


def build_agent_executor() -> RunnableWithMessageHistory:
    if not GROQ_API_KEY:
        raise RuntimeError(
            "GROQ_API_KEY is not set. Copy .env.example to .env and add your free Groq API key "
            "from https://console.groq.com/keys."
        )

    llm = ChatGroq(
        model=GROQ_MODEL,
        api_key=GROQ_API_KEY,
        temperature=0,
    )

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", SYSTEM_PROMPT),
            MessagesPlaceholder("chat_history"),
            ("human", "{input}"),
            MessagesPlaceholder("agent_scratchpad"),
        ]
    )

    agent = create_tool_calling_agent(llm, ALL_TOOLS, prompt)
    executor = AgentExecutor(
        agent=agent,
        tools=ALL_TOOLS,
        verbose=True,
        handle_parsing_errors=True,
        return_intermediate_steps=True,
        max_iterations=8,
    )

    return RunnableWithMessageHistory(
        executor,
        _get_session_history,
        input_messages_key="input",
        history_messages_key="chat_history",
    )


_agent_executor = None


def get_agent_executor() -> RunnableWithMessageHistory:
    global _agent_executor
    if _agent_executor is None:
        _agent_executor = build_agent_executor()
    return _agent_executor
