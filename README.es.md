# BAM — Business Automation Machine

[English](README.md) | Español

**BAM** es un sistema operativo local-first para un negocio tecnológico
unipersonal: encuentra empresas, las investiga con evidencia verificable,
califica oportunidades con scoring determinista y lleva el ciclo comercial
completo desde el borrador de contacto hasta la entrega y el cobro — con **un
humano aprobando cada paso sensible**.

- **Local-first**: leads, evidencia y datos de dinero viven en una base SQLite
  local; sin nube, sin cuentas, sin telemetría.
- **La evidencia manda sobre el score**: cada claim es OBSERVED (capturado
  directamente, con SHA-256), INFERRED o UNKNOWN — y un score nunca puede
  superar su evidencia.
- **Sin outreach automático, nunca**: BAM genera borradores; un humano aprueba,
  envía y registra resultados.

## Qué hace

```text
DISCOVER  →  RESEARCH  →  QUALIFY  →  SALES BRIEF  →  DRAFT OUTREACH
   →  HUMAN APPROVAL  →  CONTACT  →  RESPONSE  →  QUOTE
   →  JOB  →  DELIVERY  →  PAYMENT
```

- **Discovery**: empresas reales desde búsquedas RSS de Google News, listas
  manuales de URLs, archivos CSV o texto libre — filtradas por denylist y con
  límites de tasa.
- **Research**: fetch multi-página protegido (home + about/services/team/
  contact) con defensas SSRF, respeto por robots.txt, límites por dominio y
  presupuestos de bytes/tiempo. Extracción determinista (JSON-LD, meta,
  tecnologías, keywords de enum cerrado), descubrimiento de contactos (emails,
  teléfonos, URLs de LinkedIn, fallback WHOIS), evidencia SHA-256 por página.
- **Qualification**: scoring determinista de 8 dimensiones (pesos en YAML).
  Menos de 2 evidencias observadas ⇒ score limitado a ≤ 40; evidencia vacía ⇒
  DO NOT QUALIFY. OBSERVED gana a INFERRED; el LLM opcional nunca puede
  promover UNKNOWN → OBSERVED.
- **Workflow comercial**: brief, borradores de outreach (pantalla, archivo
  `.eml`, mailto — nunca se envían), clasificación de respuestas, cotizaciones
  y follow-ups automáticos en transiciones con gate humano.
- **Delivery**: adaptadores por subprocesso ejecutan los servicios locales
  existentes y validan reportes machine-readable antes de aceptar una entrega
  (el exit code por sí solo nunca se considera suficiente).

## Qué NO hace

- **No** envía emails, mensajes ni solicitudes de contacto.
- **No** hace scraping agresivo, ni bypassea CAPTCHAs, ni inicia sesión en
  plataformas.
- **No** corre servidores ni sincroniza con la nube.
- **No** inventa datos: sin evidencia, sin observación — sin claim.

## Servicios disponibles

| Servicio | Estado | Qué hace |
|---|---|---|
| `pdf-to-excel` | **disponible** | Extracción local PDF → Excel/CSV con auditoría por archivo y manifiestos SHA-256 |
| `excel-cleaner` | **disponible** | Limpieza local de Excel/CSV con log de cambios, cuarentena y resumen de auditoría por lote |
| `qa-agent` | *deshabilitado (Fase 2)* | Agente de automatización QA — contrato preparado, adaptador pendiente de su CLI estable |

Los servicios **no se importan** — corren como subprocessos en sus propios
entornos contra contratos congelados y versionados
(`docs/service-contracts.md`).

## Modelo de seguridad

- **El contenido web no confiable es dato, nunca instrucciones.** Las páginas
  alimentan extractores deterministas y un resumen LLM opcional validado por
  schema.
- **Defensas SSRF**: allowlist de schemes, rechazo de credenciales en URL,
  rechazo de IPs privadas/loopback/link-local/reservadas, conexiones fijadas
  por IP (anti DNS-rebinding), redirects guardados manualmente, rechazo de
  caracteres de control.
- **Integridad del dinero**: los montos deben ser finitos y no negativos; un
  pago exige monto positivo.
- **Máquina de estados**: 18 estados de lead; las transiciones HUMAN rechazan
  rutas de código genéricas; cada escritura de runtime queda auditada.
- **Los backups se prueban a sí mismos**: cada `bam backup` verifica
  integridad, conteos de filas contra la DB viva y ejecuta un simulacro de
  restauración — un backup no probado se pone en cuarentena, nunca se presenta
  como válido. Retención: se conservan los últimos N backups verificados
  (`backup.keep` en `config.yaml`, 5 por defecto); los más antiguos se podan
  automáticamente tras cada backup verificado.
- **Fallo cerrado**: configs corruptas, backups corruptos y estados inválidos
  detienen la operación en lugar de degradar en silencio.

## Instalación

