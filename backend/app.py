from __future__ import annotations

import asyncio
import ast
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Literal
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from pydantic import BaseModel, Field

try:
    from agent_framework import Agent
    from agent_framework.foundry import FoundryChatClient
    from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
except ImportError:  # Allows local UI demos before Python dependencies are installed.
    Agent = None
    FoundryChatClient = None
    DefaultAzureCredential = None
    ManagedIdentityCredential = None

load_dotenv()
logger = logging.getLogger("design-thinking-council")


AgentKind = Literal[
    "facilitator",
    "researcher",
    "framer",
    "ideation",
    "prototype",
    "validation",
    "business",
    "technical",
    "critic",
    "ethics",
]
StickyKind = Literal["Insight", "Question", "Risk", "Feature", "Metric", "Decision"]
MessageKind = Literal["contribution", "critique", "debate", "revision", "synthesis", "approval"]

EVIDENCE_BOUNDARY = (
    "Evidence boundary: Treat supplied user facts and explicit transcript observations as observed evidence. "
    "Treat agent inferences as working hypotheses or assumptions to validate. Do not call inferred material "
    "validated unless supplied evidence supports it."
)


class AgentDefinition(BaseModel):
    id: AgentKind
    role: str
    focus: str
    icon: Literal["compass", "heart", "chart", "code", "spark", "shield", "cube", "target"]
    instructions: str


class Sticky(BaseModel):
    id: str
    phase: int
    agentId: AgentKind
    kind: StickyKind
    text: str
    x: float
    y: float
    size: Literal["standard", "wide"] = "standard"


class AgentMessage(BaseModel):
    phase: int
    agentId: AgentKind
    text: str
    kind: MessageKind = "contribution"
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    meta: dict[str, Any] = Field(default_factory=dict)


class BlackboardState(BaseModel):
    assumptions: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    concepts: list[str] = Field(default_factory=list)
    objections: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    selectedConcept: str | None = None


class WorkshopEvent(BaseModel):
    type: Literal["phase", "message", "sticky", "blackboard", "brief", "done", "error", "cancelled"]
    runId: str | None = None
    traceId: str | None = None
    phase: int | None = None
    title: str | None = None
    message: AgentMessage | None = None
    sticky: Sticky | None = None
    blackboard: BlackboardState | None = None
    markdown: str | None = None
    error: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class WorkshopRequest(BaseModel):
    idea: str = Field(min_length=1, max_length=4000)


class AgentOutput(BaseModel):
    speech: str
    stickies: list[dict[str, Any]] = Field(default_factory=list)


class ApprovalOutput(BaseModel):
    speech: str
    decision: Literal["approve", "object"]
    rationale: str


class CritiqueOutput(BaseModel):
    speech: str
    issue: str
    recommendation: str


class DebateResponse(BaseModel):
    objection: str
    disposition: Literal["accept", "qualify", "reject"]
    response: str
    action: str


class DebateOutput(BaseModel):
    speech: str
    responses: list[DebateResponse] = Field(default_factory=list)


class RunRecord(BaseModel):
    id: str
    traceId: str
    idea: str
    status: Literal["running", "completed", "cancelled", "error"]
    startedAt: str
    updatedAt: str
    activePhase: int = 0
    eventCount: int = 0
    error: str | None = None
    selectedConcept: str | None = None
    selectedConceptStickyId: str | None = None
    latestBlackboard: BlackboardState = Field(default_factory=BlackboardState)
    finalBrief: str | None = None
    events: list[WorkshopEvent] = Field(default_factory=list)


class ConceptSelectionRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    stickyId: str | None = Field(default=None, max_length=120)


AGENTS: list[AgentDefinition] = [
    AgentDefinition(
        id="facilitator",
        role="Facilitator",
        focus="Keeps the process iterative and decision-ready",
        icon="compass",
        instructions=(
            "You are the workshop facilitator. Keep the standard design-thinking process moving, "
            "synthesize patterns, and make iteration decisions clear. Do not use a personal name."
        ),
    ),
    AgentDefinition(
        id="researcher",
        role="User researcher",
        focus="Context, behavior, needs, and pain points",
        icon="heart",
        instructions=(
            "Lead the Empathize stage. Focus on user context, observed behavior, needs, pain "
            "points, workarounds, and emotions."
        ),
    ),
    AgentDefinition(
        id="framer",
        role="Problem framer",
        focus="Insights, problem statements, and How Might We questions",
        icon="compass",
        instructions=(
            "Lead the Define stage. Convert user observations into insights, problem statements, "
            "design criteria, and How Might We questions."
        ),
    ),
    AgentDefinition(
        id="ideation",
        role="Ideation lead",
        focus="Divergent concepts and opportunity areas",
        icon="spark",
        instructions=(
            "Lead the Ideate stage. Generate three to four visibly divergent solution directions "
            "before converging, including practical, bold, and low-tech or manual alternatives."
        ),
    ),
    AgentDefinition(
        id="prototype",
        role="Prototype designer",
        focus="Tangible concepts, journeys, and MVP shape",
        icon="cube",
        instructions=(
            "Lead the Prototype stage. Make the concept tangible through journeys, rough flows, "
            "storyboards, MVP scopes, or clickable prototype ideas."
        ),
    ),
    AgentDefinition(
        id="validation",
        role="Validation lead",
        focus="Tests, assumptions, feedback, and iteration plan",
        icon="chart",
        instructions=(
            "Lead the Test stage. Define assumptions, user tests, feedback signals, success "
            "metrics, and what iteration should happen next."
        ),
    ),
    AgentDefinition(
        id="business",
        role="Business viability",
        focus="Adoption, differentiation, strategic fit",
        icon="chart",
        instructions=(
            "Evaluate adoption, differentiation, willingness to use or pay, strategic fit, "
            "and market wedge. Be pragmatic and concise."
        ),
    ),
    AgentDefinition(
        id="technical",
        role="Technical feasibility",
        focus="Architecture, dependencies, build risk",
        icon="code",
        instructions=(
            "Evaluate implementation realism, architecture, data dependencies, integration "
            "risk, and what should be deferred from the MVP."
        ),
    ),
    AgentDefinition(
        id="critic",
        role="Design critic",
        focus="Clarity, friction, tradeoffs, edge cases",
        icon="target",
        instructions=(
            "Challenge the idea constructively. Surface unclear value, UX friction, fake "
            "consensus, edge cases, and hidden tradeoffs."
        ),
    ),
    AgentDefinition(
        id="ethics",
        role="Ethics and trust",
        focus="Consent, safety, privacy, user control",
        icon="shield",
        instructions=(
            "Identify safety, privacy, fairness, consent, transparency, and human-control "
            "risks. Recommend lightweight guardrails."
        ),
    ),
]

PHASES = [
    ("Empathize", "Understand users, context, behaviors, workarounds, needs, and emotions."),
    ("Define", "Synthesize research into insights, a problem statement, and How Might We questions."),
    ("Ideate", "Generate many solution directions before judging or converging."),
    ("Prototype", "Make the strongest idea tangible through a journey, sketch, MVP, or concept."),
    ("Test", "Evaluate the prototype, capture feedback, and decide how to iterate."),
]

PHASE_AGENTS: list[list[AgentKind]] = [
    ["researcher", "ethics"],
    ["framer", "critic"],
    ["ideation", "business", "technical"],
    ["prototype", "technical", "critic"],
    ["validation", "business", "ethics", "facilitator"],
]

REVIEW_AGENTS: list[AgentKind] = ["business", "technical", "critic", "ethics", "validation"]

PHASE_PRIMARY_AGENT: list[AgentKind] = ["researcher", "framer", "ideation", "prototype", "validation"]

PHASE_CRITIQUE_AGENTS: list[list[AgentKind]] = [
    ["critic", "ethics"],
    ["critic", "business"],
    ["business", "technical", "ethics"],
    ["technical", "critic"],
    ["business", "ethics"],
]

LAYOUT: dict[int, list[tuple[float, float]]] = {
    0: [(5, 10), (26, 10), (47, 10), (68, 10)],
    1: [(5, 27), (26, 27), (47, 27), (68, 27)],
    2: [(5, 42), (26, 42), (47, 42), (68, 42), (5, 48.5), (26, 48.5), (47, 48.5), (68, 48.5)],
    3: [(5, 61), (24, 61), (43, 61), (62, 61), (74, 61)],
    4: [(5, 78), (24, 78), (43, 78), (62, 78), (74, 78)],
}

RUNS: dict[str, RunRecord] = {}
CANCELLED_RUNS: set[str] = set()
RUN_TASKS: dict[str, asyncio.Task[None]] = {}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_run(idea: str) -> RunRecord:
    run_id = str(uuid.uuid4())
    record = RunRecord(
        id=run_id,
        traceId=str(uuid.uuid4()),
        idea=idea,
        status="running",
        startedAt=utc_now(),
        updatedAt=utc_now(),
    )
    RUNS[run_id] = record
    return record


def record_event(record: RunRecord, event: WorkshopEvent) -> WorkshopEvent:
    event.runId = record.id
    event.traceId = record.traceId
    record.updatedAt = utc_now()
    record.eventCount += 1
    event.meta["sequence"] = record.eventCount
    if event.phase is not None:
        record.activePhase = event.phase
    if event.blackboard is not None:
        record.latestBlackboard = event.blackboard
        record.selectedConcept = event.blackboard.selectedConcept or record.selectedConcept
    if event.markdown is not None:
        record.finalBrief = event.markdown
    record.events.append(event)
    if len(record.events) > 600:
        record.events = record.events[-600:]
    return event


