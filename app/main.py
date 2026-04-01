"""
FastAPI application — full OpenEnv-compliant HTTP interface.

Core endpoints (OpenEnv spec):
  POST /reset  → Observation
  POST /step   → StepResult
  GET  /state  → EnvironmentState

Extended endpoints:
  GET  /tasks             → task list + action schema
  POST /grader            → episode score from grader
  GET  /health
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.environment import IncidentEnvironment
from app.graders import grade
from app.models import Action, EnvironmentState, Observation, StepResult
from app.scenarios import AVAILABLE_DIAGNOSTICS, AVAILABLE_FIXES, SCENARIOS

# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI(
    title="IncidentEnv",
    description=(
        "SRE Incident Response RL Environment — OpenEnv compliant.\n\n"
        "Simulates realistic production incidents that AI agents must diagnose "
        "and resolve using the standard step()/reset()/state() API."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# One global environment instance (stateful, single-user)
env = IncidentEnvironment()


# ── OpenEnv core endpoints ────────────────────────────────────────────────────


# ── OpenEnv core endpoints ────────────────────────────────────────────────────

class ResetRequest(BaseModel):
    task_id: Optional[str] = "task_easy"


@app.post(
    "/reset",
    response_model=Observation,
    summary="Reset the environment",
    description="Start a new episode. Accepts optional task_id.",
    tags=["OpenEnv Core"],
)
def reset(req: Optional[ResetRequest] = None):
    """OpenEnv validator often calls POST /reset with NO body → we must accept that."""
    task_id = req.task_id if req is not None else "task_easy"
    try:
        return env.reset(task_id=task_id or "task_easy")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@app.post(
    "/step",
    response_model=StepResult,
    summary="Take a step",
    description="Send one Action; receives Observation, reward, done, info.",
    tags=["OpenEnv Core"],
)
def step(action: Action):
    try:
        return env.step(action)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get(
    "/state",
    response_model=EnvironmentState,
    summary="Get current state",
    description="Returns the full internal episode state (debug/analysis only).",
    tags=["OpenEnv Core"],
)
def get_state():
    try:
        return env.get_state()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ── Extended endpoints ────────────────────────────────────────────────────────


@app.get(
    "/tasks",
    summary="List all tasks",
    description="Returns available tasks, their difficulty, and the Action schema.",
    tags=["Extended"],
)
def list_tasks():
    return {
        "tasks": [
            {
                "task_id": tid,
                "name": s["name"],
                "description": s["description"],
                "difficulty": s["difficulty"],
                "max_steps": s.get("max_steps", 20),
                "requires_postmortem": s.get("requires_postmortem", False),
                "root_service": s["root_service"],
                "affected_services": s["affected_services"],
            }
            for tid, s in SCENARIOS.items()
        ],
        "action_schema": {
            "action_type": {
                "type": "string",
                "enum": [
                    "run_diagnostic",
                    "apply_fix",
                    "write_postmortem",
                    "close_incident",
                    "escalate",
                    "add_note",
                ],
                "required": True,
                "description": "Type of action the agent wants to take.",
            },
            "tool": {
                "type": "string",
                "required": False,
                "description": "Diagnostic or fix name. Required for run_diagnostic / apply_fix.",
            },
            "target_service": {
                "type": "string",
                "required": False,
                "description": "Optional service context.",
            },
            "postmortem_text": {
                "type": "string",
                "required": False,
                "description": (
                    "Full postmortem text (markdown). Required for write_postmortem. "
                    "Should include: root cause, timeline, impact, remediation, prevention, follow-ups."
                ),
            },
            "note": {
                "type": "string",
                "required": False,
                "description": "Free-text annotation for add_note.",
            },
            "reasoning": {
                "type": "string",
                "required": False,
                "description": "Optional CoT explanation (not scored).",
            },
        },
        "available_diagnostics": AVAILABLE_DIAGNOSTICS,
        "available_fixes": AVAILABLE_FIXES,
    }


@app.post(
    "/grader",
    summary="Grade current episode",
    description="Returns the final grader score for the active episode.",
    tags=["Extended"],
)
def grader_endpoint():
    try:
        st = env.get_state()
        total, breakdown = grade(st.task_id, st)
        return {
            "task_id": st.task_id,
            "episode_id": st.episode_id,
            "score": total,
            "breakdown": breakdown,
            "step_count": st.step_count,
            "max_steps": st.max_steps,
            "incident_resolved": st.incident_resolved,
            "root_cause_identified": st.root_cause_identified,
            "postmortem_written": st.postmortem_written,
            "wrong_fixes_applied": st.wrong_fixes_applied,
            "diagnostics_run": st.diagnostics_run,
            "fixes_applied": st.fixes_applied,
            "discoveries": st.agent_discoveries,
        }
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post(
    "/baseline",
    summary="Run baseline agent",
    description=(
        "Launches the ReAct baseline agent against all three tasks and returns structured scores. "
        "Each result includes: final score, score breakdown, cascade events (wrong-fix consequences), "
        "reasoning quality, and a per-step action log. Requires OPENAI_API_KEY to be set."
    ),
    tags=["Extended"],
)
def baseline_endpoint():
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="OPENAI_API_KEY environment variable is not set.",
        )
    try:
        result = subprocess.run(
            [sys.executable, "baseline/run_baseline.py", "--all-tasks", "--json"],
            capture_output=True,
            text=True,
            timeout=300,
            env=dict(os.environ),
        )
        if result.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=result.stderr or "Baseline script failed.",
            )
        data = json.loads(result.stdout)
        results = data.get("baseline_results", [])
        avg = data.get("average_score", 0.0)

        # Enrich with a visual summary string per task
        summary_rows = []
        for r in results:
            bar = "█" * round(r.get("final_score", 0) * 20) + "░" * (20 - round(r.get("final_score", 0) * 20))
            cascades = len(r.get("cascade_events", []))
            summary_rows.append({
                "task_id":             r["task_id"],
                "score":               r.get("final_score", 0),
                "score_bar":           bar,
                "root_cause":          r.get("root_cause_identified", False),
                "resolved":            r.get("incident_resolved", False),
                "wrong_fixes":         r.get("wrong_fixes_applied", 0),
                "cascade_count":       cascades,
                "cascade_events":      r.get("cascade_events", []),
                "avg_reasoning_quality": r.get("avg_reasoning_quality", 0),
                "steps_used":          r.get("steps_used", 0),
            })

        return {
            "average_score": avg,
            "average_score_bar": "█" * round(avg * 20) + "░" * (20 - round(avg * 20)),
            "task_summaries": summary_rows,
            "baseline_results": results,
            "note": (
                "cascade_count > 0 means wrong fixes were applied that actively "
                "worsened the incident state (metrics degraded, new CRITICAL alerts fired). "
                "avg_reasoning_quality scores the CoT explanations the agent provided."
            ),
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Baseline script timed out after 300 s.")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to parse baseline output: {exc}")


class GenerateRequest(BaseModel):
    prompt: Optional[str] = "Create a database timeout scenario."

@app.get("/dashboard", response_class=HTMLResponse, tags=["Extended"])
def get_dashboard():
    with open("app/frontend.html", "r", encoding="utf-8") as f:
        return f.read()

@app.post("/generate_scenario", tags=["Extended"])
async def generate_scenario(req: GenerateRequest):
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=400, detail="OPENAI_API_KEY not set.")
    
    import uuid, copy
    from app.scenarios import SCENARIOS
    from openai import AsyncOpenAI
    
    try:
        client = AsyncOpenAI(api_key=api_key)
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": f"Generate a short creative incident name and a highly technical description for an SRE incident regarding: {req.prompt}. Format exactly as 'Name|Description'"}],
            temperature=0.7
        )
        result = response.choices[0].message.content.split("|")
        name = result[0].strip() if len(result) > 0 else "Dynamic Failure"
        desc = result[1].strip() if len(result) > 1 else str(req.prompt)
    except Exception as e:
        name = "Dynamic Task (API Fallback)"
        desc = f"Error calling OpenAI: {e}"

    new_id = f"task_dyn_{uuid.uuid4().hex[:8]}"
    
    # We clone task_easy as the structural base for the simulation mechanics
    new_scenario = copy.deepcopy(SCENARIOS["task_easy"])
    new_scenario["id"] = new_id
    new_scenario["name"] = name
    new_scenario["description"] = f"[AI Generated Incident] {desc}\n\n(Base simulation uses Redis OOM mechanics)"
    SCENARIOS[new_id] = new_scenario
    
    return {"status": "success", "task_id": new_id, "name": name, "description": desc}

@app.get("/health", tags=["Meta"])
def health():
    return {"status": "ok", "environment": "IncidentEnv", "version": "1.0.0"}


@app.get("/", tags=["Meta"])
def root():
    return {
        "message": "IncidentEnv is running.",
        "docs": "/docs",
        "tasks": "/tasks",
        "reset": "POST /reset",
        "step": "POST /step",
        "state": "GET /state",
        "grader": "POST /grader",
    }
