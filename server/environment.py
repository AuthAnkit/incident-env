"""
Core environment — manages episode lifecycle, processes actions,
computes step rewards, and evolves the incident state machine.

Key design choices
------------------
* Mutable scenario copy per episode → wrong fixes cascade (system gets worse)
* Reasoning evaluation → small bonus for CoT that names correct signals
* Full reward breakdown → every reward_delta is itemised in step info
* OpenEnv-compliant API (reset / step / state / grade_episode)
"""
from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any, Dict, List, Tuple

from server.models import (
    Action, Alert, DeploymentRecord, EnvironmentState,
    IncidentHistoryEntry, LogEntry, Observation, RunbookHint,
    ServiceMetrics, StepResult,
)
from server.scenarios import AVAILABLE_DIAGNOSTICS, AVAILABLE_FIXES, SCENARIOS
from server.graders import grade, score_postmortem


# ── Reasoning quality helpers ─────────────────────────────────────────────────

_REASONING_SIGNALS: List[str] = [
    "root cause", "latency", "memory", "error rate", "cpu", "deployment",
    "log", "metric", "connection", "pool", "redis", "jwt", "cache",
    "query", "rollback", "heap", "gc", "oom", "timeout", "cascade",
    "postmortem", "red herring", "correlation",
]

def _score_reasoning(text: str) -> float:
    """
    Score reasoning quality 0.0–1.0.
    Awards points for length, SRE-relevant keywords, and causal language.
    Max reward contribution = 0.03 (multiplied upstream).
    """
    if not text or len(text.strip()) < 40:
        return 0.0
    tl = text.lower()
    signal_hits = sum(1 for kw in _REASONING_SIGNALS if kw in tl)
    signal_score = min(signal_hits / 4, 1.0)
    length_score = min(len(text) / 240, 1.0)
    why_score = 1.0 if any(w in tl for w in [
        "because", "since", "therefore", "indicates", "suggests",
        "implies", "so that", "in order", "this means",
    ]) else 0.4
    return round((signal_score * 0.5 + length_score * 0.3 + why_score * 0.2), 3)


# ── Cascade degradation profile ───────────────────────────────────────────────

_CASCADE_MULTIPLIERS = {
    "error_rate":      2.5,
    "latency_p99_ms":  1.8,
    "cpu_percent":     1.25,
    "memory_percent":  1.10,
}
_CASCADE_CAPS = {
    "error_rate":      9_999.0,
    "latency_p99_ms":  60_000.0,
    "cpu_percent":     99.0,
    "memory_percent":  99.5,
}