def public_safe_slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:48] or "idea"


def create_blackboard(idea: str) -> BlackboardState:
    return BlackboardState(
        assumptions=[f"Initial idea needs validation: {sanitize_text(idea, 120)}"],
        evidence=[f"Supplied idea: {sanitize_text(idea, 120)}"],
        concepts=[],
        objections=[],
        decisions=[],
        selectedConcept=None,
    )


def _append_unique(items: list[str], value: str, limit: int = 10) -> None:
    text = sanitize_text(value, 180)
    if not text or text in items:
        return
    items.append(text)
    del items[:-limit]


def render_blackboard(blackboard: BlackboardState) -> str:
    def lines(title: str, values: list[str]) -> str:
        body = "\n".join(f"- {item}" for item in values[-6:]) or "- None captured yet"
        return f"{title}:\n{body}"

    return "\n\n".join(
        [
            EVIDENCE_BOUNDARY,
            f"Selected concept for Prototype:\n- {blackboard.selectedConcept or 'None selected yet'}",
            lines("Working hypotheses and assumptions to validate", blackboard.assumptions),
            lines("Observed evidence from supplied material", blackboard.evidence),
            lines("Concepts under consideration", blackboard.concepts),
            lines("Concrete objections", blackboard.objections),
            lines("Decisions carried forward", blackboard.decisions),
        ]
    )


def update_blackboard_after_phase(
    blackboard: BlackboardState,
    phase_name: str,
    contribution_messages: list[AgentMessage],
    phase_stickies: list[Sticky],
    critiques: list[CritiqueOutput],
    debate: DebateOutput | None,
    revision: AgentMessage | None,
    decision_text: str,
) -> BlackboardState:
    for message in contribution_messages:
        _append_unique(blackboard.assumptions, f"Working hypothesis from {phase_name} {message.agentId}: {message.text}")

    for sticky in phase_stickies:
        if sticky.kind in {"Question", "Risk"}:
            _append_unique(blackboard.assumptions, f"{phase_name}: {sticky.text}")
        elif sticky.kind in {"Insight", "Metric"}:
            _append_unique(blackboard.assumptions, f"Working hypothesis from {phase_name}: {sticky.text}")
        elif sticky.kind == "Feature":
            _append_unique(blackboard.concepts, f"{phase_name}: {sticky.text}")
        elif sticky.kind == "Decision":
            _append_unique(blackboard.decisions, f"{phase_name}: {sticky.text}")

    for critique in critiques:
        _append_unique(
            blackboard.objections,
            f"{phase_name}: {critique.issue} Recommended action: {critique.recommendation}",
        )

    if debate:
        for response in debate.responses:
            _append_unique(
                blackboard.decisions,
                f"{phase_name} debate {response.disposition}: {response.action}",
            )

    if revision:
        _append_unique(blackboard.concepts, f"{phase_name} revision: {revision.text}")

    _append_unique(blackboard.decisions, f"{phase_name}: {decision_text}")
    return blackboard


def update_blackboard_after_approvals(
    blackboard: BlackboardState,
    approvals: list[ApprovalOutput],
) -> BlackboardState:
    for approval in approvals:
        target = blackboard.objections if approval.decision == "object" else blackboard.decisions
        _append_unique(target, f"Final {approval.decision}: {approval.rationale}")
    return blackboard


def is_mock_mode() -> bool:
    return os.getenv("MOCK_AGENTS", "").lower() in {"1", "true", "yes"} or Agent is None


def build_prompt(
    agent: AgentDefinition,
    idea: str,
    phase_index: int,
    transcript: list[AgentMessage],
    blackboard: BlackboardState,
) -> str:
    phase_name, phase_objective = PHASES[phase_index]
    prior = "\n".join(f"- {item.agentId}: {item.text}" for item in transcript[-8:])
    blackboard_notes = render_blackboard(blackboard)
    is_ideation_lead = agent.id == "ideation" and phase_name == "Ideate"
    sticky_rule = (
        "Create 3 to 4 Feature stickies. Each sticky must be a distinct concept direction. "
        "Visibly label at least one Practical, one Bold, and one Low tech or Manual alternative."
        if is_ideation_lead
        else "Create exactly 1 sticky."
    )
    ideation_rule = (
        "Do not converge yet; make the alternatives different in delivery model, user effort, or technology level."
        if is_ideation_lead
        else ""
    )
    return f"""
You are the {agent.role} agent in a design-thinking whiteboard workshop.

User's idea, which all output must directly address:
{idea}

Current phase: {phase_name}
Phase objective: {phase_objective}

Prior workshop notes:
{prior or "- None yet"}

Structured swarm blackboard carried from prior work:
{blackboard_notes}

{EVIDENCE_BOUNDARY}

Return only valid JSON in this exact shape:
{{
  "speech": "one concise spoken contribution, max 35 words",
  "stickies": [
    {{
      "kind": "Insight | Question | Risk | Feature | Metric | Decision",
      "text": "one atomic sticky note, 3 to 8 words",
      "size": "standard | wide"
    }}
  ]
}}

{sticky_rule} Stay in role. Do not invent private customer, tenant, or company details.
{ideation_rule}
Sticky text must be short like a real workshop sticky: 3 to 8 words, no sentence, no period.
Do not give generic design-thinking advice.
Every "speech" and sticky "text" must include at least one concrete noun, user, workflow, constraint, or outcome from the user's idea.
Use the blackboard explicitly: preserve decisions, test assumptions, add observed evidence only when supplied, advance concepts, or reduce listed objections.
If this is Prototype, prototype the selected concept shown on the blackboard; if none is selected, use the strongest Ideate concept as a fallback and name it as a fallback.
If the user's idea is underspecified, ask a specific question about that idea instead of filling the gap with generic advice.
Use plain ASCII punctuation only. Do not use em dashes, smart quotes, or personal names.
""".strip()


def build_synthesis_prompt(
    idea: str,
    phase_index: int,
    phase_messages: list[AgentMessage],
    critiques: list[CritiqueOutput],
    debate: DebateOutput | None,
    revision: AgentMessage | None,
    prior_decisions: list[str],
    blackboard: BlackboardState,
) -> str:
    phase_name, phase_objective = PHASES[phase_index]
    phase_notes = "\n".join(f"- {item.agentId}: {item.text}" for item in phase_messages)
    critique_notes = "\n".join(
        f"- issue: {item.issue}; recommendation: {item.recommendation}" for item in critiques
    )
    debate_notes = "\n".join(
        f"- {item.disposition}: {item.objection} => {item.response}; action: {item.action}"
        for item in (debate.responses if debate else [])
    )
    decisions = "\n".join(f"- {decision}" for decision in prior_decisions)
    blackboard_notes = render_blackboard(blackboard)
    return f"""
You are the Facilitator in an advanced multi-agent design-thinking council.

User's idea:
{idea}

Current phase: {phase_name}
Phase objective: {phase_objective}

Phase contributions:
{phase_notes or "- None"}

Swarm critiques:
{critique_notes or "- None"}

Claim-level debate responses:
{debate_notes or "- None"}

Stage lead revision:
{revision.text if revision else "- None"}

Prior phase decisions:
{decisions or "- None"}

Structured swarm blackboard:
{blackboard_notes}

{EVIDENCE_BOUNDARY}

Synthesize the phase into one concrete decision that advances the workshop.
Do not summarize generically. Decide what the council learned, chose, revised, or must carry forward for this specific idea.
Resolve the current phase using the blackboard, the concrete objections, and the stage lead's claim-level responses.
The decision sticky must be 3 to 8 words, no sentence, no period.

Return only valid JSON:
{{
  "speech": "one concise facilitator synthesis, max 40 words",
  "stickies": [
    {{
      "kind": "Decision",
      "text": "one concrete phase decision, 3 to 8 words",
      "size": "wide"
    }}
  ]
}}

Use plain ASCII punctuation only. Do not use em dashes, smart quotes, or personal names.
""".strip()


def build_critique_prompt(
    agent: AgentDefinition,
    idea: str,
    phase_index: int,
    phase_messages: list[AgentMessage],
    prior_decisions: list[str],
    blackboard: BlackboardState,
) -> str:
    phase_name, phase_objective = PHASES[phase_index]
    phase_notes = "\n".join(f"- {item.agentId}: {item.text}" for item in phase_messages)
    decisions = "\n".join(f"- {decision}" for decision in prior_decisions)
    blackboard_notes = render_blackboard(blackboard)
    return f"""
You are the {agent.role} in an autonomous design-thinking swarm.

User's idea:
{idea}

Current phase: {phase_name}
Phase objective: {phase_objective}

Phase outputs to critique:
{phase_notes}

Prior phase decisions:
{decisions or "- None"}

Structured swarm blackboard:
{blackboard_notes}

{EVIDENCE_BOUNDARY}

Critique this phase output from your role. Focus on one concrete weakness, missing perspective, contradiction, or risk.
Do not be generic. Your critique must directly reference the user's idea and the current phase output.
Use the blackboard to challenge unsupported assumptions, weak evidence, vague concepts, unresolved objections, or decisions that no longer fit.

Return only valid JSON:
{{
  "speech": "one concise critique comment, max 35 words",
  "issue": "specific issue, max 20 words",
  "recommendation": "specific improvement, max 20 words"
}}

Use plain ASCII punctuation only. Do not use em dashes, smart quotes, or personal names.
""".strip()


