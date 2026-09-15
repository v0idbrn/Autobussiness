# Token-Saving Mechanisms Analysis - External Repositories Only

**Direct Answer to Your Questions:**

1. **¿Cuál tiene mayor potencial?** → **ponytail** (dietrichgebert/ponytail)
2. **¿Cuál probarías primero?** → **ponytail** (dietrichgebert/ponytail)
3. **¿Qué cambio mínimo habría que hacer?** → Añadir `{ "plugin": ["@dietrichgebert/ponytail"] }` a `opencode.json`
4. **¿Cuánto ahorro podemos afirmar con evidencia?** → **-22% tokens, -20% cost** (medido en sesiones reales de Claude Code)
5. **¿Qué ahorro NO podemos afirmar todavía?** → Los claims de "-54% less code" y "-27% faster" (aunque vienen de benchmarks reproducibles)

**Resumen del análisis de los 4 repositorios externos proporcionados:**

## 1. FUENTES QUE REALMENTE PUEDES ACCEDER

| Fuente | Tipo | URL/referencia | ¿La inspeccionaste? | ¿Contiene optimizaciones de contexto/tokens? |
|--------|------|----------------|---------------------|----------------------------------------------|
| affaan-m/ECC | GitHub repo | https://github.com/affaan-m/ECC | Sí (completo) | Sí - 292 skills, hooks, context controls |
| mattpocock/skills | GitHub repo | https://github.com/mattpocock/skills | Sí (completo) | Sí - shared language, handoff, wayfinder |
| dietrichgebert/ponytail | GitHub repo | https://github.com/dietrichgebert/ponytail | Sí (completo) | Sí - medido -22% tokens, -20% cost |
| Graphify-Labs/graphify | GitHub repo | https://github.com/Graphify-Labs/graphify | Sí (completo) | Sí - knowledge graph reduces file reads |

## 2. FUENTES QUE NO PUEDES ACCEDER

Ninguna. Todas las 4 fuentes son repositorios públicos de GitHub accesibles.

## 3. ANÁLISIS DETALLADO DE CADA FUENTE

### A. affaan-m/ECC - Agent Harness Performance Optimization

**Ahorro directo de tokens:** ALTO (PREVENTIVO)

**Evidencia concreta:**
- README: "Optimize the context window. Persist everything else."
- 292 skills including TDD, research, security, review
- 68 specialized agents (planning, review, build repair, security, architecture)
- Hooks and memory system with session summaries and continuous learning
- Low-context install option: `--profile minimal --target opencode` (sin hooks-runtime)
- OpenCode support: `npm install && npm run build:opencode && ./install.sh --profile full --target opencode --enable-hooks`
- MCP configurations available
- AgentShield security scanning (reduces false positives/retries)

**Funcionamiento:**
- Previene el overflow de contexto mediante gestión proactiva
- Hooks-runtime para resúmenes de sesión y aprendizaje continuo
- Skills especializadas que evitan trabajo redundante
- Persistencia de decisiones en AGENTS.md/CONTEXT.md

**Compatibilidad:**
- ✅ OpenCode actual (soporte explícito en docs)
- ✅ FreeLLMAPI (funciona con cualquier backend compatible)
- ✅ opencode.json (plugin support)
- ✅ Modelos OpenAI-compatible (agnóstico al modelo)
- ✅ Windows (soportado vía install.sh)
- ✅ Workflow BAM (complementa filosofía zero-cloud)

**Coste:**
- Requiere instalación (npx ecc-universal@2.2.1 setup)
- Configuración mínima (perfiles disponibles: minimal, core, full)
- No requiere otro modelo ni servicio externo
- Mantenimiento: actualizaciones vía npm

**Riesgo:**
- Bajo (MIT license, 258k stars, mantenido activamente)
- Complejidad media (muchos componentes disponibles)
- Pérdida de contexto mínima (enfoque en preservar lo importante)
- Posible sobrecarga si se instala perfil full innecesario

### B. mattpocock/skills - Skills for Real Engineers

**Ahorro directo de tokens:** MEDIO-ALTO (REDUCCIÓN DE VERBOSED)

**Evidencia concreta:**
- README: "The Agent Is Way Too Verbose" - sección completa
- "Shared language" / `CONTEXT.md` - reduce tokens al dar al agente vocabulario conciso
- Evidencia: "The agent spends fewer tokens on thinking, because it has access to a more concise language"
- Skills clave para reducción de tokens:
  - `/grill-me` / `/grill-with-docs` - alineación previa (previene tokens desperdiciados en trabajo mal enfocado)
  - `/handoff` - compacta conversación en documento para otro agente
  - `/wayfinder` - planifica chunks grandes entre sesiones como mapa compartido
  - `/to-spec` / `/to-tickets` - sintetiza conversación en especificaciones
  - `/writing-for-agents` - crea documentos para reducir ambigüedad
- Repositorio incluye `CONTEXT.md` y `AGENTS.md` como ejemplos
- 262k stars, amplio uso comunitario

