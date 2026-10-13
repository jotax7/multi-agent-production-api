"""
observability.py — [PE7] Trazas de cada ejecución hacia Arize Phoenix o LangSmith.

OBSERVABILITY=phoenix    (default) -> OpenInference + OpenTelemetry hacia el colector de Phoenix
OBSERVABILITY=langsmith            -> LangChain/LangGraph ya trazan solos con las env vars LANGSMITH_*
OBSERVABILITY=none                 -> sin trazas (tests)

Con Phoenix, el instrumentador de LangChain engancha los callbacks de LangChain/LangGraph:
cada nodo del grafo, cada llamada al LLM y cada tool call se convierte en un span, con tokens
de entrada/salida y nombre del modelo. Con eso Phoenix calcula SOLO el costo por ejecución.
Todo se inicializa UNA vez al arrancar la API (no por request).
"""
import logging

from app.config import Settings

log = logging.getLogger("observability")


def setup_observability(settings: Settings) -> str:
    """Devuelve qué backend quedó activo (para loguearlo / mostrarlo en /health)."""
    if settings.observability == "phoenix":
        from openinference.instrumentation.langchain import LangChainInstrumentor
        from phoenix.otel import register

        tracer_provider = register(
            project_name=settings.phoenix_project,
            endpoint=f"{settings.phoenix_endpoint}/v1/traces",   # OTLP/HTTP
            batch=True,                                           # exporta en lotes, sin frenar la API
            auto_instrument=False,                                # instrumentamos explícito (abajo)
        )
        LangChainInstrumentor().instrument(tracer_provider=tracer_provider)
        log.info("Trazas -> Phoenix %s (proyecto %s)", settings.phoenix_endpoint, settings.phoenix_project)
        return "phoenix"

    if settings.observability == "langsmith":
        # No hay nada que inicializar: LangChain lee LANGSMITH_TRACING / LANGSMITH_API_KEY / LANGSMITH_PROJECT
        log.info("Trazas -> LangSmith (vía variables de entorno LANGSMITH_*)")
        return "langsmith"

    return "none"