def build_debate_prompt(
    agent: AgentDefinition,
    idea: str,
    phase_index: int,
    phase_messages: list[AgentMessage],
    critiques: list[CritiqueOutput],
    prior_decisions: list[str],
    blackboard: BlackboardState,
) -> str:
    phase_name, phase_objective = PHASES[phase_index]
    phase_notes = "\n".join(f"- {item.agentId}: {item.text}" for item in phase_messages)
    critique_notes = "\n".join(
        f"- objection: {item.issue}; recommendation: {item.recommendation}" for item in critiques
    )
    decisions = "\n".join(f"- {decision}" for decision in prior_decisions)
    blackboard_notes = render_blackboard(blackboard)
    return f"""
You are the {agent.role}, the phase lead for {phase_name}, responding to critique before any revision.

User's idea:
{idea}

Phase objective:
{phase_objective}

Phase claims to defend or change:
{phase_notes}

Concrete objections to answer:
{critique_notes or "- None"}

Prior phase decisions:
{decisions or "- None"}

Structured swarm blackboard:
{blackboard_notes}

{EVIDENCE_BOUNDARY}

Respond at claim level. For each concrete objection, accept, qualify, or reject it, then state the action that should change the phase direction.
Do not smooth over disagreement. Keep the response specific to the idea, the current phase output, and the blackboard.

Return only valid JSON:
{{
  "speech": "one concise debate response summary, max 45 words",
  "responses": [
    {{
      "objection": "the objection being answered, max 20 words",
      "disposition": "accept | qualify | reject",
      "response": "specific claim-level answer, max 25 words",
      "action": "specific follow-up or revision action, max 25 words"
    }}
  ]
}}

Use plain ASCII punctuation only. Do not use em dashes, smart quotes, or personal names.
""".strip()


def build_revision_prompt(
    agent: AgentDefinition,
    idea: str,
    phase_index: int,
    phase_messages: list[AgentMessage],
    critiques: list[CritiqueOutput],
    debate: DebateOutput | None,
    prior_decisions: list[str],
    blackboard: BlackboardState,
) -> str:
    phase_name, phase_objective = PHASES[phase_index]
    phase_notes = "\n".join(f"- {item.agentId}: {item.text}" for item in phase_messages)
    critique_notes = "\n".join(
        f"- issue: {item.issue}; recommendation: {item.recommendation}" for item in critiques
    )
    debate_notes = "\n".join(
        f"- {item.disposition}: {item.objection} => {item.response}; action: {item.action}"
        for item in (debate.responses if debate else [])
    )
    decisions = "\n".join(f"- {decision}" for decision in prior_decisions)
    blackboard_notes = render_blackboard(blackboard)
    return f"""
You are the {agent.role}, the stage lead for {phase_name}, revising after swarm critique.

User's idea:
{idea}

Phase objective:
{phase_objective}

Original phase outputs:
{phase_notes}

Swarm critiques:
{critique_notes}

Claim-level debate responses:
{debate_notes or "- None"}

Prior phase decisions:
{decisions or "- None"}

Structured swarm blackboard:
{blackboard_notes}

{EVIDENCE_BOUNDARY}

Revise the phase direction so it is stronger, more specific, and ready for facilitator synthesis.
Incorporate accepted or qualified objections, preserve decisions that remain valid, and avoid reopening unrelated settled choices.
The revised sticky must be 3 to 8 words, no sentence, no period.

Return only valid JSON:
{{
  "speech": "one concise revised position, max 40 words",
  "stickies": [
    {{
      "kind": "Insight | Question | Risk | Feature | Metric | Decision",
      "text": "one revised takeaway, 3 to 8 words",
      "size": "wide"
    }}
  ]
}}

Use plain ASCII punctuation only. Do not use em dashes, smart quotes, or personal names.
""".strip()


def build_approval_prompt(
    agent: AgentDefinition,
    idea: str,
    phase_decisions: list[str],
    transcript: list[AgentMessage],
    blackboard: BlackboardState,
) -> str:
    decisions = "\n".join(f"- {decision}" for decision in phase_decisions)
    recent = "\n".join(f"- {item.agentId}: {item.text}" for item in transcript[-12:])
    blackboard_notes = render_blackboard(blackboard)
    return f"""
You are the {agent.role} reviewer in a design-thinking council.

User's idea:
{idea}

Phase decisions:
{decisions}

Recent transcript:
{recent}

Structured swarm blackboard:
{blackboard_notes}

{EVIDENCE_BOUNDARY}

Decide whether you approve the council direction or object before the final brief.
Object only for a specific unresolved issue in your role. Otherwise approve with a useful rationale.
Use the blackboard to check assumptions, evidence, concepts, objections, and decisions. Do not approve if a blocker remains unowned.

Return only valid JSON:
{{
  "speech": "Approve or object in one concise visible workshop comment, max 35 words",
  "decision": "approve | object",
  "rationale": "specific reason, max 25 words"
}}

Use plain ASCII punctuation only. Do not use em dashes, smart quotes, or personal names.
""".strip()


def build_final_brief_prompt(
    idea: str,
    phase_decisions: list[str],
    approvals: list[ApprovalOutput],
    transcript: list[AgentMessage],
    blackboard: BlackboardState,
) -> str:
    decisions = "\n".join(f"- {decision}" for decision in phase_decisions)
    review = "\n".join(f"- {item.decision}: {item.rationale}" for item in approvals)
    recent = "\n".join(f"- {item.agentId}: {item.text}" for item in transcript[-18:])
    blackboard_notes = render_blackboard(blackboard)
    return f"""
You are the Facilitator writing the final consensus artifact from a multi-agent design-thinking council.

User's idea:
{idea}

Phase decisions:
{decisions}

Reviewer approvals and objections:
{review}

Recent transcript:
{recent}

Structured swarm blackboard:
{blackboard_notes}

{EVIDENCE_BOUNDARY}

Write a concise Markdown report. It must be specific to the user's idea and include:
# Design Thinking Consensus Brief
## Target users
## Observed evidence
## Problem hypothesis
## Refined concept
## Empathy insight
## Problem statement
## How Might We
## Selected concept for prototyping
## MVP boundary
## Working hypotheses
## Assumptions to validate
## Prototype recommendation
## Test plan
## Next prototype and validation
## Open objections or risks
## Consensus decision
## Implementation handoff for coding agents
## Suggested epics and user stories
## Architecture notes
## Acceptance criteria
## Applied delivery transition: Build-readiness gate

If any reviewer objected, include it under open objections or risks and explain the mitigation.
Explicitly name the target users, observed evidence, problem hypothesis, selected concept for prototyping, MVP boundary, working hypotheses, assumptions to validate, and next prototype/validation.
Do not call the problem validated unless supplied evidence supports that claim; otherwise use problem hypothesis, observed evidence, and assumptions to validate.
Write the implementation sections so another coding agent can directly use the brief to build the solution.
For the build-readiness gate, clearly state that this is an applied delivery transition, not a canonical design-thinking phase. Include the labels "Readiness status:", "Blockers:", and "Required follow-up:".
Base the readiness status on unresolved approval objections, blackboard objections, missing evidence, and explicit phase decisions.
Use plain ASCII punctuation only. Do not use em dashes, smart quotes, or personal names.
""".strip()


