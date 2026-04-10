"""
inference.py — OpenEnv required entry point.

Uses the OpenAI client (via API_BASE_URL, MODEL_NAME, HF_TOKEN env vars)
to run an LLM-powered agent against IncidentEnv tasks.

Falls back to a deterministic heuristic agent when no API key is available.

Usage
-----
  python inference.py                   # runs task_easy
  python inference.py --task task_hard  # runs a specific task
  python inference.py --all-tasks       # runs all three tasks
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

# ── Allow running from the repo root without installing the package ───────────
sys.path.insert(0, os.path.dirname(__file__))

from server.environment import IncidentEnvironment
from server.models import Action

# ── Environment variables (provided by hackathon evaluators) ──────────────────
API_BASE_URL = os.environ.get("API_BASE_URL", "")
MODEL_NAME = os.environ.get("MODEL_NAME", "gpt-4o-mini")
HF_TOKEN = os.environ.get("HF_TOKEN", "")

# ── Try to initialise OpenAI client ──────────────────────────────────────────
_client = None
try:
    from openai import OpenAI
    if HF_TOKEN:
        _client = OpenAI(
            base_url=API_BASE_URL or "https://api.openai.com/v1",
            api_key=HF_TOKEN,
        )
except ImportError:
    pass


# ── Structured logging helpers ────────────────────────────────────────────────

def _log(tag: str, data: dict) -> None:
    """Emit a structured stdout log line in the required [TAG] format."""
    print(f"[{tag}] {json.dumps(data)}", flush=True)


# ── LLM-powered agent ────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are an expert SRE incident response agent. You are managing a production incident.

You will receive an observation containing: active alerts, service metrics, logs,
deployment history, and available tools.

Respond with EXACTLY ONE JSON object (no markdown fences) with these fields:
  - action_type: one of "run_diagnostic", "apply_fix", "write_postmortem", "close_incident", "escalate", "add_note"
  - tool: (string) required for run_diagnostic / apply_fix — the name of the diagnostic or fix
  - postmortem_text: (string) required for write_postmortem — a full markdown postmortem
  - reasoning: (string) your chain-of-thought explanation

Strategy:
1. Run diagnostics to gather evidence before applying any fix.
2. Identify the root cause from diagnostic outputs.
3. Apply the correct fix (not a guess — only after confirming root cause).
4. For hard tasks, write a comprehensive postmortem covering: root cause, timeline, impact, remediation, prevention, follow-up action items.
5. Close the incident once resolved.
"""


def _observation_to_text(obs) -> str:
    """Convert an Observation to a concise text summary for the LLM."""
    parts = []
    parts.append(f"Task: {obs.task_id} | Step: {obs.step_number}/{obs.max_steps} | Status: {obs.incident_status}")
    parts.append(f"\n--- Active Alerts ({len(obs.active_alerts)}) ---")
    for a in obs.active_alerts:
        parts.append(f"  [{a.severity.upper()}] {a.service}: {a.message}")
    parts.append(f"\n--- Service Metrics ---")
    for name, m in obs.service_metrics.items():
        parts.append(f"  {name}: CPU={m.cpu_percent}% Mem={m.memory_percent}% Err={m.error_rate}/s Lat_p99={m.latency_p99_ms}ms Status={m.status}")
    parts.append(f"\n--- Recent Logs ---")
    for lg in obs.recent_logs[-6:]:
        parts.append(f"  [{lg.level}] {lg.service}: {lg.message[:120]}")
    if obs.recent_deployments:
        parts.append(f"\n--- Recent Deployments ---")
        for d in obs.recent_deployments:
            parts.append(f"  {d.service} {d.version} (was {d.previous_version}) by {d.author}: {d.changelog[:100]}")
    if obs.incident_history:
        parts.append(f"\n--- Actions Taken So Far ---")
        for h in obs.incident_history[-5:]:
            parts.append(f"  Step {h.step}: {h.action_type} {h.tool or ''} → {h.feedback[:80]}")
    parts.append(f"\n--- Available Diagnostics: {', '.join(obs.available_diagnostics)}")
    parts.append(f"--- Available Fixes: {', '.join(obs.available_fixes)}")
    return "\n".join(parts)


