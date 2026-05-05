"""
Tests for WebhookTrigger (Phase 2), PulseAlert (Phase 3),
LiveLens Analytics (Phase 4), and JobFlow Pipelines (Phase 5).
"""
from __future__ import annotations

import asyncio
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("PTF_DEV_MODE", "true")


# ── Phase 2: WebhookTrigger ────────────────────────────────────────────────────

class TestWebhookTrigger:
    @pytest.mark.asyncio
    async def test_valid_webhook_token_returns_202(self, async_client, db_session):
        """A valid, enabled webhook token must trigger the job and return 202."""
        from backend.models.database import Job, JobStatus, TriggerType, ExecutionMode
        import json
        import secrets as _secrets

        token = _secrets.token_urlsafe(32)
        job = Job(
            name="Webhook Test Job",
            script_path="hello_world.py",
            trigger_type=TriggerType.INTERVAL,
            trigger_config=json.dumps({"seconds": 60}),
            env_vars="{}",
            status=JobStatus.ACTIVE,
            webhook_enabled=True,
            webhook_token=token,
            execution_mode=ExecutionMode.VENV,
        )
        db_session.add(job)
        await db_session.commit()
        await db_session.refresh(job)
        saved_id = job.id

        def _drain_and_stub(coro):
            # Close coroutine to avoid "never awaited" warnings in tests.
            coro.close()
            loop = asyncio.get_running_loop()
            return loop.create_task(asyncio.sleep(0))

        with patch("backend.routers.webhooks.asyncio.create_task", side_effect=_drain_and_stub):
            response = await async_client.post(f"/webhooks/jobs/{token}")
        assert response.status_code == 202
        assert response.json()["job_id"] == saved_id

    @pytest.mark.asyncio
    async def test_invalid_token_returns_404(self, async_client):
        """An unknown webhook token must return 404."""
        response = await async_client.post("/webhooks/jobs/INVALID_TOKEN_XYZ")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_disabled_webhook_returns_404(self, async_client, db_session):
        """A valid token with webhook_enabled=False must return 404."""
        from backend.models.database import Job, JobStatus, TriggerType, ExecutionMode
        import json
        import secrets as _secrets

        token = _secrets.token_urlsafe(32)
        job = Job(
            name="Disabled Webhook Job",
            script_path="hello_world.py",
            trigger_type=TriggerType.INTERVAL,
            trigger_config=json.dumps({"seconds": 60}),
            env_vars="{}",
            status=JobStatus.ACTIVE,
            webhook_enabled=False,
            webhook_token=token,
            execution_mode=ExecutionMode.VENV,
        )
        db_session.add(job)
        await db_session.commit()

        response = await async_client.post(f"/webhooks/jobs/{token}")
        assert response.status_code == 404


# ── Phase 3: PulseAlert ────────────────────────────────────────────────────────