def ensure_final_handoff_sections(
    brief: str,
    idea: str,
    phase_decisions: list[str],
    approvals: list[ApprovalOutput],
    blackboard: BlackboardState | None = None,
) -> str:
    required = [
        "## Target users",
        "## Observed evidence",
        "## Problem hypothesis",
        "## Selected concept for prototyping",
        "## MVP boundary",
        "## Working hypotheses",
        "## Assumptions to validate",
        "## Next prototype and validation",
        "## Implementation handoff for coding agents",
        "## Suggested epics and user stories",
        "## Architecture notes",
        "## Acceptance criteria",
        "## Applied delivery transition: Build-readiness gate",
        "not a canonical design-thinking phase",
        "Readiness status:",
        "Blockers:",
        "Required follow-up:",
    ]
    if all(section in brief for section in required):
        return brief

    blackboard = blackboard or BlackboardState()

    def phase_decision(phase_name: str) -> str:
        prefix = f"{phase_name}:"
        for decision in reversed(phase_decisions):
            if decision.startswith(prefix):
                return sanitize_text(decision.removeprefix(prefix).strip(), 180)
        return ""

    target_user_source = blackboard.evidence[-1] if blackboard.evidence else f"People affected by {idea}"
    target_users = f"- {sanitize_text(target_user_source, 180)}"
    observed_evidence = (
        "\n".join(f"- {sanitize_text(item, 180)}" for item in blackboard.evidence[-4:])
        if blackboard.evidence
        else "- No supplied user evidence was captured; treat problem details as hypotheses to validate."
    )
    problem_hypothesis = (
        f"- {phase_decision('Define')}"
        if phase_decision("Define")
        else f"- Working hypothesis: target users have a problem related to {sanitize_text(idea, 140)}."
    )
    selected_concept_source = (
        blackboard.selectedConcept
        or phase_decision("Ideate")
        or (blackboard.concepts[-1] if blackboard.concepts else "")
        or f"Fallback direction for {idea}"
    )
    selected_concept = f"- {sanitize_text(selected_concept_source, 180)}"
    mvp_boundary_source = phase_decision("Prototype") or f"Limit the MVP to the first testable journey for {idea}"
    mvp_boundary = f"- In scope: {sanitize_text(mvp_boundary_source, 180)}\n- Out of scope: Broad automation or scale-up before user testing."
    assumption_lines = blackboard.assumptions[-4:] or [f"Target users need this solution for {idea}"]
    working_hypotheses = "\n".join(f"- {sanitize_text(item, 180)}" for item in assumption_lines)
    assumptions_to_validate = "\n".join(f"- Validate: {sanitize_text(item, 170)}" for item in assumption_lines)
    next_test_source = phase_decision("Test") or f"Prototype the smallest journey and test it with target users"
    next_prototype_test = f"- {sanitize_text(next_test_source, 180)}"

    decisions = "\n".join(f"- {decision}" for decision in phase_decisions) or "- No phase decisions captured."
    objections = [approval for approval in approvals if approval.decision == "object"]
    objection_text = (
        "\n".join(f"- {approval.rationale}" for approval in objections)
        if objections
        else "- No blocking objections recorded."
    )
    status = (
        "Blocked until phase decisions and validation follow-up are defined."
        if not phase_decisions
        else "Conditional readiness. Resolve approval objections before build."
        if objections
        else "Ready for scoped build planning with validation follow-up."
    )
    blocker_lines = [approval.rationale for approval in objections]
    if not phase_decisions:
        blocker_lines.append("No phase decisions are available for implementation scope.")
    blockers = "\n".join(f"- {sanitize_text(blocker, 180)}" for blocker in blocker_lines) or "- No build blockers from final approvals."
    follow_up_items = [
        "Convert consensus decisions into a sequenced implementation backlog.",
        "Validate the riskiest blackboard assumption before broad build-out.",
    ]
    if blackboard.assumptions:
        follow_up_items.append(f"Test assumption: {blackboard.assumptions[-1]}")
    if blackboard.objections:
        follow_up_items.append(f"Address objection: {blackboard.objections[-1]}")
    follow_up = "\n".join(f"- {sanitize_text(item, 180)}" for item in follow_up_items)

    additions: list[str] = []
    final_artifact_fallbacks = {
        "## Target users": target_users,
        "## Observed evidence": observed_evidence,
        "## Problem hypothesis": problem_hypothesis,
        "## Selected concept for prototyping": selected_concept,
        "## MVP boundary": mvp_boundary,
        "## Working hypotheses": working_hypotheses,
        "## Assumptions to validate": assumptions_to_validate,
        "## Next prototype and validation": next_prototype_test,
    }
    for heading, body in final_artifact_fallbacks.items():
        if heading not in brief:
            additions.append(f"{heading}\n{body}\n")

    if "## Implementation handoff for coding agents" not in brief:
        additions.append(
            f"""## Implementation handoff for coding agents
Build the solution for this idea: {idea}

Use these swarm decisions as the implementation source of truth:
{decisions}

Selected concept for prototyping:
{selected_concept}

Respect this evidence boundary: build from observed evidence where available, and keep working hypotheses visible until validated.

Carry forward these objections or risks:
{objection_text}
"""
        )
    if "## Suggested epics and user stories" not in brief:
        additions.append(
            """## Suggested epics and user stories
- Epic: User problem discovery and onboarding
  - As a target user, I can describe my context so the product can tailor the first workflow.
- Epic: Core solution workflow
  - As a target user, I can complete the primary task with clear guidance and minimal friction.
- Epic: Trust, safety, and validation
  - As a target user, I can review assumptions, constraints, and limitations before relying on the output.
"""
        )
    if "## Architecture notes" not in brief:
        additions.append(
            """## Architecture notes
- Start with a thin vertical slice that proves the primary user journey.
- Keep generated recommendations explainable and editable.
- Store run state, decisions, assumptions, and final outputs separately from transient transcript events.
- Add telemetry for completion, revision, cancellation, and user export actions.
"""
        )
    if "## Acceptance criteria" not in brief:
        additions.append(
            """## Acceptance criteria
- The user can complete the primary journey without unsupported claims or hidden assumptions.
- The system shows constraints, risks, and validation steps before final handoff.
- The final brief includes enough detail for a coding agent to produce an implementation plan.
- The first prototype can be tested with target users in one session.
"""
        )
    readiness_markers = [
        "## Applied delivery transition: Build-readiness gate",
        "not a canonical design-thinking phase",
        "Readiness status:",
        "Blockers:",
        "Required follow-up:",
    ]
    if not all(marker in brief for marker in readiness_markers):
        readiness_heading = (
            "## Applied delivery transition: Build-readiness gate"
            if "## Applied delivery transition: Build-readiness gate" not in brief
            else "### Build-readiness details"
        )
        additions.append(
            f"""{readiness_heading}
This is an applied delivery transition, not a canonical design-thinking phase.

Readiness status: {status}

Blockers:
{blockers}

Required follow-up:
{follow_up}
"""
        )
    return f"{brief.rstrip()}\n\n" + "\n".join(additions)


def normalize_sticky_kind(value: Any) -> StickyKind:
    allowed: set[StickyKind] = {"Insight", "Question", "Risk", "Feature", "Metric", "Decision"}
    text = str(value or "Insight").strip().title()
    return text if text in allowed else "Insight"


def normalize_sticky_size(value: Any) -> Literal["standard", "wide"]:
    text = str(value or "standard").strip().lower()
    return "wide" if text == "wide" else "standard"


def sanitize_text(value: Any, limit: int) -> str:
    replacements = {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2026": "...",
    }
    text = str(value or "")
    for source, replacement in replacements.items():
        text = text.replace(source, replacement)
    return text.encode("ascii", errors="ignore").decode("ascii")[:limit].strip()


def sanitize_sticky_text(value: Any) -> str:
    text = sanitize_text(value, 120)
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]*", text)
    if not words:
        return "Needs sharper evidence"
    return " ".join(words[:8]).rstrip(".,;:")


def idea_keywords(idea: str) -> str:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]*", sanitize_text(idea, 80))
    return " ".join(words[:2]) if words else "concept"


def divergent_ideation_stickies(idea: str) -> list[dict[str, Any]]:
    keyword = idea_keywords(idea)
    return [
        {"kind": "Feature", "text": f"Practical {keyword} workflow", "size": "standard"},
        {"kind": "Feature", "text": f"Bold {keyword} copilot", "size": "standard"},
        {"kind": "Feature", "text": f"Low tech {keyword} worksheet", "size": "standard"},
    ]


def is_ideation_lead_phase(agent_id: AgentKind, phase: int) -> bool:
    return agent_id == "ideation" and 0 <= phase < len(PHASES) and PHASES[phase][0] == "Ideate"


def max_stickies_for_output(agent_id: AgentKind, phase: int) -> int:
    return 4 if is_ideation_lead_phase(agent_id, phase) else 1


def ensure_agent_output(agent_id: AgentKind, idea: str, phase: int, output: AgentOutput) -> AgentOutput:
    if not is_ideation_lead_phase(agent_id, phase):
        return output

    fallback = divergent_ideation_stickies(idea)
    source = [dict(sticky) for sticky in output.stickies if sticky]
    labels = [
        ("Practical", ["practical"], fallback[0]),
        ("Bold", ["bold"], fallback[1]),
        ("Low tech", ["low tech", "low-tech", "manual"], fallback[2]),
    ]
    ensured: list[dict[str, Any]] = []
    used_indices: set[int] = set()

    for index, (label, terms, fallback_sticky) in enumerate(labels):
        matched_index = next(
            (
                source_index
                for source_index, sticky in enumerate(source)
                if source_index not in used_indices
                and any(term in sanitize_sticky_text(sticky.get("text")).lower() for term in terms)
            ),
            None,
        )
        if matched_index is not None:
            candidate = source[matched_index]
            used_indices.add(matched_index)
        elif index < len(source) and index not in used_indices:
            candidate = source[index]
            used_indices.add(index)
        else:
            candidate = fallback_sticky
        text = sanitize_sticky_text(candidate.get("text"))
        lowered = text.lower()
        if not any(term in lowered for term in terms):
            text = sanitize_sticky_text(f"{label} {text}")
        ensured.append(
            {
                "kind": "Feature",
                "text": text,
                "size": normalize_sticky_size(candidate.get("size")),
            }
        )

    extra = next((sticky for index, sticky in enumerate(source) if index not in used_indices), None)
    if extra:
        ensured.append(
            {
                "kind": normalize_sticky_kind(extra.get("kind") or "Feature"),
                "text": sanitize_sticky_text(extra.get("text")),
                "size": normalize_sticky_size(extra.get("size")),
            }
        )

    return AgentOutput(speech=output.speech, stickies=ensured[:4])


def fallback_selected_concept(idea: str, phase_stickies: list[Sticky], blackboard: BlackboardState) -> str:
    ideate_features = [
        sticky.text
        for sticky in phase_stickies
        if sticky.phase == 2 and sticky.kind == "Feature" and sticky.text
    ]
    source = next(iter(ideate_features), None) or (blackboard.concepts[-1] if blackboard.concepts else "")
    return sanitize_text(source or f"Fallback direction for {idea}", 180)


