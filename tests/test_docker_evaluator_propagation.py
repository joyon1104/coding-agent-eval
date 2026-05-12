"""Tests for docker_evaluator's empty-patch failure propagation logic.

The contract: ``root_cause`` is always ``no_patch_generated`` when the patch
is empty, but ``failure_stage`` and ``failure_category`` reflect the
underlying cause (Step 1's classification when the agent errored,
``model_failure`` only when Step 1 truly succeeded with no edits).
"""

from __future__ import annotations

from src.core.models import AgentResult, EvalTask, TaskStatus
from src.evaluator.docker_evaluator import evaluate_single


def _task() -> EvalTask:
    return EvalTask(
        instance_id="dummy__repo-1",
        repo="dummy/repo",
        base_commit="abc",
        problem_statement="fix it",
    )


class TestEmptyPatchPropagation:
    def test_step1_error_with_quota_classification_propagates(self):
        """Rate-limit error from Step 1 must surface as quota_exceeded,
        not model_failure."""
        ar = AgentResult(
            instance_id="dummy__repo-1",
            agent_name="claude-code",
            patch="",
            status=TaskStatus.ERROR,
            error_message="You've hit your limit",
            failure_stage="agent_execution",
            failure_category="quota_exceeded",
            root_cause="rate_limit_exceeded",
        )
        er = evaluate_single(_task(), ar)
        assert er.eval_status == "fail"
        assert er.root_cause == "no_patch_generated"
        assert er.failure_category == "quota_exceeded"
        assert er.failure_stage == "agent_execution"
        assert er.error == "You've hit your limit"

    def test_step1_error_with_timeout_propagates(self):
        ar = AgentResult(
            instance_id="dummy__repo-1",
            agent_name="claude-code",
            patch="",
            status=TaskStatus.ERROR,
            error_message="Timeout after 1800s",
            failure_stage="agent_execution",
            failure_category="timeout",
            root_cause="agent_execution_timeout",
        )
        er = evaluate_single(_task(), ar)
        assert er.root_cause == "no_patch_generated"
        assert er.failure_category == "timeout"
        assert er.failure_stage == "agent_execution"

    def test_step1_error_without_classification_falls_back(self):
        """Legacy AgentResults predating classifier still get a sane category
        rather than being misattributed to model_failure."""
        ar = AgentResult(
            instance_id="dummy__repo-1",
            agent_name="claude-code",
            patch="",
            status=TaskStatus.ERROR,
            error_message="",
            # failure_stage/category/root_cause all default empty
        )
        er = evaluate_single(_task(), ar)
        assert er.root_cause == "no_patch_generated"
        assert er.failure_category == "internal_error"
        assert er.failure_stage == "agent_execution"

    def test_step1_success_with_empty_patch_is_model_failure(self):
        """Genuine model failure: the agent ran end-to-end but produced no
        diff. This is the only case that should count against the model."""
        ar = AgentResult(
            instance_id="dummy__repo-1",
            agent_name="claude-code",
            patch="",
            status=TaskStatus.SUCCESS,
            convergence_steps=10,
            total_cost_usd=0.05,
        )
        er = evaluate_single(_task(), ar)
        assert er.root_cause == "no_patch_generated"
        assert er.failure_category == "model_failure"
        assert er.failure_stage == "patch_extraction"
