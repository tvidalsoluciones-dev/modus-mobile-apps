#!/usr/bin/env python3
"""
Extrae fichas canonicas desde los historiales MD de Modus Mobile.
Solo lee el corpus .md; escribe JSON en una carpeta ignorada por git (.local/).
No escribe en Supabase.
"""
import argparse
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

SRC_DEFAULT = Path(__file__).resolve().parents[2]
OUT_DEFAULT = Path(__file__).resolve().parents[1] / ".local" / "fichas.json"

RE_CODE = re.compile(r"MM-(\d{4})-([A-Za-z]?)(\d{1,3})(?:-(R\d+))?")
RE_DMAC = re.compile(r"#?DMAC-(\d)")
RE_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})")

OVERRIDES = {
    "HISTORIAL_CLINICO_CLIENTE_DORIS_ROSERO_MIGRACION_LG_MOTO": {
        "dispositivo_modelo": "Motorola Moto E6 Plus",
        "nota_modelo": "Migración desde LG K51S",
        "ram_fisica": "4 GB",
    },
    "HISTORIAL_CLINICO_DIRECTOR_XIAOMI_REDMI_NOTE_9S": {"cliente_nombre": "Tomás Vidal"},
    "HISTORIAL_CLINICO_DIRECTOR_XIAOMI_REDMI_NOTE_10S": {"cliente_nombre": "Tomás Vidal"},
    "HISTORIAL_CLINICO_CLIENTE_11_VIVO_V2109": {"ram_fisica": "4 GB"},
    "HISTORIAL_CLINICO_CLIENTE_10_XIAOMI_REDMI_10C": {"ram_fisica": "4 GB"},
    "HISTORIAL_CLINICO_SUJETO_12_MOTO_E20": {"ram_fisica": "2 GB"},
}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


def meta(text: str, labels) -> str:
    alt = "|".join(re.escape(l) for l in labels)
    m = re.search(rf"^\s*(?:[-*]\s*)?\*\*(?:{alt}):\*\*\s*([^\n]+)$", text, re.I | re.M)
    return m.group(1).strip() if m else ""


def clean_name(s: str) -> str:
    s = re.sub(r"^Cliente\s+\d+\s*[—\-:]\s*", "", s, flags=re.I)
    s = re.sub(r"\s*\([^)]*\)\s*", " ", s)
    s = re.split(r"\s+—\s+|\s+-\s+", s)[0]
    return re.sub(r"\s+", " ", s).strip(" .-—")


def clean_model(s: str) -> str:
    s = re.split(r"[\(`]|\s+—\s+", s)[0]
    return re.sub(r"\s+", " ", s).strip(" .-—")


def extract_codes(text):
    out = []
    for y, letter, num, rev in RE_CODE.findall(text):
        out.append({
            "year": y,
            "letter": letter.upper(),
            "num": int(num),
            "rev": int(rev[1:]) if rev else 0,
            "raw": f"MM-{y}-{letter.upper()}{num}" + (f"-{rev}" if rev else ""),
        })
    return out


def extract_intervenciones(text):
    lines = text.splitlines()
    heads = []
    in_section = False
    for i, line in enumerate(lines):
        if line.startswith("## "):
            in_section = bool(re.search(r"INTERVENCI|PROTOCOLO|MIGRACI", line, re.I))
            continue
        if line.startswith("### ") and in_section:
            title = re.sub(r"^###\s+[A-Z]\.\s*", "", line[4:]).strip()
            title = re.sub(r"#\w[\w-]*", "", title).strip(" ()·-—")
            detalle = ""
            for nxt in lines[i + 1:i + 8]:
                n = nxt.strip()
                if not n or n.startswith(("|", "```")):
                    continue
                detalle = re.sub(r"^[\*\-]\s*", "", n)
                detalle = re.sub(r"\*\*|`", "", detalle)
                break
            if title:
                heads.append({"titulo": title[:90], "detalle": detalle[:220]})
    if not heads:
        in_section = False
        for line in lines:
            if line.startswith("## "):
                in_section = bool(re.search(r"INTERVENCI|PROTOCOLO|MIGRACI", line, re.I))
                continue
            if not in_section:
                continue
            m = re.match(r"^\s*[\*\-]\s*\*\*(.+?):\*\*\s*(.+)$", line)
            if m:
                titulo = re.sub(r"[`*]", "", m.group(1)).strip()
                detalle = re.sub(r"[`*]", "", m.group(2)).strip()
                if titulo:
                    heads.append({"titulo": titulo[:90], "detalle": detalle[:220]})
    if not heads:
        in_section = False
        for line in lines:
            if line.startswith("## "):
                in_section = bool(re.search(r"INTERVENCI|PROTOCOLO", line, re.I))
                continue
            if not in_section or not line.strip().startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 4 or cells[0].lower() == "paquete":
                continue
            if all(re.fullmatch(r":?-+:?", c) for c in cells):
                continue
            pkg = re.sub(r"[`]", "", cells[0])
            heads.append({
                "titulo": f"Extirpación: {re.sub(r'[`*]', '', cells[1])}"[:90],
                "detalle": f"{pkg} → {re.sub(r'[`*]', '', cells[2])} → {re.sub(r'[`*]', '', cells[3])}"[:220],
            })
    if not heads:
        for i, line in enumerate(lines):
            if line.startswith("### "):
                title = re.sub(r"^###\s+(?:[0-9]+\.|[A-Z]\.)\s*", "", line[4:]).strip()
                if title and not re.search(r"hallazgo|diagn[oó]stico|telemetr", title, re.I):
                    heads.append({"titulo": title[:90], "detalle": ""})
    return heads[:6]


