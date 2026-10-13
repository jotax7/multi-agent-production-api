"""
agents/research_agent.py — Agente de Investigación.

Herramientas ACOTADAS (solo lectura de fuentes):
  - search_knowledge_base: búsqueda simulada sobre data/knowledge_base.json (offline, siempre funciona)
  - web_search: Tavily real, solo se agrega si existe TAVILY_API_KEY
"""
import json
import os
import re
from pathlib import Path

from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

KB_PATH = Path(__file__).resolve().parents[2] / "data" / "knowledge_base.json"  # <repo>/data/


def _tokenize(text: str) -> set[str]:
    """Pasa a minúsculas y se queda con palabras de 3+ letras (para el score por keywords)."""
    return {w for w in re.findall(r"\w+", text.lower()) if len(w) >= 3}


@tool
def search_knowledge_base(query: str, top_k: int = 5) -> str:
    """Busca en la base de conocimiento interna (reseñas, precios, ventas).
    Devuelve los `top_k` documentos más relevantes en JSON."""
    docs = json.loads(KB_PATH.read_text(encoding="utf-8"))
    q = _tokenize(query)
    # Score = cuántas palabras de la query aparecen en topic + texto
    scored = []
    for d in docs:
        score = len(q & _tokenize(d["topic"] + " " + d["text"]))
        if score > 0:
            scored.append((score, d))
    scored.sort(key=lambda x: x[0], reverse=True)
    results = [d for _, d in scored[:top_k]]
    return json.dumps(results, ensure_ascii=False) if results else "Sin resultados."


def get_research_tools() -> list:
    tools = [search_knowledge_base]
    # Búsqueda web real solo si hay key (así el repo corre sin ella)
    if os.getenv("TAVILY_API_KEY"):
        from langchain_community.tools.tavily_search import TavilySearchResults
        tools.append(TavilySearchResults(max_results=3, name="web_search"))
    return tools


RESEARCH_PROMPT = (
    "Sos el AGENTE DE INVESTIGACIÓN. Tu único trabajo es encontrar datos usando tus herramientas.\n"
    "- Usá las herramientas SIEMPRE; no inventes datos.\n"
    "- Devolvé los datos CRUDOS y relevantes (textos de reseñas, precios, cifras), citando el id del documento.\n"
    "- NO analices ni calcules: eso lo hace el Analista."
)


def build_research_agent(llm):
    """Agente ReAct con prompt y tools propias."""
    return create_react_agent(llm, tools=get_research_tools(), prompt=RESEARCH_PROMPT)