def _parse_llm_action(text: str) -> Action:
    """Parse the LLM JSON response into an Action, with fallback."""
    text = text.strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    data = json.loads(text)
    return Action(
        action_type=data.get("action_type", "add_note"),
        tool=data.get("tool"),
        target_service=data.get("target_service"),
        postmortem_text=data.get("postmortem_text"),
        note=data.get("note"),
        reasoning=data.get("reasoning", ""),
    )


def _llm_decide(obs, history_msgs: list) -> tuple[Action, list]:
    """Ask the LLM for the next action. Returns (Action, updated_messages)."""
    obs_text = _observation_to_text(obs)
    history_msgs.append({"role": "user", "content": obs_text})

    response = _client.chat.completions.create(
        model=MODEL_NAME,
        messages=[{"role": "system", "content": _SYSTEM_PROMPT}] + history_msgs,
        temperature=0.2,
        max_tokens=1024,
    )
    reply = response.choices[0].message.content
    history_msgs.append({"role": "assistant", "content": reply})

    try:
        action = _parse_llm_action(reply)
    except (json.JSONDecodeError, KeyError):
        action = Action(
            action_type="add_note",
            note=f"LLM response parse error: {reply[:200]}",
            reasoning="Failed to parse LLM response, adding note instead.",
        )
    return action, history_msgs


# ── Heuristic agent (fallback when no API key) ──────────────────────────────

_DIAGNOSTIC_SEQUENCE = [
    "check_service_logs",
    "check_redis_memory",
    "check_redis_stats",
    "check_db_connections",
    "check_active_queries",
    "check_db_slow_query_log",
    "check_service_memory_trend",
    "check_heap_profile",
    "check_auth_service_logs",
    "check_payment_service_logs",
    "check_recent_deployments",
    "check_cpu_usage",
    "check_network_latency",
    "check_config_changes",
]

_FIX_MAP = {
    "redis_oom":                    "increase_redis_memory",
    "db_connection_pool_exhausted": "kill_long_running_query",
    "auth_memory_leak":            "rollback_auth_service",
    "real_world_cpu_stress":       "scale_up_api_gateway",
}

_POSTMORTEM_TEMPLATE = """\
## Root Cause
The incident was caused by a service-level failure identified through systematic
diagnostic investigation. The root cause was confirmed via log analysis and
service metrics review.

## Timeline
- T+0m  : Alerts fired; incident opened.
- T+2m  : Diagnostics run; root cause identified.
- T+4m  : Correct remediation applied; services began recovering.
- T+6m  : Incident resolved; monitoring confirmed stable.

## Impact
Affected services experienced elevated error rates and increased latency.
End-user impact: degraded availability during the incident window.

## Remediation
Applied the correct fix based on diagnostic findings. Services recovered
within the expected SLA window after remediation.

## Prevention
- Add automated runbook to detect and auto-remediate this class of failure.
- Increase alerting sensitivity on the root service.
- Schedule a capacity review for the affected component.

## Follow-up Action Items
1. Update runbook with findings from this incident.
2. Add regression test to catch this failure mode in staging.
3. Review similar services for the same misconfiguration.
"""


def _heuristic_decide(state, diag_queue: list, postmortem_written: bool, task_id: str) -> tuple[Action, bool]:
    """Deterministic heuristic agent. Returns (Action, postmortem_written)."""
    scenario_needs_pm = (task_id == "task_hard")

    if (
        scenario_needs_pm
        and state.root_cause_identified
        and state.fixes_applied
        and not postmortem_written
    ):
        return Action(
            action_type="write_postmortem",
            postmortem_text=_POSTMORTEM_TEMPLATE,
            reasoning="Root cause confirmed and fix applied. Writing postmortem to complete task_hard requirements.",
        ), True

    if state.root_cause_identified and not any(
        f in state.fixes_applied for f in _FIX_MAP.values()
    ):
        fix = _FIX_MAP.get(
            next((d for d in state.agent_discoveries if d in _FIX_MAP), ""),
            None,
        )
        if fix:
            return Action(
                action_type="apply_fix",
                tool=fix,
                reasoning=f"Root cause identified. Applying correct fix '{fix}'.",
            ), postmortem_written

    if state.incident_resolved:
        return Action(
            action_type="close_incident",
            reasoning="Incident resolved. Closing.",
        ), postmortem_written

    if diag_queue:
        tool = diag_queue.pop(0)
        while tool in state.diagnostics_run and diag_queue:
            tool = diag_queue.pop(0)
        return Action(
            action_type="run_diagnostic",
            tool=tool,
            reasoning=f"Systematic investigation: running '{tool}' to gather evidence.",
        ), postmortem_written

    return Action(
        action_type="escalate",
        reasoning="All diagnostics exhausted. Escalating.",
    ), postmortem_written


