"""Cron diario: procesa las ventas del día anterior."""
import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))

from mcp_server import procesar_dia

ayer = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
print(procesar_dia(ayer))
