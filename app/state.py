"""
state.py — Estado compartido del orquestador.

Todos los nodos del grafo leen y escriben sobre este State.
La idea clave: además de los `messages`, guardamos CAMPOS ESTRUCTURADOS
para saber qué agente aportó qué (trazabilidad) sin depender de parsear el chat.
"""
import operator
from typing import Annotated, Literal, TypedDict

from langgraph.graph import MessagesState

# Nombres de los nodos a los que puede ir el flujo. Se usan en el Literal
# de las aristas condicionales, así un typo rompe en tiempo de chequeo y no en runtime.
AgentName = Literal["researcher", "analyst", "executor", "FINISH"]


class Contribution(TypedDict):
    """Un aporte de un agente especialista (queda registrado en el state)."""
    agent: str      # "researcher" | "analyst" | "executor"
    step: int       # en qué paso del flujo aportó
    content: str    # lo que produjo


class OrchestratorState(MessagesState):
    """
    Hereda `messages` de MessagesState y suma campos de control.

    - contributions: lista de aportes. `operator.add` es el *reducer*:
      cada nodo devuelve [su_aporte] y LangGraph lo SUMA a la lista
      (en vez de pisarla). Así no se pierde contexto entre agentes.
    """
    contributions: Annotated[list[Contribution], operator.add]

    next_agent: AgentName      # decisión del supervisor (a quién rutear)
    instruction: str           # instrucción puntual para el especialista elegido
    step_count: int            # contador de pasos -> evita el "Supervisor Infinito"
    max_steps: int             # tope duro de pasos
    revisions: int             # cuántas veces el validador devolvió el trabajo
    validation_feedback: str   # feedback del validador para que el supervisor refine
    task_completed: bool       # True cuando el validador aprobó
    final_answer: str          # respuesta final al usuario
    approval: dict             # [PE7] decisión humana del HITL: {"approved": bool, "comment": str}
