# Modus Mobile PWA

Visor de fichas técnicas, garantía y sello de integridad para clientes de **Modus Mobile** (ATDM).
Cada ficha se abre **solo con su enlace completo**: folio visible + llave privada.

## Cómo funciona

- ``?cert=MM-2026-025`` es el folio visible.
- ``#t=<64 caracteres>`` es la llave privada del enlace. El navegador nunca la envía al servidor; viaja en el cuerpo HTTPS de la consulta.
- La PWA llama a la función `certificado_por_token` de Supabase. La tabla no es legible con la llave pública.
- El sello es un **SHA-256** calculado al emitir y verificado en el teléfono con WebCrypto.
- WhatsApp oficial: **573189308342**.

## Archivos

| Archivo | Función |
|---|---|
| `index.html` | Visor (4 estados: cargando, vacío, error con reintento, ficha) |
| `sw.js` | Service Worker *network-first*. Subir `BUILD` en cada despliegue |
| `manifest.json` + `icon-*.png` | Instalación PWA (iconos `any` y `maskable`) |
| `tools/extract.py` | Lee los historiales `HISTORIAL_CLINICO_*.md` |
| `tools/emit.py` | Reconcilia, sella y sube a Supabase |
| `tools/ping.py` | Ping de actividad (sin leer datos) |
| `.env` | `SUPABASE_URL` + `SUPABASE_SECRET_KEY` (**ignorado por git**) |
| `.local/` | `fichas_final.json` y `enlaces.txt` (**ignorado por git**) |

## Emitir o actualizar fichas

1. Añade o edita el historial en la carpeta superior (`HISTORIAL_CLINICO_*.md`).
2. Prueba sin subir:
   ```bash
   python3 tools/emit.py
   ```
3. Sube y verifica:
   ```bash
   python3 tools/emit.py --push --check
   ```
4. Copia los enlaces nuevos de `.local/enlaces.txt`.

El emisor **conserva los folios ya existentes** (por folio legado o por cliente+modelo) y solo asigna número nuevo a las fichas nuevas. No retrocedas la fecha de fichas ya emitidas: cambiaría la numeración propuesta.

## Rotar un enlace filtrado

```bash
python3 tools/emit.py --link MM-2026-025      # ver enlace actual
python3 tools/emit.py --rotate MM-2026-025    # llave nueva; el enlace viejo deja de funcionar
```

## Publicar cambios del visor

1. Subir `BUILD` en `sw.js` (por ejemplo `2026-11-13-1`).
2. Commit y push. GitHub Pages tarda 1–2 minutos.
3. La app mostrará "Hay una versión nueva disponible".

## Evitar que Supabase se pause

El plan Free pausa el proyecto tras **7 días sin actividad**. Con 2 emisiones semanales normalmente no ocurre, pero un monitor externo lo asegura. **No requiere pagar.**

**Monitor gratuito (recomendado):** crear un trabajo en `cron-job.org`:

- URL: `https://yelenqphgmpsagesrvie.supabase.co/rest/v1/rpc/certificado_por_token`
- Método: `POST`
- Cabeceras:
  - `apikey: <valor de SUPABASE_ANON en index.html>`
  - `Content-Type: application/json`
- Cuerpo:
  ```json
  {"p_token":"0000000000000000000000000000000000000000000000000000000000000000"}
  ```
- Frecuencia: cada 3 días.
- Respuesta esperada: `200` con `null`.

Prueba manual desde el taller:

```bash
python3 tools/ping.py
# Ping Supabase -> HTTP 200 | respuesta: None
```

## Reanudar el proyecto

Dashboard de Supabase → proyecto → **Resume project**.
Los datos siguen intactos y se puede reanudar hasta el **13-Dic-2027**.

## Seguridad

- `SUPABASE_SECRET_KEY` (o la legacy `service_role`) **solo** vive en `.env`, en el equipo del taller. Nunca en el repositorio ni en el chat.
- La llave pública (`anon` / `publishable`) está en `index.html`: es pública por diseño.
- El token del enlace **es la credencial**. Si se filtra, se rota.
- Supabase retira las llaves legacy a comienzos de 2027. Migración: reemplazar `SUPABASE_ANON` por la **publishable key** (`sb_publishable_...`). El código ya omite `Authorization` cuando la llave no empieza por `eyJ`.

## Solución de problemas

| Síntoma | Causa | Acción |
|---|---|---|
| `401` al leer la tabla | Correcto | No cambiar nada |
| "Enlace incompleto" | Falta `#t=` | Pedir el enlace completo |
| Enlace válido no abre | Proyecto pausado | Resume project |
| "Sello no coincide" | Contenido alterado | No usar; reemitir |
| Se ve la versión vieja | Caché del Service Worker | Subir `BUILD` y publicar |
| `401` al emitir | Llave equivocada | Usar la Secret key |

## Recuperación del esquema

Si se recrea el proyecto, ejecutar una vez en el SQL Editor:

```sql
alter table public.certificados_atdm
  add column if not exists rev int not null default 0,
  add column if not exists folio_legado text,
  add column if not exists sha256 text,
  add column if not exists token text not null
    default replace(gen_random_uuid()::text || gen_random_uuid()::text, '-', '');

create unique index if not exists certificados_atdm_token_uidx
  on public.certificados_atdm (token);
create unique index if not exists certificados_atdm_ticket_uidx
  on public.certificados_atdm (ticket);

create or replace function public.certificado_por_token(p_token text)
returns jsonb language sql security definer set search_path = public as $$
  select jsonb_build_object(
    'ticket', c.ticket, 'rev', c.rev, 'folio_legado', c.folio_legado,
    'cliente_nombre', c.cliente_nombre,
    'dispositivo_modelo', c.dispositivo_modelo,
    'fecha_intervencion', c.fecha_intervencion,
    'dias_garantia', c.dias_garantia, 'ram_fisica', c.ram_fisica,
    'ram_virtual_desactivada', c.ram_virtual_desactivada,
    'perfil_dmac', c.perfil_dmac, 'intervenciones', c.intervenciones,
    'estado', c.estado, 'sha256', c.sha256
  )
  from public.certificados_atdm c
  where length(p_token) = 64 and c.token = p_token
  limit 1;
$$;

revoke all on function public.certificado_por_token(text) from public;
grant execute on function public.certificado_por_token(text) to anon;

alter table public.certificados_atdm enable row level security;
alter table public.certificados_atdm force row level security;
revoke all on table public.certificados_atdm from anon, authenticated;
```

## Tecnologías

- Frontend: HTML5, CSS3, JavaScript sin dependencias.
- Despliegue: GitHub Pages.
- Datos: Supabase (PostgreSQL + PostgREST + RLS).
