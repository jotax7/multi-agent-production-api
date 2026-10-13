"""
scripts/load_test.py — Prueba de carga: N peticiones CONCURRENTES contra la API (default 5).

    python scripts/load_test.py                       # 5 peticiones a http://localhost:8000
    python scripts/load_test.py --n 5 --base-url http://localhost:8000

Qué hace:
  1. Dispara las N peticiones POST /tasks en el mismo instante (asyncio.gather).
  2. Hace polling de cada job hasta DONE / FAILED.
  3. Imprime la latencia end-to-end de cada una + p50 / p95 / max, y guarda load_test_results.json.

Ojo: esto mide latencia del lado del CLIENTE. La evidencia que pide la consigna (costo por ejecución
y latencia p95) se captura del DASHBOARD de Phoenix/LangSmith con estas mismas 5 corridas
(ver README, sección "Prueba de carga y capturas").
"""
import argparse
import asyncio
import json
import statistics
import time

import httpx

# 5 consultas de SOLO LECTURA (no disparan el HITL), todas resolubles con data/knowledge_base.json
QUESTIONS = [
    "Buscá las reseñas del producto Zenbuds, analizá el sentimiento y decime qué porcentaje es positivo.",
    "Calculá el precio promedio de Zenbuds entre tienda oficial, marketplace y mayorista.",
    "Calculá el precio de tienda oficial de Zenbuds con el descuento promocional aplicado.",
    "Buscá las ventas de Zenbuds por trimestre y calculá el total de unidades vendidas en 2026.",
    "Compará el precio del competidor SoundMax con el de Zenbuds en marketplace: ¿cuál es más barato y por cuánto?",
]


def percentile(values: list[float], p: float) -> float:
    """Percentil con interpolación lineal (igual que numpy.percentile)."""
    s = sorted(values)
    k = (len(s) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


async def run_one(client: httpx.AsyncClient, idx: int, question: str, poll_s: float, timeout_s: float) -> dict:
    t0 = time.perf_counter()
    r = await client.post("/tasks", json={"question": question})
    r.raise_for_status()
    accepted_ms = (time.perf_counter() - t0) * 1000          # cuánto tardó la API en devolver el job_id
    job_id = r.json()["job_id"]

    while True:
        job = (await client.get(f"/tasks/{job_id}")).json()
        if job["status"] in ("DONE", "FAILED", "AWAITING_APPROVAL"):
            break
        if time.perf_counter() - t0 > timeout_s:
            job = {"status": "CLIENT_TIMEOUT"}
            break
        await asyncio.sleep(poll_s)

    return {"n": idx, "job_id": job_id, "status": job["status"], "accepted_ms": round(accepted_ms, 1),
            "latency_s": round(time.perf_counter() - t0, 2), "error": job.get("error")}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--n", type=int, default=5, help="cantidad de peticiones concurrentes")
    ap.add_argument("--poll", type=float, default=0.5, help="segundos entre consultas de estado")
    ap.add_argument("--timeout", type=float, default=300)
    ap.add_argument("--out", default="load_test_results.json")
    args = ap.parse_args()

    questions = [QUESTIONS[i % len(QUESTIONS)] for i in range(args.n)]
    async with httpx.AsyncClient(base_url=args.base_url, timeout=30) as client:
        print(f"→ Lanzando {args.n} peticiones concurrentes a {args.base_url} ...")
        t0 = time.perf_counter()
        results = await asyncio.gather(*[run_one(client, i + 1, q, args.poll, args.timeout)
                                         for i, q in enumerate(questions)])
        wall = time.perf_counter() - t0

    print(f"\n{'#':>2}  {'status':<10} {'POST→job_id':>12} {'latencia e2e':>13}  job_id")
    for r in results:
        print(f"{r['n']:>2}  {r['status']:<10} {r['accepted_ms']:>9.0f} ms {r['latency_s']:>11.2f} s  {r['job_id']}"
              + (f"   ⚠ {r['error']}" if r["error"] else ""))

    lat = [r["latency_s"] for r in results if r["status"] == "DONE"]
    summary = {"requests": args.n, "done": len(lat), "wall_clock_s": round(wall, 2)}
    if lat:
        summary |= {"p50_s": round(statistics.median(lat), 2), "p95_s": round(percentile(lat, 95), 2),
                    "max_s": round(max(lat), 2)}
    print(f"\nResumen (cliente): {json.dumps(summary)}")
    print("→ Ahora abrí el dashboard (Phoenix: http://localhost:6006) y capturá costo por ejecución + latencia p95.")

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "results": results}, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    asyncio.run(main())
