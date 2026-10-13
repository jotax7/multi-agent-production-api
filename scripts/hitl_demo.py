"""
scripts/hitl_demo.py — Demo del flujo Human-in-the-loop de punta a punta.

    python scripts/hitl_demo.py                 # pide aprobación por consola (s/n)
    python scripts/hitl_demo.py --approve       # aprueba solo
    python scripts/hitl_demo.py --reject        # rechaza solo

1) Envía una tarea que pide PUBLICAR un precio (acción crítica).
2) Espera a que el job quede en AWAITING_APPROVAL y muestra qué se está pidiendo aprobar.
3) Envía tu decisión a POST /tasks/{id}/approve y muestra el resultado final.
Tip: mirá `outbox.jsonl`: la acción solo aparece si aprobaste.
"""
import argparse
import json
import time

import httpx

QUESTION = ("Buscá los precios de Zenbuds, calculá el precio de tienda oficial con el descuento promocional "
            "y PUBLICÁ ese nuevo precio en la tienda oficial.")


def wait(client: httpx.Client, job_id: str, wanted: set[str]) -> dict:
    while True:
        job = client.get(f"/tasks/{job_id}").json()
        if job["status"] in wanted:
            return job
        time.sleep(0.5)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--approve", action="store_true")
    g.add_argument("--reject", action="store_true")
    args = ap.parse_args()

    with httpx.Client(base_url=args.base_url, timeout=30) as c:
        job_id = c.post("/tasks", json={"question": QUESTION}).json()["job_id"]
        print(f"job_id: {job_id}\nEsperando que el sistema llegue a la acción crítica...")
        job = wait(c, job_id, {"AWAITING_APPROVAL", "DONE", "FAILED"})
        if job["status"] != "AWAITING_APPROVAL":
            print(f"El job terminó en {job['status']} sin pedir aprobación:\n{json.dumps(job, indent=2, ensure_ascii=False)}")
            return

        print("\n⏸️  PAUSADO — se pide aprobación humana:")
        print(json.dumps(job["approval_request"], indent=2, ensure_ascii=False))

        if args.approve or args.reject:
            approved = args.approve
        else:
            approved = input("\n¿Aprobás la acción? [s/N] ").strip().lower() == "s"
        r = c.post(f"/tasks/{job_id}/approve", json={"approved": approved, "comment": "demo hitl"})
        r.raise_for_status()

        job = wait(c, job_id, {"DONE", "FAILED"})
        print(f"\n{'✅ aprobada' if approved else '⛔ rechazada'} → estado final: {job['status']}")
        print(job.get("answer") or job.get("error"))


if __name__ == "__main__":
    main()