def failure_rationale(label: str, exc: BaseException) -> str:
    return sanitize_text(f"{label} failed: {type(exc).__name__}: {exc}", 220)


def has_terminal_event(record: RunRecord) -> bool:
    return bool(record.events and record.events[-1].type in {"done", "cancelled", "error"})


def serialize_sse_event(event: WorkshopEvent) -> str:
    sequence = int(event.meta.get("sequence", 0))
    return f"id: {sequence}\ndata: {event.model_dump_json()}\n\n"


def parse_model_json(text: str) -> dict[str, Any]:
    """Parse strict JSON and common fenced/Python-dict variants from model output."""
    candidate = text.strip()
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0 or end < start:
        raise ValueError("Model response did not contain a JSON object.")
    candidate = candidate[start : end + 1]
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as json_error:
        try:
            value = ast.literal_eval(candidate)
        except (ValueError, SyntaxError) as literal_error:
            raise ValueError(f"Model response was not valid JSON: {json_error}") from literal_error
    if not isinstance(value, dict):
        raise ValueError("Model response JSON must be an object.")
    return value


def fallback_agent_output(agent_id: AgentKind, idea: str, phase: int) -> AgentOutput:
    subject = idea.strip()
    outputs: dict[AgentKind, AgentOutput] = {
        "facilitator": AgentOutput(
            speech=f'Test results for "{subject}" should decide whether to revisit the problem, concept, or prototype.',
            stickies=[
                {"kind": "Decision", "text": "Iterate from test feedback", "size": "wide"}
            ],
        ),
        "researcher": AgentOutput(
            speech=f'First understand who experiences "{subject}" and what they are trying to accomplish.',
            stickies=[
                {"kind": "Question", "text": "Who feels this pain", "size": "wide"},
            ],
        ),
        "framer": AgentOutput(
            speech=f'Define the root problem behind "{subject}" before selecting a solution path.',
            stickies=[
                {"kind": "Question", "text": "Reduce core user friction", "size": "wide"},
            ],
        ),
        "ideation": AgentOutput(
            speech=f'Compare practical, bold, and low-tech ways "{subject}" could work before choosing one direction.',
            stickies=divergent_ideation_stickies(subject),
        ),
        "business": AgentOutput(
            speech=f'Prioritize the version of "{subject}" with the clearest adoption value.',
            stickies=[
                {"kind": "Insight", "text": "Clear adoption wedge", "size": "wide"},
            ],
        ),
        "technical": AgentOutput(
            speech=f'Keep the first "{subject}" prototype lightweight enough to test quickly.',
            stickies=[
                {"kind": "Risk", "text": "Architecture slows learning", "size": "standard"}
            ],
        ),
        "critic": AgentOutput(
            speech=f'Make the strongest assumptions in "{subject}" visible so the prototype can test them.',
            stickies=[
                {"kind": "Risk", "text": "Root problem unclear", "size": "standard"}
            ],
        ),
        "ethics": AgentOutput(
            speech=f'Trust and consent risks for "{subject}" should be considered from the start.',
            stickies=[
                {"kind": "Risk", "text": "Trust needs review", "size": "standard"}
            ],
        ),
        "prototype": AgentOutput(
            speech=f'Make "{subject}" tangible with a first journey, storyboard, or MVP canvas.',
            stickies=[
                {"kind": "Decision", "text": "Prototype first journey", "size": "wide"},
            ],
        ),
        "validation": AgentOutput(
            speech=f'Testing "{subject}" should capture evidence, not just opinions.',
            stickies=[
                {"kind": "Metric", "text": "Evidence over opinions", "size": "wide"},
            ],
        ),
    }
    return outputs[agent_id]


