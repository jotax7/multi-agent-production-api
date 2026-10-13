"""
graph.py — Construcción del StateGraph (Módulo 6 + Pre-entrega 7).

Topología:

    START -> supervisor -(cond)-> researcher | analyst | hitl_gate | validator
             researcher -> supervisor
             analyst    -> supervisor
             hitl_gate  -(Command)-> executor (aprobado) | supervisor (rechazado)   <- PE7: HITL
             executor   -> supervisor                                                <- PE7
             validator  -(cond)-> synthesizer | supervisor
             synthesizer -> END

Cambios respecto del M6:
  * Nodos 100% async (`ainvoke`) -> no bloquean el event loop de FastAPI.
  * `build_graph(checkpointer=...)`: el estado de cada ejecución (thread_id = job_id) se
    guarda en Redis, así el grafo se puede PAUSAR (HITL) y reanudar, incluso tras un reinicio.
  * Nuevo especialista `executor` + nodo `hitl_gate`.

Todos los nodos son inyectables (parámetro `nodes`) para testear SIN API key ni red.
"""
from typing import Callable, Literal

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph

from app.hitl import hitl_gate
from app.state import OrchestratorState
from app.supervisor import make_supervisor, user_request
from app.validator import make_synthesizer, make_validator, to_text


# ---------- Nodos de especialistas (con aislamiento de contexto) ----------

def make_specialist_node(agent, name: str, context_builder: Callable[[OrchestratorState], str]):
    """
    Envuelve un agente ReAct como nodo ASYNC del grafo.
    El especialista NO recibe el historial completo: solo lo que arma `context_builder`.
    """
    async def node(state: OrchestratorState) -> dict:
        task = context_builder(state)
        result = await agent.ainvoke({"messages": [HumanMessage(content=task)]})  # <- async nativo
        content = to_text(result["messages"][-1].content)
        return {
            "contributions": [{"agent": name, "step": state.get("step_count", 0), "content": content}],
            "messages": [AIMessage(content=content, name=name)],
        }
    return node


def researcher_context(state: OrchestratorState) -> str:
    msg = state.get("instruction") or user_request(state)
    fb = state.get("validation_feedback")
    return msg + (f"\n\nCorrección pedida por el validador: {fb}" if fb else "")


def analyst_context(state: OrchestratorState) -> str:
    data = "\n\n".join(c["content"] for c in state.get("contributions", []) if c["agent"] == "researcher")
    fb = state.get("validation_feedback")
    return (f"{state.get('instruction') or 'Analizá los datos.'}\n\n"
            f"DATOS INVESTIGADOS:\n{data}" + (f"\n\nCorrección pedida por el validador: {fb}" if fb else ""))


def executor_context(state: OrchestratorState) -> str:
    """El ejecutor ve: la orden aprobada + SOLO los resultados del analista (valores a usar)."""
    analysis = "\n\n".join(c["content"] for c in state.get("contributions", []) if c["agent"] == "analyst")
    note = (state.get("approval") or {}).get("comment")
    return (f"ORDEN APROBADA POR UN HUMANO: {state.get('instruction')}\n\n"
            f"RESULTADOS DEL ANÁLISIS (usá estos valores):\n{analysis}"
            + (f"\n\nComentario del aprobador: {note}" if note else ""))


# ---------- Aristas condicionales (Literal = destinos válidos) ----------

def route_after_supervisor(state: OrchestratorState) -> Literal["researcher", "analyst", "hitl_gate", "validator"]:
    nxt = state["next_agent"]
    if nxt == "FINISH":
        return "validator"      # FINISH => validación formal antes del END
    if nxt == "executor":
        return "hitl_gate"      # [PE7] nunca se llega al executor sin pasar por la aprobación humana
    return nxt


def route_after_validator(state: OrchestratorState) -> Literal["synthesizer", "supervisor"]:
    return "synthesizer" if state.get("task_completed") else "supervisor"


# ---------- Armado ----------

def build_graph(llm=None, nodes: dict | None = None, checkpointer=None):
    """
    `llm`: modelo de chat (si es None se crea con llm.get_llm()).
    `nodes`: overrides opcionales {"supervisor", "researcher", "analyst", "executor", "validator", "synthesizer"}
    `checkpointer`: AsyncRedisSaver en producción (InMemorySaver en tests). Obligatorio para HITL.
    """
    nodes = dict(nodes or {})
    if llm is None and len(nodes) < 6:
        from app.llm import get_llm
        llm = get_llm()

    # Imports de agentes perezosos: los tests no necesitan LLM real
    if "researcher" not in nodes:
        from app.agents.research_agent import build_research_agent
        nodes["researcher"] = make_specialist_node(build_research_agent(llm), "researcher", researcher_context)
    if "analyst" not in nodes:
        from app.agents.analyst_agent import build_analyst_agent
        nodes["analyst"] = make_specialist_node(build_analyst_agent(llm), "analyst", analyst_context)
    if "executor" not in nodes:
        from app.agents.executor_agent import build_executor_agent
        nodes["executor"] = make_specialist_node(build_executor_agent(llm), "executor", executor_context)
    nodes.setdefault("supervisor", make_supervisor(llm) if llm else None)
    nodes.setdefault("validator", make_validator(llm) if llm else None)
    nodes.setdefault("synthesizer", make_synthesizer(llm) if llm else None)

    g = StateGraph(OrchestratorState)
    for name, fn in nodes.items():
        g.add_node(name, fn)
    g.add_node("hitl_gate", hitl_gate)           # [PE7] punto de interrupción

    g.add_edge(START, "supervisor")
    g.add_conditional_edges("supervisor", route_after_supervisor)
    g.add_edge("researcher", "supervisor")       # los especialistas siempre vuelven al supervisor
    g.add_edge("analyst", "supervisor")
    g.add_edge("executor", "supervisor")
    # hitl_gate decide su destino con Command(goto=...) -> no lleva arista saliente explícita
    g.add_conditional_edges("validator", route_after_validator)
    g.add_edge("synthesizer", END)
    return g.compile(checkpointer=checkpointer)


def initial_state(question: str, max_steps: int = 8) -> dict:
    """Estado inicial con los contadores en cero."""
    return {
        "messages": [HumanMessage(content=question)],
        "contributions": [], "step_count": 0, "max_steps": max_steps,
        "revisions": 0, "validation_feedback": "", "task_completed": False, "final_answer": "",
        "approval": {},
    }
