"""
store.py — [PE7] Estado de los jobs en Redis.

Cada job es un JSON en la key `job:{job_id}` (con TTL). El estado de LangGraph (checkpoints)
vive aparte, lo maneja `AsyncRedisSaver` con thread_id = job_id.

Concurrencia: el worker y el endpoint /approve pueden escribir el mismo job a la vez, así que
toda modificación es read-modify-write con WATCH/MULTI (optimistic locking de Redis):
si alguien tocó la key en el medio, se reintenta. Esto además da `transition()`, una
compare-and-set atómica (ej: solo se puede aprobar un job que esté AWAITING_APPROVAL).
"""
import json
import time
import uuid
from typing import Any, Callable, Iterable

from redis.asyncio import Redis
from redis.exceptions import WatchError

from app.schemas import JobStatus

PREFIX = "job:"


def _key(job_id: str) -> str:
    return f"{PREFIX}{job_id}"


class JobStore:
    def __init__(self, redis: Redis, ttl_seconds: int):
        self.redis = redis
        self.ttl = ttl_seconds

    async def create(self, question: str, max_steps: int) -> dict:
        now = time.time()
        job = {
            "job_id": uuid.uuid4().hex,
            "status": JobStatus.PENDING.value,
            "question": question,
            "max_steps": max_steps,
            "answer": None,
            "error": None,
            "approval_request": None,   # qué se le muestra a quien aprueba (solo en AWAITING_APPROVAL)
            "approval": None,           # decisión tomada
            "steps": [],                # traza de delegación (quién aportó qué)
            "elapsed_s": 0.0,           # tiempo de ejecución acumulado (sin contar la espera de aprobación)
            "created_at": now,
            "updated_at": now,
        }
        await self.redis.set(_key(job["job_id"]), json.dumps(job, ensure_ascii=False), ex=self.ttl)
        return job

    async def get(self, job_id: str) -> dict | None:
        raw = await self.redis.get(_key(job_id))
        return json.loads(raw) if raw else None

    async def _mutate(self, job_id: str, fn: Callable[[dict], dict | None]) -> dict | None:
        """
        Read-modify-write atómico. `fn(job)` modifica el dict y lo devuelve,
        o devuelve None para abortar sin escribir. Devuelve el job nuevo (o None si no existe/abortó).
        """
        key = _key(job_id)
        async with self.redis.pipeline(transaction=True) as pipe:
            while True:
                try:
                    await pipe.watch(key)                       # si la key cambia antes del EXEC -> WatchError
                    raw = await pipe.get(key)
                    if raw is None:
                        await pipe.unwatch()
                        return None
                    job = fn(json.loads(raw))
                    if job is None:
                        await pipe.unwatch()
                        return None
                    job["updated_at"] = time.time()
                    pipe.multi()
                    pipe.set(key, json.dumps(job, ensure_ascii=False), ex=self.ttl)
                    await pipe.execute()
                    return job
                except WatchError:
                    continue

    async def update(self, job_id: str, **fields: Any) -> dict | None:
        def apply(job: dict) -> dict:
            job.update(fields)
            return job
        return await self._mutate(job_id, apply)

    async def transition(self, job_id: str, expected: Iterable[JobStatus], **fields: Any) -> dict | None:
        """Compare-and-set: cambia campos SOLO si el status actual está en `expected`. None si no."""
        allowed = {s.value for s in expected}

        def apply(job: dict) -> dict | None:
            if job["status"] not in allowed:
                return None
            job.update(fields)
            return job
        return await self._mutate(job_id, apply)

    async def fail_orphans(self) -> int:
        """
        Al arrancar: los jobs PENDING/RUNNING quedaron huérfanos (el proceso que los corría murió)
        y el cliente haría polling para siempre -> los pasamos a FAILED.
        Los AWAITING_APPROVAL NO se tocan: su checkpoint está en Redis y se pueden reanudar.
        (Asume UN solo proceso de API; con varios workers habría que usar un lock/heartbeat.)
        """
        count = 0
        async for key in self.redis.scan_iter(match=f"{PREFIX}*"):
            job_id = key[len(PREFIX):]
            res = await self.transition(
                job_id, [JobStatus.PENDING, JobStatus.RUNNING],
                status=JobStatus.FAILED.value, error="La API se reinició mientras el job estaba en curso. Reenviá la tarea.",
            )
            count += 1 if res else 0
        return count
