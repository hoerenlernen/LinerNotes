#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Goran Ristic and contributors
"""Start ohne Shell-Auswertung der Konfigurationsdatei."""
import os
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "app"))
import umgebung
umgebung.laden()
import uvicorn
if __name__ == "__main__":
    uvicorn.run("main:app", host=os.environ.get("LINER_BIND", "127.0.0.1"),
                port=int(os.environ.get("LINER_PORT", "5060")), workers=1,
                proxy_headers=False)
