#!/usr/bin/env python3
"""
Emisor de fichas Modus Mobile.
- Reconcilia historiales MD + presets del generador.
- Renumera por fecha (MM-AAAA-NNN) conservando el folio legado.
- Calcula SHA-256 real sobre una cadena canonica.
- Con --push: upsert a Supabase con service_role (nunca en el repo).

Uso:
  python3 tools/emit.py            # dry-run: valida y escribe .local/fichas_final.json
  python3 tools/emit.py --push     # sube a Supabase (requiere .env)
"""
import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract import (  # noqa: E402
    OVERRIDES, SRC_DEFAULT, clean_model, clean_name, norm, parse_file,
)

BASE = Path(__file__).resolve().parents[1]
SRC = Path(SRC_DEFAULT)
CERT_HTML = SRC / "landing_web" / "certificado.html"
OUT = BASE / ".local" / "fichas_final.json"
LINKS = BASE / ".local" / "enlaces.txt"
ENV = BASE / ".env"
DEMO_ID = "4b93f0ff-4b27-4b92-b887-461a80049a78"
PUBLIC_URL = "https://tvidalsoluciones-dev.github.io/modus-mobile-apps/"
LINK_VERSION = "v=2"
MESES = {"Ene": "01", "Feb": "02", "Mar": "03", "Abr": "04", "May": "05", "Jun": "06",
         "Jul": "07", "Ago": "08", "Sep": "09", "Oct": "10", "Nov": "11", "Dic": "12"}

OVR = {
    "HISTORIAL_CLINICO_CLIENTE_11_VIVO_V2109": {"ram_fisica": "4 GB"},
    "HISTORIAL_CLINICO_CLIENTE_DORIS_ROSERO_MIGRACION_LG_MOTO": {"ram_fisica": "4 GB"},
    "HISTORIAL_CLINICO_CLIENTE_10_XIAOMI_REDMI_10C": {"ram_fisica": "4 GB"},
    "HISTORIAL_CLINICO_SUJETO_12_MOTO_E20": {"ram_fisica": "2 GB"},
}


def load_presets():
    if not CERT_HTML.exists():
        return {}
    html = CERT_HTML.read_text(encoding="utf-8")
    start = html.index("const presets = {")
    end = html.index("\n    };", start)
    block = html[start:end]
    parts = re.split(r"\n      ([a-z0-9]+): \{", block)
    out = {}
    for i in range(1, len(parts), 2):
        key, body = parts[i], parts[i + 1]

        def g(pattern):
            m = re.search(pattern, body, re.S)
            return m.group(1) if m else ""

        d = re.search(r"date:\s*'(\d{2}) / (\w{3}) / (\d{4})'", body)
        fecha = f"{d.group(3)}-{MESES.get(d.group(2), '01')}-{d.group(1)}" if d else ""
        titles = [x.replace("\\'", "'") for x in re.findall(r"title:\s*'((?:[^'\\]|\\.)*)'", body)]
        descs = [x.replace("\\'", "'") for x in re.findall(r"desc:\s*'((?:[^'\\]|\\.)*)'", body)]
        out[key] = {
            "key": key,
            "ticket": g(r"ticket:\s*'([^']*)'").lstrip("#").strip(),
            "client": clean_name(g(r"client:\s*'([^']*)'")),
            "model": clean_model(g(r"model:\s*'([^']*)'")),
            "fecha": fecha,
            "days": int(g(r"days:\s*'(\d+)") or 15),
            "swap": g(r"swap:\s*(true|false)") == "true",
            "treatments": [{"titulo": t, "detalle": dd}
                           for t, dd in zip(titles, descs)][:6],
        }
    return out


def code_base(t):
    t = (t or "").replace("#", "").strip().upper()
    return re.sub(r"-R\d+$", "", t)


def pick_preset(rec, presets, used):
    if rec.get("folio_legado"):
        for k, p in presets.items():
            if k in used:
                continue
            if code_base(p["ticket"]) == code_base(rec["folio_legado"]):
                return p
    rm = norm(rec["dispositivo_modelo"])
    cands = []
    for k, p in presets.items():
        if k in used:
            continue
        pm = norm(p["model"])
        if rm and pm and (rm in pm or pm in rm):
            cands.append((k, p))
    return cands[0][1] if len(cands) == 1 else None


