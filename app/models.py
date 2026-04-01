"""
Pydantic models for IncidentEnv — the full OpenEnv-compliant typed interface.
All models are strict Pydantic v2, JSON-serializable, and thoroughly documented.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# ──────────────────────────────────────────────────────────────────────────────
# Sub-models that compose the Observation
# ──────────────────────────────────────────────────────────────────────────────


class Alert(BaseModel):
    """A firing monitoring alert."""

    alert_id: str = Field(..., description="Unique alert identifier, e.g. ALT-001")
    service: str = Field(..., description="Service that fired the alert")
    severity: Literal["critical", "warning", "info"] = Field(..., description="Alert severity level")
    message: str = Field(..., description="Human-readable alert message")
    timestamp: str = Field(..., description="ISO-8601 UTC timestamp when the alert fired")
    metric: Optional[str] = Field(None, description="Metric name that triggered the alert")
    value: Optional[float] = Field(None, description="Current metric value")


class ServiceMetrics(BaseModel):
    """Real-time telemetry snapshot for a single service."""

    service_name: str
    cpu_percent: float = Field(..., ge=0.0, le=100.0, description="CPU utilisation %")
    memory_percent: float = Field(..., ge=0.0, le=100.0, description="Memory utilisation %")
    error_rate: float = Field(..., ge=0.0, description="Errors per second")
    latency_p99_ms: float = Field(..., ge=0.0, description="99th-percentile request latency in ms")
    status: Literal["healthy", "degraded", "down"] = Field(..., description="Derived health status")
    request_rate: float = Field(..., ge=0.0, description="Requests per second")
    # Extra fields that make the environment richer
    disk_io_mbps: Optional[float] = Field(None, description="Disk I/O in MB/s, if relevant")
    open_connections: Optional[int] = Field(None, description="Open network connections")
    gc_pause_ms: Optional[float] = Field(None, description="Last GC pause duration in ms")


class LogEntry(BaseModel):
    """A single structured log line."""

    timestamp: str
    service: str
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    message: str
    correlation_id: Optional[str] = Field(None, description="Distributed trace / request ID")


class DeploymentRecord(BaseModel):
    """Recent deployment metadata visible in the observation."""

    timestamp: str
    service: str
    version: str
    previous_version: str
    author: str
    changelog: str


class RunbookHint(BaseModel):
    """
    Contextual runbook hint surfaced based on active alerts.
    The agent may choose to follow or ignore these.
    """

    alert_pattern: str
    hint: str
    suggested_diagnostics: List[str]


class IncidentHistoryEntry(BaseModel):
    """One recorded action taken by the agent during this episode."""

    step: int
    action_type: str
    tool: Optional[str] = None
    target_service: Optional[str] = None
    feedback: str
    reward_delta: float = 0.0


# ──────────────────────────────────────────────────────────────────────────────
# Core OpenEnv types
# ──────────────────────────────────────────────────────────────────────────────


class Observation(BaseModel):
    """
    Everything an agent sees at each step.
    Designed to closely mirror what an SRE sees in a real incident dashboard.
    """

    # Monitoring signals
    active_alerts: List[Alert] = Field(..., description="Currently firing alerts")
    service_metrics: Dict[str, ServiceMetrics] = Field(
        ..., description="Real-time metrics keyed by service name"
    )
    recent_logs: List[LogEntry] = Field(..., description="Most recent log entries (newest last)")
    recent_deployments: List[DeploymentRecord] = Field(
        default_factory=list,
        description="Deployments in the last 24h",
    )

    # Agent affordances
    available_diagnostics: List[str] = Field(
        ..., description="Tool names the agent can run via run_diagnostic"
    )
    available_fixes: List[str] = Field(
        ..., description="Remediation names the agent can apply via apply_fix"
    )

    # Episode context
    incident_history: List[IncidentHistoryEntry] = Field(
        default_factory=list, description="Actions taken so far this episode"
    )
    runbook_hints: List[RunbookHint] = Field(
        default_factory=list,
        description="Optional runbook hints based on active alert patterns",
    )
    time_elapsed_minutes: float = Field(..., ge=0.0, description="Simulated time since incident opened")
    incident_status: Literal["open", "investigating", "mitigating", "mitigated", "resolved"] = Field(
        ..., description="Current incident lifecycle state"
    )
    task_id: str
    step_number: int = Field(0, description="Current step number within the episode")
    max_steps: int = Field(20, description="Maximum allowed steps for this task")


class Action(BaseModel):
    """
    All actions an agent can take. One action per step.

    action_type must be one of:
      run_diagnostic  — run a named diagnostic tool
      apply_fix       — apply a named remediation
      write_postmortem— write a structured postmortem text (task_hard only)
      close_incident  — mark the incident resolved
      escalate        — page a human SRE for help
      add_note        — annotate the incident timeline (no reward effect, for traceability)
    """

    action_type: Literal[
        "run_diagnostic",
        "apply_fix",
        "write_postmortem",
        "close_incident",
        "escalate",
        "add_note",
    ]
    tool: Optional[str] = Field(
        None,
        description="Diagnostic or fix name for run_diagnostic / apply_fix",
    )
    target_service: Optional[str] = Field(
        None, description="Optional service context for the action"
    )
    parameters: Optional[Dict[str, Any]] = Field(
        None, description="Extra parameters (future extensibility)"
    )
    postmortem_text: Optional[str] = Field(
        None,
        description="Full postmortem markdown for write_postmortem action. "
        "Should cover: root cause, timeline, impact, remediation, prevention, follow-ups.",
    )
    note: Optional[str] = Field(
        None, description="Free-text note for add_note action"
    )
    reasoning: Optional[str] = Field(
        None,
        description="Optional chain-of-thought explanation. Not used for scoring but improves interpretability.",
    )


class Reward(BaseModel):
    """Structured reward returned from grader endpoints."""

    total: float = Field(..., ge=0.0, le=1.0, description="Overall episode score")
    breakdown: Dict[str, float] = Field(..., description="Per-criterion scores")
    feedback: str = Field(..., description="Human-readable explanation of the score")


class StepResult(BaseModel):
    """Return value of POST /step."""

    observation: Observation
    reward: float = Field(..., ge=0.0, le=1.0, description="Reward for this step (clipped to [0, 1])")
    done: bool = Field(..., description="True if the episode is finished")
    info: Dict[str, Any] = Field(..., description="Diagnostic metadata, not for training use")


# ──────────────────────────────────────────────────────────────────────────────
# Full environment state (returned by GET /state)
# ──────────────────────────────────────────────────────────────────────────────


class EnvironmentState(BaseModel):
    """
    Full internal state of one episode. Returned by GET /state.
    The agent may use this for debugging; it should NOT rely on it during training.
    """

    task_id: str
    episode_id: str
    step_count: int
    max_steps: int

    # Discovery tracking
    agent_discoveries: List[str] = Field(
        default_factory=list,
        description="Root-cause clues the agent has found via diagnostics",
    )
    fixes_applied: List[str] = Field(default_factory=list)
    diagnostics_run: List[str] = Field(default_factory=list)

    # Progress flags
    root_cause_identified: bool = False
    incident_resolved: bool = False
    postmortem_written: bool = False
    postmortem_quality: float = Field(0.0, ge=0.0, le=1.0)

    # Error tracking
    wrong_fixes_applied: int = 0
    repeated_diagnostics: int = 0
    escalated: bool = False
    closed_without_resolution: bool = False

    # Full action log
    action_log: List[IncidentHistoryEntry] = Field(default_factory=list)

    # Snapshot of last observation
    current_observation: Observation
