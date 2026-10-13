"""
Tests del grafo (M6 adaptado a async + checkpointer + HITL). Sin API key ni red.
"""
import json

from langgraph.types import Command

from app.agents.analyst_agent import calculate, sentiment_analysis, validate_schema
from app.agents.research_agent import search_knowledge_base
from app.graph import initial_state
from tests.conftest import CRITICAL, FakeLLM, fake_node, make_graph, route, verdict


def cfg(tid="t1"):
    return {"configurable": {"thread_id": tid}}


# ---------------- Tools (igual que M6) ----------------

def test_kb_search_finds_reviews():
    out = json.loads(search_knowledge_base.invoke({"query": "reseñas zenbuds"}))
    assert len(out) >= 5 and all("zenbuds" in d["topic"] for d in out)


def test_sentiment_and_calculate_and_schema():
    s = json.loads(sentiment_analysis.invoke({"texts": ["Excelente, lo recomiendo", "Terrible, defectuoso", "ok"]}))
    assert s["positivo"] == 1 and s["negativo"] == 1 and s["neutro"] == 1
    assert calculate.invoke({"expression": "(120 + 105 + 90) / 3"}) == "105.0"
    assert calculate.invoke({"expression": "__import__('os')"}).startswith("Error")
    assert validate_schema.invoke({"json_str": '{"a": 1}', "required_keys": ["a", "b"]}).startswith("Faltan")


# ---------------- Flujo del grafo (M6, ahora async) ----------------

async def test_happy_path_delegation_order():
    graph = make_graph()
    state = await graph.ainvoke(initial_state("consulta"), cfg())
    assert [c["agent"] for c in state["contributions"]] == ["researcher", "analyst"]
    assert state["task_completed"] and state["final_answer"] == "RESPUESTA FINAL (fake)"


async def test_guard_blocks_early_finish():
    graph = make_graph(dict(route=[route("FINISH"), route("analyst"), route("FINISH")], validation=[verdict(True)]))
    state = await graph.ainvoke(initial_state("consulta"), cfg())
    assert state["contributions"][0]["agent"] == "researcher"


async def test_validator_rejection_triggers_refinement():
    graph = make_graph(dict(
        route=[route("researcher"), route("analyst"), route("FINISH"), route("analyst", "corregí"), route("FINISH")],
        validation=[verdict(False, "falta el porcentaje"), verdict(True)]))
    state = await graph.ainvoke(initial_state("consulta"), cfg())
    assert state["revisions"] == 1
    assert [c["agent"] for c in state["contributions"]].count("analyst") == 2


async def test_no_infinite_loop_step_limit():
    graph = make_graph(dict(route=[route("researcher")], validation=[verdict(True)]))
    state = await graph.ainvoke(initial_state("consulta", max_steps=3), cfg())
    assert state["step_count"] <= 4 and state["final_answer"]


async def test_forced_approval_after_max_revisions():
    graph = make_graph(dict(route=[route("researcher"), route("analyst"), route("FINISH")],
                            validation=[verdict(False, "nunca conforme")]))
    state = await graph.ainvoke(initial_state("consulta", max_steps=30), cfg())
    assert state["task_completed"] and "FORZADO" in state["validation_feedback"]


# ---------------- HITL (PE7) ----------------

async def test_hitl_pauses_before_executor_and_resumes_on_approval():
    log = []
    graph = make_graph(CRITICAL, log=log)
    await graph.ainvoke(initial_state("publicá el precio"), cfg())

    snap = await graph.aget_state(cfg())
    assert snap.next == ("hitl_gate",)                      # pausado en el nodo HITL
    assert "executor" not in log                            # la acción crítica NO corrió
    req = snap.tasks[0].interrupts[0].value
    assert req["type"] == "approval_required" and req["proposed_action"] == "publicá USD 102"

    state = await graph.ainvoke(Command(resume={"approved": True, "comment": "ok"}), cfg())
    assert "executor" in log and state["final_answer"]
    assert state["approval"] == {"approved": True, "comment": "ok"}


async def test_hitl_rejection_skips_executor():
    log = []
    graph = make_graph(CRITICAL, log=log)
    await graph.ainvoke(initial_state("publicá el precio"), cfg())
    state = await graph.ainvoke(Command(resume={"approved": False, "comment": "precio mal"}), cfg())

    assert "executor" not in log                            # nunca se ejecutó
    rejected = [c for c in state["contributions"] if c["agent"] == "executor"]
    assert len(rejected) == 1 and "RECHAZADA" in rejected[0]["content"]
    assert state["final_answer"]                            # igual se cierra el flujo


async def test_read_only_task_never_hits_hitl():
    graph = make_graph()
    await graph.ainvoke(initial_state("solo lectura"), cfg())
    assert (await graph.aget_state(cfg())).next == ()       # terminó, sin pausas