class MafWorkshopOrchestrator:
    def __init__(self) -> None:
        self.project_endpoint = os.getenv("FOUNDRY_PROJECT_ENDPOINT", "")
        self.default_model = os.getenv("FOUNDRY_MODEL_DEPLOYMENT_NAME", "gpt-4o-mini")
        self.worker_model = os.getenv("FOUNDRY_WORKER_MODEL_DEPLOYMENT_NAME", self.default_model)
        self.reviewer_model = os.getenv("FOUNDRY_REVIEWER_MODEL_DEPLOYMENT_NAME", self.default_model)
        self.facilitator_model = os.getenv("FOUNDRY_FACILITATOR_MODEL_DEPLOYMENT_NAME", self.default_model)
        self.final_model = os.getenv("FOUNDRY_FINAL_MODEL_DEPLOYMENT_NAME", self.facilitator_model)
        self.agent_timeout_seconds = int(os.getenv("AGENT_TIMEOUT_SECONDS", "90"))
        self.final_timeout_seconds = int(os.getenv("FINAL_BRIEF_TIMEOUT_SECONDS", "180"))
        self.max_attempts = int(os.getenv("AGENT_MAX_ATTEMPTS", "2"))
        self.ideate_selection_wait_seconds = max(0, int(os.getenv("IDEATE_SELECTION_WAIT_SECONDS", "12")))

    def _credential(self) -> ManagedIdentityCredential | DefaultAzureCredential:
        return (
            ManagedIdentityCredential(client_id=os.getenv("AZURE_CLIENT_ID"))
            if os.getenv("AZURE_CLIENT_ID")
            else DefaultAzureCredential(exclude_interactive_browser_credential=True)
        )

    def _client(self, model: str) -> FoundryChatClient:
        return FoundryChatClient(
            project_endpoint=self.project_endpoint,
            model=model,
            credential=self._credential(),
        )

    async def _with_retries(self, label: str, timeout_seconds: int, operation):
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            started = time.perf_counter()
            try:
                result = await asyncio.wait_for(operation(), timeout=timeout_seconds)
                elapsed_ms = round((time.perf_counter() - started) * 1000)
                logger.info("%s succeeded attempt=%s elapsed_ms=%s", label, attempt, elapsed_ms)
                return result
            except Exception as exc:
                last_error = exc
                logger.warning("%s failed attempt=%s error=%s", label, attempt, exc)
                if attempt < self.max_attempts:
                    await asyncio.sleep(0.5 * attempt)
        raise RuntimeError(f"{label} failed after {self.max_attempts} attempts: {last_error}") from last_error

    async def _run_agent(
        self,
        agent_definition: AgentDefinition,
        idea: str,
        phase_index: int,
        transcript: list[AgentMessage],
        blackboard: BlackboardState,
    ) -> AgentOutput:
        if is_mock_mode():
            await asyncio.sleep(0.4)
            return ensure_agent_output(
                agent_definition.id,
                idea,
                phase_index,
                fallback_agent_output(agent_definition.id, idea, phase_index),
            )

        if not self.project_endpoint:
            raise RuntimeError("FOUNDRY_PROJECT_ENDPOINT is required unless MOCK_AGENTS=true.")

        client = self._client(self.worker_model)
        agent = Agent(
            client=client,
            name=public_safe_slug(agent_definition.role),
            instructions=agent_definition.instructions,
        )
        result = await self._with_retries(
            f"worker:{agent_definition.id}:phase:{phase_index}",
            self.agent_timeout_seconds,
            lambda: agent.run(build_prompt(agent_definition, idea, phase_index, transcript, blackboard)),
        )
        text = str(result).strip()
        data = parse_model_json(text)
        return ensure_agent_output(agent_definition.id, idea, phase_index, AgentOutput.model_validate(data))

    async def _run_facilitator_synthesis(
        self,
        idea: str,
        phase_index: int,
        phase_messages: list[AgentMessage],
        critiques: list[CritiqueOutput],
        debate: DebateOutput | None,
        revision: AgentMessage | None,
        prior_decisions: list[str],
        blackboard: BlackboardState,
    ) -> AgentOutput:
        if is_mock_mode():
            phase_name = PHASES[phase_index][0]
            decision = f"{phase_name} decision for {idea[:52]}"
            return AgentOutput(
                speech=f"The council will carry forward this {phase_name} decision for the idea.",
                stickies=[{"kind": "Decision", "text": decision, "size": "wide"}],
            )

        facilitator = next(agent for agent in AGENTS if agent.id == "facilitator")
        client = self._client(self.facilitator_model)
        agent = Agent(client=client, name="facilitator-synthesis", instructions=facilitator.instructions)
        result = await self._with_retries(
            f"facilitator:synthesis:phase:{phase_index}",
            self.agent_timeout_seconds,
            lambda: agent.run(
                build_synthesis_prompt(
                    idea,
                    phase_index,
                    phase_messages,
                    critiques,
                    debate,
                    revision,
                    prior_decisions,
                    blackboard,
                )
            ),
        )
        text = str(result).strip()
        data = parse_model_json(text)
        return AgentOutput.model_validate(data)

    async def _run_phase_critique(
        self,
        agent_definition: AgentDefinition,
        idea: str,
        phase_index: int,
        phase_messages: list[AgentMessage],
        prior_decisions: list[str],
        blackboard: BlackboardState,
    ) -> CritiqueOutput:
        if is_mock_mode():
            phase_name = PHASES[phase_index][0]
            return CritiqueOutput(
                speech=f"Critique: make the {phase_name} output more specific to {idea[:54]}.",
                issue=f"{phase_name} needs sharper grounding.",
                recommendation="Tie the next decision to user behavior.",
            )

        client = self._client(self.reviewer_model)
        agent = Agent(
            client=client,
            name=f"{public_safe_slug(agent_definition.role)}-phase-critique",
            instructions=agent_definition.instructions,
        )
        result = await self._with_retries(
            f"critique:{agent_definition.id}:phase:{phase_index}",
            self.agent_timeout_seconds,
            lambda: agent.run(
                build_critique_prompt(
                    agent_definition,
                    idea,
                    phase_index,
                    phase_messages,
                    prior_decisions,
                    blackboard,
                )
            ),
        )
        text = str(result).strip()
        data = parse_model_json(text)
        return CritiqueOutput.model_validate(data)

    async def _run_phase_debate(
        self,
        agent_definition: AgentDefinition,
        idea: str,
        phase_index: int,
        phase_messages: list[AgentMessage],
        critiques: list[CritiqueOutput],
        prior_decisions: list[str],
        blackboard: BlackboardState,
    ) -> DebateOutput:
        if is_mock_mode():
            phase_name = PHASES[phase_index][0]
            responses = [
                DebateResponse(
                    objection=sanitize_text(critique.issue, 120),
                    disposition="accept",
                    response=f"{phase_name} should narrow this claim for {idea[:48]}.",
                    action=sanitize_text(critique.recommendation, 120),
                )
                for critique in critiques
            ]
            return DebateOutput(
                speech=f"Debate: accepted concrete objections before revising the {phase_name} direction.",
                responses=responses,
            )

        client = self._client(self.reviewer_model)
        agent = Agent(
            client=client,
            name=f"{public_safe_slug(agent_definition.role)}-debate",
            instructions=agent_definition.instructions,
        )
        result = await self._with_retries(
            f"debate:{agent_definition.id}:phase:{phase_index}",
            self.agent_timeout_seconds,
            lambda: agent.run(
                build_debate_prompt(
                    agent_definition,
                    idea,
                    phase_index,
                    phase_messages,
                    critiques,
                    prior_decisions,
                    blackboard,
                )
            ),
        )
        text = str(result).strip()
        data = parse_model_json(text)
        return DebateOutput.model_validate(data)

    async def _run_phase_revision(
        self,
        agent_definition: AgentDefinition,
        idea: str,
        phase_index: int,
        phase_messages: list[AgentMessage],
        critiques: list[CritiqueOutput],
        debate: DebateOutput | None,
        prior_decisions: list[str],
        blackboard: BlackboardState,
    ) -> AgentOutput:
        if is_mock_mode():
            phase_name = PHASES[phase_index][0]
            return AgentOutput(
                speech=f"Revision: the {phase_name} stage will focus on the most testable part of {idea[:54]}.",
                stickies=[
                    {"kind": "Decision", "text": f"Revise {phase_name} around the most testable user behavior.", "size": "wide"}
                ],
            )

        client = self._client(self.reviewer_model)
        agent = Agent(
            client=client,
            name=f"{public_safe_slug(agent_definition.role)}-revision",
            instructions=agent_definition.instructions,
        )
        result = await self._with_retries(
            f"revision:{agent_definition.id}:phase:{phase_index}",
            self.agent_timeout_seconds,
            lambda: agent.run(
                build_revision_prompt(
                    agent_definition,
                    idea,
                    phase_index,
                    phase_messages,
                    critiques,
                    debate,
                    prior_decisions,
                    blackboard,
                )
            ),
        )
        text = str(result).strip()
        data = parse_model_json(text)
        return AgentOutput.model_validate(data)

    async def _run_reviewer_approval(
        self,
        agent_definition: AgentDefinition,
        idea: str,
        phase_decisions: list[str],
        transcript: list[AgentMessage],
        blackboard: BlackboardState,
    ) -> ApprovalOutput:
        if is_mock_mode():
            return ApprovalOutput(
                speech=f"Approve: the direction is specific enough to test {idea[:56]}.",
                decision="approve",
                rationale=f"Clear next test for {idea[:56]}.",
            )

        client = self._client(self.reviewer_model)
        agent = Agent(
            client=client,
            name=f"{public_safe_slug(agent_definition.role)}-reviewer",
            instructions=agent_definition.instructions,
        )
        result = await self._with_retries(
            f"approval:{agent_definition.id}",
            self.final_timeout_seconds,
            lambda: agent.run(build_approval_prompt(agent_definition, idea, phase_decisions, transcript, blackboard)),
        )
        text = str(result).strip()
        data = parse_model_json(text)
        return ApprovalOutput.model_validate(data)

    async def _run_final_brief(
        self,
        idea: str,
        phase_decisions: list[str],
        approvals: list[ApprovalOutput],
        transcript: list[AgentMessage],
        blackboard: BlackboardState,
    ) -> str:
        if is_mock_mode():
            return ensure_final_handoff_sections(build_brief(idea), idea, phase_decisions, approvals, blackboard)

        facilitator = next(agent for agent in AGENTS if agent.id == "facilitator")
        client = self._client(self.final_model)
        agent = Agent(client=client, name="facilitator-final-brief", instructions=facilitator.instructions)
        result = await self._with_retries(
            "final-brief",
            self.final_timeout_seconds,
            lambda: agent.run(build_final_brief_prompt(idea, phase_decisions, approvals, transcript, blackboard)),
        )
        brief = sanitize_text(result, 6000)
        return ensure_final_handoff_sections(brief, idea, phase_decisions, approvals, blackboard)

    def _raise_if_cancelled(self, run_id: str) -> None:
        if run_id in CANCELLED_RUNS:
            raise asyncio.CancelledError()

    async def _await_selected_concept(
        self,
        run_id: str,
        idea: str,
        phase_stickies: list[Sticky],
        blackboard: BlackboardState,
    ) -> tuple[str, str]:
        deadline = time.perf_counter() + self.ideate_selection_wait_seconds
        while time.perf_counter() < deadline:
            self._raise_if_cancelled(run_id)
            record = RUNS.get(run_id)
            if record and record.selectedConcept:
                selected = sanitize_text(record.selectedConcept, 180)
                blackboard.selectedConcept = selected
                _append_unique(blackboard.decisions, f"Selected concept for Prototype: {selected}")
                return selected, "user-selected"
            await asyncio.sleep(0.5)

        selected = fallback_selected_concept(idea, phase_stickies, blackboard)
        record = RUNS.get(run_id)
        if record:
            record.selectedConcept = selected
        blackboard.selectedConcept = selected
        _append_unique(blackboard.decisions, f"Fallback selected concept for Prototype: {selected}")
        return selected, "fallback"

    async def run(self, idea: str, run_id: str) -> AsyncIterator[WorkshopEvent]:
        transcript: list[AgentMessage] = []
        sticky_count = 0
        phase_slot_counts: dict[int, int] = {}
        phase_decisions: list[str] = []
        blackboard = create_blackboard(idea)

        for phase_index, (title, _) in enumerate(PHASES):
            self._raise_if_cancelled(run_id)
            yield WorkshopEvent(type="phase", phase=phase_index, title=title)
            phase_messages: list[AgentMessage] = []
            contribution_messages: list[AgentMessage] = []
            phase_stickies: list[Sticky] = []
            phase_agent_definitions = [
                next(agent for agent in AGENTS if agent.id == agent_id)
                for agent_id in PHASE_AGENTS[phase_index]
            ]
            phase_results = await asyncio.gather(
                *[
                    self._run_agent(agent_definition, idea, phase_index, transcript, blackboard)
                    for agent_definition in phase_agent_definitions
                ],
                return_exceptions=True,
            )

            for agent_definition, result in zip(phase_agent_definitions, phase_results):
                self._raise_if_cancelled(run_id)
                agent_id = agent_definition.id
                if isinstance(result, BaseException):
                    rationale = failure_rationale(f"{agent_definition.role} contribution", result)
                    logger.warning("using contribution fallback run_id=%s error=%s", run_id, rationale)
                    _append_unique(blackboard.objections, f"{PHASES[phase_index][0]}: {rationale}")
                    fallback = fallback_agent_output(agent_id, idea, phase_index)
                    output = ensure_agent_output(agent_id, idea, phase_index, fallback)
                    output.speech = f"{rationale}. Fallback workshop note used so the run can continue."
                else:
                    output = result
                message = AgentMessage(phase=phase_index, agentId=agent_id, text=sanitize_text(output.speech, 280))
                transcript.append(message)
                phase_messages.append(message)
                contribution_messages.append(message)
                yield WorkshopEvent(type="message", phase=phase_index, message=message)

                for sticky_data in output.stickies[: max_stickies_for_output(agent_id, phase_index)]:
                    points = LAYOUT[phase_index]
                    slot_index = phase_slot_counts.get(phase_index, 0)
                    phase_slot_counts[phase_index] = slot_index + 1
                    x, y = points[min(slot_index, len(points) - 1)]
                    sticky_count += 1
                    sticky = Sticky(
                        id=f"{agent_id}-{phase_index}-{sticky_count}",
                        phase=phase_index,
                        agentId=agent_id,
                        kind=normalize_sticky_kind(sticky_data.get("kind")),
                        text=sanitize_sticky_text(sticky_data.get("text")),
                        size=normalize_sticky_size(sticky_data.get("size")),
                        x=x,
                        y=y,
                    )
                    phase_stickies.append(sticky)
                    yield WorkshopEvent(type="sticky", phase=phase_index, sticky=sticky)

            critique_definitions = [
                next(agent for agent in AGENTS if agent.id == agent_id)
                for agent_id in PHASE_CRITIQUE_AGENTS[phase_index]
            ]
            critique_results = await asyncio.gather(
                *[
                    self._run_phase_critique(
                        agent_definition, idea, phase_index, phase_messages, phase_decisions, blackboard
                    )
                    for agent_definition in critique_definitions
                ],
                return_exceptions=True,
            )
            critiques: list[CritiqueOutput] = []
            for agent_definition, result in zip(critique_definitions, critique_results):
                if isinstance(result, BaseException):
                    rationale = failure_rationale(f"{agent_definition.role} critique", result)
                    logger.warning("using critique fallback run_id=%s error=%s", run_id, rationale)
                    _append_unique(blackboard.objections, f"{PHASES[phase_index][0]}: {rationale}")
                    critiques.append(
                        CritiqueOutput(
                            speech=f"{rationale}. Validate this role perspective manually.",
                            issue=f"{agent_definition.role} critique unavailable",
                            recommendation="Validate this perspective manually.",
                        )
                    )
                else:
                    critiques.append(result)
            for agent_definition, critique in zip(critique_definitions, critiques):
                self._raise_if_cancelled(run_id)
                critique_message = AgentMessage(
                    phase=phase_index,
                    agentId=agent_definition.id,
                    text=f"Critique: {sanitize_text(critique.speech, 260)}",
                    kind="critique",
                )
                transcript.append(critique_message)
                phase_messages.append(critique_message)
                yield WorkshopEvent(type="message", phase=phase_index, message=critique_message)

            primary_agent = next(agent for agent in AGENTS if agent.id == PHASE_PRIMARY_AGENT[phase_index])
            try:
                debate_output = await self._run_phase_debate(
                    primary_agent, idea, phase_index, phase_messages, list(critiques), phase_decisions, blackboard
                )
            except Exception as exc:
                rationale = failure_rationale(f"{primary_agent.role} debate", exc)
                logger.warning("using debate fallback run_id=%s error=%s", run_id, rationale)
                _append_unique(blackboard.objections, f"{title}: {rationale}")
                debate_output = DebateOutput(
                    speech=f"{rationale}. Carry critique actions forward for validation.",
                    responses=[
                        DebateResponse(
                            objection=sanitize_text(critique.issue, 120),
                            disposition="qualify",
                            response="Lead response unavailable; treat as unresolved until checked.",
                            action=sanitize_text(critique.recommendation, 120),
                        )
                        for critique in critiques
                    ],
                )
            self._raise_if_cancelled(run_id)
            debate_items = "; ".join(
                f"{item.disposition} {item.objection}: {item.action}" for item in debate_output.responses[:4]
            )
            debate_message = AgentMessage(
                phase=phase_index,
                agentId=primary_agent.id,
                text=f"Debate: {sanitize_text(debate_items or debate_output.speech, 360)}",
                kind="debate",
                meta={"responses": [item.model_dump() for item in debate_output.responses]},
            )
            transcript.append(debate_message)
            phase_messages.append(debate_message)
            yield WorkshopEvent(type="message", phase=phase_index, message=debate_message)

            try:
                revision_output = await self._run_phase_revision(
                    primary_agent,
                    idea,
                    phase_index,
                    phase_messages,
                    list(critiques),
                    debate_output,
                    phase_decisions,
                    blackboard,
                )
            except Exception as exc:
                rationale = failure_rationale(f"{primary_agent.role} revision", exc)
                logger.warning("using revision fallback run_id=%s error=%s", run_id, rationale)
                _append_unique(blackboard.objections, f"{title}: {rationale}")
                revision_output = fallback_agent_output(primary_agent.id, idea, phase_index)
                revision_output.speech = f"{rationale}. Fallback revision used so synthesis can continue."
            self._raise_if_cancelled(run_id)
            revision_message = AgentMessage(
                phase=phase_index,
                agentId=primary_agent.id,
                text=f"Revision: {sanitize_text(revision_output.speech, 260)}",
                kind="revision",
            )
            transcript.append(revision_message)
            phase_messages.append(revision_message)
            yield WorkshopEvent(type="message", phase=phase_index, message=revision_message)

            try:
                synthesis = await self._run_facilitator_synthesis(
                    idea,
                    phase_index,
                    phase_messages,
                    list(critiques),
                    debate_output,
                    revision_message,
                    phase_decisions,
                    blackboard,
                )
            except Exception as exc:
                rationale = failure_rationale("Facilitator synthesis", exc)
                logger.warning("using synthesis fallback run_id=%s error=%s", run_id, rationale)
                _append_unique(blackboard.objections, f"{title}: {rationale}")
                synthesis = AgentOutput(
                    speech=f"{rationale}. Carry forward the strongest available phase decision.",
                    stickies=[{"kind": "Decision", "text": f"{title} needs manual validation", "size": "wide"}],
                )
            self._raise_if_cancelled(run_id)
            synthesis_message = AgentMessage(
                phase=phase_index,
                agentId="facilitator",
                text=sanitize_text(synthesis.speech, 280),
                kind="synthesis",
            )
            transcript.append(synthesis_message)
            yield WorkshopEvent(type="message", phase=phase_index, message=synthesis_message)

            decision_text = sanitize_sticky_text(
                synthesis.stickies[0].get("text") if synthesis.stickies else synthesis.speech
            )
            phase_decisions.append(f"{title}: {decision_text}")
            update_blackboard_after_phase(
                blackboard,
                title,
                contribution_messages,
                phase_stickies,
                list(critiques),
                debate_output,
                revision_message,
                decision_text,
            )
            points = LAYOUT[phase_index]
            slot_index = phase_slot_counts.get(phase_index, 0)
            phase_slot_counts[phase_index] = slot_index + 1
            x, y = points[min(slot_index, len(points) - 1)]
            sticky_count += 1
            yield WorkshopEvent(
                type="sticky",
                phase=phase_index,
                sticky=Sticky(
                    id=f"facilitator-{phase_index}-{sticky_count}",
                    phase=phase_index,
                    agentId="facilitator",
                    kind="Decision",
                    text=decision_text,
                    size="wide",
                    x=x,
                    y=y,
                ),
            )
            yield WorkshopEvent(type="blackboard", phase=phase_index, blackboard=blackboard)

            if phase_index == 2:
                selection_prompt = AgentMessage(
                    phase=phase_index,
                    agentId="facilitator",
                    text=(
                        "Select an Ideate concept sticky for Prototype now. "
                        "If none is selected, the first viable concept will be used as a fallback."
                    ),
                    kind="synthesis",
                )
                transcript.append(selection_prompt)
                yield WorkshopEvent(type="message", phase=phase_index, message=selection_prompt)
                selected, source = await self._await_selected_concept(run_id, idea, phase_stickies, blackboard)
                selection_message = AgentMessage(
                    phase=phase_index,
                    agentId="facilitator",
                    text=f"Selected concept for Prototype ({source}): {selected}",
                    kind="synthesis",
                    meta={"selectedConcept": selected, "selectionSource": source},
                )
                transcript.append(selection_message)
                yield WorkshopEvent(type="message", phase=phase_index, message=selection_message)
                yield WorkshopEvent(
                    type="blackboard",
                    phase=phase_index,
                    blackboard=blackboard,
                    meta={"selectedConcept": selected, "selectionSource": source},
                )

        review_definitions = [next(agent for agent in AGENTS if agent.id == agent_id) for agent_id in REVIEW_AGENTS]
        approval_results = await asyncio.gather(
            *[
                self._run_reviewer_approval(agent_definition, idea, phase_decisions, transcript, blackboard)
                for agent_definition in review_definitions
            ],
            return_exceptions=True,
        )
        approvals: list[ApprovalOutput] = []
        for agent_definition, result in zip(review_definitions, approval_results):
            if isinstance(result, BaseException):
                rationale = failure_rationale(f"{agent_definition.role} approval", result)
                logger.warning("using approval fallback run_id=%s error=%s", run_id, rationale)
                approvals.append(
                    ApprovalOutput(
                        speech=f"Object: {rationale}. Validate this review perspective before build.",
                        decision="object",
                        rationale=rationale,
                    )
                )
            else:
                approvals.append(result)
        update_blackboard_after_approvals(blackboard, list(approvals))
        for agent_definition, approval in zip(review_definitions, approvals):
            self._raise_if_cancelled(run_id)
            review_message = AgentMessage(
                phase=len(PHASES) - 1,
                agentId=agent_definition.id,
                text=sanitize_text(approval.speech, 280),
                kind="approval",
            )
            transcript.append(review_message)
            yield WorkshopEvent(type="message", phase=len(PHASES) - 1, message=review_message)

        yield WorkshopEvent(type="blackboard", phase=len(PHASES) - 1, blackboard=blackboard)

        try:
            final_markdown = await self._run_final_brief(idea, phase_decisions, approvals, transcript, blackboard)
        except Exception as exc:
            rationale = failure_rationale("Final brief generation", exc)
            logger.warning("using final brief fallback run_id=%s error=%s", run_id, rationale)
            _append_unique(blackboard.objections, f"Final artifact: {rationale}")
            yield WorkshopEvent(type="blackboard", phase=len(PHASES) - 1, blackboard=blackboard)
            final_markdown = ensure_final_handoff_sections(
                build_brief(idea),
                idea,
                phase_decisions,
                approvals
                + [
                    ApprovalOutput(
                        speech=f"Object: {rationale}",
                        decision="object",
                        rationale=rationale,
                    )
                ],
                blackboard,
            )
        self._raise_if_cancelled(run_id)
        yield WorkshopEvent(type="brief", phase=4, markdown=final_markdown)
        yield WorkshopEvent(type="done", phase=4)