# ── Episode runner ────────────────────────────────────────────────────────────

def run_episode(task_id: str) -> dict:
    """
    Run one full episode against a task. Uses LLM if available, heuristic otherwise.
    Emits structured [START], [STEP], [END] logs to stdout.
    """
    use_llm = _client is not None
    agent_name = f"llm ({MODEL_NAME})" if use_llm else "heuristic (rule-based)"

    env = IncidentEnvironment()
    obs = env.reset(task_id)

    _log("START", {
        "task_id": task_id,
        "model": MODEL_NAME if use_llm else "heuristic",
        "agent": agent_name,
        "max_steps": obs.max_steps,
        "timestamp": time.time(),
    })

    step = 0
    done = False
    diag_queue = list(_DIAGNOSTIC_SEQUENCE)
    postmortem_written = False
    llm_messages: list = []

    while not done:
        step += 1
        state = env.state()

        if use_llm:
            try:
                action, llm_messages = _llm_decide(obs, llm_messages)
            except Exception as e:
                # Fallback to heuristic on LLM error
                action, postmortem_written = _heuristic_decide(
                    state, diag_queue, postmortem_written, task_id
                )
        else:
            action, postmortem_written = _heuristic_decide(
                state, diag_queue, postmortem_written, task_id
            )

        result = env.step(action)
        obs = result.observation
        done = result.done

        _log("STEP", {
            "step": step,
            "task_id": task_id,
            "action_type": action.action_type,
            "tool": action.tool or "",
            "reward": result.reward,
            "done": done,
            "feedback": result.info.get("feedback", "")[:120],
            "reasoning": (action.reasoning or "")[:120],
            "timestamp": time.time(),
        })

    # Final grade
    score, breakdown = env.grade_episode()
    # Clamp score to strictly (0, 1) — OpenEnv requires this
    score = round(max(0.01, min(0.99, score)), 4)

    _log("END", {
        "task_id": task_id,
        "score": score,
        "breakdown": breakdown,
        "steps": step,
        "root_cause_identified": env.state().root_cause_identified,
        "incident_resolved": env.state().incident_resolved,
        "agent": agent_name,
        "timestamp": time.time(),
    })

    return {
        "task_id": task_id,
        "score": score,
        "breakdown": breakdown,
        "steps": step,
        "root_cause_identified": env.state().root_cause_identified,
        "incident_resolved": env.state().incident_resolved,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="OpenEnv inference.py — IncidentEnv agent"
    )
    parser.add_argument(
        "--task", default=None,
        choices=["task_easy", "task_medium", "task_hard"],
        help="Single task to run (default: task_easy)",
    )
    parser.add_argument(
        "--all-tasks", action="store_true", default=True,
        help="Run all three tasks (default: True)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output results as JSON (in addition to structured logs)",
    )
    args = parser.parse_args()

    # Default: run all 3 tasks (OpenEnv requires at least 3 tasks with graders)
    if args.task:
        tasks = [args.task]
    else:
        tasks = ["task_easy", "task_medium", "task_hard"]
    results = []

    for task_id in tasks:
        try:
            result = run_episode(task_id)
        except Exception as exc:
            result = {"task_id": task_id, "error": str(exc), "score": 0.01}
            _log("END", {"task_id": task_id, "error": str(exc), "score": 0.01, "timestamp": time.time()})
        results.append(result)

    if args.json:
        avg = sum(r.get("score", 0.0) for r in results) / len(results)
        print(json.dumps({"results": results, "average_score": round(avg, 4)}, indent=2))


if __name__ == "__main__":
    main()
