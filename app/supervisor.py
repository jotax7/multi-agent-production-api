"""
supervisor.py — Nodo Supervisor (router inteligente + controlador de flujo).

Qué hace en cada vuelta:
  1. Corta por contador de pasos (anti "Supervisor Infinito"): sin llamar al LLM.
  2. Le pide al LLM una decisión ESTRUCTURADA (Pydantic) -> next / instruction.
  3. Aplica guardas determinísticas (no confiamos ciegamente en el LLM).
  4. Escribe la decisión en el State; la arista condicional (graph.py) la lee.
"""
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.prompts import SUPERVISOR_SYSTEM
from app.state import OrchestratorState


class RouteDecision(BaseModel):
    """Salida estructurada del supervisor. Literal -> solo valores válidos."""
    next: Literal["researcher", "analyst", "executor", "FINISH"] = Field(description="Próximo nodo")
    instruction: str = Field(description="Orden corta y específica para ese agente")
    reasoning: str = Field(description="Por qué se tomó esta decisión (1 línea)")


def user_request(state: OrchestratorState) -> str:
    """La consigna original = primer mensaje humano."""
    for m in state["messages"]:
        if m.type == "human":
            return m.content
    return ""


def summarize_contributions(state: OrchestratorState, max_chars: int = 600) -> str:
    """Resumen compacto de los aportes (truncado) para no inflar el contexto del supervisor."""
    contribs = state.get("contributions", [])
    if not contribs:
        return "(todavía no hay aportes)"
    return "\n".join(
        f"[paso {c['step']}] {c['agent']}: {c['content'][:max_chars]}" for c in contribs
    )


def apply_guards(decision: RouteDecision, state: OrchestratorState) -> RouteDecision:
    """Reglas duras que corrigen decisiones incoherentes del LLM."""
    agents_done = {c["agent"] for c in state.get("contributions", [])}
    # [PE7] La acción con efectos secundarios solo se pide UNA vez (aprobada o rechazada, ya quedó registrada)
    if decision.next == "executor" and "executor" in agents_done:
        return RouteDecision(next="FINISH", instruction="", reasoning="Guarda: la acción ya se resolvió.")
    # No se puede analizar, actuar ni cerrar sin datos investigados
    if decision.next in ("analyst", "executor", "FINISH") and "researcher" not in agents_done:
        return RouteDecision(next="researcher", instruction=user_request(state),
                             reasoning="Guarda: falta investigar antes de continuar.")
    # No se puede actuar ni cerrar sin análisis
    if decision.next in ("executor", "FINISH") and "analyst" not in agents_done:
        return RouteDecision(next="analyst", instruction=decision.instruction or "Analizá los datos.",
                             reasoning="Guarda: falta el análisis antes de cerrar.")
    return decision


def make_supervisor(llm):
    """Fábrica: devuelve la función-nodo con el LLM ya inyectado."""
    router = llm.with_structured_output(RouteDecision)

    async def supervisor_node(state: OrchestratorState) -> dict:
        step = state.get("step_count", 0)

        # 1) Tope duro de pasos: forzamos cierre (la validación igual corre después)
        if step >= state.get("max_steps", 8):
            return {"next_agent": "FINISH", "instruction": "", "step_count": step + 1}

        # 2) Decisión del LLM sobre un contexto ACOTADO (consigna + resumen + feedback)
        prompt = (
            f"Consigna del usuario:\n{user_request(state)}\n\n"
            f"Aportes hasta ahora:\n{summarize_contributions(state)}\n\n"
            f"Feedback del validador (si hay): {state.get('validation_feedback') or 'ninguno'}\n"
            f"Paso {step + 1} de {state.get('max_steps', 8)}."
        )
        decision = await router.ainvoke([SystemMessage(content=SUPERVISOR_SYSTEM), HumanMessage(content=prompt)])

        # 3) Guardas determinísticas
        decision = apply_guards(decision, state)

        # 4) Escribimos en el State
        return {"next_agent": decision.next, "instruction": decision.instruction, "step_count": step + 1}

    return supervisor_node
