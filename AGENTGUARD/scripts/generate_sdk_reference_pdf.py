"""Generates docs/AgentGuard_SDK_Reference.pdf — a reference document for
developers embedding the AgentGuard SDK directly in their own agent code.

Content here is a description of the ACTUAL public API surface exposed by
`agentguard/__init__.py` (plus a few advanced, explicitly-importable
extras: `agentguard.context.call_tool`, `agentguard.tools.registry`,
`agentguard.recovery.seed`, `agentguard.evaluators.base.Evaluator`) as of
this build — not aspirational/future functionality. Regenerate this
script (not the PDF by hand) if the SDK's public surface changes.

Run:  .venv/Scripts/python.exe scripts/generate_sdk_reference_pdf.py
"""
from __future__ import annotations

import os

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "AgentGuard_SDK_Reference.pdf")

# --------------------------------------------------------------------- #
# Styles
# --------------------------------------------------------------------- #

styles = getSampleStyleSheet()

styles.add(ParagraphStyle(
    "AGTitle", parent=styles["Title"], fontSize=25, leading=30,
    textColor=colors.HexColor("#12213a"), spaceAfter=6,
))
styles.add(ParagraphStyle(
    "AGSubtitle", parent=styles["Normal"], fontSize=12.5, leading=17,
    textColor=colors.HexColor("#5b6472"), spaceAfter=4,
))
styles.add(ParagraphStyle(
    "AGH1", parent=styles["Heading1"], fontSize=17, leading=21,
    textColor=colors.white, backColor=colors.HexColor("#1f3a63"),
    spaceBefore=18, spaceAfter=10, leftIndent=0, borderPadding=(6, 8, 6, 8),
))
styles.add(ParagraphStyle(
    "AGH2", parent=styles["Heading2"], fontSize=13.5, leading=17,
    textColor=colors.HexColor("#12213a"), spaceBefore=14, spaceAfter=6,
    borderWidth=0, borderColor=colors.HexColor("#1f3a63"),
))
styles.add(ParagraphStyle(
    "AGH3", parent=styles["Heading3"], fontSize=11.3, leading=14,
    textColor=colors.HexColor("#1f3a63"), spaceBefore=10, spaceAfter=4,
    fontName="Helvetica-Bold",
))
styles.add(ParagraphStyle(
    "AGBody", parent=styles["Normal"], fontSize=9.8, leading=14.5,
    textColor=colors.HexColor("#22262e"), spaceAfter=6, alignment=TA_LEFT,
))
styles.add(ParagraphStyle(
    "AGNote", parent=styles["Normal"], fontSize=9.2, leading=13.2,
    textColor=colors.HexColor("#5b4600"), backColor=colors.HexColor("#fff6dd"),
    borderPadding=(6, 8, 6, 8), spaceAfter=8, spaceBefore=2,
))
styles.add(ParagraphStyle(
    "AGSig", parent=styles["Normal"], fontName="Courier-Bold", fontSize=9.6,
    leading=13, textColor=colors.HexColor("#0a3d62"),
    backColor=colors.HexColor("#eef3fa"), borderPadding=(6, 8, 6, 8), spaceAfter=6,
))
styles.add(ParagraphStyle(
    "AGCell", parent=styles["Normal"], fontSize=8.6, leading=11.5,
    textColor=colors.HexColor("#22262e"),
))
styles.add(ParagraphStyle(
    "AGCellMono", parent=styles["Normal"], fontName="Courier", fontSize=8.3,
    leading=11, textColor=colors.HexColor("#0a3d62"),
))
styles.add(ParagraphStyle(
    "AGToc", parent=styles["Normal"], fontSize=10.2, leading=16,
    textColor=colors.HexColor("#12213a"),
))


