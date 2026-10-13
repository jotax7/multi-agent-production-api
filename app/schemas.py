"""
schemas.py — Contratos de la API (request/response). Pydantic valida todo en la frontera.
"""
from enum import Enum

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    PENDING = "PENDING"                        # encolado, esperando un lugar para correr
    RUNNING = "RUNNING"                        # el grafo se está ejecutando
    AWAITING_APPROVAL = "AWAITING_APPROVAL"    # pausado en el nodo HITL, espera a una persona
    DONE = "DONE"                              # terminó bien (`answer` completo)
    FAILED = "FAILED"                          # falló o expiró (`error` explica por qué)


# Estados finales: el cliente puede dejar de hacer polling
TERMINAL_STATUSES = {JobStatus.DONE, JobStatus.FAILED}


class TaskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000, description="Consigna para el orquestador multi-agente")
    max_steps: int = Field(default=8, ge=1, le=15, description="Tope duro de pasos del supervisor")


class TaskCreated(BaseModel):
    job_id: str
    status: JobStatus
    status_url: str


class ApprovalRequest(BaseModel):
    approved: bool
    comment: str = Field(default="", max_length=500)
