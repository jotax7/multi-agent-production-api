"""
Test de integración del HITL con el agente Ejecutor REAL (ReAct + tools) y un "LLM" guionado
que emite tool calls. Prueba el punto central: la tool con efecto secundario NO se ejecuta
hasta que un humano aprueba, y si rechaza, el archivo de salida nunca se crea.
"""
import json

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.agents.executor_agent import build_executor_agent
from app.graph import build_graph, executor_context, initial_state, make_specialist_node
from tests.conftest import CRITICAL, FakeLLM, fake_node


class ScriptedToolModel(GenericFakeChatModel):
    """Chat model falso que acepta bind_tools (el ReAct agent lo exige) y devuelve mensajes guionados."""
    def bind_tools(self, tools, **kwargs):
        return self


def executor_node():
    llm = ScriptedToolModel(messages=iter([
        AIMessage(content="", tool_calls=[{"name": "publish_price_update", "id": "c1",
                                           "args": {"product": "Zenbuds", "channel": "tienda oficial", "new_price": 102.0}}]),
        AIMessage(content="Publiqué USD 102 en tienda oficial."),
    ]))
    return make_specialist_node(build_executor_agent(llm), "executor", executor_context)


def build(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTBOX_PATH", str(tmp_path / "outbox.jsonl"))
    nodes = {"researcher": fake_node("researcher", "DATOS"), "analyst": fake_node("analyst", "precio 102"),
             "executor": executor_node()}
    return build_graph(llm=FakeLLM(**CRITICAL), nodes=nodes, checkpointer=InMemorySaver())


CFG = {"configurable": {"thread_id": "j1"}}


async def test_side_effect_runs_only_after_approval(tmp_path, monkeypatch):
    graph = build(tmp_path, monkeypatch)
    outbox = tmp_path / "outbox.jsonl"

    await graph.ainvoke(initial_state("publicá el precio"), CFG)
    assert not outbox.exists()                                   # pausado: nada se ejecutó

    state = await graph.ainvoke(Command(resume={"approved": True, "comment": ""}), CFG)
    line = json.loads(outbox.read_text().strip())
    assert line["action"] == "publish_price_update" and line["new_price"] == 102.0
    assert state["final_answer"]


async def test_side_effect_never_runs_when_rejected(tmp_path, monkeypatch):
    graph = build(tmp_path, monkeypatch)
    await graph.ainvoke(initial_state("publicá el precio"), CFG)
    await graph.ainvoke(Command(resume={"approved": False, "comment": "no"}), CFG)
    assert not (tmp_path / "outbox.jsonl").exists()
