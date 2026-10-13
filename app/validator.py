"""
validator.py — Nodo de Validación (+ Sintetizador).

Validator: antes de dar el END revisa los aportes contra la rúbrica.
  - Chequeo determinístico primero (barato, sin LLM): ¿aportaron ambos agentes?
  - Después juicio del LLM con la rúbrica (salida estructurada).
  - Si ya se agotaron las revisiones, aprueba con salvedad (evita bucles eternos).

Synthesizer: arma la respuesta final con los aportes aprobados.
"""
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.prompts import SYNTHESIZER_SYSTEM, VALIDATOR_SYSTEM
from app.state import OrchestratorState
from app.supervisor import summarize_contributions, user_request

MAX_REVISIONS = 2  # cuántas veces puede devolver el trabajo el validador


class ValidationResult(BaseModel):
    approved: bool = Field(description="True si se cumple TODA la rúbrica")
    feedback: str = Field(description="Qué falta y qué agente lo corrige (vacío si aprobó)")


def to_text(content) -> str:
    """Algunos proveedores devuelven el contenido como lista de bloques; lo normalizamos a str."""
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)


def make_validator(llm):
    judge = llm.with_structured_output(ValidationResult)

    async def validator_node(state: OrchestratorState) -> dict:
        revisions = state.get("revisions", 0)
        agents_done = {c["agent"] for c in state.get("contributions", [])}

        # 1) Chequeo determinístico: faltan aportes -> rechazo sin gastar LLM
        missing = {"researcher", "analyst"} - agents_done
        if missing:
            result = ValidationResult(approved=False, feedback=f"Faltan aportes de: {sorted(missing)}")
        else:
            # 2) Juicio con rúbrica
            prompt = (f"Consigna:\n{user_request(state)}\n\n"
                      f"Aportes:\n{summarize_contributions(state, max_chars=1500)}")
            result = await judge.ainvoke([SystemMessage(content=VALIDATOR_SYSTEM), HumanMessage(content=prompt)])

        # 3) Límites: sin revisiones o sin pasos disponibles no hay a dónde volver -> cerramos con salvedad
        budget_exhausted = state.get("step_count", 0) > state.get("max_steps", 8)
        if not result.approved and (revisions >= MAX_REVISIONS or budget_exhausted):
            motivo = "límite de pasos" if budget_exhausted else "límite de revisiones"
            return {"task_completed": True,
                    "validation_feedback": f"[APROBADO FORZADO por {motivo}] {result.feedback}"}

        if result.approved:
            return {"task_completed": True, "validation_feedback": ""}
        return {"task_completed": False, "revisions": revisions + 1, "validation_feedback": result.feedback}

    return validator_node


def make_synthesizer(llm):
    async def synthesizer_node(state: OrchestratorState) -> dict:
        note = state.get("validation_feedback", "")
        prompt = (f"Consigna:\n{user_request(state)}\n\n"
                  f"Aportes:\n{summarize_contributions(state, max_chars=2000)}\n\n"
                  f"Nota de validación: {note or 'aprobado sin observaciones'}")
        resp = await llm.ainvoke([SystemMessage(content=SYNTHESIZER_SYSTEM), HumanMessage(content=prompt)])
        text = to_text(resp.content)
        return {"final_answer": text, "messages": [AIMessage(content=text, name="synthesizer")]}

    return synthesizer_node