def code_block(text: str) -> Table:
    pre = Preformatted(text.strip("\n"), ParagraphStyle(
        "code", fontName="Courier", fontSize=8.3, leading=11,
        textColor=colors.HexColor("#0b3d0b"),
    ))
    t = Table([[pre]], colWidths=[6.3 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f2f6ef")),
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#c7d6bd")),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t


def param_table(rows: list[tuple[str, str, str]]) -> Table:
    """rows: (name/type, default, description)"""
    header = [Paragraph("<b>Parameter</b>", styles["AGCell"]),
              Paragraph("<b>Default</b>", styles["AGCell"]),
              Paragraph("<b>Description</b>", styles["AGCell"])]
    data = [header]
    for name, default, desc in rows:
        data.append([
            Paragraph(name, styles["AGCellMono"]),
            Paragraph(default, styles["AGCellMono"]),
            Paragraph(desc, styles["AGCell"]),
        ])
    t = Table(data, colWidths=[1.55 * inch, 0.95 * inch, 3.8 * inch], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3a63")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d5dae2")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f7fa")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def quick_table(rows: list[tuple[str, str]], widths=(2.1, 4.2)) -> Table:
    data = [[Paragraph("<b>Name</b>", styles["AGCell"]), Paragraph("<b>What it does</b>", styles["AGCell"])]]
    for name, desc in rows:
        data.append([Paragraph(name, styles["AGCellMono"]), Paragraph(desc, styles["AGCell"])])
    t = Table(data, colWidths=[widths[0] * inch, widths[1] * inch], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3a63")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d5dae2")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f7fa")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


story: list = []


def h1(text: str) -> None:
    story.append(Paragraph(text, styles["AGH1"]))


def h2(text: str) -> None:
    story.append(Paragraph(text, styles["AGH2"]))


def h3(text: str) -> None:
    story.append(Paragraph(text, styles["AGH3"]))


def p(text: str) -> None:
    story.append(Paragraph(text, styles["AGBody"]))


def note(text: str) -> None:
    story.append(Paragraph("<b>Note — </b>" + text, styles["AGNote"]))


def sig(text: str) -> None:
    story.append(Paragraph(text.replace(" ", "&nbsp;").replace("&nbsp;&nbsp;", "&nbsp;&nbsp;"), styles["AGSig"]))


def sig_raw(text: str) -> None:
    # Preformatted keeps real whitespace/newlines, better for multi-line signatures
    story.append(Table([[Preformatted(text.strip("\n"), ParagraphStyle(
        "sig", fontName="Courier-Bold", fontSize=9.3, leading=12.6,
        textColor=colors.HexColor("#0a3d62"),
    ))]], colWidths=[6.3 * inch], style=TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#eef3fa")),
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#b9cbe6")),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ])))
    story.append(Spacer(1, 5))


def bullets(items: list[str]) -> None:
    story.append(ListFlowable(
        [ListItem(Paragraph(t, styles["AGBody"]), leftIndent=6) for t in items],
        bulletType="bullet", start="•", leftIndent=14,
    ))


def spacer(h: float = 8) -> None:
    story.append(Spacer(1, h))


def hr() -> None:
    story.append(HRFlowable(width="100%", color=colors.HexColor("#d5dae2"), thickness=0.7, spaceBefore=6, spaceAfter=10))


# --------------------------------------------------------------------- #
# Cover
# --------------------------------------------------------------------- #

story.append(Spacer(1, 1.6 * inch))
story.append(Paragraph("AgentGuard SDK Reference", styles["AGTitle"]))
story.append(Paragraph("Functions, decorators and helpers you import directly into your agent's code", styles["AGSubtitle"]))
story.append(Spacer(1, 10))
story.append(HRFlowable(width="100%", color=colors.HexColor("#1f3a63"), thickness=1.4))
story.append(Spacer(1, 14))
story.append(Paragraph(
    "This document describes every public function, class and decorator in the "
    "<b>agentguard</b> Python package: what it does, its exact parameters, what it "
    "returns, and a copy-pasteable example. It is written for a developer who is "
    "adding AgentGuard reliability monitoring to an existing AI agent's own code — "
    "not for the dashboard or backend internals.",
    styles["AGBody"],
))
story.append(Spacer(1, 8))
story.append(Paragraph(
    "Package version: <b>0.2.0</b> &nbsp;&nbsp;|&nbsp;&nbsp; Import root: <b>agentguard</b> "
    "&nbsp;&nbsp;|&nbsp;&nbsp; Requires: Python 3.10+, an AgentGuard API key for multi-user mode",
    styles["AGBody"],
))
story.append(PageBreak())

# --------------------------------------------------------------------- #
# 1. Install & quick start
# --------------------------------------------------------------------- #

h1("1. Installation &amp; Quick Start")

h2("1.1 Install")
code_block_install = """pip install agentguard
# or, from a local checkout:
pip install -e .
"""
story.append(code_block(code_block_install))

h2("1.2 Get an API key")
p("Sign up on the AgentGuard dashboard (Settings → API Keys → Create API Key), copy the "
  "raw key shown once, and store it as an environment variable. Never hard-code it in source "
  "and never expose it to frontend/browser JavaScript.")
story.append(code_block("export AGENTGUARD_API_KEY=\"agp_live_...\"      # macOS/Linux\nset AGENTGUARD_API_KEY=agp_live_...           # Windows cmd\n$env:AGENTGUARD_API_KEY=\"agp_live_...\"        # PowerShell"))

h2("1.3 Minimal agent — three lines added")
story.append(code_block("""import os
from agentguard import AgentGuard

guard = AgentGuard(api_key=os.getenv("AGENTGUARD_API_KEY"), project="production")

@guard.monitor
def my_agent(task: str) -> str:
    # ... your existing agent logic, unchanged ...
    return f"handled: {task}"

my_agent("close the support ticket")
# -> a Run now appears in your AgentGuard dashboard, fully traced, evaluated,
#    risk-scored and hash-chained for audit, with zero other code changes.
"""))
note("<font name='Courier'>AgentGuard(...)</font> is synchronous and does real (in-process) "
     "key resolution at construction time. Construct it once, at module scope or at the top "
     "of a synchronous <font name='Courier'>main()</font> — never from inside code that is "
     "already running under <font name='Courier'>asyncio.run(...)</font> / inside an "
     "<font name='Courier'>async def</font> that owns the loop. See §2.3.")

h2("1.4 No account yet / local & CI use")
p("You can use AgentGuard's reliability engine (Decision Engine, evaluators, risk scoring, "
  "audit trail) with the plain, standalone decorator too — no API key, no dashboard account, "
  "single-tenant. This is what the framework has always supported and remains fully backward "
  "compatible:")
story.append(code_block("""from agentguard import monitor, Policy

@monitor(policy=Policy(max_cost=60000))
def my_agent(task: str) -> str:
    ...
"""))

story.append(PageBreak())

# --------------------------------------------------------------------- #
# 2. AgentGuard client
# --------------------------------------------------------------------- #

h1("2. The AgentGuard Client")
p("<font name='Courier-Bold'>class agentguard.AgentGuard(api_key: str | None = None, project: str = \"production\")</font>")
p("An authenticated handle to one workspace/project in your AgentGuard account, resolved once "
  "from an API key when it is constructed. Every run produced through this client's "
  "<font name='Courier'>.monitor</font> is permanently tagged with the resolved workspace and "
  "project, and is only ever visible to that account — this isolation is enforced on every "
  "dashboard/API query, not just hidden in the UI.")

h2("2.1 Constructor")
sig_raw("AgentGuard(api_key: str | None = None, project: str = \"production\")")
story.append(param_table([
    ("api_key", "None", "Your AgentGuard API key (\"agp_live_...\"). If omitted, read from the "
                        "AGENTGUARD_API_KEY environment variable. Raises AuthenticationError if "
                        "neither is present, the key is unknown, or the key was revoked."),
    ("project", '"production"', "Project name within your workspace to attach runs to (e.g. "
                                "\"production\", \"staging\", \"development\"). Created "
                                "automatically on first use if it doesn't exist yet."),
]))
h3("Raises")
bullets([
    "<font name='Courier'>agentguard.AuthenticationError</font> — no key available, or the key is invalid/revoked.",
    "<font name='Courier'>RuntimeError</font> — constructed from inside an already-running event loop (see §2.3).",
])
h3("Attributes set after construction")
story.append(quick_table([
    ("guard.api_key", "The raw key you passed in (or read from the environment)."),
    ("guard.user_id", "The AgentGuard user account this key belongs to."),
    ("guard.workspace_id", "The workspace this key resolved to — every run is tagged with this."),
    ("guard.project_id", "The resolved project's id (created if it didn't exist)."),
], widths=(1.7, 4.6)))

h2("2.2 guard.monitor — attach to an agent function")
sig_raw("""guard.monitor(
    func=None, *,
    policy: Policy | None = None,
    evaluators: list[Evaluator] | None = None,
    llm_judge: bool | AsyncEvaluator | None = True,
    agent_version: str | None = None,
)""")
p("Identical to the standalone <font name='Courier'>agentguard.monitor</font> decorator "
  "(§3) — it reuses that exact same execution pipeline — except every run it produces is "
  "automatically tagged with this client's authenticated <font name='Courier'>workspace_id</font> "
  "/ <font name='Courier'>project_id</font>, and the agent's function name is auto-registered "
  "as an Agent in your workspace the first time it runs. See §3 for the full parameter reference "
  "— it is the same for both forms.")
story.append(code_block("""guard = AgentGuard(api_key=os.getenv("AGENTGUARD_API_KEY"), project="production")

@guard.monitor(policy=Policy(max_cost=60000), agent_version="v3")
async def refund_agent(order_id: str) -> str:
    ...
"""))

h2("2.3 Async-context constraint")
note("<font name='Courier'>AgentGuard(...)</font> deliberately raises rather than silently "
     "hopping threads when called from a running event loop, because a production "
     "PostgreSQL connection pool is bound to the loop it was created on — silently sharing it "
     "across threads could corrupt it. Construct the client once, synchronously, before your "
     "async code starts:")
story.append(code_block("""# Correct
guard = AgentGuard(api_key=KEY, project="production")   # module scope, or top of main()

async def main():
    @guard.monitor
    async def agent(): ...
    await agent()

asyncio.run(main())

# Wrong — raises RuntimeError
async def main():
    guard = AgentGuard(api_key=KEY)   # <- constructed INSIDE the running loop
"""))

story.append(PageBreak())

# --------------------------------------------------------------------- #
# 3. monitor decorator
# --------------------------------------------------------------------- #

h1("3. @monitor — the Core Decorator")
p("<font name='Courier-Bold'>agentguard.monitor</font> (standalone) and "
  "<font name='Courier-Bold'>guard.monitor</font> (authenticated) wrap any agent function — "
  "sync or async — with the full reliability pipeline: run tracing, state capture, "
  "constraint/LLM evaluation, causal root-cause analysis, dynamic risk scoring, a "
  "CONTINUE / RETRY / REPLAN / HUMAN / STOP decision, per-step checkpoints, and a "
  "SHA-256 hash-chained audit trail — with zero required changes to your function's own logic.")

h2("3.1 Signature")
sig_raw("""@monitor
@monitor(
    policy: Policy | None = None,
    evaluators: list[Evaluator] | None = None,
    llm_judge: bool | AsyncEvaluator | None = True,
    agent_version: str | None = None,
)
def your_agent(...): ...          # sync or async, unchanged signature/return value""")

story.append(param_table([
    ("policy", "Policy()", "Declarative safety boundary for this agent — max cost, forbidden "
                           "actions, approval-gated actions, retry/replan bounds. See §4."),
    ("evaluators", "[ConstraintAdherenceEvaluator()]",
     "List of Evaluator instances to score each run. Pass your own to add custom, "
     "domain-specific scoring — see §8."),
    ("llm_judge", "True", "True runs the built-in LLM-as-Judge evaluator as a background task "
                          "after the run (never on the critical path). False disables it. Pass "
                          "a custom AsyncEvaluator instance to use your own judge/provider."),
    ("agent_version", "None", "Optional label (e.g. \"v2\", \"prompt-B\") stamped on every run — "
                              "used by Regression Comparison / A-B analysis on the dashboard. "
                              "Purely informational."),
]))

h2("3.2 What it guarantees")
bullets([
    "<b>Never changes your return value or swallows your exceptions</b> — every exception your "
    "function raises still propagates to the caller after being recorded, except the control-flow "
    "signals below, which the decorator interprets itself.",
    "<b>Sync and async both supported.</b> A sync function is internally run via "
    "<font name='Courier'>asyncio.run(...)</font> — so it cannot itself already be inside a "
    "running loop, and cannot call the async-only primitives (perform_action/request_approval). "
    "Define your agent as <font name='Courier'>async def</font> if you need those.",
    "<b>Backward compatible</b> — a plain <font name='Courier'>@monitor</font> with no arguments "
    "behaves exactly like the framework's original release: nothing here is opt-out, everything "
    "extra is opt-in from your agent's own code.",
])

h2("3.3 The execution loop your function can drive")
p("From inside a monitored function, you can raise these to control what AgentGuard does next "
  "(all defined in <font name='Courier'>agentguard.errors</font> — full reference in §6):")
story.append(quick_table([
    ("raise TransientError(msg)", "Bounded RETRY — the SAME call is retried, up to Policy.retry_limit times."),
    ("raise ReplanRequested(reason)", "Bounded REPLAN — your function is called again with a fresh attempt "
                                       "budget; agentguard.get_replan_context() returns your reason/context "
                                       "on that next call."),
    ("await agentguard.perform_action(name, **evidence)", "Synchronous policy guardrail + optional live "
                                                            "human-approval gate for one named action — see §5."),
], widths=(2.6, 3.7)))

h2("3.4 Full example")
story.append(code_block("""from agentguard import monitor, Policy
from agentguard.errors import TransientError, ReplanRequested

@monitor(policy=Policy(max_cost=60000, retry_limit=2, max_replans=1))
async def research_agent(query: str) -> str:
    try:
        result = call_search_api(query)
    except TimeoutError as exc:
        raise TransientError(f"search API timed out: {exc}")

    if not result:
        raise ReplanRequested("search returned nothing, trying a narrower query")

    return summarize(result)
"""))

story.append(PageBreak())

# --------------------------------------------------------------------- #
# 4. Policy
# --------------------------------------------------------------------- #

h1("4. Policy — Declarative Safety Boundaries")
p("<font name='Courier-Bold'>class agentguard.Policy</font> (a Pydantic model) — the safety "
  "contract you attach to a monitored agent via <font name='Courier'>@monitor(policy=...)</font>. "
  "Every field has a safe default, so <font name='Courier'>Policy()</font> alone is valid.")

story.append(param_table([
    ("max_cost", "None", "A numeric budget ceiling. The default evaluator (ConstraintAdherenceEvaluator) "
                        "checks this against your agent's reported state (see AgentState's "
                        "max_budget field, §5)."),
    ("require_approval", "[]", "List of action names that must be approved by a human before "
                               "proceeding — see agentguard.perform_action (§5)."),
    ("forbidden_actions", "[]", "List of action names that immediately raise "
                                "ForbiddenActionError the instant your agent attempts them — "
                                "no human round-trip, purely local/synchronous."),
    ("default_on_uncertain", '"stop"', "\"continue\" | \"stop\" | \"human\" — fallback behavior "
                                       "when an evaluator's own confidence is too low to trust its "
                                       "verdict."),
    ("retry_limit", "2", "Max RETRY transitions (from TransientError) before falling through "
                        "to on_retry_exhausted."),
    ("on_retry_exhausted", '"replan"', "\"replan\" | \"stop\" — what happens once retry_limit is hit."),
    ("max_replans", "1", "Max REPLAN transitions (from ReplanRequested, or from exhausted "
                        "retries if on_retry_exhausted=\"replan\") before the run is STOPped."),
    ("human_timeout_s", "120.0", "Seconds to wait for a human approval decision before treating "
                                "it as a denial. The agent never hangs forever."),
    ("on_tool_exhausted", '"replan"', "\"replan\" | \"human\" — what agentguard.call_tool() does "
                                      "when a tool and its fallback both fail (§7)."),
    ("version", "1", "Increment by hand whenever you change what a named policy means. Every "
                    "audit event for a run permanently records the policy version active when "
                    "that run started."),
]))

h2("Example")
story.append(code_block("""from agentguard import Policy

policy = Policy(
    max_cost=60000,
    forbidden_actions=["drop_database", "wire_transfer_over_10k"],
    require_approval=["refund", "send_customer_email"],
    default_on_uncertain="human",
    retry_limit=3,
    max_replans=2,
    human_timeout_s=90,
)
"""))

story.append(PageBreak())

# --------------------------------------------------------------------- #
# 5. State + actions
# --------------------------------------------------------------------- #

h1("5. Reporting State &amp; Guarded Actions")
p("These functions operate on an ambient \"current run\" context — call them from anywhere "
  "inside a function wrapped by <font name='Courier'>@monitor</font> / "
  "<font name='Courier'>@guard.monitor</font>, with no object to thread through your own code. "
  "Calling any of them outside a monitored run raises <font name='Courier'>RuntimeError</font>.")

h2("5.1 get_state()")
sig_raw("agentguard.get_state() -> AgentState")
p("Returns the mutable state object for the run currently executing. Read any field you or "
  "AgentGuard has set (e.g. after a <font name='Courier'>Policy(max_cost=...)</font>, "
  "<font name='Courier'>state.max_budget</font> is pre-seeded for you).")

h2("5.2 update_state(**kwargs)")
sig_raw("agentguard.update_state(**kwargs) -> None")
p("Merges the given keyword arguments into the run's state, AND records a new, timestamped "
  "state snapshot (S1, S2, S3, ...) that the causal Root-Cause Engine diffs if the run later "
  "fails a constraint — this is how AgentGuard tells you exactly which step introduced a "
  "problem.")
story.append(code_block("""from agentguard import update_state

@monitor(policy=Policy(max_cost=60000))
def budget_agent(task):
    update_state(max_budget=60000, spent_so_far=0)
    for step in plan(task):
        cost = execute(step)
        update_state(spent_so_far=get_state().spent_so_far + cost)
    return "done"
"""))

h2("5.3 reset_state(**kwargs)")
sig_raw("agentguard.reset_state(**kwargs) -> None")
p("Replaces the ENTIRE state with a fresh one (only the given kwargs survive) — models a real "
  "failure mode where an agent's working memory is silently truncated or clobbered between "
  "steps (e.g. a context-window trim). Also records a new snapshot.")

h2("5.4 record_tokens(...)")
sig_raw("""agentguard.record_tokens(
    *, model_name: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: float | None = None,
) -> None""")
p("Reports REAL LLM token usage/cost for the Tokens &amp; Cost dashboard page. Call this only "
  "with numbers you actually have from your LLM provider's response — there is no built-in "
  "estimate. A run that never calls this simply shows \"N/A\" on the dashboard, never a "
  "fabricated number. Multiple calls in the same run accumulate (input/output tokens and cost "
  "sum; model_name is overwritten by the latest call).")
story.append(code_block("""response = openai_client.chat.completions.create(...)
agentguard.record_tokens(
    model_name="gpt-4o",
    input_tokens=response.usage.prompt_tokens,
    output_tokens=response.usage.completion_tokens,
    cost_usd=estimate_cost(response.usage),
)
"""))

h2("5.5 perform_action(action, **evidence)")
sig_raw("agentguard.perform_action(action: str, **evidence) -> PolicyFinding | None")
p("Declares that your agent is about to take a named action, in two steps: (1) an immediate, "
  "local, synchronous check against <font name='Courier'>Policy.forbidden_actions</font> — "
  "raises <font name='Courier'>ForbiddenActionError</font> right away if listed, with no I/O; "
  "(2) if the action is listed in <font name='Courier'>Policy.require_approval</font>, "
  "<i>awaits</i> a live human decision (see 5.6) before returning. Must be awaited — only "
  "usable from an <font name='Courier'>async def</font> agent.")
story.append(code_block("""from agentguard import perform_action
from agentguard.errors import ForbiddenActionError, HumanRejected

@monitor(policy=Policy(
    forbidden_actions=["drop_database"],
    require_approval=["refund"],
))
async def support_agent(ticket):
    if ticket.wants_refund:
        try:
            await perform_action("refund", amount=ticket.amount, customer=ticket.customer_id)
        except HumanRejected:
            return "refund denied by reviewer"
        issue_refund(ticket)
    return "handled"
"""))

h2("5.6 request_approval(...) — low-level primitive")
sig_raw("""agentguard.request_approval(
    action: str, *, finding: PolicyFinding | None = None,
    reason: str = "", evidence: dict | None = None,
) -> HumanDecision""")
p("What <font name='Courier'>perform_action</font> calls internally to publish a pending "
  "approval request to the dashboard/WebSocket channel and await a human's response, subject "
  "to <font name='Courier'>Policy.human_timeout_s</font>. Raises "
  "<font name='Courier'>HumanRejected</font> on rejection or timeout (timeout always denies — "
  "the agent never hangs indefinitely), raises "
  "<font name='Courier'>HumanReplanRequested</font> if the reviewer asks for a re-plan instead, "
  "or returns the resolved <font name='Courier'>HumanDecision</font> on approval. Most agent "
  "code should call <font name='Courier'>perform_action</font> instead — use this directly only "
  "if you need a human gate with no policy-forbidden check attached to it.")

h2("5.7 get_replan_context()")
sig_raw("agentguard.get_replan_context() -> dict")
p("On the invocation of your agent function that follows a REPLAN transition, returns the "
  "reason/context that triggered it (empty dict on a run's first attempt) — read this at the "
  "top of your function to change your plan instead of blindly repeating it.")
story.append(code_block("""@monitor(policy=Policy(max_replans=2))
async def planner_agent(task):
    ctx = agentguard.get_replan_context()
    if ctx:
        task = f"{task} (avoid: {ctx.get('reason')})"
    ...
"""))

story.append(PageBreak())

# --------------------------------------------------------------------- #
# 6. Errors
# --------------------------------------------------------------------- #

h1("6. Exceptions Your Agent Code Raises")
p("Import from <font name='Courier'>agentguard.errors</font>. These are how your agent "
  "communicates a non-success outcome to the Decision Engine — raise them from inside a "
  "monitored function instead of returning a special value or swallowing the failure yourself.")

story.append(quick_table([
    ("TransientError(message, **evidence)", "\"Retry me.\" Caught by @monitor's RETRY path, "
                                             "bounded by Policy.retry_limit. Extra kwargs are "
                                             "stored as structured evidence on the audit trail."),
    ("ReplanRequested(reason, *, context=None)", "\"My plan is no longer valid.\" Caught by "
                                                  "@monitor's REPLAN path, bounded by "
                                                  "Policy.max_replans. context is handed back via "
                                                  "get_replan_context() on the next attempt."),
    ("ForbiddenActionError(action)", "Raised automatically by perform_action() for an action "
                                      "listed in Policy.forbidden_actions — you normally don't "
                                      "raise this yourself, but you may catch it."),
    ("HumanRejected(action, reason)", "Raised by perform_action()/request_approval() when a "
                                       "human denies the action (or it times out). Catch this to "
                                       "handle a denied action gracefully instead of letting the "
                                       "run STOP."),
    ("HumanReplanRequested(action, reason)", "A subclass of ReplanRequested raised when a human "
                                              "reviewer asks for a re-plan instead of approving/"
                                              "denying — handled by the same REPLAN path."),
], widths=(2.5, 3.8)))

story.append(PageBreak())

# --------------------------------------------------------------------- #
# 7. Tool calls
# --------------------------------------------------------------------- #

h1("7. Tool Calls &amp; Reliability")
p("Optional, advanced: wrap your agent's tool invocations through AgentGuard's tool registry "
  "to get per-tool reliability tracking (success rate, latency, timeout rate) and automatic "
  "fallback to an explicitly registered alternative tool when the primary tool is failing.")

h2("7.1 Register your tools once at startup")
sig_raw("""from agentguard.tools.registry import get_registry

get_registry().register_tool(name: str, fn: Callable) -> None
get_registry().register_alternative(
    primary: str, fallback: str, *, reliability_threshold: float = 0.8,
) -> ToolAlternative""")
story.append(code_block("""from agentguard.tools.registry import get_registry

registry = get_registry()
registry.register_tool("search_primary", call_primary_search_api)
registry.register_tool("search_backup", call_backup_search_api)
registry.register_alternative("search_primary", "search_backup", reliability_threshold=0.8)
"""))
note("AgentGuard never substitutes a tool that wasn't explicitly registered as an alternative — "
     "there is no automatic/implicit fallback to an arbitrary tool.")

h2("7.2 Call a registered tool from your agent")
sig_raw("""from agentguard.context import call_tool

await call_tool(
    tool_name: str, *args,
    primary_attempts: int = 1,
    timeout_s: float | None = None,
    **kwargs,
) -> Any""")
p("Invokes the named tool, recording latency/outcome/duplicate-call detection for every "
  "attempt. If the primary tool fails <font name='Courier'>primary_attempts</font> times, "
  "AgentGuard checks the tool's historical reliability profile; if a fallback is registered "
  "and the primary is unreliable or has insufficient history, it tries the fallback once. If "
  "both fail (or no fallback exists), it raises <font name='Courier'>ReplanRequested</font> — "
  "or escalates to a human approval, per <font name='Courier'>Policy.on_tool_exhausted</font> — "
  "so your existing REPLAN/HUMAN handling in the monitored function takes over; it never "
  "invents a new control-flow path.")
story.append(code_block("""from agentguard.context import call_tool

@monitor(policy=Policy(on_tool_exhausted="replan", max_replans=1))
async def search_agent(query):
    results = await call_tool("search_primary", query, primary_attempts=2, timeout_s=5.0)
    return summarize(results)
"""))

story.append(PageBreak())

# --------------------------------------------------------------------- #
# 8. Custom evaluators
# --------------------------------------------------------------------- #

h1("8. Custom Evaluators")
p("Every run is scored by a list of <font name='Courier'>Evaluator</font> instances (default: "
  "just the built-in <font name='Courier'>ConstraintAdherenceEvaluator</font>). Add your own "
  "domain-specific scoring by subclassing and passing it in via "
  "<font name='Courier'>@monitor(evaluators=[...])</font>.")

sig_raw("""from agentguard.evaluators.base import Evaluator
from agentguard import EvalResult, Run

class MyEvaluator(Evaluator):
    name = "my_custom_check"

    def evaluate(self, run: Run) -> EvalResult:
        # inspect run.initial_state / run.final_state / run.policy / run.actions
        passed = ...
        return EvalResult(
            evaluator=self.name, passed=passed, score=1.0 if passed else 0.0,
            label="pass" if passed else "fail", confidence=0.9, reason="...",
        )""")

story.append(code_block("""from agentguard import monitor, Policy
from agentguard.evaluators.rule_based import ConstraintAdherenceEvaluator

@monitor(policy=Policy(max_cost=60000), evaluators=[
    ConstraintAdherenceEvaluator(),   # keep the built-in check too
    MyEvaluator(),
])
def my_agent(task): ...
"""))
p("This runs synchronously as part of the monitored call. For a slower, asynchronous check "
  "(e.g. an LLM-as-judge call to a model), pass your own "
  "<font name='Courier'>AsyncEvaluator</font> via the decorator's "
  "<font name='Courier'>llm_judge=</font> parameter instead — it runs as a background task and "
  "never blocks your agent's return value; if it later flags the run unsafe, AgentGuard records "
  "a fresh STOP decision after the fact rather than mutating the original one.")

story.append(PageBreak())

# --------------------------------------------------------------------- #
# 9. Recovery (advanced)
# --------------------------------------------------------------------- #

h1("9. Recovery &amp; Rollback (Advanced)")
p("Rollback and replay are dashboard/operator-invoked operations, not something your agent's "
  "own code calls during a normal run. If you are building an automated recovery workflow "
  "around AgentGuard (rather than using the dashboard's Rollback button), the primitive your "
  "own script uses to start a fresh, recovered run is:")
sig_raw("""from agentguard.recovery.seed import seed_recovery_state

seed_recovery_state(
    state: dict, *, parent_run_id: str | None = None, checkpoint_id: str | None = None,
) -> None""")
story.append(code_block("""result = await agentguard.recovery.rollback(repository, run_id, "S2")
agentguard.recovery.seed_recovery_state(
    result.restored_state, parent_run_id=run_id, checkpoint_id=result.checkpoint.id,
)
recovered = await safe_agent("retry the task")   # a normal @monitor call, seeded from S2
"""))
note("This is a one-shot signal consumed by the very next @monitor call on the current context "
     "— it never leaks into an unrelated later run, and a normal call made without seeding is "
     "completely unaffected.")

story.append(PageBreak())

# --------------------------------------------------------------------- #
# 10. Quick reference
# --------------------------------------------------------------------- #

h1("10. Quick Reference — Everything At a Glance")

h2("Import from agentguard (top-level, everyday use)")
story.append(quick_table([
    ("AgentGuard(api_key=None, project=\"production\")", "Authenticated client — construct once, use .monitor."),
    ("AuthenticationError", "Raised by AgentGuard() for a missing/invalid/revoked key."),
    ("monitor", "The core decorator — standalone, single-tenant form."),
    ("Policy(...)", "Declarative safety boundary passed to monitor(policy=...)."),
    ("get_state()", "Read the current run's mutable state."),
    ("update_state(**kwargs)", "Merge into state + record a new diffable snapshot."),
    ("reset_state(**kwargs)", "Replace the entire state + record a new snapshot."),
    ("record_tokens(...)", "Report real LLM token/cost usage for this run."),
    ("perform_action(action, **evidence)", "Guardrail check + optional human-approval gate."),
    ("request_approval(...)", "Low-level human-approval primitive."),
    ("get_replan_context()", "What the last REPLAN handed back to this attempt."),
    ("wait_for_background_tasks()", "Test/CLI helper: await pending background LLM-judge tasks."),
    ("configure(repository)", "Advanced/test-only: point the SDK at a specific storage backend."),
], widths=(2.9, 3.4)))

h2("Import from agentguard.errors")
story.append(quick_table([
    ("TransientError(message, **evidence)", "Raise for a bounded RETRY."),
    ("ReplanRequested(reason, context=None)", "Raise for a bounded REPLAN."),
    ("ForbiddenActionError(action)", "Raised internally by perform_action(); catchable."),
    ("HumanRejected(action, reason)", "Raised when a human denies / a request times out."),
    ("HumanReplanRequested(action, reason)", "Raised when a human asks for a re-plan."),
], widths=(2.9, 3.4)))

h2("Other advanced imports")
story.append(quick_table([
    ("agentguard.context.call_tool(...)", "Reliability-tracked tool invocation with fallback."),
    ("agentguard.tools.registry.get_registry()", "Register tools/alternatives at startup."),
    ("agentguard.evaluators.base.Evaluator", "Base class for a custom evaluator."),
    ("agentguard.recovery.seed.seed_recovery_state(...)", "Seed the next run from a checkpoint."),
    ("agentguard.EvalResult / Decision / RiskAssessment / RootCause / HumanDecision / StateSnapshot / AgentState / RunStatus",
     "Typed data models you may read from run results, evaluators, or the dashboard API — you "
     "do not usually construct these yourself except EvalResult in a custom evaluator."),
], widths=(2.9, 3.4)))

spacer(10)
hr()
p("<i>Generated from the current agentguard package source "
  "(agentguard/__init__.py, client.py, decorator.py, context.py, models.py, errors.py, "
  "tools/registry.py, evaluators/base.py, recovery/seed.py). "
  "Everything shown here is real, shipped, and covered by the project's automated test suite — "
  "no placeholder or planned-but-unbuilt functionality is included.</i>")

# --------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------- #


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#8a93a3"))
    canvas.drawString(0.75 * inch, 0.5 * inch, "AgentGuard SDK Reference")
    canvas.drawRightString(LETTER[0] - 0.75 * inch, 0.5 * inch, f"Page {doc.page}")
    canvas.restoreState()


doc = SimpleDocTemplate(
    OUT_PATH, pagesize=LETTER,
    leftMargin=0.75 * inch, rightMargin=0.75 * inch,
    topMargin=0.75 * inch, bottomMargin=0.75 * inch,
    title="AgentGuard SDK Reference", author="AgentGuard",
)
doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
print(f"Wrote {os.path.abspath(OUT_PATH)}")
