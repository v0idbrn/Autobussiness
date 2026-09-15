## Token-Saving Mechanisms Analysis for OpenCode/FreeLLMAPI

**Respuesta directa a tus preguntas:**

1. **¿Cuál tiene mayor potencial?** → strategic-compact
2. **¿Cuál probarías primero?** → strategic-compact
3. **¿Qué cambio mínimo habría que hacer?** → Adaptar el script suggest-compact.js desde Excel Cleaner a OpenCode
4. **¿Cuánto ahorro podemos afirmar con evidencia?** → Ningún ahorro afirmable todavía (los claims necesitan verificación directa)
5. **¿Qué ahorro NO podemos afirmar todavía?** → Los claims de "315KB → 5.4KB" y "95%+ token reduction"

**Resumen**: Basado en la investigación, ninguno de los tres mecanismos de ahorro de tokens está disponible en la configuración actual de OpenCode.

| Mechanism | Ahorro directo de tokens | Evidencia | Compatibilidad | Coste | Riesgo | Recomendación |
|-----------|------------------------|-----------|--------------|------|------|---------------|
| context-mode | NO VERIFICADO | Referencia solo en docs de Excel Cleaner, no existe en OpenCode | No compatible | No instalado | N/A | NO VALE LA PENA |
| token-optimizer MCP | NO VERIFICADO | Referencia solo en Excel Cleaner, sin implementación en OpenCode | No compatible | No instalado | N/A | NO VALE LA PENA |
| strategic-compact (lazy-loading) | PROBAR PRIMERO | Implementado solo en Excel Cleaner `.agents/skills/strategic-compact/`; no está en OpenCode | No compatible | Ya existe en Excel Cleaner | Bajo | PROBAR PRIMERO |

## Findings Details

### 1. context-mode
- **EXPLORACIÓN**: Solo encontrado en comentarios del README de Excel Cleaner
- **IMPLEMENTACIÓN**: No existe en OpenCode actual
- **COMPATIBILIDAD**: No disponible en opencode.json actual o en FreeLLMAPI
- **IMPACTO**: No puede ser utilizado actualmente

### 2. token-optimizer MCP
- **EXPLORACIÓN**: Solo encontrado en Excel Cleaner `.agents/skills/strategic-compact/SKILL.md` como claim de `"95%+ token reduction via content deduplication"`
- **IMPLEMENTACIÓN**: No existe en OpenCode actual; no hay MCP disponibles en la configuración actual
- **COMPATIBILIDAD**: No existe en OpenCode actual; el MCP requiere instalación
- **IMPACTO**: No puede ser utilizado actualmente

### 3. strategic-compact (lazy-loading)
- **IMPLEMENTACIÓN**: `F:/Gigs/Excel Cleaner/.agents/skills/strategic-compact/SKILL.md` (disponible)
- **FUNCIONAMIENTO**: Mecanismo de "compaction suggestion" que sugiere `/compact` a los agentes usando un script `suggest-compact.js` que combina:
  1. Señal de tamaño de contexto (context-size)
  2. Señal de contador de llamadas a herramientas (tool-call count)
- **EVIDENCIA**: Claims de `"context virtualization (315KB to 5.4KB demonstrated)"` y `"95%+ token reduction"` sin verificación directa
- **COMPATIBILIDAD**: Ya implementado en Excel Cleaner; requeriría adaptación para OpenCode
- **COSTE**: Ya implementado (requiriría adaptación de código)

## Prioridad y Recomendaciones

### 1. Probar strategic-compact primero
- **Razón**: Ya tiene implementación completa en Excel Cleaner
- **Próximo paso**: Adaptar el script `suggest-compact.js` para OpenCode
- **Implementación mínima**: Migrar el mecanismo de alerta de contexto desde Excel Cleaner a OpenCode
- **Evidencia posible**: Verificar las reducciónes de tokens claimadas mediante benchmarks

### 2. No hay implementación directa de context-mode o token-optimizer MCP actualmente disponibles
- **Próximo paso**: Investigar OpenCode de terceros MCPs que puedan proporcionar estas funcionalidades, o considerar implementarlas desde cero si son necesarias
- **Aviso**: Estas funcionalidades requieren inversión de desarrollo significativa

## Compromisos y Limitaciones

### strategic-compact (Excel Cleaner)
- **RECOMPENSA**: Previene el overflow de contexto
- **RIESGO**: "Contexto dejado atrás" al compactar - la funcionalidad actual no explícitamente documentada
- **COMPATIBILIDAD**: Implementado solo en Excel Cleaner, no reutilizado en OpenCode
- **COMPLEJIDAD**: Requiere comprensión del `suggest-compact.js` y su configuración

### NO VERIFICADO Claims
- Claims de `"315KB → 5.4KB"` y `"95%+ token reduction"` necesitan verificación directa
- No hay evidencia disponible en OpenCode actual para estas especificaciones de rendimiento

## Conclusión

Actualmente, el único mecanismo de reducción de tokens disponible es la implementación de `strategic-compact` en Excel Cleaner.

Para OpenCode/FreeLLMAPI:

1. **NO HAY**
   - `context-mode`
   - `token-optimizer` MCP

2. **SÍ HAY**
   - `strategic-compact` (en Excel Cleaner, no en OpenCode)

**Próximo paso**: Adaptar el mecanismo de compaction de Excel Cleaner para OpenCode si los ahorros de tokens claimados son necesarios.

**Ahorro de tokens NO AFIRMABLE** hasta que la implementación sea verificada contra benchmarks reales en OpenCode.
