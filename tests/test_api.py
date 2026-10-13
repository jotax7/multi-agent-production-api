"""
Tests de la API: asincronía real, estado en Redis, FAILED ante excepciones, HITL por HTTP.
Redis: fakeredis (o REDIS_TEST_URL). Grafo: nodos falsos + InMemorySaver.
"""
import asyncio
import json
import time

import httpx
import pytest

from app.config import Settings
from app.main import create_app
from tests.conftest import CRITICAL, fake_node, make_graph


@pytest.fixture
def make_client(redis_client):
    """Devuelve un context manager async que levanta la app (con lifespan) sobre un grafo dado."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _make(graph, **settings):
        app = create_app(settings=Settings(**settings), redis_client=redis_client, graph=graph)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                yield c, app
    return _make


async def wait_status(client, job_id, wanted, timeout=5.0):
    """Polling como lo haría un cliente real."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        job = (await client.get(f"/tasks/{job_id}")).json()
        if job["status"] in wanted:
            return job
        await asyncio.sleep(0.02)
    raise AssertionError(f"timeout esperando {wanted}; último estado: {job['status']}")


# ---------- 1. Asincronía ----------

async def test_post_returns_job_id_without_waiting_for_the_agent(make_client):
    gate = asyncio.Event()                       # el investigador queda bloqueado hasta que lo liberemos
    async with make_client(make_graph(gate=gate)) as (c, _):
        t0 = time.monotonic()
        r = await c.post("/tasks", json={"question": "hola mundo"})
        assert r.status_code == 202 and time.monotonic() - t0 < 0.5
        job_id = r.json()["job_id"]

        job = await wait_status(c, job_id, {"RUNNING"})       # el job está en curso...
        assert job["answer"] is None
        assert (await c.get("/health")).json()["redis"] is True   # ...y la API sigue respondiendo (event loop libre)

        gate.set()
        job = await wait_status(c, job_id, {"DONE"})
        assert job["answer"] == "RESPUESTA FINAL (fake)"
        assert [s["agent"] for s in job["steps"]] == ["researcher", "analyst"]


async def test_five_concurrent_requests_run_in_parallel(make_client):
    async def slow(state):
        await asyncio.sleep(0.3)
        return {"contributions": [{"agent": "researcher", "step": 1, "content": "DATOS"}]}

    graph = make_graph(nodes={"researcher": slow})
    # (el script del FakeLLM es compartido entre jobs; las guardas del supervisor garantizan que todas completen)
    async with make_client(graph, max_concurrent_jobs=10) as (c, _):
        t0 = time.monotonic()
        ids = [(await c.post("/tasks", json={"question": f"consulta {i}"})).json()["job_id"] for i in range(5)]
        jobs = await asyncio.gather(*[wait_status(c, i, {"DONE", "FAILED"}) for i in ids])
        total = time.monotonic() - t0
    assert len({j["job_id"] for j in jobs}) == 5
    assert total < 5 * 0.3           # en paralelo: bastante menos que 5 corridas en serie (1.5 s)


# ---------- 2. Estado en Redis + manejo de errores ----------

async def test_exception_in_background_marks_job_failed(make_client, redis_client):
    async with make_client(make_graph(boom=True)) as (c, _):
        job_id = (await c.post("/tasks", json={"question": "esto va a fallar"})).json()["job_id"]
        job = await wait_status(c, job_id, {"FAILED", "DONE"})
        assert job["status"] == "FAILED" and "el LLM explotó" in job["error"]
        # y está en REDIS, no en memoria del proceso
        stored = json.loads(await redis_client.get(f"job:{job_id}"))
        assert stored["status"] == "FAILED"


async def test_job_timeout_marks_failed(make_client):
    gate = asyncio.Event()                                    # nunca se libera
    async with make_client(make_graph(gate=gate), job_timeout_seconds=1) as (c, _):
        job_id = (await c.post("/tasks", json={"question": "se cuelga"})).json()["job_id"]
        job = await wait_status(c, job_id, {"FAILED"}, timeout=5)
        assert "Timeout" in job["error"]


