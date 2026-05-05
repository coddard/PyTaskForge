"""
PyTaskForge – JobFlow Pipeline Runner
=======================================
Executes a pipeline by traversing its DAG using Kahn's topological sort
algorithm and respecting per-edge ``on_condition`` rules.

Algorithm:
  1. Build an in-degree map and adjacency list from the pipeline's edges.
  2. Use Kahn's algorithm to determine a valid topological execution order.
  3. Execute each job node via the scheduler's execution engine.
  4. After each node completes, evaluate ``on_condition`` for its outbound
     edges; skip downstream nodes whose condition is not met.
  5. Broadcast per-node status updates via WebSocket.

Cycle detection:
  If a cycle is detected during topological sort, a ``ValueError`` is raised
  before any job is executed.
"""
from __future__ import annotations

import logging
from collections import deque
from typing import Dict, List, Optional, Set

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.database import (
    AsyncSessionLocal,
    EdgeCondition,
    Job,
    PipelineEdge,
    RunHistory,
    RunStatus,
)
from backend.services.scheduler import TaskScheduler, _broadcast_to_channel

logger = logging.getLogger(__name__)


# ── Graph utilities ───────────────────────────────────────────────────────────

def build_graph(
    edges: List[PipelineEdge],
) -> tuple[Dict[int, List[PipelineEdge]], Dict[int, int], Set[int]]:
    """Build adjacency list, in-degree map, and all node IDs from edge list.

    Args:
        edges: List of PipelineEdge ORM objects.

    Returns:
        Tuple of (adjacency_list, in_degree_map, all_node_ids).
        adjacency_list: {upstream_job_id: [PipelineEdge, ...]}
        in_degree_map:  {job_id: number_of_incoming_edges}
        all_node_ids:   set of all job IDs referenced in the pipeline
    """
    adjacency: Dict[int, List[PipelineEdge]] = {}
    in_degree: Dict[int, int] = {}
    all_nodes: Set[int] = set()

    for edge in edges:
        all_nodes.add(edge.upstream_job_id)
        all_nodes.add(edge.downstream_job_id)
        adjacency.setdefault(edge.upstream_job_id, []).append(edge)
        in_degree.setdefault(edge.upstream_job_id, 0)
        in_degree[edge.downstream_job_id] = in_degree.get(edge.downstream_job_id, 0) + 1

    # Ensure all nodes appear in in_degree (even those with no incoming edges).
    for node in all_nodes:
        in_degree.setdefault(node, 0)

    return adjacency, in_degree, all_nodes


def topological_sort(
    edges: List[PipelineEdge],
) -> List[int]:
    """Return a topologically sorted list of job IDs using Kahn's algorithm.

    Args:
        edges: Pipeline edges defining the DAG.

    Returns:
        Ordered list of job IDs (roots first).

    Raises:
        ValueError: The pipeline graph contains a cycle.
    """
    adjacency, in_degree, all_nodes = build_graph(edges)

    queue: deque[int] = deque(
        node for node in all_nodes if in_degree[node] == 0
    )
    sorted_order: List[int] = []

    while queue:
        node = queue.popleft()
        sorted_order.append(node)
        for edge in adjacency.get(node, []):
            in_degree[edge.downstream_job_id] -= 1
            if in_degree[edge.downstream_job_id] == 0:
                queue.append(edge.downstream_job_id)

    if len(sorted_order) != len(all_nodes):
        raise ValueError(
            "Circular dependency detected in pipeline. "
            "Please remove the cycle and try again."
        )

    return sorted_order


# ── Pipeline execution ────────────────────────────────────────────────────────

async def execute_pipeline(pipeline_id: int) -> None:
    """Execute all jobs in a pipeline respecting dependency order.

    Args:
        pipeline_id: The database ID of the pipeline to execute.
    """
    from sqlalchemy import select
    from backend.models.database import Pipeline

    async with AsyncSessionLocal() as db:
        pipeline = await db.get(Pipeline, pipeline_id)
        if not pipeline:
            logger.error("execute_pipeline: pipeline_id=%s not found.", pipeline_id)
            return

        # Eagerly load edges via the relationship.
        result = await db.execute(
            select(PipelineEdge).where(PipelineEdge.pipeline_id == pipeline_id)
        )
        edges: List[PipelineEdge] = result.scalars().all()

    if not edges:
        logger.warning("execute_pipeline: pipeline_id=%s has no edges.", pipeline_id)
        return

    try:
        ordered_job_ids = topological_sort(edges)
    except ValueError as exc:
        logger.error("execute_pipeline: %s (pipeline_id=%s)", exc, pipeline_id)
        return

    # Build condition map: {(upstream_id, downstream_id): EdgeCondition}
    condition_map: Dict[tuple[int, int], EdgeCondition] = {
        (e.upstream_job_id, e.downstream_job_id): e.on_condition
        for e in edges
    }

    # Build upstream map: {downstream_id: [upstream_id, ...]}
    upstream_map: Dict[int, List[int]] = {}
    for edge in edges:
        upstream_map.setdefault(edge.downstream_job_id, []).append(edge.upstream_job_id)

    job_results: Dict[int, RunStatus] = {}

    logger.info(
        "Pipeline execution started: pipeline_id=%s nodes=%s",
        pipeline_id,
        ordered_job_ids,
    )

    for job_id in ordered_job_ids:
        upstreams = upstream_map.get(job_id, [])

        # Determine if this node should be skipped based on upstream outcomes.
        should_skip = False
        for upstream_id in upstreams:
            upstream_status = job_results.get(upstream_id)
            condition = condition_map.get((upstream_id, job_id), EdgeCondition.SUCCESS)

            if condition == EdgeCondition.SUCCESS and upstream_status != RunStatus.SUCCESS:
                should_skip = True
                break
            elif condition == EdgeCondition.FAILURE and upstream_status not in (
                RunStatus.FAILED, RunStatus.TIMEOUT
            ):
                should_skip = True
                break
            # EdgeCondition.ALWAYS: never skip

        if should_skip:
            logger.info(
                "Pipeline node skipped: pipeline_id=%s job_id=%s", pipeline_id, job_id
            )
            job_results[job_id] = RunStatus.PENDING  # treat skip as not-run
            continue

        logger.info(
            "Pipeline executing node: pipeline_id=%s job_id=%s", pipeline_id, job_id
        )
        try:
            await TaskScheduler._run_job_inner(job_id)

            # Determine final status by reading the most recent run.
            run_status = await _get_last_run_status(job_id)
            job_results[job_id] = run_status

            logger.info(
                "Pipeline node finished: pipeline_id=%s job_id=%s status=%s",
                pipeline_id, job_id, run_status,
            )
        except Exception as exc:
            logger.exception(
                "Pipeline node error: pipeline_id=%s job_id=%s error=%s",
                pipeline_id, job_id, exc,
            )
            job_results[job_id] = RunStatus.FAILED

    logger.info(
        "Pipeline execution complete: pipeline_id=%s results=%s",
        pipeline_id, {k: v.value for k, v in job_results.items()},
    )


async def _get_last_run_status(job_id: int) -> RunStatus:
    """Return the status of the most recently completed run for *job_id*."""
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(RunHistory)
            .where(RunHistory.job_id == job_id)
            .order_by(RunHistory.id.desc())
            .limit(1)
        )
        run: Optional[RunHistory] = result.scalar_one_or_none()
        return run.status if run else RunStatus.FAILED