class TestPulseAlert:
    """Unit tests for the notifier service."""

    @pytest.mark.asyncio
    async def test_alert_dispatched_on_failure(self):
        """dispatch_alert must POST to the target URL on failure."""
        from backend.models.database import AlertChannel, AlertPolicy, AlertTrigger, RunHistory, RunStatus, Job, ExecutionMode, TriggerType
        from backend.services.notifier import dispatch_alert
        from datetime import datetime, timezone
        import json

        policy = MagicMock(spec=AlertPolicy)
        policy.id = 1
        policy.channel = AlertChannel.WEBHOOK
        policy.trigger = AlertTrigger.ON_FAILURE
        policy.target_url = "https://example.com/alert"

        job = MagicMock(spec=Job)
        job.id = 1
        job.name = "Test Job"

        run = MagicMock(spec=RunHistory)
        run.id = 1
        run.status = RunStatus.FAILED
        run.exit_code = 1
        run.started_at = datetime.now(timezone.utc)
        run.finished_at = datetime.now(timezone.utc)

        with patch("backend.services.notifier.httpx.AsyncClient") as mock_client:
            mock_response = AsyncMock()
            mock_response.raise_for_status = MagicMock()
            mock_instance = AsyncMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_instance.post = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_instance

            await dispatch_alert(policy, run, job)
            mock_instance.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_http_failure_does_not_propagate(self):
        """A network error in dispatch_alert must be swallowed (never raise)."""
        from backend.models.database import AlertChannel, AlertPolicy, AlertTrigger, RunHistory, RunStatus, Job
        from backend.services.notifier import dispatch_alert
        from datetime import datetime, timezone
        import httpx

        policy = MagicMock(spec=AlertPolicy)
        policy.id = 1
        policy.channel = AlertChannel.SLACK
        policy.target_url = "https://hooks.slack.com/test"

        job = MagicMock(spec=Job)
        job.id = 1
        job.name = "Failing Alert Job"

        run = MagicMock(spec=RunHistory)
        run.status = RunStatus.FAILED
        run.exit_code = 1
        run.started_at = datetime.now(timezone.utc)
        run.finished_at = datetime.now(timezone.utc)

        with patch("backend.services.notifier.httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_instance.post = AsyncMock(side_effect=httpx.RequestError("network error"))
            mock_client.return_value = mock_instance

            # Must not raise — alerts must never crash the execution pipeline
            await dispatch_alert(policy, run, job)

    @pytest.mark.asyncio
    async def test_sla_breach_alert_fires_when_over_limit(self):
        """check_and_alert must dispatch when run duration exceeds SLA."""
        from backend.models.database import AlertChannel, AlertPolicy, AlertTrigger, RunHistory, RunStatus, Job
        from backend.services.notifier import check_and_alert
        from datetime import datetime, timezone, timedelta

        policy = MagicMock(spec=AlertPolicy)
        policy.id = 1
        policy.is_active = True
        policy.channel = AlertChannel.WEBHOOK
        policy.trigger = AlertTrigger.ON_SLA_BREACH
        policy.sla_max_duration_seconds = 30
        policy.target_url = "https://example.com/sla"

        job = MagicMock(spec=Job)
        job.id = 1
        job.name = "Slow Job"
        job.alert_policies = [policy]

        run = MagicMock(spec=RunHistory)
        run.status = RunStatus.SUCCESS
        run.exit_code = 0
        run.started_at = datetime.now(timezone.utc) - timedelta(seconds=120)
        run.finished_at = datetime.now(timezone.utc)

        with patch("backend.services.notifier.dispatch_alert", new_callable=AsyncMock) as mock_dispatch:
            await check_and_alert(job=job, run=run, db=None)
            mock_dispatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_alert_not_dispatched_when_condition_not_met(self):
        """check_and_alert must NOT dispatch when the run status doesn't match."""
        from backend.models.database import AlertChannel, AlertPolicy, AlertTrigger, RunHistory, RunStatus, Job
        from backend.services.notifier import check_and_alert
        from datetime import datetime, timezone

        policy = MagicMock(spec=AlertPolicy)
        policy.is_active = True
        policy.trigger = AlertTrigger.ON_FAILURE  # only on failure
        policy.sla_max_duration_seconds = None

        job = MagicMock(spec=Job)
        job.alert_policies = [policy]

        run = MagicMock(spec=RunHistory)
        run.status = RunStatus.SUCCESS  # success — should NOT trigger ON_FAILURE
        run.started_at = datetime.now(timezone.utc)
        run.finished_at = datetime.now(timezone.utc)

        with patch("backend.services.notifier.dispatch_alert", new_callable=AsyncMock) as mock_dispatch:
            await check_and_alert(job=job, run=run, db=None)
            mock_dispatch.assert_not_called()


# ── Phase 4: LiveLens Analytics ───────────────────────────────────────────────

class TestLiveLensAnalytics:
    @pytest.mark.asyncio
    async def test_summary_endpoint_returns_expected_keys(self, async_client):
        """GET /api/analytics/summary must return required KPI fields."""
        response = await async_client.get("/api/analytics/summary")
        assert response.status_code == 200
        body = response.json()
        assert "active_jobs" in body
        assert "runs_last_24h" in body
        assert "failure_rate_pct" in body

    @pytest.mark.asyncio
    async def test_heatmap_returns_correct_day_count(self, async_client):
        """GET /api/analytics/jobs/{id}/heatmap must return 90+1 day entries."""
        response = await async_client.get("/api/analytics/jobs/99999/heatmap?days=90")
        assert response.status_code == 200
        data = response.json()
        # 0..90 inclusive = 91 entries
        assert len(data) == 91
        for entry in data:
            assert "date" in entry
            assert "total" in entry

    @pytest.mark.asyncio
    async def test_anomaly_detection_flags_slow_job(self, db_session):
        """get_anomalous_jobs must flag a job whose last run was anomalously slow."""
        from backend.models.database import Job, RunHistory, RunStatus, JobStatus, TriggerType, ExecutionMode
        from datetime import datetime, timezone, timedelta
        import json

        job = Job(
            name="Anomaly Test Job",
            script_path="hello_world.py",
            trigger_type=TriggerType.INTERVAL,
            trigger_config=json.dumps({"seconds": 60}),
            env_vars="{}",
            status=JobStatus.ACTIVE,
            execution_mode=ExecutionMode.VENV,
        )
        db_session.add(job)
        await db_session.flush()

        base = datetime.now(timezone.utc)
        for i in range(10):
            run = RunHistory(
                job_id=job.id,
                status=RunStatus.SUCCESS,
                started_at=base - timedelta(hours=10 - i),
                finished_at=base - timedelta(hours=10 - i) + timedelta(seconds=10),
                exit_code=0,
            )
            db_session.add(run)

        anomalous = RunHistory(
            job_id=job.id,
            status=RunStatus.SUCCESS,
            started_at=base - timedelta(minutes=5),
            finished_at=base - timedelta(minutes=5) + timedelta(seconds=120),
            exit_code=0,
        )
        db_session.add(anomalous)
        await db_session.commit()

        from backend.services.analytics import get_anomalous_jobs
        anomalies = await get_anomalous_jobs(db_session, z_score_threshold=2.0, min_runs=5)

        flagged_ids = [a["job_id"] for a in anomalies]
        assert job.id in flagged_ids


# ── Phase 5: JobFlow DAG Pipelines ────────────────────────────────────────────

class TestJobFlowPipelines:
    def test_topological_sort_orders_correctly(self):
        """A → B → C must be returned in order [A, B, C]."""
        from backend.services.pipeline_runner import topological_sort

        class E:
            def __init__(self, u, d):
                self.upstream_job_id = u
                self.downstream_job_id = d

        # A=1, B=2, C=3
        edges = [E(1, 2), E(2, 3)]
        order = topological_sort(edges)  # type: ignore
        assert order.index(1) < order.index(2) < order.index(3)

    def test_cycle_detection_raises_value_error(self):
        """A cycle in the DAG must raise ValueError before any execution."""
        from backend.services.pipeline_runner import topological_sort

        class E:
            def __init__(self, u, d):
                self.upstream_job_id = u
                self.downstream_job_id = d

        # A → B → C → A (cycle)
        edges = [E(1, 2), E(2, 3), E(3, 1)]
        with pytest.raises(ValueError, match="[Cc]ircular"):
            topological_sort(edges)  # type: ignore

    @pytest.mark.asyncio
    async def test_create_pipeline_returns_201(self, async_client):
        """POST /api/pipelines must return 201."""
        response = await async_client.post(
            "/api/pipelines",
            json={"name": "Test Pipeline", "edges": []},
        )
        assert response.status_code == 201
        assert response.json()["name"] == "Test Pipeline"

    @pytest.mark.asyncio
    async def test_create_pipeline_with_cycle_returns_422(self, async_client):
        """Creating a pipeline with a circular dependency must return 422."""
        response = await async_client.post(
            "/api/pipelines",
            json={
                "name": "Cyclic Pipeline",
                "edges": [
                    {"upstream_job_id": 1, "downstream_job_id": 2, "on_condition": "success"},
                    {"upstream_job_id": 2, "downstream_job_id": 1, "on_condition": "success"},
                ],
            },
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_downstream_job_skipped_on_upstream_failure(self):
        """A downstream job with on_condition=success must be skipped when upstream fails."""
        from backend.models.database import EdgeCondition, RunStatus, PipelineStatus

        # Pure mock test — no DB required
        with patch(
            "backend.services.pipeline_runner.AsyncSessionLocal"
        ) as mock_session_cls:
            from backend.models.database import Pipeline, PipelineEdge
            mock_pipeline = MagicMock(spec=Pipeline)
            mock_pipeline.id = 1

            mock_edge = MagicMock(spec=PipelineEdge)
            mock_edge.upstream_job_id = 100
            mock_edge.downstream_job_id = 101
            mock_edge.on_condition = EdgeCondition.SUCCESS

            mock_db = AsyncMock()
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_db.get = AsyncMock(return_value=mock_pipeline)
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = [mock_edge]
            mock_db.execute = AsyncMock(return_value=mock_result)

            with patch(
                "backend.services.pipeline_runner.TaskScheduler._run_job_inner",
                new_callable=AsyncMock,
            ) as mock_run, patch(
                "backend.services.pipeline_runner._get_last_run_status",
                new_callable=AsyncMock,
                return_value=RunStatus.FAILED,
            ):
                from backend.services.pipeline_runner import execute_pipeline
                await execute_pipeline(1)

                called_ids = [args.args[0] for args in mock_run.call_args_list]
                assert 101 not in called_ids

    @pytest.mark.asyncio
    async def test_downstream_runs_when_condition_is_always(self):
        """on_condition=always must run the downstream job regardless of upstream outcome."""
        from backend.models.database import EdgeCondition, RunStatus

        with patch(
            "backend.services.pipeline_runner.AsyncSessionLocal"
        ) as mock_session_cls:
            from backend.models.database import Pipeline, PipelineEdge
            mock_pipeline = MagicMock(spec=Pipeline)
            mock_pipeline.id = 2

            mock_edge = MagicMock(spec=PipelineEdge)
            mock_edge.upstream_job_id = 200
            mock_edge.downstream_job_id = 201
            mock_edge.on_condition = EdgeCondition.ALWAYS

            mock_db = AsyncMock()
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_db.get = AsyncMock(return_value=mock_pipeline)
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = [mock_edge]
            mock_db.execute = AsyncMock(return_value=mock_result)

            with patch(
                "backend.services.pipeline_runner.TaskScheduler._run_job_inner",
                new_callable=AsyncMock,
            ) as mock_run, patch(
                "backend.services.pipeline_runner._get_last_run_status",
                new_callable=AsyncMock,
                return_value=RunStatus.FAILED,
            ):
                from backend.services.pipeline_runner import execute_pipeline
                await execute_pipeline(2)

                called_ids = [args.args[0] for args in mock_run.call_args_list]
                assert 201 in called_ids

