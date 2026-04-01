"""
Baseline inference script for IncidentEnv.

Runs a ReAct-style agent (GPT-4o-mini by default) against all three tasks
using the OpenAI API and reports reproducible scores.

Usage
-----
  # Single task
  python baseline/run_baseline.py --task task_easy

  # All tasks, human-readable output
  python baseline/run_baseline.py --all-tasks

  # All tasks, JSON (used by POST /baseline endpoint)
  python baseline/run_baseline.py --all-tasks --json

  # Custom model / environment URL
  python baseline/run_baseline.py --all-tasks --model gpt-4o --env-url http://localhost:7860

Environment variables
---------------------
  OPENAI_API_KEY      Required
  INCIDENT_ENV_URL    Default: http://localhost:7860
  OPENAI_BASE_URL     Optional custom base URL
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

import requests
from openai import OpenAI

# ── Config ────────────────────────────────────────────────────────────────────

ENV_URL: str = os.getenv("INCIDENT_ENV_URL", "http://localhost:7860")
DEFAULT_MODEL: str = "gpt-4o-mini"
MAX_STEPS: int = 25

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY", ""),
    base_url=os.getenv("OPENAI_BASE_URL", None) or None,
)

# ── Visual helpers ────────────────────────────────────────────────────────────

# ANSI colours (disabled when --json)
_USE_COLOR = True

def _c(code: str, text: str) -> str:
    if not _USE_COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"

RED    = lambda t: _c("31", t)
GREEN  = lambda t: _c("32", t)
YELLOW = lambda t: _c("33", t)
CYAN   = lambda t: _c("36", t)
BOLD   = lambda t: _c("1",  t)
DIM    = lambda t: _c("2",  t)

DIFFICULTY_BADGE = {
    "easy":   GREEN("● EASY  "),
    "medium": YELLOW("● MEDIUM"),
    "hard":   RED("● HARD  "),
}

def _bar(value: float, width: int = 20, full: str = "█", empty: str = "░") -> str:
    filled = round(value * width)
    return full * filled + empty * (width - filled)

def _score_colour(score: float) -> str:
    if score >= 0.80:
        return GREEN(f"{score:.3f}")
    if score >= 0.55:
        return YELLOW(f"{score:.3f}")
    return RED(f"{score:.3f}")

def _reward_icon(r: float) -> str:
    if r > 0.15:   return GREEN("▲")
    if r > 0.0:    return CYAN("·")
    if r == 0.0:   return DIM("○")
    return RED("▼")

TASK_INFO = {
    "task_easy":   {"badge": DIFFICULTY_BADGE["easy"],   "emoji": "🔴"},
    "task_medium": {"badge": DIFFICULTY_BADGE["medium"], "emoji": "🟠"},
    "task_hard":   {"badge": DIFFICULTY_BADGE["hard"],   "emoji": "🔴"},
}

# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are an expert Site Reliability Engineer (SRE). A production incident is open.
Your goal: diagnose the root cause systematically and apply the correct fix.

ACTION TYPES (you take exactly one per turn):
  run_diagnostic   — run a named diagnostic tool. Set "tool" to the tool name.
  apply_fix        — apply a named remediation. Set "tool" to the fix name.
  write_postmortem — write a full postmortem. Set "postmortem_text" to the text.
  close_incident   — mark the incident resolved (use after fix + postmortem if needed).
  escalate         — escalate to senior on-call as a last resort.
  add_note         — annotate the timeline (no score effect).

SCORING RULES:
  • Run diagnostics FIRST — identify root cause before applying any fix.
  • Wrong fixes incur a −5 pt penalty AND make the system worse (cascade effect).
  • Repeating a diagnostic incurs a −2 pt penalty.
  • Do NOT chase red-herring symptoms — look for the underlying root cause.
  • For task_hard, write a thorough postmortem after fixing (root cause, timeline,
    impact, remediation, prevention, follow-ups — all sections needed for full score).

REASONING:
  Always include a "reasoning" field explaining WHY you chose this action.
  Mention the specific metric, log line, or signal that led you to this decision.
  Well-reasoned actions earn a small bonus (up to +3 pts).

ALWAYS respond with a single JSON object — no prose, no markdown fences.

Example:
{"action_type": "run_diagnostic", "tool": "check_redis_memory", "reasoning": "The OOM alert on redis-cache and 10x PostgreSQL read spike suggest Redis memory exhaustion. Checking redis memory to confirm maxmemory limit breach before applying fix."}
"""