Requisitos: **Windows 10/11**, Python **3.14+**, [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/v0idbrn/Autobussiness.git
cd Autobussiness
uv sync
uv run bam doctor
```

`bam doctor` verifica config, base de datos, dependencias y preflight de
servicios.

## Inicio rápido

```bash
uv run bam discover --source rss --query "accounting firms" --limit 10

# Campaña de intención comercial: primero directorios curados, luego catálogo
# de queries (por servicio), con filtrado automático de publishers/agregadores:
uv run bam discover --campaign pdf_to_excel excel_cleaning \
    --directory "https://www.cpadirectory.com/" --per-query 5
uv run bam research https://empresa-ejemplo.com
uv run bam leads
uv run bam next
```

El recorrido comercial completo está en [QUICKSTART.es.md](QUICKSTART.es.md).

## Ejemplos de CLI

```bash
bam doctor                                   # chequeo de entorno
bam discover --source rss --query "..."      # encontrar empresas (1 request/query)
bam research <url>                           # investigación con evidencia
bam leads [--state approval_required]        # vista del pipeline
bam next                                     # qué hacer ahora
bam sales-brief <id>                         # brief basado en evidencia
bam draft-outreach <id> [--file|--mailto]    # solo borrador - nunca envía
bam approve-contact <id> -y                  # gate de aprobación HUMANA
bam contact <id> contacted                   # registrar un resultado que enviaste
bam quote <id> --files 10                    # sugerencia de cotización
bam deliver <ruta> --service pdf-to-excel    # ejecutar un servicio real
bam pay <job> --amount 150                   # registrar solo dinero real
bam digest                                   # métricas (sin revenue inventado)
bam backup                                   # backup auto-verificado
bam services                                 # registro + estado de servicios
```

## Configuración

- `config/config.yaml` — límites de fetch, presupuesto LLM (deshabilitado por
  defecto), rutas.
- `config/weights.yaml` — pesos y umbrales de scoring.
- `config/service-registry.yaml` — ids de servicios, versiones de contrato
  congeladas, comandos de preflight.
- `data/denylist.yaml` — dominios/empresas que BAM nunca debe investigar ni
  contactar.
- La variable de entorno `BAM_ROOT` reubica el árbol completo (la usan los
  tests; también es como el EXE resuelve su carpeta).

## Estructura de directorios

```text
bam/            paquete: cli, store, pipeline, fetcher, evidence, extractors,
                scorer, llm, commercial, contacts, discovery, reporting,
                adapters, router, manifest, denylist, approvals, config
config/         configuración YAML (versionada)
data/           datos de runtime (gitignored) + política denylist (versionada)
docs/           contratos de servicios congelados
tests/          suite pytest offline (188 tests, sin red)
backups/        backups auto-verificados (gitignored)
```

## Tests

```bash
uv run pytest tests/ -q      # 188 tests, totalmente offline y deterministas
```

La suite nunca toca la red, datos de producción ni servicios externos.

## Build (EXE Windows)

```bash
uv run pyinstaller bam.spec --noconfirm
```

El bundle es **onedir** en `dist/bam/`: `bam.exe` corre **sin Python
instalado**, mantiene `config/` y `data/` junto al ejecutable (`BAM_ROOT` los
reubica) y muestra errores útiles en lugar de crashear en silencio. Los
artefactos de build son reproducibles vía el `bam.spec` versionado; detalles en
`docs/BUILDING.md`. Nota: algunos antivirus marcan bundles PyInstaller sin
firma — verificá el hash antes de confiar en un binario.

## Modelo de adaptadores de servicios

Cada entrega ejecuta el protocolo de 9 pasos: sandbox → copia de input →
subprocesso (argv fijo, sin shell) → captura stdout/stderr → verificación de
exit code → validación de reporte machine-readable → hashing de artefactos →
run manifest → resultado normalizado. La trampa de Excel Cleaner (exit 0 con
archivos en cuarentena) y los reportes por archivo de PDF→Excel se manejan
explícitamente. Las versiones de contrato están congeladas: un mismatch con el
registro rechaza la ejecución.

## Workflow comercial

Ver [QUICKSTART.es.md](QUICKSTART.es.md). Resumen: la investigación produce
evidencia y un score; calificar deja el lead en `approval_required`;
`bam approve-contact` registra la decisión humana; los borradores se entregan
como archivos que enviás vos; resultados, cotizaciones, entregas y pagos se
registran a medida que ocurren de verdad — `bam digest` reporta revenue solo
de pagos registrados.

## Privacidad

Todos los datos quedan en tu máquina. Los extractos de evidencia se
**redactan** (emails, teléfonos, tokens de API) antes de almacenarse; los
hashes y URLs identifican las fuentes. Nada sale de la máquina salvo los
requests HTTP que vos mismo disparás.

## Uso responsable

BAM automatiza *investigación*, no persuasión. Vos sos responsable de cumplir
las leyes y términos aplicables a tu outreach — ver [LEGAL.md](LEGAL.md).
Que algo aparezca en discovery no implica permiso para contactar; datos de
contacto públicos no son consentimiento. Sin mensajería masiva.

## Limitaciones

- robots.txt que no se puede obtener falla abierto (los límites duros siguen
  aplicando).
- La evidencia guarda un extracto redactado + hash, no el HTML completo;
  reverificar implica re-fetch.
- El LLM opcional está deshabilitado por defecto; los claims INFERRED quedan
  inactivos.
- La calidad del discovery depende de la disponibilidad del RSS de Google
  News.
- La extracción WHOIS depende del layout HTML de whois.com (falla en silencio
  si cambia).
- El adaptador del QA Agent espera su CLI de Fase 2.

## Roadmap

- Adaptador del QA Agent (Fase 2)
- lotes de discovery multi-query acotados
- exportación CSV para handoff contable
- generación de propuestas desde perfiles aprobados

## Licencia

MIT — ver [LICENSE](LICENSE). Los componentes de terceros están listados en
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). MIT cubre el código de este
repositorio, no los servicios de terceros, datos externos ni outputs
generados.

## Autor

Mantenido por **v0idbrn**. Construido con una filosofía
evidencia-primero, humano-decide: la máquina prepara, el humano decide.
