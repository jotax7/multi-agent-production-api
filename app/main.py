"""
main.py — API REST (FastAPI) del orquestador multi-agente.

    POST /tasks                 -> encola la tarea y devuelve {job_id} al instante (202). NO bloquea.
    GET  /tasks/{job_id}        -> estado: PENDING | RUNNING | AWAITING_APPROVAL | DONE | FAILED
    POST /tasks/{job_id}/approve-> decisión humana para un job pausado en el HITL
    GET  /health                -> la API y Redis responden

Levantar:  uvicorn app.main:app --port 8000      (usar UN solo worker de uvicorn, ver README)
"""
import logging
from contextlib import AsyncExitStack, asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from redis.asyncio import Redis

from app.config import Settings
from app.observability import setup_observability
from app.schemas import ApprovalRequest, JobStatus, TaskCreated, TaskRequest
from app.store import JobStore
from app.worker import JobRunner

log = logging.getLogger("api")


def create_app(*, settings: Settings | None = None, redis_client: Redis | None = None, graph=None) -> FastAPI:
    """
    Fábrica de la app. En producción se llama sin argumentos (todo sale del entorno).
    Los tests inyectan `redis_client` y `graph` para correr sin Redis Stack ni LLM.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        load_dotenv()
        cfg = settings or Settings.from_env()
        async with AsyncExitStack() as stack:
            # 1) Observabilidad: una sola vez al arrancar
            backend = setup_observability(cfg) if graph is None else "none"

            # 2) Redis para el estado de los jobs
            redis = redis_client or Redis.from_url(cfg.redis_url, decode_responses=True)
            stack.push_async_callback(redis.aclose)
            await redis.ping()                                    # falla rápido y claro si Redis no está

            # 3) Grafo con checkpointer en Redis (o el grafo inyectado en tests)
            if graph is None:
                from langgraph.checkpoint.redis.aio import AsyncRedisSaver
                from app.graph import build_graph
                try:
                    saver = await stack.enter_async_context(     # crea los índices en Redis la 1ª vez
                        AsyncRedisSaver.from_conn_string(cfg.redis_url, ttl={"default_ttl": cfg.checkpoint_ttl_minutes})
                    )
                except Exception as e:  # noqa: BLE001
                    if "unknown command" in str(e).lower():       # FT.* / JSON.* -> Redis sin módulos
                        raise RuntimeError(
                            "Tu Redis no tiene los módulos RedisJSON/RediSearch que necesita el checkpointer de LangGraph. "
                            "Usá Redis Stack: `docker compose up -d redis` (imagen redis/redis-stack-server)."
                        ) from e
                    raise
                compiled = build_graph(checkpointer=saver)
            else:
                compiled = graph

            store = JobStore(redis, cfg.job_ttl_seconds)
            runner = JobRunner(compiled, store, cfg.max_concurrent_jobs, cfg.job_timeout_seconds)
            stack.push_async_callback(runner.shutdown)

            orphans = await store.fail_orphans()
            if orphans:
                log.warning("%s job(s) huérfanos marcados como FAILED", orphans)

            app.state.store, app.state.runner, app.state.observability = store, runner, backend
            yield

    app = FastAPI(title="Multi-Agent Orchestrator API", version="7.0", lifespan=lifespan)

    @app.post("/tasks", response_model=TaskCreated, status_code=202)
    async def create_task(body: TaskRequest, request: Request):
        """Guarda el job en Redis, lo encola en el worker y responde ya. No espera al LLM."""
        job = await app.state.store.create(body.question, body.max_steps)
        app.state.runner.start(job)
        return TaskCreated(job_id=job["job_id"], status=JobStatus.PENDING,
                           status_url=str(request.url_for("get_task", job_id=job["job_id"])))

    @app.get("/tasks/{job_id}", name="get_task")
    async def get_task(job_id: str):
        job = await app.state.store.get(job_id)
        if job is None:
            raise HTTPException(404, "job_id inexistente (o expirado)")
        return job

    @app.post("/tasks/{job_id}/approve", status_code=202)
    async def approve_task(job_id: str, body: ApprovalRequest):
        """Reanuda un job pausado. Solo válido si está AWAITING_APPROVAL (compare-and-set atómico)."""
        job = await app.state.store.transition(
            job_id, [JobStatus.AWAITING_APPROVAL],
            status=JobStatus.PENDING.value,                      # evita que dos aprobaciones reanuden el mismo job
            approval={"approved": body.approved, "comment": body.comment},
        )
        if job is None:
            current = await app.state.store.get(job_id)
            if current is None:
                raise HTTPException(404, "job_id inexistente (o expirado)")
            raise HTTPException(409, f"El job está en estado {current['status']}; solo se aprueba AWAITING_APPROVAL")
        app.state.runner.resume(job_id, body.approved, body.comment)
        return {"job_id": job_id, "status": JobStatus.PENDING, "approved": body.approved}

    @app.get("/health")
    async def health():
        try:
            await app.state.store.redis.ping()
            redis_ok = True
        except Exception:  # noqa: BLE001
            redis_ok = False
        return {"status": "ok" if redis_ok else "degraded", "redis": redis_ok,
                "observability": app.state.observability}

    return app


app = create_app()
