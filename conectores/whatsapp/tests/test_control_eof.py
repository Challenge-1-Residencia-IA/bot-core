import asyncio
import io
from conectores.whatsapp.src import control


def test_monitor_encerra_em_eof(monkeypatch):
    monkeypatch.setattr(control.sys, 'stdin', io.StringIO(''))
    asyncio.run(asyncio.wait_for(control.monitor_pause_input(), timeout=1))
