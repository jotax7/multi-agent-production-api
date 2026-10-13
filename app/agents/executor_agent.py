"""
agents/executor_agent.py — [PE7] Agente Ejecutor: único con EFECTOS SECUNDARIOS.

Las tools son SIMULADAS: en vez de publicar/enviar de verdad, agregan una línea a un
archivo `outbox.jsonl`. Sirven para demostrar el HITL: si el humano no aprueba, el archivo
NO se toca (prueba concreta de que la acción crítica no corrió).

En un caso real, acá irían las llamadas a la API de la tienda / al proveedor de email.
"""
import json
import os
import time
from pathlib import Path

from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

# Dónde se "ejecutan" las acciones simuladas (configurable para tests)
def outbox_path() -> Path:
    return Path(os.getenv("OUTBOX_PATH", "outbox.jsonl"))


def _record(action: str, payload: dict) -> str:
    line = {"ts": time.time(), "action": action, **payload}
    with outbox_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    return f"OK (simulado): {action} {json.dumps(payload, ensure_ascii=False)}"


@tool
def publish_price_update(product: str, channel: str, new_price: float) -> str:
    """Publica un nuevo precio de `product` en `channel` (tienda oficial, marketplace, mayorista).
    EFECTO SECUNDARIO: modifica el precio visible para clientes."""
    return _record("publish_price_update", {"product": product, "channel": channel, "new_price": new_price})


@tool
def send_email(to: str, subject: str, body: str) -> str:
    """Envía un email. EFECTO SECUNDARIO: sale un mensaje real a un tercero."""
    return _record("send_email", {"to": to, "subject": subject, "body": body})


EXECUTOR_TOOLS = [publish_price_update, send_email]

# Nombres de las tools críticas: hitl.py las usa para describir el riesgo en el pedido de aprobación
CRITICAL_TOOL_NAMES = [t.name for t in EXECUTOR_TOOLS]

EXECUTOR_PROMPT = (
    "Sos el AGENTE EJECUTOR. Ejecutás UNA acción ya aprobada por un humano, usando tus herramientas.\n"
    "- Usá exactamente los valores de la orden; no inventes ni cambies cifras.\n"
    "- Hacé solo lo que dice la orden (nada extra) y confirmá qué ejecutaste."
)


def build_executor_agent(llm):
    return create_react_agent(llm, tools=EXECUTOR_TOOLS, prompt=EXECUTOR_PROMPT)