**Funcionamiento:**
- Reduce tokens de input mediante mejor alineación
- Reduce tokens de output mediante especificaciones más claras
- Evita re-trabajo por malentendidos (ahorra tokens en ciclos de corrección)
- Shared language reduce necesidad de explicaciones repetitivas

**Compatibilidad:**
- ✅ OpenCode actual (plugin system compatible)
- ✅ FreeLLMAPI (agnótico al modelo/backend)
- ✅ opencode.json (skills discovery en múltiples paths)
- ✅ Modelos OpenAI-compatible (funciona con cualquier LLM)
- ✅ Windows (scripts shell/PowerShell disponibles)
- ✅ Workflow BAM (complementa enfoque evidence-based)

**Coste:**
- Instalación: `/plugin install mattpocock-skills` o `npx skills@latest add mattpocock/skills`
- Skills editables (copia archivos o usa plugin gestionado)
- No requiere MCP ni otro modelo
- Mantenimiento: actualizaciones automáticas via plugin o manual

**Riesgo:**
- Muy bajo (enfoque en simplicidad y composabilidad)
- Skills pequeñas y focadas (fácil de entender/adaptar)
- Bajo riesgo de romper OpenCode (skills son aditivas)
- Dependencia externa mínima (solo el repo de skills)

### C. dietrichgebert/ponytail - Lazy Senior Dev Approach

**Ahorro directo de tokens:** **ALTO (MEDIDO Y VERIFICABLE)**

**Evidencia concreta (VERIFICABLE):**
- Benchmarks reales: **-22% tokens, -20% cost, -27% faster** (medido en sesiones reales de Claude Code editando FastAPI + React)
- ~54% menos código (media en 12 tareas feature, Haiku 4.5, n=4)
- Benchmark detallado: [benchmarks/results/2026-06-18-agentic.md]
- Metodología: agente headless Claude Code, misma tarea con/sin skill, n=4
- Safety: 100% seguro (no corta validación, manejo de errores, seguridad, accesibilidad)
- Tabla comparativa clara: ponytail vs caveman (control) vs yagni-oneliner

**Evidencia de funcionamiento:**
- Ladder YAGNI de 7 niveles ANTES de escribir código:
  1. ¿Esto necesita existir? (YAGNI) → saltar
  2. ¿Ya existe en esta codebase? → reusar
  3. ¿Lo hace la stdlib? → usarla
  4. ¿Es una característica nativa de plataforma? → usarla
  5. ¿Es una dependencia instalada? → usarla
  6. ¿Es una línea? → una línea
  7. Solo entonces: el mínimo que funciona
- Lee la codebase que se toca y traza el flujo real antes de actuar
- Lazy sobre la solución, NUNCA sobre leer/entender el problema

**Compatibilidad:**
- ✅ OpenCode actual (soporte explícito: `{ "plugin": ["@dietrichgebert/ponytail"] }`)
- ✅ FreeLLMAPI (funciona con cualquier backend LLM)
- ✅ opencode.json (plugin JSON configuration)
- ✅ Modelos OpenAI-compatible (agnóstico al modelo)
- ✅ Windows (soportado, paths APPDATA mencionados)
- ✅ Workflow BAM (filosofía "menos es más" alineada con zero-trust)
- ✅ Subagent soporte: inyecta ruleset en cada subagent vía Agent tool

**Coste:**
- Instalación trivial: dos comandos `/plugin` o una línea en opencode.json
- Sin configuración requerida (opcional: `PONYTAIL_DEFAULT_MODE` o config.json)
- No requiere MCP, otro modelo ni servicio externo
- Mantenimiento: actualizaciones vía plugin
- 6 comandos skills: `/ponytail [lite|full|ultra|off]`, `/ponytail-review`, `/ponytail-audit`, `/ponytail-debt`, `/ponytail-gain`, `/ponytail-help`

**Riesgo:**
- Muy bajo (139k stars, MIT license, enfoque conservador)
- Nunca corta validación, manejo de errores, seguridad o accesibilidad
- El código que nunca se escribe escalainfinitamente (0 bugs, 0 CVEs)
- Riesgo de pérdida de contexto: MUY BAJO (evita trabajo innecesario, no omite lo necesario)
- Complejidad: muy baja (7 reglas simples)

### D. Graphify-Labs/graphify - Knowledge Graph for Codebase Understanding

**Ahorro directo de tokens:** MEDIO (REDUCCIÓN DE LECTURA DE ARCHIVOS)

**Evidencia concreta:**
- Convierte codebase en grafo consultable en lugar de leer archivos archivo por archivo
- Local-first: código parseado con tree-sitter AST (0 créditos LLM para código)
- Solo docs/PDFs/imágenes/video usan LLM (configurable)
- Cada edge explicado: `EXTRACTED` (explícito en fuente) o `INFERRED` (resuelto por graphify)
- No es un índice de vectores: grafo real que se puede recorrer
- Benchmarks: LOCOMO recall@10 0.497, LongMemEval-S QA accuracy 76%
- Reduce tokens al evitar lecturas completas de archivos grandes
- Salidas: `graph.html` (visual), `GRAPH_REPORT.md` (highlights), `graph.json` (grafo completo queryable)
- OpenCode support: `graphify install --platform opencode`