# ── Observation → prompt ──────────────────────────────────────────────────────

def obs_to_prompt(obs: dict) -> str:
    alerts = "\n".join(
        f"  [{a['severity'].upper():8s}] [{a['service']}] {a['message']}"
        for a in obs.get("active_alerts", [])
    ) or "  (none)"

    metrics = "\n".join(
        f"  {m['service_name']:22s}│ CPU {m['cpu_percent']:5.1f}%"
        f" │ MEM {m['memory_percent']:5.1f}%"
        f" │ ERR {m['error_rate']:8.1f}/s"
        f" │ p99 {m['latency_p99_ms']:8.1f}ms"
        f" │ {m['status'].upper()}"
        for m in obs.get("service_metrics", {}).values()
    )

    logs = "\n".join(
        f"  [{l['level']:8s}] [{l['service']}] {l['message']}"
        for l in obs.get("recent_logs", [])[-8:]
    )

    deployments = "\n".join(
        f"  {d['timestamp']} — {d['service']} {d['version']} "
        f"(prev {d['previous_version']}): {d['changelog']}"
        for d in obs.get("recent_deployments", [])
    ) or "  (none in last 24h)"

    history = obs.get("incident_history", [])
    history_str = "\n".join(
        f"  Step {h['step']:2d} [{h['action_type']:20s}] "
        f"{h.get('tool',''):30s} → {h.get('feedback','')[:100]}"
        for h in history[-10:]
    ) if history else "  (no actions yet)"

    runbook = "\n".join(
        f"  Pattern: {r['alert_pattern']}\n    Hint: {r['hint']}"
        for r in obs.get("runbook_hints", [])
    ) or "  (none)"

    diags  = ", ".join(obs.get("available_diagnostics", []))
    fixes  = ", ".join(obs.get("available_fixes", []))
    step   = obs.get("step_number", "?")
    msteps = obs.get("max_steps", "?")
    elapsed= obs.get("time_elapsed_minutes", 0)
    status = obs.get("incident_status", "?").upper()

    return f"""\
╔══ INCIDENT STATUS: {status} ══ Step {step}/{msteps} ══ Elapsed {elapsed:.0f} min ══╗

ACTIVE ALERTS:
{alerts}

SERVICE METRICS:
{metrics}

RECENT LOGS (last 8):
{logs}

RECENT DEPLOYMENTS:
{deployments}

RUNBOOK HINTS:
{runbook}

ACTIONS TAKEN:
{history_str}

AVAILABLE DIAGNOSTICS: {diags}
AVAILABLE FIXES:       {fixes}
╚══════════════════════════════════════════════════════╝

Respond with ONE JSON action object (include "reasoning" field)."""


