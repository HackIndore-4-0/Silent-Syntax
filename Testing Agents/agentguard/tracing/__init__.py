"""Fine-grained tracing inside an already-@monitor-wrapped run.

@traceable records one TraceStep per call to any function; wrap_llm_client
does the same automatically for an LLM SDK client's call methods, plus
token/cost bookkeeping. traced_completion()/traced_acompletion() are the
provider-agnostic LLM Gateway entry points (any provider LiteLLM
supports, real cost via litellm.completion_cost(), plus optional
Policy.llm_gateway enforcement/fallback — see litellm_wrap.py). All
require an active @monitor run and nest into a real parent/child tree
with zero manual bookkeeping from the caller. See
agentguard/tracing/recording.py for the shared core.
"""
from .litellm_wrap import traced_acompletion, traced_completion
from .llm_wrap import wrap_llm_client
from .traceable import traceable

__all__ = ["traceable", "wrap_llm_client", "traced_completion", "traced_acompletion"]