class IncidentEnvironment:
    """
    OpenEnv-compliant environment.

    Episode lifecycle
    -----------------
    1. reset(task_id)  → Observation   (start fresh episode)
    2. step(action)    → StepResult    (repeated until done=True)
    3. state()         → EnvironmentState
    4. grade_episode() → (score, breakdown)

    Cascade mechanic (WOW factor)
    -----------------------------
    Every wrong fix degrades the live scenario metrics by _CASCADE_MULTIPLIERS
    and injects a new CRITICAL alert. The agent's observation gets *objectively
    worse* after bad decisions — not just a point deduction, but a harder state.
    This makes the environment a true test of systematic diagnosis, not guessing.
    """

    def __init__(self) -> None:
        self._state: EnvironmentState | None = None
        self._scenario: dict | None = None   # mutable per-episode copy

    # ── Public OpenEnv API ────────────────────────────────────────────────────

    def reset(self, task_id: str = "task_easy") -> Observation:
        """Start a new episode. Returns the initial Observation."""
        if task_id not in SCENARIOS:
            raise ValueError(
                f"Unknown task_id '{task_id}'. "
                f"Available: {list(SCENARIOS.keys())}"
            )

        self._scenario = deepcopy(SCENARIOS[task_id])
        obs = self._build_observation(task_id, [], 0.0, "open", 0)

        self._state = EnvironmentState(
            task_id=task_id,
            episode_id=str(uuid.uuid4()),
            step_count=0,
            max_steps=self._scenario.get("max_steps", 20),
            agent_discoveries=[],
            fixes_applied=[],
            diagnostics_run=[],
            root_cause_identified=False,
            incident_resolved=False,
            postmortem_written=False,
            postmortem_quality=0.0,
            wrong_fixes_applied=0,
            repeated_diagnostics=0,
            escalated=False,
            closed_without_resolution=False,
            action_log=[],
            current_observation=obs,
        )
        return obs

    def step(self, action: Action) -> StepResult:
        """
        Process one action. Returns observation, reward, done, info.
        Reward is clipped to [0, 1].
        """
        if self._state is None or self._scenario is None:
            raise RuntimeError("Call reset() before step().")

        self._state.step_count += 1
        scenario = self._scenario
        reward_parts: Dict[str, float] = {}
        feedback_parts: list[str] = []

        # ── Reasoning bonus ───────────────────────────────────────────────────
        reasoning_quality_score: float | None = None
        if action.reasoning:
            rq = _score_reasoning(action.reasoning)
            reasoning_quality_score = round(rq, 3)
            if rq > 0.0:
                reward_parts["reasoning_quality"] = round(rq * 0.03, 4)

        # ── Dispatch action ───────────────────────────────────────────────────
        atype = action.action_type

        if atype == "run_diagnostic":
            r, fb, new_disc = self._do_diagnostic(action.tool or "", scenario)
            reward_parts["diagnostic"] = r
            feedback_parts.append(fb)
            if (
                scenario["root_cause"] in new_disc
                and not self._state.root_cause_identified
            ):
                self._state.root_cause_identified = True
                reward_parts["root_cause_bonus"] = 0.15
                feedback_parts.append("✓ Root cause identified.")

        elif atype == "apply_fix":
            r, fb, resolved, cascaded = self._do_fix(action.tool or "", scenario)
            reward_parts["fix"] = r
            feedback_parts.append(fb)
            if cascaded:
                reward_parts["cascade_penalty"] = -0.05
                feedback_parts.append(
                    "⚡ CASCADE: System degrading further. New alerts firing. "
                    "Re-diagnose before applying more fixes."
                )
            if resolved:
                self._state.incident_resolved = True
                reward_parts["resolution_bonus"] = 0.10
                feedback_parts.append("✓ Incident resolved.")

        elif atype == "write_postmortem":
            r, fb = self._do_postmortem(action.postmortem_text or "")
            reward_parts["postmortem"] = r
            feedback_parts.append(fb)

        elif atype == "escalate":
            if not self._state.escalated:
                self._state.escalated = True
                reward_parts["escalate"] = 0.02
                feedback_parts.append("Escalation recorded. Senior on-call SRE paged.")
            else:
                reward_parts["escalate"] = -0.02
                feedback_parts.append("Already escalated — duplicate page.")

        elif atype == "close_incident":
            if self._state.incident_resolved:
                reward_parts["close"] = 0.05
                feedback_parts.append("Incident closed correctly.")
            else:
                self._state.closed_without_resolution = True
                reward_parts["close"] = -0.05
                feedback_parts.append("⚠ Closed without confirmed resolution.")

        elif atype == "add_note":
            reward_parts["add_note"] = 0.0
            feedback_parts.append(f"Note recorded: {action.note or '(empty)'}")

        # ── Step cost (time pressure) ─────────────────────────────────────────
        reward_parts["step_cost"] = -0.01

        # ── Compute and record ────────────────────────────────────────────────
        step_reward = round(max(0.0, min(1.0, sum(reward_parts.values()))), 4)
        full_feedback = " ".join(feedback_parts)

        entry = IncidentHistoryEntry(
            step=self._state.step_count,
            action_type=action.action_type,
            tool=action.tool,
            target_service=action.target_service,
            feedback=full_feedback,
            reward_delta=step_reward,
        )
        self._state.action_log.append(entry)

        # ── Incident status transition ────────────────────────────────────────
        status = self._compute_status()

        # ── Build new observation (from live, possibly degraded, scenario) ────
        new_obs = self._build_observation(
            self._state.task_id,
            self._state.action_log,
            self._state.step_count * 2.0,
            status,
            self._state.step_count,
        )
        self._state.current_observation = new_obs

        # ── Terminal condition ────────────────────────────────────────────────
        done = (
            self._state.incident_resolved
            or self._state.step_count >= self._state.max_steps
            or action.action_type == "close_incident"
        )

        return StepResult(
            observation=new_obs,
            reward=step_reward,
            done=done,
            info={
                "reward_breakdown": reward_parts,
                "feedback": full_feedback,
                "step": self._state.step_count,
                "max_steps": self._state.max_steps,
                "discoveries": list(self._state.agent_discoveries),
                "fixes_applied": list(self._state.fixes_applied),
                "diagnostics_run": list(self._state.diagnostics_run),
                "root_cause_identified": self._state.root_cause_identified,
                "incident_resolved": self._state.incident_resolved,
                "postmortem_written": self._state.postmortem_written,
                "postmortem_quality": self._state.postmortem_quality,
                "wrong_fixes_applied": self._state.wrong_fixes_applied,
                "reasoning_quality": reasoning_quality_score,
                "episode_id": self._state.episode_id,
                "done": done,
            },
        )

    def state(self) -> EnvironmentState:
        """Return the full internal episode state."""
        if self._state is None:
            raise RuntimeError("Call reset() first.")
        return self._state

    def get_state(self) -> EnvironmentState:
        return self.state()

    def grade_episode(self) -> Tuple[float, dict]:
        """Run the grader for the current episode. Returns (score, breakdown)."""
        if self._state is None:
            raise RuntimeError("Call reset() first.")
        return grade(self._state.task_id, self._state)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _do_diagnostic(
        self, tool: str, scenario: dict
    ) -> Tuple[float, str, list[str]]:
        diag_map = scenario.get("diagnostic_outputs", {})
        if tool not in diag_map:
            return -0.02, f"Unknown diagnostic '{tool}'. No action taken.", []

        if tool in self._state.diagnostics_run:
            self._state.repeated_diagnostics += 1
            return (
                -0.02,
                f"'{tool}' already run — no new information (repeated diagnostic penalty).",
                [],
            )

        self._state.diagnostics_run.append(tool)
        result = diag_map[tool]
        raw_reveals: list[str] = result.get("reveals", [])
        new_disc = [d for d in raw_reveals if d not in self._state.agent_discoveries]
        self._state.agent_discoveries.extend(new_disc)

        reward = 0.05 if new_disc else 0.01
        output_snippet = result["output"][:300] + ("…" if len(result["output"]) > 300 else "")
        return reward, f"[{tool}] {output_snippet}", new_disc

    def _do_fix(
        self, fix: str, scenario: dict
    ) -> Tuple[float, str, bool, bool]:
        """
        Apply a fix. Returns (reward, feedback, incident_resolved, cascade_triggered).

        Cascade mechanic: wrong fixes degrade live scenario metrics so the next
        observation looks objectively worse — not just a score penalty, but a
        harder state the agent must now navigate from.
        """
        if fix not in AVAILABLE_FIXES:
            return -0.02, f"Unknown fix '{fix}'.", False, False

        if fix in self._state.fixes_applied:
            return -0.03, f"Fix '{fix}' already applied — duplicate.", False, False

        self._state.fixes_applied.append(fix)
        correct_fix = scenario["correct_fix"]
        wrong_fixes = scenario.get("wrong_fixes", [])

        if fix == correct_fix:
            needs_pm = scenario.get("requires_postmortem", False)
            resolved = not needs_pm or self._state.postmortem_written
            return (
                0.30,
                f"✓ Correct fix applied: '{fix}'. Services recovering.",
                resolved,
                False,
            )

        elif fix in wrong_fixes:
            self._state.wrong_fixes_applied += 1
            self._trigger_cascade(fix, scenario)
            return (
                -0.05,
                (
                    f"✗ Wrong fix: '{fix}'. Root cause unaddressed. "
                    f"Secondary failures now spreading — the incident is getting worse."
                ),
                False,
                True,
            )

        else:
            return (
                0.0,
                f"Fix '{fix}' applied but has no effect on this incident.",
                False,
                False,
            )

    def _trigger_cascade(self, wrong_fix: str, scenario: dict) -> None:
        """
        Mutate the live scenario to simulate cascading failure after a wrong fix.
        Next observation will show degraded metrics and new CRITICAL alerts.
        """
        n = self._state.wrong_fixes_applied

        # 1. Multiply service metrics
        for metrics in scenario["service_metrics"].values():
            for field, mult in _CASCADE_MULTIPLIERS.items():
                if field in metrics:
                    cap = _CASCADE_CAPS[field]
                    metrics[field] = round(min(metrics[field] * mult, cap), 2)
            if metrics.get("error_rate", 0) > 50 or metrics.get("cpu_percent", 0) > 90:
                metrics["status"] = "down"
            elif metrics.get("error_rate", 0) > 10:
                metrics["status"] = "degraded"

        # 2. Inject a new CRITICAL alert
        scenario["initial_alerts"].append({
            "alert_id": f"ALT-CASCADE-{n:02d}",
            "service": "platform",
            "severity": "critical",
            "message": (
                f"⚡ CASCADE #{n}: '{wrong_fix}' triggered secondary failures. "
                f"All error rates escalating. Root cause still unresolved."
            ),
            "timestamp": "2025-03-28T02:20:00Z",
            "metric": "error_rate",
            "value": 9999.0,
        })

        # 3. Inject cascade log entries
        scenario["logs"].append({
            "timestamp": "2025-03-28T02:20:00Z",
            "service": "platform",
            "level": "CRITICAL",
            "message": (
                f"CASCADE FAILURE after '{wrong_fix}': downstream error rates "
                f"multiplied {_CASCADE_MULTIPLIERS['error_rate']}x. "
                f"Root cause still unresolved. Revert your diagnostic approach."
            ),
            "correlation_id": f"cascade-{n}",
        })
        scenario["logs"].append({
            "timestamp": "2025-03-28T02:20:01Z",
            "service": "incident-manager",
            "level": "CRITICAL",
            "message": (
                f"INCIDENT ESCALATED (wrong fix #{n}). MTTR clock running. "
                f"Identify root cause before applying further changes."
            ),
            "correlation_id": f"cascade-{n}",
        })

    def _do_postmortem(self, text: str) -> Tuple[float, str]:
        if not text or len(text.strip()) < 80:
            return 0.0, "Postmortem too short (< 80 chars) — not accepted."

        quality = score_postmortem(text)
        self._state.postmortem_written = True
        self._state.postmortem_quality = quality

        scenario = self._scenario
        if (
            scenario.get("requires_postmortem", False)
            and scenario["correct_fix"] in self._state.fixes_applied
        ):
            self._state.incident_resolved = True

        reward = round(quality * 0.20, 3)
        if quality >= 0.8:
            fb = f"Excellent postmortem (quality {quality:.2f}). All key sections present."
        elif quality >= 0.5:
            fb = f"Good postmortem (quality {quality:.2f}). Add missing sections for full credit."
        else:
            fb = f"Weak postmortem (quality {quality:.2f}). Missing root cause / timeline / prevention."
        return reward, fb

    def _compute_status(self) -> str:
        if self._state.incident_resolved:
            return "resolved"
        if self._state.fixes_applied:
            return "mitigating"
        if self._state.diagnostics_run:
            return "investigating"
        return "open"

    def _build_observation(
        self,
        task_id: str,
        history: list[IncidentHistoryEntry],
        elapsed: float,
        status: str,
        step_number: int,
    ) -> Observation:
        scenario = self._scenario
        max_steps = scenario.get("max_steps", 20)

        if task_id == "task_real_cpu":
            import psutil
            real_cpu = psutil.cpu_percent()
            scenario["service_metrics"]["api-gateway"]["cpu_percent"] = max(10.0, real_cpu)
            scenario["initial_alerts"][0]["value"] = max(10.0, real_cpu)

        return Observation(
            active_alerts=[Alert(**a) for a in scenario["initial_alerts"]],
            service_metrics={
                k: ServiceMetrics(**v) for k, v in scenario["service_metrics"].items()
            },
            recent_logs=[LogEntry(**lg) for lg in scenario["logs"]],
            recent_deployments=[
                DeploymentRecord(**d) for d in scenario.get("recent_deployments", [])
            ],
            available_diagnostics=list(AVAILABLE_DIAGNOSTICS),
            available_fixes=list(AVAILABLE_FIXES),
            incident_history=list(history),
            runbook_hints=[
                RunbookHint(**h) for h in scenario.get("runbook_hints", [])
            ],
            time_elapsed_minutes=elapsed,
            incident_status=status,
            task_id=task_id,
            step_number=step_number,
            max_steps=max_steps,
        )
