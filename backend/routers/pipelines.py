"""
PyTaskForge – JobFlow Pipelines Router
========================================
GET    /api/pipelines              →  list pipelines (paginated)
POST   /api/pipelines              →  create a pipeline with edges
GET    /api/pipelines/{id}         →  get pipeline + edges
PUT    /api/pipelines/{id}         →  update pipeline metadata
DELETE /api/pipelines/{id}         →  delete a pipeline
POST   /api/pipelines/{id}/trigger →  trigger pipeline execution immediately
"""
from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.security import TokenData, require_authenticated
from backend.models.database import (
    EdgeCondition,
    Pipeline,
    PipelineEdge,
    PipelineStatus,
    get_db,
)
from backend.services.pipeline_runner import execute_pipeline, topological_sort

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pipelines", tags=["pipelines"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class EdgeCreate(BaseModel):
    upstream_job_id: int
    downstream_job_id: int
    on_condition: EdgeCondition = EdgeCondition.SUCCESS

    @model_validator(mode="after")
    def _no_self_loop(self) -> "EdgeCreate":
        if self.upstream_job_id == self.downstream_job_id:
            raise ValueError("upstream_job_id and downstream_job_id must be different.")
        return self


class PipelineCreate(BaseModel):
    name: str
    description: Optional[str] = None
    edges: List[EdgeCreate] = []


class PipelineUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[PipelineStatus] = None
    edges: Optional[List[EdgeCreate]] = None


class EdgeResponse(BaseModel):
    id: int
    upstream_job_id: int
    downstream_job_id: int
    on_condition: EdgeCondition

    model_config = {"from_attributes": True}


class PipelineResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    status: PipelineStatus
    owner_id: Optional[int]
    edges: List[EdgeResponse] = []

    model_config = {"from_attributes": True}


# ── Helper ────────────────────────────────────────────────────────────────────

def _check_for_cycles(edges: List[EdgeCreate]) -> None:
    """Raise ValueError if the proposed edges contain a cycle."""
    from backend.models.database import PipelineEdge as _PE

    # Build mock PipelineEdge objects for the cycle check.
    mock_edges = [
        type("E", (), {"upstream_job_id": e.upstream_job_id, "downstream_job_id": e.downstream_job_id})()
        for e in edges
    ]
    try:
        topological_sort(mock_edges)  # type: ignore[arg-type]
    except ValueError as exc:
        raise ValueError(str(exc)) from exc


async def _serialize_pipeline(db: AsyncSession, pipeline: Pipeline) -> PipelineResponse:
    """Build a fully materialized pipeline response (including edges)."""
    edge_result = await db.execute(
        select(PipelineEdge).where(PipelineEdge.pipeline_id == pipeline.id)
    )
    edges = [
        EdgeResponse(
            id=edge.id,
            upstream_job_id=edge.upstream_job_id,
            downstream_job_id=edge.downstream_job_id,
            on_condition=edge.on_condition,
        )
        for edge in edge_result.scalars().all()
    ]
    return PipelineResponse(
        id=pipeline.id,
        name=pipeline.name,
        description=pipeline.description,
        status=pipeline.status,
        owner_id=pipeline.owner_id,
        edges=edges,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=List[PipelineResponse],
    summary="List all pipelines",
)
async def list_pipelines(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(require_authenticated),
) -> List[PipelineResponse]:
    result = await db.execute(
        select(Pipeline)
        .where(Pipeline.status != PipelineStatus.ARCHIVED)
        .offset(offset)
        .limit(limit)
    )
    pipelines = result.scalars().all()
    return [await _serialize_pipeline(db, pipeline) for pipeline in pipelines]


@router.post(
    "",
    response_model=PipelineResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a pipeline with dependency edges",
)
async def create_pipeline(
    body: PipelineCreate,
    db: AsyncSession = Depends(get_db),
    token: TokenData = Depends(require_authenticated),
) -> PipelineResponse:
    if body.edges:
        try:
            _check_for_cycles(body.edges)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            )

    pipeline = Pipeline(
        name=body.name,
        description=body.description,
        owner_id=token.user_id,
        status=PipelineStatus.ACTIVE,
    )
    db.add(pipeline)
    await db.flush()  # get the pipeline.id before adding edges

    for edge_data in body.edges:
        edge = PipelineEdge(
            pipeline_id=pipeline.id,
            upstream_job_id=edge_data.upstream_job_id,
            downstream_job_id=edge_data.downstream_job_id,
            on_condition=edge_data.on_condition,
        )
        db.add(edge)

    await db.commit()
    await db.refresh(pipeline)
    return await _serialize_pipeline(db, pipeline)


@router.get(
    "/{pipeline_id}",
    response_model=PipelineResponse,
    summary="Get a pipeline with its edges",
)
async def get_pipeline(
    pipeline_id: int,
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(require_authenticated),
) -> PipelineResponse:
    pipeline = await db.get(Pipeline, pipeline_id)
    if not pipeline or pipeline.status == PipelineStatus.ARCHIVED:
        raise HTTPException(status_code=404, detail="Pipeline not found.")
    return await _serialize_pipeline(db, pipeline)


@router.put(
    "/{pipeline_id}",
    response_model=PipelineResponse,
    summary="Update a pipeline",
)
async def update_pipeline(
    pipeline_id: int,
    body: PipelineUpdate,
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(require_authenticated),
) -> PipelineResponse:
    pipeline = await db.get(Pipeline, pipeline_id)
    if not pipeline or pipeline.status == PipelineStatus.ARCHIVED:
        raise HTTPException(status_code=404, detail="Pipeline not found.")

    if body.edges is not None:
        try:
            _check_for_cycles(body.edges)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            )
        # Replace all existing edges.
        existing = await db.execute(
            select(PipelineEdge).where(PipelineEdge.pipeline_id == pipeline_id)
        )
        for edge in existing.scalars().all():
            await db.delete(edge)

        for edge_data in body.edges:
            db.add(PipelineEdge(
                pipeline_id=pipeline_id,
                upstream_job_id=edge_data.upstream_job_id,
                downstream_job_id=edge_data.downstream_job_id,
                on_condition=edge_data.on_condition,
            ))

    for field, value in body.model_dump(exclude_none=True, exclude={"edges"}).items():
        setattr(pipeline, field, value)

    await db.commit()
    await db.refresh(pipeline)
    return await _serialize_pipeline(db, pipeline)


@router.delete(
    "/{pipeline_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Archive (soft-delete) a pipeline",
)
async def delete_pipeline(
    pipeline_id: int,
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(require_authenticated),
) -> None:
    pipeline = await db.get(Pipeline, pipeline_id)
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found.")
    pipeline.status = PipelineStatus.ARCHIVED
    await db.commit()


@router.post(
    "/{pipeline_id}/trigger",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger a pipeline execution immediately",
)
async def trigger_pipeline(
    pipeline_id: int,
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(require_authenticated),
) -> dict:
    """Execute the pipeline immediately, respecting job dependency order."""
    pipeline = await db.get(Pipeline, pipeline_id)
    if not pipeline or pipeline.status == PipelineStatus.ARCHIVED:
        raise HTTPException(status_code=404, detail="Pipeline not found.")

    asyncio.create_task(execute_pipeline(pipeline_id))
    return {"detail": f"Pipeline {pipeline_id} execution started.", "pipeline_id": pipeline_id}