def _parse_action(raw: str) -> dict:
    """Extract a JSON object from the model response (handles markdown fences)."""
    clean = raw.strip()
    clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.MULTILINE)
    clean = re.sub(r"\s*```$", "", clean, flags=re.MULTILINE).strip()
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        m = re.search(r"\{.*?\}", clean, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
    return {"action_type": "add_note", "note": f"Parse error on: {raw[:80]}"}


# ── Episode runner ────────────────────────────────────────────────────────────

def run_episode(task_id: str, model: str = DEFAULT_MODEL, verbose: bool = True) -> dict:
    """Run one full episode and return score data."""

    r = requests.post(f"{ENV_URL}/reset", json={"task_id": task_id}, timeout=30)
    r.raise_for_status()
    obs = r.json()

    info = TASK_INFO.get(task_id, {"badge": "", "emoji": "⚙️"})

    if verbose:
        width = 68
        print()
        print("┌" + "─" * (width - 2) + "┐")
        title = f"  {info['emoji']}  {task_id.upper().replace('_', ' ')}  │  {model}"
        print(f"│{title:<{width-2}}│")
        badge_line = f"  {info['badge']}  │  max {obs.get('max_steps','?')} steps"
        print(f"│{badge_line:<{width-2}}│")
        print("└" + "─" * (width - 2) + "┘")

    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    total_step_reward: float = 0.0
    steps: int = 0
    done: bool = False
    wrong_fix_count = 0
    cascade_events: list[str] = []
    reasoning_scores: list[float] = []
    step_log: list[dict] = []

    while not done and steps < MAX_STEPS:
        user_content = obs_to_prompt(obs)
        messages.append({"role": "user", "content": user_content})

        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.1,
            max_tokens=768,
        )
        raw = response.choices[0].message.content or ""
        messages.append({"role": "assistant", "content": raw})

        action = _parse_action(raw)

        step_r = requests.post(f"{ENV_URL}/step", json=action, timeout=30)
        step_r.raise_for_status()
        result = step_r.json()

        obs = result["observation"]
        reward = result["reward"]
        total_step_reward += reward
        done = result["done"]
        steps += 1
        info_data = result.get("info", {})

        rq = info_data.get("reasoning_quality")
        if rq is not None:
            reasoning_scores.append(rq)

        is_cascade = "⚡ CASCADE" in info_data.get("feedback", "")
        is_wrong   = info_data.get("reward_breakdown", {}).get("fix", 0) < 0
        if is_wrong:
            wrong_fix_count += 1
        if is_cascade:
            cascade_events.append(action.get("tool", "?"))

        step_log.append({
            "step": steps,
            "action_type": action.get("action_type", "?"),
            "tool": action.get("tool", ""),
            "reward": reward,
            "is_wrong": is_wrong,
            "is_cascade": is_cascade,
            "reasoning": action.get("reasoning", ""),
        })

        if verbose:
            atype     = action.get("action_type", "?")
            tool      = action.get("tool", "")
            reasoning = action.get("reasoning", "")
            fb        = info_data.get("feedback", "")[:100]

            icon = _reward_icon(reward)
            wrong_tag  = RED(" [WRONG FIX → CASCADE]") if is_wrong else ""
            repeat_tag = YELLOW(" [REPEATED]") if "repeated" in fb.lower() else ""

            print(
                f"  {DIM(f'Step {steps:2d}')}  "
                f"{CYAN(f'{atype:20s}')}  "
                f"{BOLD(tool):30s}  "
                f"{icon} {_score_colour(reward)}"
                f"{wrong_tag}{repeat_tag}"
            )
            if reasoning:
                print(f"           {DIM('↳ WHY:')} {DIM(reasoning[:90])}")
            if fb and fb != reasoning[:100]:
                print(f"           {DIM('↳ ENV:')} {fb}")

    # ── Final grade ───────────────────────────────────────────────────────────
    grade_r = requests.post(f"{ENV_URL}/grader", timeout=30)
    grade_r.raise_for_status()
    grade_data = grade_r.json()

    final_score = grade_data["score"]
    breakdown   = grade_data["breakdown"]
    avg_rq      = round(sum(reasoning_scores) / len(reasoning_scores), 3) if reasoning_scores else 0.0

    if verbose:
        w = 68
        print()
        print("  " + "─" * (w - 4))
        print(f"  {BOLD('RESULTS')}")
        print(f"  Final score   : {_score_colour(final_score)}  {_bar(final_score)}")
        print(f"  Steps used    : {steps}/{obs.get('max_steps','?')}")
        print(f"  Cumulative Δr : {total_step_reward:.4f}")
        if cascade_events:
            print(f"  {RED('CASCADE events')}: {len(cascade_events)}  ← wrong fixes: {cascade_events}")
        else:
            print(f"  {GREEN('No cascades')}  (zero wrong fixes applied)")
        print(f"  Avg reasoning : {avg_rq:.3f}  {_bar(avg_rq)}")
        print()
        print(f"  {BOLD('Score Breakdown:')}")
        for k, v in breakdown.items():
            bar = _bar(v, width=15)
            check = GREEN("✓") if v > 0 else RED("✗")
            print(f"    {check} {k:35s}: {_score_colour(v)}  {bar}")

        if wrong_fix_count > 0:
            print()
            print(f"  {RED('⚠  Wrong fixes applied: ' + str(wrong_fix_count))}")
            print(f"     Each wrong fix triggered a CASCADE, making the system state")
            print(f"     objectively worse in the next observation. See above for details.")

    return {
        "task_id":                task_id,
        "model":                  model,
        "steps_used":             steps,
        "cumulative_step_reward": round(total_step_reward, 4),
        "final_score":            final_score,
        "score_breakdown":        breakdown,
        "root_cause_identified":  grade_data["root_cause_identified"],
        "incident_resolved":      grade_data["incident_resolved"],
        "postmortem_written":     grade_data.get("postmortem_written", False),
        "wrong_fixes_applied":    grade_data.get("wrong_fixes_applied", 0),
        "cascade_events":         cascade_events,
        "avg_reasoning_quality":  avg_rq,
        "step_log":               step_log,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def _print_summary(results: list[dict]) -> None:
    scores  = [r.get("final_score", 0.0) for r in results]
    avg     = sum(scores) / len(scores)
    w = 68
    print()
    print("┌" + "═" * (w - 2) + "┐")
    print(f"│{'  BASELINE SUMMARY':^{w-2}}│")
    print("├" + "─" * (w - 2) + "┤")
    for r in results:
        if "error" in r:
            line = f"  {r['task_id']:15s}  ERROR: {r['error'][:40]}"
        else:
            score  = r.get("final_score", 0.0)
            rc     = GREEN("✓") if r.get("root_cause_identified") else RED("✗")
            res    = GREEN("✓") if r.get("incident_resolved")     else RED("✗")
            cas    = (RED(f"  {len(r.get('cascade_events',[]))}× cascade") 
                      if r.get("cascade_events") else GREEN("  clean"))
            line = (
                f"  {r['task_id']:15s}  "
                f"score {_score_colour(score)}  "
                f"{_bar(score, 12)}  "
                f"RC {rc}  resolved {res}{cas}"
            )
        print(f"│{line:<{w-2}}│")
    print("├" + "─" * (w - 2) + "┤")
    avg_line = f"  Average score :  {_score_colour(avg)}  {_bar(avg, 12)}"
    print(f"│{avg_line:<{w-2}}│")
    print("└" + "═" * (w - 2) + "┘")


def main() -> None:
    global ENV_URL, _USE_COLOR

    parser = argparse.ArgumentParser(description="IncidentEnv baseline agent")
    parser.add_argument("--task", default="task_easy",
                        choices=["task_easy", "task_medium", "task_hard"])
    parser.add_argument("--all-tasks", action="store_true",
                        help="Run all three tasks in sequence")
    parser.add_argument("--json", action="store_true",
                        help="Output results as JSON (used by /baseline endpoint)")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"OpenAI model to use (default: {DEFAULT_MODEL})")
    parser.add_argument("--env-url", default=ENV_URL,
                        help=f"IncidentEnv base URL (default: {ENV_URL})")
    args = parser.parse_args()

    ENV_URL = args.env_url
    if args.json:
        _USE_COLOR = False

    if not os.getenv("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    tasks   = (["task_easy", "task_medium", "task_hard"] if args.all_tasks else [args.task])
    verbose = not args.json
    results = []

    for task_id in tasks:
        try:
            result = run_episode(task_id, model=args.model, verbose=verbose)
        except Exception as exc:
            result = {
                "task_id":       task_id,
                "error":         str(exc),
                "final_score":   0.0,
                "score_breakdown": {},
            }
        results.append(result)

    if args.json:
        avg = sum(r.get("final_score", 0.0) for r in results) / len(results)
        print(json.dumps({"baseline_results": results, "average_score": round(avg, 4)}, indent=2))
    else:
        _print_summary(results)


if __name__ == "__main__":
    main()