def reconcile():
    presets = load_presets()
    files = sorted(SRC.glob("HISTORIAL_CLINICO_*.md"))
    records = [parse_file(f) for f in files]
    used, discarded = set(), []

    for rec in records:
        p = pick_preset(rec, presets, used)
        if not p:
            continue
        used.add(p["key"])
        legacy = rec.get("folio_legado") or ""
        if not legacy:
            rec["folio_legado"] = p["ticket"]
        elif code_base(legacy) != code_base(p["ticket"]):
            rec["folio_legado"] = f"{legacy} / {p['ticket']}"
        rec["dias_garantia"] = p["days"]
        rec["ram_virtual_desactivada"] = p["swap"]
        if p["treatments"]:
            rec["intervenciones"] = p["treatments"]
        if p["key"] == "p14":
            rec["fecha_intervencion"] = p["fecha"]
        if not rec.get("fecha_intervencion") and p["fecha"]:
            rec["fecha_intervencion"] = p["fecha"]

    for key, p in presets.items():
        if key in used:
            continue
        if key == "p1":
            discarded.append({"clave": key, "ticket": p["ticket"], "motivo": "sin historial MD (Redmi 9 fantasma)"})
            continue
        records.append({
            "source": f"certificado.html#{key}",
            "folio_legado": p["ticket"],
            "rev": 0,
            "fecha_intervencion": p["fecha"],
            "cliente_nombre": p["client"],
            "dispositivo_modelo": p["model"],
            "ram_fisica": "",
            "ram_virtual_desactivada": p["swap"],
            "perfil_dmac": "",
            "dias_garantia": p["days"],
            "tecnico_responsable": "Tomás Vidal",
            "intervenciones": p["treatments"],
            "estado": "ACTIVA",
            "needs_review": ["fuente_generador", "ram_no_detectada"],
        })
        used.add(key)

    for rec in records:
        rec.update(OVR.get(Path(rec["source"]).stem, {}))

    records.sort(key=lambda r: (r["fecha_intervencion"] or "9999",
                                r.get("folio_legado") or "", r["source"]))
    counters = {}
    for rec in records:
        year = (rec["fecha_intervencion"] or "0000")[:4]
        counters[year] = counters.get(year, 0) + 1
        rec["ticket"] = f"MM-{year}-{counters[year]:03d}"
        rec["sha256"] = seal(rec)
    return records, discarded


def canonical(rec):
    items = "\n".join(
        f"{(i.get('titulo') or '').strip()}||{(i.get('detalle') or '').strip()}"
        for i in rec["intervenciones"])
    return "|".join([
        rec["ticket"],
        str(rec["rev"]),
        rec["fecha_intervencion"] or "",
        (rec["cliente_nombre"] or "").strip(),
        (rec["dispositivo_modelo"] or "").strip(),
        (rec.get("ram_fisica") or "").strip(),
        "1" if rec["ram_virtual_desactivada"] else "0",
        (rec.get("perfil_dmac") or "").strip(),
        str(rec["dias_garantia"]),
        items,
    ])


def seal(rec):
    return hashlib.sha256(canonical(rec).encode("utf-8")).hexdigest()