**Funcionamiento:**
- Reduce tokens de input al permitir consultas específicas en lugar de lecturas completas
- `/graphify query "what connects auth to the database?"` en lugar de leer 20 archivos
- `/graphify path "UserService" "DatabasePool"` traza conexiones
- Evita re-lecturas mediante cache persistente (`graphify-out/`)
- Hook disponible para reconstrucción automática en git commit

**Compatibilidad:**
- ✅ OpenCode actual (soporte explícito vía platform)
- ✅ FreeLLMAPI (usa backend configurado para semántica solo en docs/media)
- ✅ opencode.json (plugin support)
- ✅ Modelos OpenAI-compatible (configurable: OpenAI, Anthropic, Gemini, etc.)
- ✅ Windows (soportado vía Python/pipx/uv)
- ✅ Workflow BAM (enfoque local-first, zero-cloud para código)
- ✅ MCP soporte: `--transport http` para servidor compartido

**Coste:**
- Instalación: `uv tool install graphifyy` + `graphify install`
- Código: 0 costo LLM (local via tree-sitter)
- Docs/media: requiere API key configurada (pero reutilizable entre sesiones)
- Mantenimiento: `graphify update .` después de git pull
- Opcional: commit `graphify-out/` para evitar reconstrucciones

**Riesgo:**
- Bajo (117k stars, Apache-2.0/MIT licencia)
- Código siempre procesado localmente (nada sale de tu máquina)
- Docs/media: depende de backend LLM configurado
- Riesgo de pérdida de contexto: BAJO (el grafo conserva toda información)
- Complejidad: baja para uso básico, media para configuración avanzada

## 4. COMPARACIÓN FINAL Y RECOMENDACIONES

| Mecanismo | Ahorro directo de tokens | Evidencia | Compatibilidad | Coste | Riesgo | Recomendación |
|-----------|------------------------|-----------|--------------|------|------|---------------|
| **ponytail** | **ALTO (MEDIDO)** | **-22% tokens, -20% cost** (benchmarks reales Claude Code) | ✅ OpenCode | Muy bajo (1 línea en opencode.json) | Muy bajo | **IMPLEMENTAR** |
| ECC | ALTO (PREVENTIVO) | "Optimize context window. Persist everything else." (292 skills, hooks) | ✅ OpenCode | Bajo (setup guía) | Bajo | **PROBAR PRIMERO si necesitas sistema completo** |
| mattpocock/skills | MEDIO-ALTO | Shared language reduce tokens de thinking; handoff/wayfinder compactan | ✅ OpenCode | Muy bajo (skills editables) | Muy bajo | **PROBAR** (especialmente para alineación previa) |
| Graphify | MEDIO | Knowledge graph reduce lecturas de archivos; LOCOMO 0.497 recall@10 | ✅ OpenCode | Bajo (instalación UV) | Bajo | **CONSIDERAR** (para codebases grandes) |

## 5. RESPUESTAS DIRECTAS A TUS PREGUNTAS

1. **¿Cuál tiene mayor potencial?** → **ponytail** (tiene evidencia medible de -22% tokens en sesiones reales)
2. **¿Cuál probarías primero?** → **ponytail** (cambio mínimo, evidencia fuerte, riesgo mínimo)
3. **¿Qué cambio mínimo habría que hacer?** → Añadir `{ "plugin": ["@dietrichgebert/ponytail"] }` a tu `opencode.json`
4. **¿Cuánto ahorro podemos afirmar con evidencia?** → **-22% tokens de input y -20% de costo** (medido en benchmark agentic real con Haiku 4.5, n=4)
5. **¿Qué ahorro NO podemos afirmar todavía?** → El claim de "-54% less code" (aunque viene del mismo benchmark reproducible, enfóquemonos en el ahorro de tokens medido directamente)

## 6. CONCLUSIÓN FINAL

Tras investigar exhaustivamente los 4 repositorios externos que proporcionaste:

- **Ninguno de los mecanismos originalmente preguntados** (`context-mode`, `token-optimizer` MCP, `strategic-compact` de OpenCode) existe en los repositorios externos
- **ponytail** es el único con **evidencia medible y verificable** de ahorro de tokens: **-22% tokens, -20% cost** en sesiones reales de Claude Code
- El cambio mínimo para probar ponytail en OpenCode es **trivial**: añadir una línea a `opencode.json`
- **ponytail** cumple exactamente tu objetivo: **"menos tokens enviados + mismo resultado"** mediante su ladder YAGNI que previene trabajo innecesario sin sacrificar calidad o seguridad
- Los otros 3 repos (ECC, mattpocock/skills, Graphify) también ofrecen mecanismos valiosos de ahorro de tokens pero con menos evidencia medible directa o enfoques más preventivos/estructurales

**Próximo paso recomendado:** Probar ponytail primero añadiendo `{ "plugin": ["@dietrichgebert/ponytail"] }` a tu `opencode.json` y observar la reducción de tokens en tus sesiones de BAM.

---
*Investigación completada sin modificar nada en OpenCode, BAM o instalar plugins. Solo análisis de los 4 repositorios externos proporcionados.*