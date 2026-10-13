"""
hitl.py — [PE7] Nodo Human-in-the-loop (aprobación humana).

Cómo funciona (clave para entender LangGraph):
  1. Cuando el supervisor decide `executor` (acción con efectos secundarios), el flujo pasa
     primero por este nodo.
  2. `interrupt(payload)` PAUSA el grafo: LangGraph guarda un checkpoint en Redis y
     `ainvoke` devuelve el control. El worker marca el job como AWAITING_APPROVAL.
  3. Una persona llama POST /tasks/{id}/approve. El worker reanuda con
     `Command(resume={"approved": ..., "comment": ...})` y `interrupt()` devuelve ese valor.
  4. Aprobado -> sigue al `executor`. Rechazado -> se registra el rechazo y vuelve al supervisor
     (sin ejecutar nada).

Ojo: al reanudar, LangGraph vuelve a correr el nodo DESDE EL PRINCIPIO. Por eso todo lo que va
antes de `interrupt()` tiene que ser idempotente (acá es solo armar un dict).
"""
from typing import Literal

from langgraph.types import Command, interrupt

from app.agents.executor_agent import CRITICAL_TOOL_NAMES
from app.state import OrchestratorState
from app.supervisor import user_request


def assess_risk(state: OrchestratorState) -> dict:
    """
    Cómo el sistema IDENTIFICA una tarea crítica: todo lo que pasa por el executor lo es,
    porque sus herramientas tienen efectos secundarios (publicar precios, mandar emails).
    Devuelve el detalle que verá la persona que aprueba.
    """
    return {
        "level": "critical",
        "reason": "El agente ejecutor tiene herramientas con efectos secundarios irreversibles.",
        "tools_at_risk": CRITICAL_TOOL_NAMES,
        "proposed_action": state.get("instruction", ""),
        "user_request": user_request(state),
    }


async def hitl_gate(state: OrchestratorState) -> Command[Literal["executor", "supervisor"]]:
    """Pausa el grafo hasta recibir aprobación externa. (async solo por consistencia con el resto.)"""
    request = {"type": "approval_required", **assess_risk(state)}

    # ⏸️ El grafo se detiene acá. Cuando alguien reanuda, `decision` es lo que mandó /approve.
    decision = interrupt(request)

    approved = bool(decision.get("approved"))
    comment = decision.get("comment", "")
    record = {"approved": approved, "comment": comment}

    if approved:
        return Command(goto="executor", update={"approval": record})

    # Rechazo: dejamos constancia como aporte (el supervisor y el sintetizador lo ven) y NO ejecutamos.
    rejected = {
        "agent": "executor",
        "step": state.get("step_count", 0),
        "content": f"ACCIÓN RECHAZADA por el aprobador humano. Motivo: {comment or 'sin comentario'}. No se ejecutó nada.",
    }
    return Command(goto="supervisor", update={"approval": record, "contributions": [rejected]})
