"""
Comprehensive test suite for IncidentEnv.

Run with:  pytest tests/ -v
Or just:   pytest
"""
from __future__ import annotations

import pytest
from server.environment import IncidentEnvironment
from server.graders import grade, score_postmortem
from server.models import Action, EnvironmentState


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def env_easy():
    e = IncidentEnvironment()
    e.reset("task_easy")
    return e

@pytest.fixture
def env_medium():
    e = IncidentEnvironment()
    e.reset("task_medium")
    return e

@pytest.fixture
def env_hard():
    e = IncidentEnvironment()
    e.reset("task_hard")
    return e


# ── reset() ───────────────────────────────────────────────────────────────────

class TestReset:
    def test_returns_observation(self, env_easy):
        obs = env_easy.reset("task_easy")
        assert obs.task_id == "task_easy"
        assert len(obs.active_alerts) > 0
        assert len(obs.available_diagnostics) > 0
        assert len(obs.available_fixes) > 0
        assert obs.incident_status == "open"
        assert obs.step_number == 0

    def test_all_tasks_resetable(self):
        e = IncidentEnvironment()
        for task_id in ["task_easy", "task_medium", "task_hard"]:
            obs = e.reset(task_id)
            assert obs.task_id == task_id

    def test_unknown_task_raises(self):
        e = IncidentEnvironment()
        with pytest.raises(ValueError, match="Unknown task_id"):
            e.reset("task_nonexistent")

    def test_reset_clears_state(self, env_easy):
        env_easy.step(Action(action_type="run_diagnostic", tool="check_redis_memory"))
        env_easy.reset("task_easy")
        st = env_easy.get_state()
        assert st.step_count == 0
        assert st.agent_discoveries == []
        assert st.fixes_applied == []


# ── step() ────────────────────────────────────────────────────────────────────

class TestStep:
    def test_step_before_reset_raises(self):
        e = IncidentEnvironment()
        with pytest.raises(RuntimeError, match="reset"):
            e.step(Action(action_type="run_diagnostic", tool="check_redis_memory"))

    def test_reward_in_bounds(self, env_easy):
        result = env_easy.step(Action(action_type="run_diagnostic", tool="check_redis_memory"))
        assert 0.0 <= result.reward <= 1.0

    def test_step_increments_counter(self, env_easy):
        env_easy.step(Action(action_type="run_diagnostic", tool="check_redis_memory"))
        env_easy.step(Action(action_type="run_diagnostic", tool="check_service_logs"))
        assert env_easy.get_state().step_count == 2

    def test_unknown_diagnostic_penalized(self, env_easy):
        result = env_easy.step(Action(action_type="run_diagnostic", tool="nonexistent_tool"))
        assert result.reward <= 0.0

    def test_repeated_diagnostic_penalized(self, env_easy):
        env_easy.step(Action(action_type="run_diagnostic", tool="check_redis_memory"))
        result = env_easy.step(Action(action_type="run_diagnostic", tool="check_redis_memory"))
        assert result.reward <= 0.0

    def test_wrong_fix_penalized(self, env_easy):
        result = env_easy.step(Action(action_type="apply_fix", tool="restart_api_gateway"))
        assert result.reward < 0.05
        assert env_easy.get_state().wrong_fixes_applied == 1

    def test_correct_fix_resolves_easy(self, env_easy):
        env_easy.step(Action(action_type="run_diagnostic", tool="check_redis_memory"))
        result = env_easy.step(Action(action_type="apply_fix", tool="increase_redis_memory"))
        assert result.info["incident_resolved"] is True
        assert result.done is True

    def test_done_at_max_steps(self, env_easy):
        state = env_easy.get_state()
        max_s = state.max_steps
        for i in range(max_s):
            result = env_easy.step(Action(action_type="add_note", note=f"step {i}"))
            if result.done:
                break
        assert result.done is True

    def test_observation_updates_each_step(self, env_easy):
        obs1 = env_easy.get_state().current_observation
        env_easy.step(Action(action_type="run_diagnostic", tool="check_redis_memory"))
        obs2 = env_easy.get_state().current_observation
        assert obs2.step_number == 1
        assert len(obs2.incident_history) == 1

    def test_status_transitions(self, env_easy):
        assert env_easy.get_state().current_observation.incident_status == "open"
        env_easy.step(Action(action_type="run_diagnostic", tool="check_redis_memory"))
        assert env_easy.get_state().current_observation.incident_status == "investigating"
        env_easy.step(Action(action_type="apply_fix", tool="increase_redis_memory"))
        assert env_easy.get_state().current_observation.incident_status == "resolved"


