Challenge 1: Real-Time Circuit Breaker via OpenTelemetry Tracing Implement an execution 
circuit breaker that leverages an observability framework (e.g., OpenTelemetry or LangSmith) to 
actively monitor an agent's loop iterations and token consumption in real-time. The backend 
must intercept the workflow and halt execution when a predefined threshold (such as 4 
consecutive failed tool calls or a maximum token budget) is breached, gracefully degrading the 
system rather than entering an infinite loop. 
Expected Outcome: 
● A functioning telemetry pipeline capturing real-time metrics for LLM calls, tool execution, 
and loop counts. 
● A circuit breaker logic that reliably stops runaway agent loops without crashing the host 
application. 
● A structured error trace explicitly showing the exact node and reasoning that triggered 
the execution halt.