def build_brief(idea: str) -> str:
    return f"""# Design Thinking Consensus Brief

## Target users
People who need a clearer path to evaluate and shape {idea}.

## Observed evidence
No supplied user evidence was captured in this fallback brief.

## Problem hypothesis
The initial problem is a working hypothesis that needs target-user validation before broad build-out.

## Refined concept
{idea}

## Recommended product direction
A guided whiteboard workshop where role-based MAF agents follow the standard design-thinking process: Empathize, Define, Ideate, Prototype, and Test.

## Agent roles
- User researcher: investigates context, behaviors, needs, pains, workarounds, and emotions
- Problem framer: synthesizes insights into problem statements and How Might We questions
- Ideation lead: generates multiple divergent solution directions
- Prototype designer: makes concepts tangible through journeys, storyboards, and MVP scopes
- Validation lead: designs tests, assumptions, metrics, and iteration decisions
- Facilitator: keeps the process iterative and decision-ready
- Business viability: tests adoption, differentiation, and strategic fit
- Technical feasibility: evaluates implementation path and constraints
- Design critic: challenges clarity, friction, and hidden tradeoffs
- Ethics and trust: identifies privacy, consent, and safety guardrails

## MVP
Prompt intake, streamed workshop phases, live canvas mutations, editable board notes, Markdown export, JSON session export, and print-to-PDF.

## Selected concept for prototyping
Fallback direction for {idea}.

## Assumptions to validate
- Target users recognize the problem as urgent.
- A lightweight prototype produces actionable feedback.

## Validation plan
Run five 15-minute sessions with target users. Success means each user leaves with a brief they would share with a teammate.
"""


