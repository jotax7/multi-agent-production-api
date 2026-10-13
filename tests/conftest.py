"""
Fixtures y dobles de prueba. Todo OFFLINE: sin API key, sin LLM, sin Redis Stack.

- Redis: `fakeredis` por defecto. Si definís REDIS_TEST_URL (un Redis real, aunque no sea Stack)
  los mismos tests corren contra ese Redis.
- Checkpointer: InMemorySaver (misma interfaz que AsyncRedisSaver, que necesita Redis Stack).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fakeredis
import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from redis.asyncio import Redis

from app.graph import build_graph
from app.supervisor import RouteDecision
from app.validator import ValidationResult


# ---------------- Doble de prueba del LLM (async) ----------------

class _Structured:
    """Imita llm.with_structured_output(Schema): devuelve el próximo valor del guion."""
    def __init__(self, fake, schema):
        self.fake, self.schema = fake, schema

    async def ainvoke(self, _messages):
        script = self.fake.route if self.schema is RouteDecision else self.fake.validation
        return script.pop(0) if len(script) > 1 else script[0]   # el último se repite


class FakeLLM:
    def __init__(self, route, validation):
        self.route, self.validation = list(route), list(validation)

    def with_structured_output(self, schema):
        return _Structured(self, schema)

    async def ainvoke(self, _messages):  # lo usa el sintetizador
        return AIMessage(content="RESPUESTA FINAL (fake)")


def route(next_, instr="ok"):
    return RouteDecision(next=next_, instruction=instr, reasoning="test")


def verdict(approved, feedback=""):
    return ValidationResult(approved=approved, feedback=feedback)


def fake_node(name, text, log=None, gate=None, boom=False):
    """
    Especialista de mentira. `log`: lista donde anota que corrió. `gate`: asyncio.Event que lo
    bloquea hasta que el test lo libere. `boom`: lanza una excepción (para probar FAILED).
    """
    async def node(state):
        if log is not None:
            log.append(name)
        if gate is not None:
            await gate.wait()
        if boom:
            raise RuntimeError("el LLM explotó")
        return {"contributions": [{"agent": name, "step": state["step_count"], "content": text}]}
    return node


HAPPY = dict(route=[route("researcher"), route("analyst"), route("FINISH")], validation=[verdict(True)])
CRITICAL = dict(route=[route("researcher"), route("analyst"), route("executor", "publicá USD 102"), route("FINISH")],
                validation=[verdict(True)])


def make_graph(script=HAPPY, log=None, gate=None, boom=False, nodes=None):
    base = {
        "researcher": fake_node("researcher", "DATOS", log, gate, boom),
        "analyst": fake_node("analyst", "ANALISIS", log),
        "executor": fake_node("executor", "EJECUTADO", log),
    }
    base.update(nodes or {})
    return build_graph(llm=FakeLLM(**script), nodes=base, checkpointer=InMemorySaver())


@pytest.fixture
async def redis_client():
    url = os.getenv("REDIS_TEST_URL")
    client = Redis.from_url(url, decode_responses=True) if url else fakeredis.FakeAsyncRedis(decode_responses=True)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()
