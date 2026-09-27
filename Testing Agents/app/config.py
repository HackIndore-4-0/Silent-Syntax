"""Environment configuration for the Flight Booking AI Agent (demo project)."""
import os

from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
APP_HOST = os.getenv("APP_HOST", "127.0.0.1")
APP_PORT = int(os.getenv("APP_PORT", "8000"))

# --- AgentGuard evaluator integration (optional) ---------------------------
# Every chat turn is wrapped in agentguard.monitor(...) (see
# app/agentguard_integration.py) so an external AgentGuard dashboard/CLI can
# score it. None of these are required to run the demo: with no
# AGENTGUARD_API_KEY the SDK still runs fully locally (unauthenticated,
# in-memory storage) and every run is still recorded and evaluated.
AGENTGUARD_API_KEY = os.getenv("AGENTGUARD_API_KEY", "")
AGENTGUARD_PROJECT = os.getenv("AGENTGUARD_PROJECT", "production")
AGENTGUARD_DATABASE_URL = os.getenv("AGENTGUARD_DATABASE_URL", "")

# Deterministic timeout (seconds) for a book_flight/cancel_booking human
# approval request (see app/agentguard_integration.py's
# Policy(require_approval=...)) — on timeout the action is denied, never
# left hanging forever. Longer than AgentGuard's own 120s default since a
# human resolving this by hand via curl/Postman during testing may need
# more time than a wired-up dashboard button click would.
AGENTGUARD_HUMAN_TIMEOUT_S = float(os.getenv("AGENTGUARD_HUMAN_TIMEOUT_S", "300"))

# "off" (default, correct behavior) | "budget_violation" (deliberately makes
# search_flights/recommend_flights accept flights above the user's stated
# budget, so a booking that violates it is reproducible on demand — for
# feeding known-bad runs to the AgentGuard evaluator). Never enable this
# outside of evaluator testing.
FAULT_INJECTION_MODE = os.getenv("FAULT_INJECTION_MODE", "off")