async def run_workshop_background(record: RunRecord) -> None:
    orchestrator = MafWorkshopOrchestrator()
    try:
        async for event in orchestrator.run(record.idea, record.id):
            if record.id in CANCELLED_RUNS or record.status == "cancelled":
                raise asyncio.CancelledError()
            event = record_event(record, event)
            if event.type == "done":
                record.status = "completed"
                record.updatedAt = utc_now()
    except asyncio.CancelledError:
        record.status = "cancelled"
        record.updatedAt = utc_now()
        if not has_terminal_event(record):
            record_event(record, WorkshopEvent(type="cancelled", phase=record.activePhase))
    except Exception as exc:
        logger.exception("workshop run failed run_id=%s", record.id)
        record.status = "error"
        record.error = str(exc)
        record.updatedAt = utc_now()
        if not has_terminal_event(record):
            record_event(record, WorkshopEvent(type="error", phase=record.activePhase, error=str(exc)))
    finally:
        RUN_TASKS.pop(record.id, None)


def ensure_run_task(record: RunRecord) -> None:
    if record.status != "running":
        return
    existing = RUN_TASKS.get(record.id)
    if existing and not existing.done():
        return
    RUN_TASKS[record.id] = asyncio.create_task(run_workshop_background(record))


app = FastAPI(title="Design Thinking Council API")


@app.middleware("http")
async def require_authenticated_user(request: Request, call_next):
    if os.getenv("APP_AUTH_REQUIRED", "").lower() not in {"1", "true", "yes"}:
        return await call_next(request)

    path = request.url.path
    if path == "/api/health" or path.startswith("/.auth/"):
        return await call_next(request)

    if request.headers.get("x-ms-client-principal"):
        return await call_next(request)

    if path.startswith("/api/"):
        return JSONResponse(status_code=401, content={"detail": "Authentication required"})

    return RedirectResponse(url=f"/.auth/login/aad?post_login_redirect_uri={path}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173,http://127.0.0.1:5174").split(","),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "mafAvailable": Agent is not None,
        "mockAgents": is_mock_mode(),
        "model": os.getenv("FOUNDRY_MODEL_DEPLOYMENT_NAME", "gpt-4o-mini"),
        "models": {
            "worker": os.getenv("FOUNDRY_WORKER_MODEL_DEPLOYMENT_NAME", os.getenv("FOUNDRY_MODEL_DEPLOYMENT_NAME", "gpt-4o-mini")),
            "reviewer": os.getenv("FOUNDRY_REVIEWER_MODEL_DEPLOYMENT_NAME", os.getenv("FOUNDRY_MODEL_DEPLOYMENT_NAME", "gpt-4o-mini")),
            "facilitator": os.getenv("FOUNDRY_FACILITATOR_MODEL_DEPLOYMENT_NAME", os.getenv("FOUNDRY_MODEL_DEPLOYMENT_NAME", "gpt-4o-mini")),
            "final": os.getenv("FOUNDRY_FINAL_MODEL_DEPLOYMENT_NAME", os.getenv("FOUNDRY_FACILITATOR_MODEL_DEPLOYMENT_NAME", os.getenv("FOUNDRY_MODEL_DEPLOYMENT_NAME", "gpt-4o-mini"))),
        },
        "orchestration": "generate with blackboard -> critique -> debate -> revise -> synthesize -> approve -> readiness gate -> finalize",
    }


@app.get("/api/agents")
async def list_agents() -> dict[str, Any]:
    return {"agents": [agent.model_dump(exclude={"instructions"}) for agent in AGENTS], "phases": PHASES}


@app.post("/api/workshops")
async def create_workshop(request: WorkshopRequest) -> dict[str, str]:
    record = create_run(request.idea)
    return {"id": record.id, "traceId": record.traceId, "streamUrl": f"/api/workshops/stream?idea={quote(request.idea)}&runId={record.id}"}


@app.get("/api/workshop-runs/{run_id}")
async def get_workshop(run_id: str) -> RunRecord:
    record = RUNS.get(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="Run not found")
    return record


@app.post("/api/workshop-runs/{run_id}/cancel")
async def cancel_workshop(run_id: str) -> dict[str, str]:
    record = RUNS.get(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="Run not found")
    CANCELLED_RUNS.add(run_id)
    record.status = "cancelled"
    record.updatedAt = utc_now()
    task = RUN_TASKS.get(run_id)
    if task and not task.done():
        task.cancel()
    elif not has_terminal_event(record):
        record_event(record, WorkshopEvent(type="cancelled", phase=record.activePhase))
    return {"id": run_id, "status": "cancelled"}


@app.post("/api/workshop-runs/{run_id}/selected-concept")
async def select_concept(run_id: str, request: ConceptSelectionRequest) -> dict[str, str | None]:
    record = RUNS.get(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="Run not found")
    if record.status in {"completed", "cancelled", "error"}:
        raise HTTPException(status_code=409, detail=f"Run is already {record.status}")

    selected = sanitize_text(request.text, 180)
    record.selectedConcept = selected
    record.selectedConceptStickyId = request.stickyId
    record.latestBlackboard.selectedConcept = selected
    _append_unique(record.latestBlackboard.decisions, f"User selected concept for Prototype: {selected}")
    record_event(
        record,
        WorkshopEvent(
            type="blackboard",
            phase=max(record.activePhase, 2),
            blackboard=record.latestBlackboard,
            meta={"selectedConcept": selected, "selectedConceptStickyId": request.stickyId},
        ),
    )
    return {"id": run_id, "selectedConcept": selected, "stickyId": request.stickyId}


@app.get("/api/workshops/stream")
async def stream_workshop(
    request: Request,
    idea: str = Query(min_length=1, max_length=4000),
    runId: str | None = None,
    since: int = Query(0, ge=0),
) -> StreamingResponse:
    if runId:
        record = RUNS.get(runId)
        if record is None:
            raise HTTPException(
                status_code=404,
                detail="Run not found. The backend may have restarted and lost in-memory run state.",
            )
    else:
        record = create_run(idea)
    ensure_run_task(record)

    async def event_stream() -> AsyncIterator[str]:
        last_event_id = request.headers.get("last-event-id")
        try:
            last_sent = max(since, int(last_event_id or 0))
        except ValueError:
            last_sent = since

        while True:
            if await request.is_disconnected():
                return

            pending = [
                event
                for event in record.events
                if int(event.meta.get("sequence", 0)) > last_sent
            ]
            for event in pending:
                if await request.is_disconnected():
                    return
                last_sent = int(event.meta.get("sequence", last_sent))
                yield serialize_sse_event(event)

            if record.status in {"completed", "cancelled", "error"} and not pending:
                return

            await asyncio.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

    @app.get("/{path:path}")
    async def serve_frontend(path: str) -> FileResponse:
        requested = static_dir / path
        if requested.is_file():
            return FileResponse(requested)
        index = static_dir / "index.html"
        if index.exists():
            return FileResponse(index)
        raise HTTPException(status_code=404, detail="Not found")
