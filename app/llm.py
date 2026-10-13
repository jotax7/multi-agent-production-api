"""
llm.py — Fábrica de modelos de chat.

El grafo NO depende de un proveedor: todo se pide a `get_llm()`.
Se cambia de proveedor con la variable de entorno LLM_PROVIDER, sin tocar código:

    LLM_PROVIDER=openai     (default)  -> OPENAI_API_KEY   (gpt-4o-mini: Phoenix conoce su precio y calcula el costo solo)
    LLM_PROVIDER=gemini                -> GOOGLE_API_KEY o GEMINI_API_KEY
    LLM_PROVIDER=groq                  -> GROQ_API_KEY
    LLM_PROVIDER=anthropic             -> ANTHROPIC_API_KEY

Opcional: LLM_MODEL para forzar un modelo puntual.
Opcional: LLM_MAX_RETRIES para reintentos ante 429 / 5xx (default 6).
   - Gemini: parámetro `max_retries` en ChatGoogleGenerativeAI (default upstream 6).
   - Groq:   parámetro `max_retries` en ChatGroq (default upstream 2).
"""
import os

# Modelo por defecto de cada proveedor (editables por LLM_MODEL)
DEFAULT_MODELS = {
    "gemini": "gemini-2.0-flash",
    "groq": "llama-3.3-70b-versatile",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-4-5",
}


def get_llm(temperature: float = 0.0):
    provider = os.getenv("LLM_PROVIDER", "openai").lower()
    model = os.getenv("LLM_MODEL", DEFAULT_MODELS.get(provider, ""))
    max_retries = int(os.getenv("LLM_MAX_RETRIES", "6"))

    # Los imports son perezosos: solo necesitás instalar el paquete del proveedor que uses.
    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        # langchain-google-genai 4.x lee GOOGLE_API_KEY o GEMINI_API_KEY; pasamos explícito por si solo está la segunda.
        api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        return ChatGoogleGenerativeAI(
            model=model, temperature=temperature, google_api_key=api_key, max_retries=max_retries
        )
    if provider == "groq":
        from langchain_groq import ChatGroq
        return ChatGroq(model=model, temperature=temperature, max_retries=max_retries)
    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model, temperature=temperature)
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model, temperature=temperature)

    raise ValueError(f"LLM_PROVIDER desconocido: {provider!r}")
