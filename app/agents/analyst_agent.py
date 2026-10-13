"""
agents/analyst_agent.py — Agente de Análisis / Cómputo.

Herramientas ACOTADAS (cómputo determinístico, sin red):
  - sentiment_analysis: sentimiento por lexicón (positivo/negativo/neutro) sobre una lista de textos
  - calculate: calculadora segura (AST, sin eval)
  - validate_schema: valida que un JSON tenga las claves requeridas
"""
import ast
import json
import operator
import re

from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

# --- Sentimiento por lexicón (simple y explicable; reemplazable por un modelo) ---
POSITIVE = {"excelente", "increíble", "recomiendo", "buena", "buenos", "muy buenos", "encantan",
            "mejor", "genial", "cómodos", "bueno", "great", "good", "love"}
NEGATIVE = {"malos", "pésima", "arrepiento", "terrible", "defectuoso", "tardaron", "desconectan",
            "malo", "bad", "worst", "hate"}


def _score_text(text: str) -> int:
    t = text.lower()
    words = set(re.findall(r"\w+", t))
    pos = len(words & POSITIVE)
    neg = len(words & NEGATIVE)
    return pos - neg


@tool
def sentiment_analysis(texts: list[str]) -> str:
    """Clasifica cada texto como positivo/negativo/neutro y devuelve el resumen
    (conteos y porcentaje de positivos) en JSON."""
    labels = []
    for t in texts:
        s = _score_text(t)
        labels.append("positivo" if s > 0 else "negativo" if s < 0 else "neutro")
    n = len(labels) or 1
    return json.dumps({
        "total": len(labels),
        "positivo": labels.count("positivo"),
        "negativo": labels.count("negativo"),
        "neutro": labels.count("neutro"),
        "pct_positivo": round(100 * labels.count("positivo") / n, 1),
        "detalle": labels,
    }, ensure_ascii=False)


# --- Calculadora segura: solo permite aritmética, nada de eval() ---
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Pow: operator.pow, ast.USub: operator.neg,
        ast.Mod: operator.mod}


def _eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    raise ValueError("Expresión no permitida")


@tool
def calculate(expression: str) -> str:
    """Evalúa una expresión aritmética, ej: '(120 + 105 + 90) / 3 * 0.85'."""
    try:
        return str(round(_eval(ast.parse(expression, mode="eval").body), 4))
    except Exception as e:  # devolvemos el error como texto para que el agente se corrija
        return f"Error: {e}"


@tool
def validate_schema(json_str: str, required_keys: list[str]) -> str:
    """Valida que `json_str` sea un JSON válido y contenga todas las `required_keys`."""
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        return f"JSON inválido: {e}"
    missing = [k for k in required_keys if k not in data]
    return "OK" if not missing else f"Faltan claves: {missing}"


ANALYST_TOOLS = [sentiment_analysis, calculate, validate_schema]

ANALYST_PROMPT = (
    "Sos el AGENTE ANALISTA. Recibís datos ya investigados y los procesás con tus herramientas.\n"
    "- Para sentimiento usá sentiment_analysis; para cuentas usá calculate. NUNCA calcules de memoria.\n"
    "- Devolvé resultados numéricos claros y una interpretación breve.\n"
    "- NO busques información nueva: trabajá solo con los datos que te pasan."
)


def build_analyst_agent(llm):
    return create_react_agent(llm, tools=ANALYST_TOOLS, prompt=ANALYST_PROMPT)
