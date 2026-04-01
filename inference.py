"""
inference.py — OpenEnv required entry point.

This file is called by the OpenEnv validator to confirm the environment
can run a complete episode end-to-end using only the standard
reset() → step() → grader() loop.

It also serves as the simplest possible example of how to interact with
IncidentEnv programmatically (no OpenAI key required — uses a rule-based
heuristic agent).

Usage
-----
  python inference.py                   # runs task_easy with heuristic agent
  python inference.py --task task_hard  # runs a specific task
  python inference.py --all-tasks       # runs all three tasks
"""
from __future__ import annotations

import argparse
import json
import sys
import os

# ── Allow running from the repo root without installing the package ───────────
sys.path.insert(0, os.path.dirname(__file__))

from server.environment import IncidentEnvironment
from server.models import Action


# ── Heuristic agent (no API key needed) ──────────────────────────────────────

# Ordered diagnostic strategy: check logs → check service-specific tools → deployments
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

# Map discovered root causes → correct fix
_FIX_MAP = {
    "redis_oom":                      "increase_redis_memory",
    "db_connection_pool_exhausted":   "kill_long_running_query",
    "auth_memory_leak":               "rollback_auth_service",
    "real_world_cpu_stress":          "scale_up_api_gateway",
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


def run_heuristic_episode(task_id: str, verbose: bool = True) -> dict:
    """
    Run one episode with a deterministic heuristic agent.
    No API key required. Returns the final grader result dict.
    """
    env = IncidentEnvironment()
    obs = env.reset(task_id)

    if verbose:
        print(f"\n{'─'*60}")
        print(f"  Task : {task_id}")
        print(f"  Agent: heuristic (rule-based, no API key needed)")
        print(f"{'─'*60}")

    diag_queue = list(_DIAGNOSTIC_SEQUENCE)
    step = 0
    done = False
    postmortem_written = False

    while not done:
        step += 1
        state = env.state()

        # 1. Write postmortem if needed and not yet done
        scenario_needs_pm = (task_id == "task_hard")
        if (
            scenario_needs_pm
            and state.root_cause_identified
            and state.fixes_applied
            and not postmortem_written
        ):
            action = Action(
                action_type="write_postmortem",
                postmortem_text=_POSTMORTEM_TEMPLATE,
                reasoning="Root cause confirmed and fix applied. Writing postmortem to complete task_hard requirements.",
            )
            postmortem_written = True

        # 2. Apply fix if root cause known and no correct fix applied yet
        elif state.root_cause_identified and not any(
            f in state.fixes_applied
            for f in _FIX_MAP.values()
        ):
            fix = _FIX_MAP.get(
                next((d for d in state.agent_discoveries if d in _FIX_MAP), ""),
                None,
            )
            if fix:
                action = Action(
                    action_type="apply_fix",
                    tool=fix,
                    reasoning=f"Root cause identified as '{state.agent_discoveries[-1]}'. Applying correct fix '{fix}'.",
                )
            else:
                action = Action(
                    action_type="add_note",
                    note="Root cause identified but fix mapping not found — escalating.",
                    reasoning="No fix mapping available for discovered root cause.",
                )

        # 3. Close if resolved
        elif state.incident_resolved:
            action = Action(
                action_type="close_incident",
                reasoning="Incident resolved. Closing.",
            )

        # 4. Run next diagnostic
        elif diag_queue:
            tool = diag_queue.pop(0)
            # Skip already-run diagnostics
            while tool in state.diagnostics_run and diag_queue:
                tool = diag_queue.pop(0)
            action = Action(
                action_type="run_diagnostic",
                tool=tool,
                reasoning=f"Systematic investigation: running '{tool}' to gather evidence before applying any fix.",
            )

        # 5. Escalate as last resort
        else:
            action = Action(
                action_type="escalate",
                reasoning="All diagnostics exhausted without identifying root cause. Escalating to senior on-call.",
            )

        result = env.step(action)
        done = result.done

        if verbose:
            atype  = action.action_type
            tool   = action.tool or action.action_type
            reward = result.reward
            fb     = result.info.get("feedback", "")[:80]
            rq     = result.info.get("reasoning_quality", "")
            print(
                f"  Step {step:2d}  {atype:20s}  {tool:30s}  "
                f"r={reward:.3f}  rq={rq}"
            )
            if fb:
                print(f"         ↳ {fb}")

    # Final grade
    score, breakdown = env.grade_episode()

    if verbose:
        print(f"\n  Final score : {score:.3f}")
        for k, v in breakdown.items():
            bar = "█" * round(v * 20) + "░" * (20 - round(v * 20))
            print(f"    {k:35s}: {v:.3f}  {bar}")

    return {
        "task_id":   task_id,
        "score":     score,
        "breakdown": breakdown,
        "steps":     step,
        "root_cause_identified": env.state().root_cause_identified,
        "incident_resolved":     env.state().incident_resolved,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="OpenEnv inference.py — heuristic agent for IncidentEnv"
    )
    parser.add_argument(
        "--task", default="task_easy",
        choices=["task_easy", "task_medium", "task_hard"],
        help="Single task to run (default: task_easy)",
    )
    parser.add_argument(
        "--all-tasks", action="store_true",
        help="Run all three tasks",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output results as JSON",
    )
    args = parser.parse_args()

    tasks   = ["task_easy", "task_medium", "task_hard"] if args.all_tasks else [args.task]
    verbose = not args.json
    results = []

    for task_id in tasks:
        try:
            result = run_heuristic_episode(task_id, verbose=verbose)
        except Exception as exc:
            result = {"task_id": task_id, "error": str(exc), "score": 0.0}
        results.append(result)

    if args.json:
        avg = sum(r.get("score", 0.0) for r in results) / len(results)
        print(json.dumps({"results": results, "average_score": round(avg, 4)}, indent=2))
    else:
        scores = [r.get("score", 0.0) for r in results]
        avg = sum(scores) / len(scores)
        print(f"\n{'─'*60}")
        print(f"  Average score across {len(tasks)} task(s): {avg:.3f}")
        print(f"{'─'*60}")


if __name__ == "__main__":
    main()