# ── state() ───────────────────────────────────────────────────────────────────

class TestState:
    def test_state_before_reset_raises(self):
        e = IncidentEnvironment()
        with pytest.raises(RuntimeError):
            e.state()

    def test_state_tracks_discoveries(self, env_easy):
        env_easy.step(Action(action_type="run_diagnostic", tool="check_redis_memory"))
        st = env_easy.state()
        assert "redis_oom" in st.agent_discoveries

    def test_state_tracks_fixes(self, env_easy):
        env_easy.step(Action(action_type="apply_fix", tool="increase_redis_memory"))
        st = env_easy.state()
        assert "increase_redis_memory" in st.fixes_applied


# ── Graders ───────────────────────────────────────────────────────────────────

class TestGraders:
    def test_easy_perfect_run(self):
        e = IncidentEnvironment()
        e.reset("task_easy")
        e.step(Action(action_type="run_diagnostic", tool="check_redis_memory"))
        e.step(Action(action_type="apply_fix", tool="increase_redis_memory"))
        score, breakdown = e.grade_episode()
        assert score >= 0.8
        assert breakdown["root_cause_identified"] == 0.40
        assert breakdown["correct_fix_applied"] == 0.40

    def test_easy_wrong_fix_reduces_score(self):
        e = IncidentEnvironment()
        e.reset("task_easy")
        e.step(Action(action_type="apply_fix", tool="restart_api_gateway"))   # wrong
        e.step(Action(action_type="run_diagnostic", tool="check_redis_memory"))
        e.step(Action(action_type="apply_fix", tool="increase_redis_memory"))  # correct
        score, breakdown = e.grade_episode()
        assert breakdown["no_wrong_fixes"] < 0.20

    def test_medium_correct_path(self):
        e = IncidentEnvironment()
        e.reset("task_medium")
        e.step(Action(action_type="run_diagnostic", tool="check_db_connections"))
        e.step(Action(action_type="run_diagnostic", tool="check_active_queries"))
        e.step(Action(action_type="apply_fix", tool="kill_long_running_query"))
        e.step(Action(action_type="close_incident"))
        score, breakdown = e.grade_episode()
        assert score >= 0.75
        assert breakdown["root_cause_identified"] == 0.30
        assert breakdown["query_pid_identified"] == 0.05
        assert breakdown["correct_fix_applied"] == 0.35

    def test_hard_requires_postmortem(self):
        e = IncidentEnvironment()
        e.reset("task_hard")
        e.step(Action(action_type="run_diagnostic", tool="check_service_memory_trend"))
        e.step(Action(action_type="run_diagnostic", tool="check_recent_deployments"))
        e.step(Action(action_type="apply_fix", tool="rollback_auth_service"))
        # No postmortem — should not be resolved
        assert e.get_state().incident_resolved is False

    def test_hard_with_postmortem_resolves(self):
        e = IncidentEnvironment()
        e.reset("task_hard")
        e.step(Action(action_type="run_diagnostic", tool="check_service_memory_trend"))
        e.step(Action(action_type="run_diagnostic", tool="check_recent_deployments"))
        e.step(Action(action_type="apply_fix", tool="rollback_auth_service"))
        e.step(Action(
            action_type="write_postmortem",
            postmortem_text=(
                "## Root Cause\n"
                "auth-service v2.4.1 introduced an unbounded JWTCache with no eviction policy.\n\n"
                "## Timeline\n"
                "00:02 — v2.4.1 deployed. 01:20 — memory warning. 02:09 — GC pauses. "
                "02:10 — payment failures.\n\n"
                "## Impact\n"
                "78 % of payment transactions failed for ~25 min. ~$140k revenue impact.\n\n"
                "## Remediation\n"
                "Rolled back auth-service to v2.4.0, which restored JWT validation within 3 min.\n\n"
                "## Prevention\n"
                "Add eviction policy (TTL + max-size) to all caches. Require load test "
                "in staging before deploy.\n\n"
                "## Follow-up Action Items\n"
                "1. Fix JWTCache in v2.4.2 with maximumSize(50000).expireAfterWrite(15, MINUTES).\n"
                "2. Add memory growth alert threshold at 10 MB/min.\n"
                "3. Route analytics queries to read-replica only."
            ),
        ))
        assert e.get_state().incident_resolved is True

    def test_hard_score_components(self):
        e = IncidentEnvironment()
        e.reset("task_hard")
        e.step(Action(action_type="run_diagnostic", tool="check_service_memory_trend"))
        e.step(Action(action_type="run_diagnostic", tool="check_recent_deployments"))
        e.step(Action(action_type="run_diagnostic", tool="check_auth_service_logs"))
        e.step(Action(action_type="run_diagnostic", tool="check_heap_profile"))
        e.step(Action(action_type="apply_fix", tool="rollback_auth_service"))
        e.step(Action(
            action_type="write_postmortem",
            postmortem_text=(
                "Root cause: JWTCache memory leak in v2.4.1.\n"
                "Timeline: deployed 00:02, leak grew, failures at 02:10.\n"
                "Impact: payment service down 25 min.\n"
                "Remediation: rollback to v2.4.0.\n"
                "Prevention: cache eviction policy required.\n"
                "Follow-up: hotfix v2.4.2 with bounded cache."
            ),
        ))
        score, breakdown = e.grade_episode()
        assert breakdown["root_cause_identified"] == 0.25
        assert breakdown["deployment_linked"] == 0.10
        assert breakdown["correct_fix_applied"] == 0.25
        assert breakdown["postmortem_quality"] > 0.10
        assert score >= 0.70

    def test_unknown_task_grader_raises(self):
        e = IncidentEnvironment()
        e.reset("task_easy")
        with pytest.raises(ValueError):
            grade("task_bogus", e.get_state())


