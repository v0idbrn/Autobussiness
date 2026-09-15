# BAM — Inicio Rápido

**Productivo en 5 minutos.** (English version: [QUICKSTART.md](QUICKSTART.md))

## Paso 1: Verificar que funciona

```bash
uv run bam doctor
```

Deberías ver:
```
config          : ok
database        : data\bam.db
runtime deps    : httpx, pyyaml (only)
service pdf-to-excel  : ok
service excel-cleaner : ok
```

## Paso 2: Encontrar empresas

```bash
# Desde una búsqueda de noticias (empresas reales desde resultados RSS)
uv run bam discover --source rss --query "accounting firms" --limit 10

# Desde una lista de URLs
uv run bam discover --urls https://acme.test https://beta.test

# Desde un archivo CSV (columnas: name, url, domain, industry)
uv run bam discover --csv companies.csv
```

Los dominios en denylist se filtran automáticamente.

## Paso 3: Investigar un prospecto

```bash
uv run bam research https://example.com
```

La investigación cubre la home más hasta 4 subpáginas (`/about`, `/services`,
`/team`, `/contact` y nav links coincidentes). Cada página queda como
evidencia SHA-256; cada claim registra de qué página vino.

## Paso 4: Revisar, aprobar, contactar

```bash
uv run bam leads                # ver todos los leads
uv run bam next                 # ¿qué hago ahora?

# Aprobar outreach (decisión HUMANA)
uv run bam approve-contact 1 -y

# Generar el mensaje de outreach
uv run bam draft-outreach 1            # imprimirlo
uv run bam draft-outreach 1 --mailto   # abrirlo en tu cliente de email
uv run bam draft-outreach 1 --file     # guardarlo como .eml en data/drafts/

# Vos lo enviás. BAM nunca envía nada.

# Registrar el resultado
uv run bam contact 1 contacted
```

Los follow-ups se crean automáticamente: tras el primer contacto (+3 días),
tras una respuesta (+7), al aprobarse una cotización (+5) y tras una entrega
(+30, para negocio repetido).

```bash
uv run bam followups            # follow-ups pendientes
uv run bam followups --overdue  # solo vencidos
```

## Paso 5: Entregar un servicio

```bash
# PDF a Excel
uv run bam deliver factura.pdf --service pdf-to-excel --client "ACME Corp" --agreed 150

# Limpieza de Excel
uv run bam deliver datos.csv --service excel-cleaner --client "ACME Corp" --agreed 75
```

## Paso 6: Cobrar y registrarlo

```bash
uv run bam pay 1 --amount 150
```

## Paso 7: Ver tus números

```bash
uv run bam digest
```

## Eso es todo

Completaste un ciclo comercial completo: discover → research → approve →
outreach → deliver → cobro. Repetí con el próximo cliente.

## ¿Necesitás ayuda?

```bash
uv run bam doctor      # salud del sistema
uv run bam --help      # todos los comandos
uv run bam backup      # backup auto-verificado; conserva los últimos 5 por
                       # defecto y poda los antiguos (backup.keep en config.yaml)
```

Antes de hacer outreach real, leé [LEGAL.md](LEGAL.md): que BAM encuentre un
contacto no significa que tengas permiso para usarlo.
