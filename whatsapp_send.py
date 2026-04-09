"""Cliente mínimo para mandar mensajes de WhatsApp vía whatsapp-mcp.

El MCP corre como servicio systemd en http://localhost:3100/mcp (StreamableHTTP).
Usamos el SDK oficial de MCP en Python para hacer una sesión efímera por mensaje.

Es sync a propósito — quien lo llama es el que decide si lo corre en thread.
"""
import asyncio

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

_MCP_URL = "http://localhost:3100/mcp"
# Tu número personal — el WHATSAPP_PHONE del unit file de whatsapp-mcp.
PABLO_JID = "5491140461603@s.whatsapp.net"


async def _enviar_async(contacto: str, mensaje: str) -> tuple[bool, str]:
    async with streamablehttp_client(_MCP_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "enviar_mensaje",
                {"contacto": contacto, "mensaje": mensaje},
            )
            if result.isError:
                texto = ""
                for block in result.content:
                    if getattr(block, "type", None) == "text":
                        texto += block.text
                return False, texto or "error desconocido"
            return True, "ok"


def enviar(mensaje: str, contacto: str = PABLO_JID) -> tuple[bool, str]:
    """Manda un mensaje. Retorna (ok, detalle). Bloquea hasta que termine."""
    try:
        return asyncio.run(_enviar_async(contacto, mensaje))
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
