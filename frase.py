"""Frase motivacional del día generada con la API de Anthropic.

Se llama UNA vez al arrancar la app y queda en memoria hasta cerrarla.
Para refrescarla hay que reiniciar la app — es a propósito, no spammeamos
la API ni distraemos al usuario con cambios constantes.
"""
from pathlib import Path

from dotenv import load_dotenv

# Reusamos el .env del gestor que ya tiene la ANTHROPIC_API_KEY.
_GESTOR_ENV = Path.home() / "Proyectos" / "gestor-productos" / ".env"
load_dotenv(_GESTOR_ENV)

_frase: str | None = None
_loaded = False

_PROMPT = (
    "Generá una frase motivacional con MUCHA onda (1 a 3 oraciones) sobre "
    "negocios, ventas, emprender o cerrar tratos. Tiene que ser original, "
    "punzante, con actitud, en español rioplatense bien argento (usá 'vos', "
    "'che', 'dale', 'la posta' si queda natural). Metele 5 o 6 emojis "
    "potentes intercalados (💪🚀🔥💰📈🏆⚡️🎯💎). "
    "Respondé SOLO con la frase, sin comillas, sin introducción, sin "
    "explicaciones, sin firmar."
)


def cargar() -> str | None:
    """Llama a la API de Anthropic y cachea la frase. Retorna la frase o None si falla."""
    global _frase, _loaded
    try:
        import anthropic

        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": _PROMPT}],
        )
        # El SDK devuelve una lista de bloques; juntamos el texto.
        parts = [b.text for b in msg.content if getattr(b, "type", None) == "text"]
        text = "".join(parts).strip()
        _frase = text or None
    except Exception:
        _frase = None
    _loaded = True
    return _frase


def get() -> str | None:
    return _frase


def loaded() -> bool:
    return _loaded
