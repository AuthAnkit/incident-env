"""
Deterministic graders for all three tasks.

Each grader:
  - accepts an EnvironmentState
  - returns (total: float[0,1], breakdown: dict[str,float])
  - is entirely deterministic — same state always produces same score
  - rewards partial progress (not just binary win/lose)
"""
from __future__ import annotations

from app.models import EnvironmentState
from typing import Tuple


# ── Postmortem quality scorer ─────────────────────────────────────────────────

_POSTMORTEM_SECTIONS = {
    "root cause": 0.20,
    "timeline": 0.15,
    "impact": 0.15,
    "remediation": 0.20,
    "prevention": 0.15,
    "follow-up": 0.10,
    "action item": 0.05,   # alias for follow-up
}

def score_postmortem(text: str) -> float:
    """
    Score a postmortem string 0.0–1.0.
    Checks for the presence of key sections by keyword matching.
    Bonuses for length (thoroughness) and section headers (structure).
    """
    if not text or len(text.strip()) < 80:
        return 0.0

    text_lower = text.lower()
    score = 0.0
    for keyword, weight in _POSTMORTEM_SECTIONS.items():
        if keyword in text_lower:
            score += weight

    # Cap at 1.0 — "action item" and "follow-up" are aliases so both can't add
    score = min(score, 0.95)

    # Thoroughness bonus
    if len(text) >= 600:
        score = min(1.0, score + 0.05)

    return round(score, 3)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _has(state: EnvironmentState, discovery: str) -> bool:
    return discovery in state.agent_discoveries

def _fixed(state: EnvironmentState, fix: str) -> bool:
    return fix in state.fixes_applied

def _any_wrong(state: EnvironmentState, wrongs: list[str]) -> bool:
    return any(f in state.fixes_applied for f in wrongs)

def _wrong_count(state: EnvironmentState, wrongs: list[str]) -> int:
    return sum(1 for f in wrongs if f in state.fixes_applied)


# ── Task graders ──────────────────────────────────────────────────────────────

def grade_task_easy(state: EnvironmentState) -> Tuple[float, dict]:
    """
    Task 1 — Redis OOM
    ┌────────────────────────────────┬───────┐
    │ Criterion                      │ Weight│
    ├────────────────────────────────┼───────┤
    │ Root cause identified          │  40 % │
    │ Correct fix applied            │  40 % │
    │ No wrong fixes                 │  20 % │
    └────────────────────────────────┴───────┘
    """
    b: dict[str, float] = {}

    # 40 pts — root cause
    b["root_cause_identified"] = 0.40 if _has(state, "redis_oom") else 0.0

    # 40 pts — correct fix
    b["correct_fix_applied"] = 0.40 if _fixed(state, "increase_redis_memory") else 0.0

    # 20 pts — no wrong fixes (graded down 7 pts per wrong fix applied)
    wrongs = ["restart_api_gateway", "scale_up_api_gateway", "flush_redis_cache",
              "restart_user_service"]
    n_wrong = _wrong_count(state, wrongs)
    b["no_wrong_fixes"] = round(max(0.0, 0.20 - 0.07 * n_wrong), 3)

    total = round(min(1.0, sum(b.values())), 3)
    return total, b


def grade_task_medium(state: EnvironmentState) -> Tuple[float, dict]:
    """
    Task 2 — DB Connection Pool
    ┌────────────────────────────────┬───────┐
    │ Criterion                      │ Weight│
    ├────────────────────────────────┼───────┤
    │ Root cause identified          │  30 % │
    │ Specific query PID identified  │   5 % │
    │ Correct fix applied            │  35 % │
    │ No wrong fixes                 │  15 % │
    │ Incident resolved              │  10 % │
    │ Avoided red-herring escalation │   5 % │
    └────────────────────────────────┴───────┘
    """
    b: dict[str, float] = {}

    # 30 pts — root cause (db pool, NOT api-gateway CPU)
    b["root_cause_identified"] = 0.30 if _has(state, "db_connection_pool_exhausted") else 0.0

    # 5 pts — bonus: identified the specific query PID
    b["query_pid_identified"] = 0.05 if _has(state, "long_running_query_pid_28842") else 0.0

    # 35 pts — correct fix
    b["correct_fix_applied"] = 0.35 if _fixed(state, "kill_long_running_query") else 0.0

    # 15 pts — no wrong fixes
    wrongs = ["restart_api_gateway", "scale_up_api_gateway",
              "restart_user_service", "increase_connection_pool_size"]
    n_wrong = _wrong_count(state, wrongs)
    b["no_wrong_fixes"] = round(max(0.0, 0.15 - 0.05 * n_wrong), 3)

    # 10 pts — incident marked resolved
    b["incident_resolved"] = 0.10 if state.incident_resolved else 0.0

    # 5 pts — didn't escalate when not necessary
    b["no_premature_escalation"] = 0.0 if state.escalated else 0.05

    total = round(min(1.0, sum(b.values())), 3)
    return total, b


def grade_task_hard(state: EnvironmentState) -> Tuple[float, dict]:
    """
    Task 3 — Auth Memory Leak
    ┌────────────────────────────────┬───────┐
    │ Criterion                      │ Weight│
    ├────────────────────────────────┼───────┤
    │ Root cause identified          │  25 % │
    │ Deployment linked              │  10 % │
    │ Correct fix applied            │  25 % │
    │ Postmortem quality             │  20 % │
    │ Avoided red herrings           │  15 % │
    │ Incident resolved              │   5 % │
    └────────────────────────────────┴───────┘
    """
    b: dict[str, float] = {}

    # 25 pts — correct root cause (auth leak, NOT payment disk I/O or api CPU)
    b["root_cause_identified"] = 0.25 if _has(state, "auth_memory_leak") else 0.0

    # 10 pts — linked to the v2.4.1 deployment
    b["deployment_linked"] = 0.10 if _has(state, "auth_v241_deployment") else 0.0

    # 25 pts — rollback (NOT restart, NOT memory bump)
    b["correct_fix_applied"] = 0.25 if _fixed(state, "rollback_auth_service") else 0.0

    # 20 pts — postmortem quality (scaled)
    b["postmortem_quality"] = round(0.20 * state.postmortem_quality, 3)

    # 15 pts — avoided red herrings (payment disk I/O, api CPU chase)
    wrongs = [
        "restart_payment_service", "restart_api_gateway",
        "restart_auth_service",
        "increase_payment_service_memory", "increase_auth_service_memory",
        "kill_long_running_query",
    ]
    n_wrong = _wrong_count(state, wrongs)
    b["avoided_red_herrings"] = round(max(0.0, 0.15 - 0.05 * n_wrong), 3)

    # 5 pts — incident resolved
    b["incident_resolved"] = 0.05 if state.incident_resolved else 0.0

    total = round(min(1.0, sum(b.values())), 3)
    return total, b


# ── Dispatch ──────────────────────────────────────────────────────────────────

_GRADERS = {
    "task_easy": grade_task_easy,
    "task_medium": grade_task_medium,
    "task_hard": grade_task_hard,
}


def grade(task_id: str, state: EnvironmentState) -> Tuple[float, dict]:
    """Route to the correct grader. Raises ValueError for unknown task_id."""
    if task_id not in _GRADERS:
        raise ValueError(
            f"Unknown task_id '{task_id}'. Available: {list(_GRADERS.keys())}"
        )
    return _GRADERS[task_id](state)
