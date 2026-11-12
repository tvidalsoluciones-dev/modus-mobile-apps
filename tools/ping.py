#!/usr/bin/env python3
"""
Ping minimo a Supabase para evitar la pausa por inactividad.
No lee ni escribe datos: llama a la funcion con un token inexistente.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import emit  # noqa: E402


def main():
    env = emit.load_env()
    url = (env.get("SUPABASE_URL") or "").rstrip("/")
    html = (emit.BASE / "index.html").read_text(encoding="utf-8")
    m = re.search(r'const SUPABASE_ANON = "([^"]+)"', html)
    if not url or not m:
        print("ERROR: falta SUPABASE_URL o la llave publica en index.html")
        return 1
    code, data = emit.api(
        url, m.group(1), "/rest/v1/rpc/certificado_por_token",
        method="POST", body={"p_token": "0" * 64})
    print(f"Ping Supabase -> HTTP {code} | respuesta: {data}")
    return 0 if code == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
