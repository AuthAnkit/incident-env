---
title: Incident Env
emoji: 🚨
colorFrom: red
colorTo: indigo
sdk: docker
app_file: inference.py
pinned: false
---
# IncidentEnv 🚨

**SRE Incident Response Simulator — OpenEnv compliant**

> Train and evaluate AI agents to diagnose and resolve production incidents using a realistic monitoring environment. One of the most expensive daily engineering workflows at every tech company — now a structured RL problem.

[![OpenEnv](https://img.shields.io/badge/OpenEnv-compliant-blue)](https://openenv.dev)
[![Python](https://img.shields.io/badge/python-3.11-blue)](https://www.python.org)
[![License](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)

---

## Why IncidentEnv?

Every tech company has on-call engineers. Responding correctly to a production incident — in the right order, without chasing red herrings — is a skill that takes years to develop. IncidentEnv turns this into a structured, measurable RL problem:

- **Real signals**: typed alerts, service metrics (CPU/memory/error-rate/latency), structured logs, deployment history — exactly what an SRE sees
- **Partial rewards at every step**: correct diagnostics earn credit; wrong fixes incur penalties; time pressure from a per-step cost
- **Cascading consequences**: wrong fixes actively degrade the environment state (metrics spike, new alerts fire) — not just a score penalty, but a harder next state to navigate
- **Reasoning evaluation**: agents that explain *why* they take each action earn a small bonus, encouraging chain-of-thought
- **Deterministic graders**: reproducible scores with per-criterion breakdown
- **Escalating difficulty**: from a clear Redis OOM to a multi-service cascade with intentional red herrings

---

## Environment Description

The agent plays the role of an on-call SRE. Each episode, a production incident is open. The agent must:

1. Inspect alerts, metrics, and logs
2. Run targeted diagnostic tools to identify the root cause
3. Apply the correct fix ← **careful: wrong fixes make things worse**
4. (For task_hard) Write a structured postmortem
5. Close the incident

The environment returns a meaningful reward signal at every step — not just a binary win at the end.

---

## 🔥 The Cascade Mechanic (WOW Factor)

This is what makes IncidentEnv unique: **wrong fixes don't just cost points — they actively break things.**

```
Agent applies wrong fix
        │
        ▼
┌──────────────────────────────────────────┐
│  _trigger_cascade() fires                │
│  • All service error_rate  × 2.5         │
│  • All latency_p99_ms      × 1.8         │
│  • All cpu_percent         × 1.25        │
│  • Services promoted to "down" status    │
│  • New CRITICAL alert injected           │
│  • New cascade log entries added         │
└──────────────────────────────────────────┘
        │
        ▼
Next observation is objectively WORSE.
Agent must re-diagnose a degraded system.
```

**Why this matters for RL training:**
- Eliminates lucky-guess strategies: random fix applications collapse the environment
- Forces systematic diagnosis before action
- Trains agents that explain *why* before acting
- Reward landscape has a deep gradient around correct diagnosis → correct fix

**Example cascade in the terminal:**
```
  Step  4  apply_fix             restart_api_gateway           ▼ 0.0000
           ↳ WHY: CPU looks high, maybe restarting will help
           ↳ ENV: ✗ Wrong fix. Root cause unaddressed. Secondary failures spreading.
           ↳ ⚡ CASCADE: System degrading further. New alerts firing. Re-diagnose.

  Step  5  run_diagnostic        check_redis_memory             ▲ 0.2000
           ↳ WHY: After cascade, error logs point to OOM. Checking Redis now.
           ↳ ENV: [check_redis_memory] maxmemory: 2gb, used: 2.0gb (100%), hits: 0
```

---

## Tasks

| Task ID | Name | Difficulty | Max Steps | Postmortem | Root Cause |
|---|---|---|---|---|---|
| `task_easy` | Redis Cache Out-of-Memory | ● EASY | 12 | No | Redis OOM with noeviction policy |
| `task_medium` | DB Connection Pool Exhaustion | ● MEDIUM | 18 | No | Analytics query on prod DB; API CPU is a red herring |
| `task_hard` | Auth Service Memory Leak | ● HARD | 22 | **Yes** | v2.4.1 deployment unbounded JWTCache; 2 red herrings |

### Task 1 — Easy: Redis Cache OOM
Redis has hit its 2 GB `maxmemory` limit with `noeviction` policy. Cache writes are rejected; direct PostgreSQL reads spike 10×; API latency rises. Signal is clear, no red herrings. Fix: `increase_redis_memory`.

### Task 2 — Medium: DB Connection Pool Exhaustion
An analytics query (`analytics_bot`) runs a full 42M-row sequential scan on the production PostgreSQL primary, holding its connection for 26 minutes until all 200 slots are exhausted. Red herring: API gateway CPU spike from retry storm. Fix: `kill_long_running_query` (PID 28842).

### Task 3 — Hard: Auth Service Memory Leak
`auth-service v2.4.1` introduced an unbounded LRU JWT cache (no TTL, no max-size). Heap grows ~12 MB/min, causing 4200ms GC pauses and 14-second JWT validation latency. Two red herrings: (1) payment-service disk I/O, (2) API CPU spike. Fix: `rollback_auth_service` + thorough postmortem.

---

## Observation Space

```python
class Observation(BaseModel):
    active_alerts: List[Alert]               # firing monitoring alerts
    service_metrics: Dict[str, ServiceMetrics]  # CPU, memory, error_rate, latency, status
    recent_logs: List[LogEntry]              # structured log lines
    recent_deployments: List[DeploymentRecord]  # last 24h deployments with changelogs
    available_diagnostics: List[str]         # tools callable via run_diagnostic
    available_fixes: List[str]               # remediations callable via apply_fix
    incident_history: List[IncidentHistoryEntry]  # all prior steps this episode
    runbook_hints: List[RunbookHint]         # optional contextual hints
    time_elapsed_minutes: float
    incident_status: str                     # open | investigating | mitigating | resolved
    task_id: str
    step_number: int
    max_steps: int
```

---

## Action Space

```python
class Action(BaseModel):
    action_type: Literal[
        "run_diagnostic",    # run a named diagnostic tool
        "apply_fix",         # apply a named remediation
        "write_postmortem",  # write a scored postmortem (task_hard)
        "close_incident",    # mark incident resolved
        "escalate",          # page senior on-call
        "add_note",          # annotate timeline (no score effect)
    ]
    tool: Optional[str]            # diagnostic or fix name
    target_service: Optional[str]
    postmortem_text: Optional[str] # full markdown for write_postmortem
    note: Optional[str]
    reasoning: Optional[str]       # chain-of-thought — SCORED (up to +0.03)
```

**Available diagnostics** (14 tools):
`check_service_logs`, `check_auth_service_logs`, `check_payment_service_logs`,
`check_service_memory_trend`, `check_heap_profile`, `check_cpu_usage`,
`check_network_latency`, `check_db_connections`, `check_active_queries`,
`check_db_slow_query_log`, `check_redis_memory`, `check_redis_stats`,
`check_recent_deployments`, `check_config_changes`

**Available fixes** (15 actions):
`increase_redis_memory`, `flush_redis_cache`, `set_redis_eviction_policy`,
`kill_long_running_query`, `increase_connection_pool_size`, `add_db_index`,
`restart_api_gateway`, `restart_user_service`, `restart_payment_service`,
`restart_auth_service`, `rollback_auth_service`,
`scale_up_api_gateway`, `increase_payment_service_memory`, `increase_auth_service_memory`

---

## Reward Function

| Event | Reward delta | Notes |
|---|---|---|
| Correct diagnostic (new discovery) | +0.05 | Per unique new finding |
| Root cause identified (bonus) | +0.15 | One-time, on first discovery |
| Correct fix applied | +0.30 | Largest single-step reward |
| Incident resolved (bonus) | +0.10 | After correct fix (+ postmortem for task_hard) |
| Good postmortem (task_hard, up to) | +0.20 | Scaled by section coverage |
| **Good reasoning (new!)** | **+0.03** | **CoT quality score × 0.03 per step** |
| Wrong fix applied | −0.05 | **+ CASCADE: system gets worse** |
| Repeated diagnostic | −0.02 | No new info penalty |
| Duplicate fix attempt | −0.03 | |
| Closing without resolution | −0.05 | |
| Per-step cost | −0.01 | Time pressure |

All step rewards are clipped to `[0, 1]`. Episode scores from graders are also in `[0, 1]`.

### Why this reward design matters

**1. Dense signals, not sparse wins.**
The environment rewards every useful diagnostic step (+0.05) so the agent receives learning signal *before* it ever tries a fix. This prevents reward hacking where an agent gets lucky with a fix on step 1.

**2. The cascade amplifies wrong-fix penalties beyond the score.**
A −0.05 penalty is easy to overlook. But a cascaded observation — where error rates are 2.5× higher, services are "down", and new CRITICAL alerts are firing — forces the agent to *work harder* for the remaining steps. This creates a strong gradient away from exploratory fixing.

**3. Step cost creates urgency without hard time limits.**
The −0.01/step cost means a perfect solution in 3 steps outscores the same solution in 15 steps. This trains efficient reasoning, not exhaustive tool-calling.

**4. Reasoning bonus rewards explainability.**
SRE agents that explain their actions (citing specific metrics, log lines, or alert patterns) earn up to +0.03/step. This incentivises chain-of-thought that generalises — the agent can't just memorise action sequences; it must demonstrate that it understands *why*.

**5. Partial credit for partial progress.**
An agent that correctly identifies the root cause but applies the wrong fix still earns 30–40% of the episode score (depending on task). This smooth gradient is essential for policy gradient and value-function methods.

---

## Reasoning Evaluation

The `reasoning` field in every Action is scored by `_score_reasoning()` in `environment.py`:

```
Scoring formula:
  signal_score  = min(SRE_keyword_hits / 4, 1.0)     [50% weight]
  length_score  = min(len(reasoning) / 240, 1.0)      [30% weight]
  why_score     = 1.0 if causal language present       [20% weight]
                  (because / since / therefore / indicates / suggests)

  quality = signal_score * 0.5 + length_score * 0.3 + why_score * 0.2
  reward  = quality * 0.03   (max +0.03 per step)
```

**SRE keywords scored:** root cause, latency, memory, error rate, cpu, deployment, log, metric, connection, pool, redis, jwt, cache, query, rollback, heap, gc, oom, timeout, cascade, postmortem, red herring, correlation.

**Example — good reasoning (quality ≈ 0.85, +0.025 reward):**
```json
{
  "action_type": "run_diagnostic",
  "tool": "check_redis_memory",
  "reasoning": "The CRITICAL alert shows Redis OOM and PostgreSQL read latency has spiked 10×. This indicates Redis is rejecting writes (noeviction policy), so reads fall through to PostgreSQL. I need to confirm maxmemory utilisation before applying a fix."
}
```

**Example — weak reasoning (quality ≈ 0.15, +0.004 reward):**
```json
{
  "action_type": "run_diagnostic",
  "tool": "check_redis_memory",
  "reasoning": "checking redis"
}
```

---

## Grader Criteria

### task_easy
| Criterion | Weight |
|---|---|
| Root cause identified (`redis_oom`) | 40% |
| Correct fix applied (`increase_redis_memory`) | 40% |
| No wrong fixes (−7% per wrong fix) | 20% |

### task_medium
| Criterion | Weight |
|---|---|
| Root cause identified (`db_connection_pool_exhausted`) | 30% |
| Specific query PID identified | 5% |
| Correct fix (`kill_long_running_query`) | 35% |
| No wrong fixes | 15% |
| Incident resolved | 10% |
| No premature escalation | 5% |

### task_hard
| Criterion | Weight |
|---|---|
| Root cause identified (`auth_memory_leak`) | 25% |
| Linked to v2.4.1 deployment | 10% |
| Correct fix (`rollback_auth_service`) | 25% |
| Postmortem quality (section coverage) | 20% |
| Avoided red herrings | 15% |
| Incident resolved | 5% |

---

## Setup & Usage

### Local Development

```bash
cd incident-env
pip install -r requirements.txt
uvicorn app.main:app --reload --port 7860
curl http://localhost:7860/health
curl http://localhost:7860/tasks
```

### Run Tests

```bash
pytest tests/ -v
```

### Interact manually

```bash
# Reset to easy task
curl -X POST http://localhost:7860/reset \
     -H "Content-Type: application/json" \
     -d '{"task_id": "task_easy"}'

# Run a diagnostic
curl -X POST http://localhost:7860/step \
     -H "Content-Type: application/json" \
     -d '{"action_type": "run_diagnostic", "tool": "check_redis_memory", "reasoning": "Alert shows Redis OOM — checking memory before applying fix."}'

# Apply the correct fix
curl -X POST http://localhost:7860/step \
     -H "Content-Type: application/json" \
     -d '{"action_type": "apply_fix", "tool": "increase_redis_memory"}'

# Get grader score
curl -X POST http://localhost:7860/grader

# Explore the API
open http://localhost:7860/docs
```

### Run Baseline Agent

```bash
export OPENAI_API_KEY=sk-...

# All tasks, rich visual output
python baseline/run_baseline.py --all-tasks

# All tasks, JSON output
python baseline/run_baseline.py --all-tasks --json

# Single task with GPT-4o
python baseline/run_baseline.py --task task_hard --model gpt-4o
```

**Baseline output sample:**
```
┌────────────────────────────────────────────────────────────────────┐
│  🔴  TASK EASY  │  gpt-4o-mini                                     │
│  ● EASY    │  max 12 steps                                          │
└────────────────────────────────────────────────────────────────────┘
  Step  1  run_diagnostic        check_redis_memory             ▲ 0.1900
           ↳ WHY: OOM alert + Postgres latency spike suggests Redis noeviction
           ↳ ENV: [check_redis_memory] maxmemory: 2gb, used: 2.0gb (100%)...
  Step  2  apply_fix             increase_redis_memory          ▲ 0.3900
           ↳ WHY: Confirmed OOM. Correct fix is to increase maxmemory.
           ↳ ENV: ✓ Correct fix applied. Services recovering. ✓ Incident resolved.

  ────────────────────────────────────────────────────────────────────
  RESULTS
  Final score   : 0.980  ████████████████████
  Steps used    : 2/12
  No cascades  (zero wrong fixes applied)
  Avg reasoning : 0.720  ██████████████░░░░░░

  Score Breakdown:
    ✓ root_cause_identified            : 0.400  ████████
    ✓ correct_fix_applied              : 0.400  ████████
    ✓ no_wrong_fixes                   : 0.200  ████
```

### Docker

```bash
docker build -t incident-env .
docker run -p 7860:7860 -e OPENAI_API_KEY=$OPENAI_API_KEY incident-env
curl http://localhost:7860/health
```

---

## Deploy to Hugging Face Spaces

1. Create a new Space → **Docker SDK**
2. Push this repository:
   ```bash
   git remote add space https://huggingface.co/spaces/YOUR_USERNAME/incident-env
   git push space main
   ```
3. In Space **Settings → Secrets**, add `OPENAI_API_KEY`
4. Tag the Space with `openenv`

Your Space URL: `https://YOUR_USERNAME-incident-env.hf.space`

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/reset` | Start new episode. Body: `{"task_id": "task_easy"}` |
| `POST` | `/step` | Take one action. Body: `Action` JSON |
| `GET` | `/state` | Full internal episode state |
| `GET` | `/tasks` | All tasks + action schema |
| `POST` | `/grader` | Current episode score + breakdown |
| `POST` | `/baseline` | Run baseline agent (needs `OPENAI_API_KEY`) |
| `GET` | `/health` | Liveness check |
| `GET` | `/docs` | Swagger UI |

---

## Baseline Scores (GPT-4o-mini)

| Task | Score | Root Cause | Resolved | Cascades |
|---|---|---|---|---|
| `task_easy` | ~0.85 | ✓ | ✓ | 0 |
| `task_medium` | ~0.72 | ✓ | ✓ | 0–1 |
| `task_hard` | ~0.58 | ✓ | ✓ | 1–2 |
| **Average** | **~0.72** | | | |

*Run `python baseline/run_baseline.py --all-tasks` to reproduce.*

---

## Project Structure

```
incident-env/
├── app/
│   ├── __init__.py
│   ├── main.py          # FastAPI app — all HTTP endpoints
│   ├── environment.py   # Core IncidentEnvironment — cascade mechanic + reasoning eval
│   ├── models.py        # Pydantic v2 typed models
│   ├── scenarios.py     # Scenario data (alerts, metrics, logs, diagnostic outputs)
│   └── graders.py       # Deterministic per-task graders + postmortem scorer
├── baseline/
│   └── run_baseline.py  # ReAct-style baseline agent with rich visual output
├── tests/
│   └── test_env.py      # Comprehensive pytest test suite (30+ tests)
├── Dockerfile
├── openenv.yaml
├── requirements.txt
└── README.md
```

---

## License

Apache 2.0