def load_env():
    env = dict(os.environ)
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def api(url, key, path, method="GET", body=None, prefer=None):
    headers = {"apikey": key}
    if key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {key}"
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if prefer:
        headers["Prefer"] = prefer
    req = urllib.request.Request(f"{url}{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


def key_role(k):
    if k.startswith("sb_secret_"):
        return "service_role"
    if k.startswith("sb_publishable_"):
        return "anon"
    if k.startswith("eyJ"):
        try:
            p = k.split(".")[1]
            p += "=" * (-len(p) % 4)
            return json.loads(base64.urlsafe_b64decode(p)).get("role")
        except Exception:
            return None
    return None


def public_link(ticket, token):
    return f"{PUBLIC_URL}?{LINK_VERSION}&cert={ticket}#t={token}"


def update_links_file(ticket, legacy, link):
    if not LINKS.exists():
        return
    lines = LINKS.read_text(encoding="utf-8").splitlines()
    out, found = [], False
    for line in lines:
        parts = line.split("\t")
        if parts and parts[0] == ticket:
            out.append(f"{ticket}\t{legacy}\t{link}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{ticket}\t{legacy}\t{link}")
    LINKS.write_text("\n".join(out) + "\n", encoding="utf-8")


def admin_env():
    env = load_env()
    url = (env.get("SUPABASE_URL") or "").rstrip("/")
    key = (env.get("SUPABASE_SECRET_KEY") or env.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key or "pega_aqui" in key:
        print("ERROR: configura SUPABASE_URL y SUPABASE_SECRET_KEY en modus-pwa/.env")
        return None, None
    if key_role(key) != "service_role":
        print("ERROR: se requiere la Secret key (service_role).")
        return None, None
    return url, key


def rotate_or_link(ticket, rotate):
    url, key = admin_env()
    if not url:
        return
    ticket = ticket.strip()
    if rotate:
        new_token = secrets.token_hex(32)
        code, data = api(url, key,
                         f"/rest/v1/certificados_atdm?ticket=eq.{urllib.parse.quote(ticket)}",
                         method="PATCH", body={"token": new_token},
                         prefer="return=representation")
        if code not in (200, 204) or not data:
            print("ERROR al rotar:", code, data)
            return
        print(f"Token rotado para {ticket}. El enlace anterior deja de funcionar.")
    else:
        code, data = api(
            url, key,
            f"/rest/v1/certificados_atdm?ticket=eq.{urllib.parse.quote(ticket)}&select=ticket,token,folio_legado")
        if code != 200 or not data:
            print("Ficha no encontrada:", code, data)
            return
    row = data[0] if isinstance(data, list) else data
    link = public_link(row["ticket"], row["token"])
    update_links_file(row["ticket"], row.get("folio_legado") or "", link)
    print(link)


def fetch_existing(url, key):
    code, rows = api(
        url, key,
        "/rest/v1/certificados_atdm?select=ticket,folio_legado,cliente_nombre,dispositivo_modelo")
    if code != 200 or not isinstance(rows, list):
        print("ERROR: no se pudo leer la base para conservar los tickets:", code)
        return None
    return rows


def legacy_codes(value):
    return {code_base(part) for part in re.split(r"\s*/\s*", value or "") if part.strip()}


def reuse_tickets(records, existing):
    used, matched = set(), set()
    for rec in records:
        match = None
        rec_legacy = legacy_codes(rec.get("folio_legado"))
        if rec_legacy:
            for row in existing:
                if row["ticket"] in used:
                    continue
                if rec_legacy & legacy_codes(row.get("folio_legado")):
                    match = row
                    break
        if match is None:
            for row in existing:
                if row["ticket"] in used:
                    continue
                if (norm(rec.get("cliente_nombre") or "") == norm(row.get("cliente_nombre") or "")
                        and norm(rec.get("dispositivo_modelo") or "") == norm(row.get("dispositivo_modelo") or "")):
                    match = row
                    break
        if match is not None:
            rec["ticket"] = match["ticket"]
            used.add(match["ticket"])
            matched.add(id(rec))

    max_by_year = {}
    for row in existing:
        m = re.fullmatch(r"MM-(\d{4})-(\d+)", row["ticket"])
        if m:
            max_by_year[m.group(1)] = max(max_by_year.get(m.group(1), 0), int(m.group(2)))

    pending = [r for r in records if id(r) not in matched]
    pending.sort(key=lambda r: (r.get("fecha_intervencion") or "9999", r.get("folio_legado") or ""))
    for rec in pending:
        year = (rec.get("fecha_intervencion") or "0000")[:4]
        max_by_year[year] = max_by_year.get(year, 0) + 1
        rec["ticket"] = f"MM-{year}-{max_by_year[year]:03d}"
        used.add(rec["ticket"])

    for rec in records:
        rec["sha256"] = seal(rec)

    tickets = [r["ticket"] for r in records]
    if len(tickets) != len(set(tickets)):
        print("ERROR: tickets duplicados tras reutilizar; abortando.")
        return None
    print(f"Tickets conservados: {len(matched)} | Nuevos: {len(pending)}")
    return records


def push(records):
    env = load_env()
    url = (env.get("SUPABASE_URL") or "").rstrip("/")
    key = (env.get("SUPABASE_SECRET_KEY") or env.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key:
        print("ERROR: falta SUPABASE_URL o SUPABASE_SECRET_KEY en modus-pwa/.env")
        return False
    if "pega_aqui" in key or len(key) < 20:
        print("ERROR: modus-pwa/.env todavía tiene el marcador. Pega la Secret key real.")
        return False
    role = key_role(key)
    if role != "service_role":
        print(f"ERROR: la llave cargada no es de escritura (rol={role}).")
        print("       Usa la Secret key (sb_secret_...) o la legacy service_role.")
        return False

    code, data = api(url, key, f"/rest/v1/certificados_atdm?id=eq.{DEMO_ID}&select=ticket")
    if code == 200 and data:
        print(f"Demo a reemplazar: id={DEMO_ID} ticket={data[0].get('ticket')}")
        code, _ = api(url, key, f"/rest/v1/certificados_atdm?id=eq.{DEMO_ID}",
                      method="DELETE", prefer="return=minimal")
        print(f"  borrado demo -> HTTP {code}")
        if code not in (200, 204):
            return False

    payload = []
    for r in records:
        payload.append({
            "ticket": r["ticket"],
            "cliente_nombre": r["cliente_nombre"],
            "dispositivo_modelo": r["dispositivo_modelo"],
            "fecha_intervencion": r["fecha_intervencion"],
            "dias_garantia": r["dias_garantia"],
            "ram_fisica": r.get("ram_fisica") or None,
            "ram_virtual_desactivada": bool(r["ram_virtual_desactivada"]),
            "perfil_dmac": r.get("perfil_dmac") or None,
            "intervenciones": r["intervenciones"],
            "hash_seguridad": r["sha256"][:24],
            "tecnico_responsable": r.get("tecnico_responsable") or "Tomás Vidal",
            "estado": "ACTIVA",
            "rev": int(r.get("rev") or 0),
            "folio_legado": r.get("folio_legado") or None,
            "sha256": r["sha256"],
        })

    for i in range(0, len(payload), 40):
        chunk = payload[i:i + 40]
        code, body = api(url, key, "/rest/v1/certificados_atdm?on_conflict=ticket",
                         method="POST", body=chunk,
                         prefer="resolution=merge-duplicates,return=representation")
        print(f"Upsert {i + 1}-{i + len(chunk)} -> HTTP {code}")
        if code not in (200, 201):
            print("  RESPUESTA:", body)
            return False

    code, rows = api(url, key, "/rest/v1/certificados_atdm?select=ticket,token,folio_legado&order=ticket")
    if code != 200:
        print("No se pudieron leer los tokens:", code, rows)
        return False
    LINKS.parent.mkdir(parents=True, exist_ok=True)
    with LINKS.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(f"{row['ticket']}\t{row.get('folio_legado') or ''}\t"
                     f"{public_link(row['ticket'], row['token'])}\n")
    print(f"\nFilas en base: {len(rows)} | Enlaces: {LINKS}")
    for row in rows[:3]:
        print(f"  {row['ticket']} -> ?cert={row['ticket']}#t={row['token'][:12]}…")
    return True


def check(records):
    env = load_env()
    url = (env.get("SUPABASE_URL") or "").rstrip("/")
    html = (BASE / "index.html").read_text(encoding="utf-8")
    m = re.search(r'SUPABASE_ANON\s*=\s*"([^"]+)"', html)
    if not url or not m:
        print("Sin datos para verificar (URL o anon key).")
        return
    anon = m.group(1)
    code, _ = api(url, anon, "/rest/v1/certificados_atdm?select=ticket&limit=1")
    print(f"anon SELECT directo -> HTTP {code} (esperado 401/403)")
    tokens = {}
    for line in LINKS.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            tokens[parts[0]] = parts[2].split("#t=")[-1]
    ok = 0
    for r in records[:3]:
        tok = tokens.get(r["ticket"])
        code, data = api(url, anon, "/rest/v1/rpc/certificado_por_token",
                         method="POST", body={"p_token": tok})
        if code == 200 and data and data.get("sha256") == r["sha256"]:
            ok += 1
        else:
            print(f"  FALLO {r['ticket']} -> {code} {data}")
    print(f"RPC verificado: {ok}/3 con SHA-256 coincidente")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true", help="sube a Supabase")
    ap.add_argument("--check", action="store_true", help="verifica RPC y bloqueo")
    ap.add_argument("--rotate", metavar="TICKET", help="genera un token nuevo para una ficha")
    ap.add_argument("--link", metavar="TICKET", help="imprime el enlace actual de una ficha")
    args = ap.parse_args()

    if args.rotate:
        rotate_or_link(args.rotate, True)
        return
    if args.link:
        rotate_or_link(args.link, False)
        return

    records, discarded = reconcile()

    if args.push:
        url, key = admin_env()
        if not url:
            sys.exit(1)
        existing = fetch_existing(url, key)
        if existing is None:
            sys.exit(1)
        records = reuse_tickets(records, existing)
        if records is None:
            sys.exit(1)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Fichas: {len(records)} | Descartadas: {len(discarded)}")
    for d in discarded:
        print(f"  descartado {d['ticket']} ({d['motivo']})")
    print("\nTICKET        LEGADO                        REV FECHA       MODELO                          DIAS HASH")
    for r in records:
        print(f"{r['ticket']:<13} {(r.get('folio_legado') or '-'):<29} {r['rev']:<3} "
              f"{r['fecha_intervencion']:<11} {(r['dispositivo_modelo'] or '')[:31]:<31} "
              f"{r['dias_garantia']:<4} {r['sha256'][:16]}")
    print(f"\nSalida local: {OUT}")

    if args.push:
        if push(records):
            print("\nPush completado.")
            if args.check:
                check(records)
        else:
            sys.exit(1)
    elif args.check:
        check(records)


if __name__ == "__main__":
    main()
