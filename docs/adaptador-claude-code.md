# Adaptador de Claude Code (v1)

`proof-harness run adapt claude-code <transcript.jsonl> --declaration decl.json
--out DIR [--code-root DIR] [--ingest]`

Convierte el transcript JSONL que Claude Code ya escribe
(`~/.claude/projects/<slug>/<sessionId>.jsonl`) en los artefactos canónicos
(envelope + features + outcome), sin tocar la sesión ni el harness: solo
lectura, post-hoc.

## Reglas del mapeo (normativas)

- **Cadena principal**: los registros con `isSidechain: true` (subagentes) se
  excluyen y se reportan en un warning con recuento.
- **Eventos**: un `tool_call` por `tool_use`, emparejado con su `tool_result`
  (`success = !is_error`); una llamada sin resultado registrado se marca
  fallida y se avisa. **Nunca se copian contenidos**: ni prompts ni payloads —
  solo esqueleto y contadores.
- **`usage`**: fresco = Σ(`input` + `cache_creation`); cacheado =
  Σ`cache_read`; `reasoning_tokens = 0` (el transcript no los separa; no se
  inventan); `latency_ms` = último − primer timestamp.
- **`run_id`**: `RUN-YYYYMMDD-<decimal de los 8 primeros hex del sessionId>` —
  determinista, sin contadores.
- **`context` ausente**: ninguna sesión pre-integración partió de un paquete
  compilado de context-runtime (enmienda D7).
- **Referencias reclamadas**: los refs de las invocaciones
  `grafos query explain/symbol/impact/callers/callees …` y
  `grafos memory for …` observadas en los Bash de la sesión — lo que el agente
  consultó de verdad; la verificación contra el índice ocurre en el ingest.

## Declaración de tarea (los campos que un transcript no puede dar)

JSON con `task_id`, `task_type`, `difficulty`, `ambiguity`, `risk`, `budget`,
`harness_id` y `verifiers` (≥1). Lo mecánico se deriva: `requires_tools`,
`requires_code_change` (hubo Edit/Write), `repository_size_bucket` del índice
Grafos (<100 small · ≤1000 medium · >1000 large), `changed_files_bucket:
unknown` (el transcript no registra el commit de partida — no se finge).

## Outcome con verificadores reales

Cada comando de `verifiers` se ejecuta en `--code-root` (sin shell, entorno
TLS purgado, timeout); el reporte completo (rc, duración, colas de salida) se
guarda como artifact content-addressed y `success` = todos exit 0. Es
**evidencia de una ejecución**, no una función pura: lleva tiempos.

**Regla de los comandos**: estilo POSIX resueltos por PATH (`uv run pytest -q`,
`python -c "…"`); una ruta Windows con backslashes sería destrozada por el
split POSIX — misma convención que los checks de context-runtime.

## Determinismo

Envelope y features son función pura de (transcript, declaración, tamaño del
índice): mismos bytes en cada ejecución (hay test). El outcome no lo es, por
diseño.
