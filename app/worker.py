"""
worker.py — [PE7] Worker en background: corre el grafo fuera del request HTTP.

Patrón: el endpoint hace `runner.submit(...)` y responde YA con el job_id; el grafo corre en una
`asyncio.Task` del mismo event loop. Como todos los nodos son async nativos, N jobs avanzan
concurrentemente sin bloquearse. (Escalar a un proceso aparte = reemplazar esta clase por una
tarea de Arq/Celery; el resto de la API no cambia.)

Regla de oro (error común de la consigna): CUALQUIER excepción en segundo plano termina en
status=FAILED en Redis. Si no, el cliente haría polling para siempre.
"""
import asyncio
import logging
import time

from langgraph.types import Command

from app.graph import initial_state
from app.schemas import JobStatus
from app.store import JobStore

log = logging.getLogger("worker")


class JobRunner:
    def __init__(self, graph, store: JobStore, max_concurrent: int, timeout_s: int):
        self.graph = graph
        self.store = store
        self.timeout_s = timeout_s
        self._sem = asyncio.Semaphore(max_concurrent)     # tope de jobs simultáneos
        self._tasks: set[asyncio.Task] = set()            # referencias fuertes: si no, el GC puede matar la task

    # ---- API pública ----

    def start(self, job: dict) -> None:
        """Primera ejecución de un job."""
        self._spawn(job["job_id"], initial_state(job["question"], job["max_steps"]))

    def resume(self, job_id: str, approved: bool, comment: str) -> None:
        """Reanuda un job pausado en el HITL con la decisión humana."""
        self._spawn(job_id, Command(resume={"approved": approved, "comment": comment}))

    async def shutdown(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    # ---- internos ----

    def _spawn(self, job_id: str, graph_input) -> None:
        task = asyncio.create_task(self._run(job_id, graph_input), name=f"job-{job_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, job_id: str, graph_input) -> None:
        # thread_id = job_id: el checkpointer guarda/recupera el estado de ESTE job.
        # metadata -> Phoenix/LangSmith agrupan todas las trazas del job en una misma sesión.
        config = {
            "configurable": {"thread_id": job_id},
            "metadata": {"job_id": job_id, "session_id": job_id},
            "run_name": "orchestrator",
        }
        async with self._sem:
            t0 = time.perf_counter()
            try:
                await self.store.update(job_id, status=JobStatus.RUNNING.value, approval_request=None)
                await asyncio.wait_for(self.graph.ainvoke(graph_input, config), timeout=self.timeout_s)
                await self._save_outcome(job_id, config, time.perf_counter() - t0)

            except asyncio.TimeoutError:
                await self._fail(job_id, f"Timeout: el job superó {self.timeout_s}s", time.perf_counter() - t0)
            except asyncio.CancelledError:
                # La API se está apagando: dejamos el job en FAILED (no huérfano) y propagamos la cancelación.
                await asyncio.shield(self._fail(job_id, "Cancelado: la API se está apagando", time.perf_counter() - t0))
                raise
            except Exception as e:  # noqa: BLE001 — queremos atrapar TODO error del agente/LLM/Redis
                log.exception("Job %s falló", job_id)
                await self._fail(job_id, f"{type(e).__name__}: {e}", time.perf_counter() - t0)

    async def _save_outcome(self, job_id: str, config: dict, elapsed: float) -> None:
        """Mira cómo terminó el grafo: ¿pausado en el HITL o terminado?"""
        snap = await self.graph.aget_state(config)
        pending = [i.value for t in snap.tasks for i in t.interrupts]
        values = snap.values
        job = await self.store.get(job_id) or {}
        total = job.get("elapsed_s", 0.0) + elapsed
        steps = [{"agent": c["agent"], "step": c["step"], "content": c["content"][:500]}
                 for c in values.get("contributions", [])]

        if pending:   # el grafo quedó en interrupt() -> esperando a una persona
            await self.store.update(job_id, status=JobStatus.AWAITING_APPROVAL.value,
                                    approval_request=pending[0], steps=steps, elapsed_s=round(total, 3))
        else:
            await self.store.update(job_id, status=JobStatus.DONE.value, answer=values.get("final_answer", ""),
                                    approval=values.get("approval") or None, steps=steps,
                                    approval_request=None, elapsed_s=round(total, 3))

    async def _fail(self, job_id: str, error: str, elapsed: float) -> None:
        try:
            job = await self.store.get(job_id) or {}
            await self.store.update(job_id, status=JobStatus.FAILED.value, error=error,
                                    elapsed_s=round(job.get("elapsed_s", 0.0) + elapsed, 3))
        except Exception:  # noqa: BLE001 — si Redis mismo está caído no hay mucho más que hacer que loguear
            log.exception("No pude marcar FAILED el job %s", job_id)