# ── Postmortem scorer ─────────────────────────────────────────────────────────

class TestPostmortemScorer:
    def test_empty_returns_zero(self):
        assert score_postmortem("") == 0.0

    def test_too_short_returns_zero(self):
        assert score_postmortem("root cause: redis") == 0.0

    def test_full_postmortem_scores_high(self):
        text = (
            "Root cause: cache OOM.\n"
            "Timeline: alert at 02:14, fix at 02:18.\n"
            "Impact: API latency spike.\n"
            "Remediation: increased maxmemory.\n"
            "Prevention: add memory alert.\n"
            "Follow-up: tune eviction policy."
        )
        score = score_postmortem(text)
        assert score >= 0.80

    def test_partial_postmortem_partial_score(self):
        text = "Root cause: redis out of memory. Impact: users could not log in for 5 minutes. This is extra text to pass the length check."
        score = score_postmortem(text)
        assert 0.1 <= score < 0.8

    def test_long_postmortem_bonus(self):
        short = "Root cause: x. Timeline: y. Impact: z. Remediation: a. Prevention: b. "
        long_text = short + ("Follow-up: c. " * 60)
        short_score = score_postmortem(short + " " * 20)
        long_score = score_postmortem(long_text)
        assert long_score >= short_score


# ── Full episode smoke tests ──────────────────────────────────────────────────

class TestFullEpisodes:
    def test_easy_episode_runs_to_completion(self):
        """Episode must terminate at max_steps even if not resolved."""
        e = IncidentEnvironment()
        e.reset("task_easy")  # max_steps=12
        result = None
        for i in range(12):
            result = e.step(Action(action_type="add_note", note=f"step {i}"))
        # After max_steps steps the episode should be done
        assert result is not None
        assert result.done is True

    def test_episode_info_keys(self):
        e = IncidentEnvironment()
        e.reset("task_easy")
        result = e.step(Action(action_type="run_diagnostic", tool="check_redis_memory"))
        info = result.info
        for key in [
            "reward_breakdown", "feedback", "step", "max_steps",
            "discoveries", "fixes_applied", "episode_id", "done",
        ]:
            assert key in info, f"Missing info key: {key}"

    def test_grade_low_for_no_action(self):
        """No meaningful actions taken — root cause not found, no fix applied.
        Graders award small passive credit (no_wrong_fixes, no_premature_escalation)
        but score stays well below the minimum passing threshold."""
        e = IncidentEnvironment()
        e.reset("task_easy")
        score, breakdown = e.grade_episode()
        # root_cause_identified and correct_fix_applied are both 0
        assert breakdown["root_cause_identified"] == 0.0
        assert breakdown["correct_fix_applied"] == 0.0
        # Total score is below 0.25 (only passive credits can accumulate)
        assert score < 0.25