async def test_orphan_jobs_are_failed_on_restart(redis_client):
    """Un job que quedó RUNNING porque la API murió no debe dejar al cliente esperando para siempre."""
    from app.store import JobStore
    store = JobStore(redis_client, 3600)
    job = await store.create("huérfano", 8)
    await store.update(job["job_id"], status="RUNNING")
    paused = await store.create("pausado", 8)
    await store.update(paused["job_id"], status="AWAITING_APPROVAL")

    assert await store.fail_orphans() == 1
    assert (await store.get(job["job_id"]))["status"] == "FAILED"
    assert (await store.get(paused["job_id"]))["status"] == "AWAITING_APPROVAL"   # este sí se puede reanudar


async def test_unknown_job_and_validation(make_client):
    async with make_client(make_graph()) as (c, _):
        assert (await c.get("/tasks/nope")).status_code == 404
        assert (await c.post("/tasks", json={"question": "x"})).status_code == 422          # muy corta
        assert (await c.post("/tasks", json={"question": "ok ok", "max_steps": 99})).status_code == 422


# ---------- 3. HITL por HTTP ----------

async def test_hitl_flow_over_http_approve(make_client):
    log = []
    async with make_client(make_graph(CRITICAL, log=log)) as (c, _):
        job_id = (await c.post("/tasks", json={"question": "publicá el precio"})).json()["job_id"]
        job = await wait_status(c, job_id, {"AWAITING_APPROVAL", "DONE", "FAILED"})
        assert job["status"] == "AWAITING_APPROVAL"
        assert job["approval_request"]["level"] == "critical" and "executor" not in log

        r = await c.post(f"/tasks/{job_id}/approve", json={"approved": True, "comment": "dale"})
        assert r.status_code == 202
        job = await wait_status(c, job_id, {"DONE", "FAILED"})
        assert job["status"] == "DONE" and "executor" in log
        assert job["approval"] == {"approved": True, "comment": "dale"}


async def test_hitl_flow_over_http_reject(make_client):
    log = []
    async with make_client(make_graph(CRITICAL, log=log)) as (c, _):
        job_id = (await c.post("/tasks", json={"question": "publicá el precio"})).json()["job_id"]
        await wait_status(c, job_id, {"AWAITING_APPROVAL"})
        await c.post(f"/tasks/{job_id}/approve", json={"approved": False, "comment": "no"})
        job = await wait_status(c, job_id, {"DONE", "FAILED"})
        assert job["status"] == "DONE" and "executor" not in log
        assert any("RECHAZADA" in s["content"] for s in job["steps"])


async def test_approve_only_valid_when_awaiting_and_only_once(make_client):
    async with make_client(make_graph(CRITICAL)) as (c, _):
        job_id = (await c.post("/tasks", json={"question": "publicá el precio"})).json()["job_id"]
        await wait_status(c, job_id, {"AWAITING_APPROVAL"})

        # dos aprobaciones simultáneas: solo una gana
        body = {"approved": True, "comment": ""}
        r1, r2 = await asyncio.gather(c.post(f"/tasks/{job_id}/approve", json=body),
                                      c.post(f"/tasks/{job_id}/approve", json=body))
        assert sorted([r1.status_code, r2.status_code]) == [202, 409]
        await wait_status(c, job_id, {"DONE"})
        assert (await c.post(f"/tasks/{job_id}/approve", json=body)).status_code == 409   # ya terminó
        assert (await c.post("/tasks/nope/approve", json=body)).status_code == 404


async def test_read_only_job_never_needs_approval(make_client):
    async with make_client(make_graph()) as (c, _):
        job_id = (await c.post("/tasks", json={"question": "solo lectura"})).json()["job_id"]
        job = await wait_status(c, job_id, {"DONE", "AWAITING_APPROVAL", "FAILED"})
        assert job["status"] == "DONE"