def fallback_client(stem: str) -> str:
    if "DIRECTOR" in stem.upper():
        return "Tomás Vidal"
    m = re.search(r"(CLIENTE|SUJETO|PACIENTE)_(\d{1,3})", stem, re.I)
    if m:
        serie = {"CLIENTE": "Cliente", "SUJETO": "Sujeto de prueba", "PACIENTE": "Paciente"}[m.group(1).upper()]
        return f"{serie} {int(m.group(2))}"
    return ""


def fallback_model(stem: str, title: str) -> str:
    m = re.search(r"\(\s*([A-Z0-9][A-Za-z0-9 \-\.\+]+?)\s*\)", title)
    if m:
        return clean_model(m.group(1))
    s = re.sub(r"^HISTORIAL_CLINICO_", "", stem, flags=re.I)
    s = re.sub(r"^(CLIENTE|SUJETO|PACIENTE|DIRECTOR)_\d*_?", "", s, flags=re.I)
    return clean_model(s.replace("_", " ").title())


def parse_file(path: Path):
    text = path.read_text(encoding="utf-8", errors="replace")
    title = text.splitlines()[0] if text else ""

    codes = extract_codes(text)
    codes.sort(key=lambda c: c["rev"], reverse=True)
    chosen = codes[0] if codes else None

    fecha_raw = meta(text, ["Fecha", "Fecha de Intervención", "Fecha de Servicio"])
    m_fecha = RE_DATE.search(fecha_raw)
    fecha = m_fecha.group(1) if m_fecha else ""
    fecha_reing = meta(text, ["Fecha de Reingreso"])
    m_re = RE_DATE.search(fecha_reing)
    if chosen and chosen["rev"] > 0 and m_re:
        fecha = m_re.group(1)

    cliente = clean_name(meta(text, [
        "Cliente", "Propietaria", "Propietario",
        "Titular del Dispositivo", "Titular del Terminal",
        "Titular / Director de Operaciones", "Titular",
    ]))
    if not cliente or re.fullmatch(r"Cliente \d+", cliente, re.I):
        cliente = cliente or fallback_client(path.stem)

    modelo = clean_model(meta(text, [
        "Marca y Modelo", "Fabricante y Modelo", "Modelo", "Dispositivo", "Terminal",
    ]))
    if not modelo:
        modelo = fallback_model(path.stem, title)

    ram = ""
    ram_line = meta(text, ["Memoria RAM Física"])
    ram_source = "fisica" if ram_line else ""
    if not ram_line:
        ram_line = meta(text, ["Memoria RAM Total", "Memoria RAM"])
        ram_source = "total" if ram_line else ""
    m_ram = re.search(r"(\d+(?:[.,]\d+)?)\s*GB", ram_line)
    if m_ram:
        ram = m_ram.group(1).replace(",", ".")
        if ram.endswith(".0"):
            ram = ram[:-2]
        ram = f"{ram} GB"

    virtual = bool(re.search(
        r"(anulaci[oó]n|desactivaci[oó]n|deshabilitaci[oó]n|extirpaci[oó]n|supresi[oó]n)"
        r"[^.\n]{0,70}(memoria virtual|zram|swap|ram expansion|memfusion|falso swap|ram virtual)"
        r"|0\s*GB\s*(?:de\s*)?swap", text, re.I))

    perfiles = sorted(set(RE_DMAC.findall(text)))
    perfil = f"DMAC-{perfiles[0]}" if perfiles else ""

    m_days = re.search(
        r"(\d+)\s*d[ií]as(?:\s*calendario)?\s+de\s+(?:soporte|cobertura|acompa[ñn]amiento|garant[ií]a)",
        text, re.I)
    dias_garantia = int(m_days.group(1)) if m_days else 15

    tecnico = clean_name(meta(text, ["Director de Operaciones", "Director Técnico", "Director"])) or "Tomás Vidal"

    folio_legado = chosen["raw"] if chosen else ""

    needs = []
    if not chosen:
        needs.append("sin_codigo_legado")
    if len(perfiles) > 1:
        needs.append("perfil_multiple:" + ",".join(perfiles))
    if not ram:
        needs.append("ram_no_detectada")
    elif ram_source == "total" and re.search(r"zram|swap", ram_line, re.I):
        needs.append("ram_total_incluye_swap")
    if not cliente:
        needs.append("cliente_no_detectado")
    if chosen and chosen["rev"] > 0:
        needs.append(f"revision_R{chosen['rev']}")

    rec = {
        "source": path.name,
        "folio_legado": folio_legado,
        "rev": chosen["rev"] if chosen else 0,
        "fecha_intervencion": fecha,
        "cliente_nombre": cliente,
        "dispositivo_modelo": modelo,
        "ram_fisica": ram,
        "ram_virtual_desactivada": virtual,
        "perfil_dmac": perfil,
        "dias_garantia": dias_garantia,
        "perfiles_detectados": [f"DMAC-{p}" for p in perfiles],
        "tecnico_responsable": tecnico,
        "intervenciones": extract_intervenciones(text),
        "estado": "ACTIVA",
        "needs_review": needs,
        "_chars": len(text),
    }
    rec.update(OVERRIDES.get(path.stem, {}))
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(SRC_DEFAULT))
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    args = ap.parse_args()

    src = Path(args.src)
    out = Path(args.out)
    files = sorted(src.glob("HISTORIAL_CLINICO_*.md"))

    fichas = []
    for f in files:
        try:
            fichas.append(parse_file(f))
        except Exception as e:
            print(f"[ERROR] {f.name}: {e}")

    groups = {}
    for r in fichas:
        if r["folio_legado"]:
            key = "code:" + re.sub(r"-R\d+$", "", r["folio_legado"])
        else:
            key = "nm:" + norm(r["cliente_nombre"]) + "|" + norm(r["dispositivo_modelo"])
        groups.setdefault(key, []).append(r)

    finales, grupos_dup, posibles = [], [], []
    for key, rows in groups.items():
        rows.sort(key=lambda r: (r["rev"], r["_chars"]), reverse=True)
        keep = rows[0]
        if len(rows) > 1:
            grupos_dup.append({
                "clave": key,
                "archivos": [r["source"] for r in rows],
                "conservado": keep["source"],
                "descartados": [r["source"] for r in rows[1:]],
            })
            keep["merged_from"] = [r["source"] for r in rows[1:]]
        finales.append(keep)

    seen = {}
    for r in finales:
        k = (norm(r["cliente_nombre"]), norm(r["dispositivo_modelo"]))
        if all(k):
            seen.setdefault(k, []).append(r["folio_legado"] or r["source"])
    for k, v in seen.items():
        if len(v) > 1:
            posibles.append({"cliente_modelo": k, "folios": v})

    finales.sort(key=lambda r: (r["fecha_intervencion"] or "9999", r["folio_legado"] or r["source"]))
    counter = {}
    for r in finales:
        year = (r["fecha_intervencion"] or "0000")[:4]
        counter[year] = counter.get(year, 0) + 1
        r["folio_propuesto"] = f"MM-{year}-{counter[year]:03d}"
        r.pop("_chars", None)

    payload = {
        "generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "corpus": str(src),
        "total_archivos": len(files),
        "total_fichas": len(finales),
        "fichas": finales,
        "grupos_duplicados": grupos_dup,
        "posibles_duplicados": posibles,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Archivos: {len(files)} | Fichas finales: {len(finales)} | Fusionados: {len(grupos_dup)}")
    print(f"Salida: {out}\n")
    print("LEGADO          REV  PROPUESTO       FECHA       MODELO                              RAM        REVISAR")
    for r in finales:
        flag = ",".join(r["needs_review"]) or "-"
        print(f"{r['folio_legado'] or '(sin codigo)':<15} {r['rev']:<4} {r['folio_propuesto']:<15} "
              f"{r['fecha_intervencion'] or '----------':<11} {r['dispositivo_modelo'][:35]:<35} "
              f"{r['ram_fisica'] or '-':<10} {flag}")
    if grupos_dup:
        print("\nGRUPOS FUSIONADOS:")
        for g in grupos_dup:
            print(f"  {g['clave']}: conservado={g['conservado']} descartados={g['descartados']}")
    if posibles:
        print("\nPOSIBLES DUPLICADOS (mismo cliente+modelo):")
        for p in posibles:
            print(f"  {p['cliente_modelo']} -> {p['folios']}")


if __name__ == "__main__":
    main()
