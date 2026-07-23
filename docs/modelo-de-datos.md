# Modelo de datos — reglas canónicas (v1)

Desde el incremento 1, **`schemas/` es la fuente canónica** de los cuatro
artefactos; los modelos Pydantic de `src/proof_harness/schemas/` los
implementan y `tests/test_schemas_contract.py` verifica que no derivan
(round-trip: todo ejemplo válido parsea con el modelo Y su forma canónica
revalida contra el JSON Schema; todo inválido es rechazado por ambas capas).

## Reglas transversales

**Serialización canónica.** JSON con claves ordenadas y separadores compactos,
sin escapes ASCII. Los campos opcionales con valor `None` **se omiten**
(«ausente ≡ desconocido»); por eso ningún campo del contrato es
required-y-nullable a la vez. Sin timestamps en líneas persistidas: la misma
entrada produce los mismos bytes.

**Hashes propios vs identificadores externos.**

- Hashes de proof-harness: prefijo de algoritmo, `sha256:<64 hex>`
  (`HarnessBundle.content_hash`, `artifacts[].sha256`).
- Identificadores externos viajan **verbatim** con el formato nativo de su
  productor — la procedencia no se normaliza:
  `context.context_hash` (hex desnudo 64, context-runtime),
  `grafos_index_id` (`sha256:<16 hex>`, Grafos),
  `repository_revision` (`git:<sha 7-40>`).

**`HarnessBundle.content_hash`** cubre solo la política:
`{schema_version, harness_id, parent_harness_id, scope, dimensions}` en JSON
canónico. Fuera del hash: `content_hash`, `created_at`, `status` y `evidence`
— promocionar un bundle o anexarle evidencia no fabrica otra «versión»; solo
cambiar la política lo hace.

**Referencias `artifact://`.** Opacas, resolubles contra el almacén
(`.proof-harness/`); los blobs van content-addressed
(`runs/artifacts/<sha256>.json`) y las trayectorias por id lógico
(`artifact://trajectories/<run_id>.json` → línea en `trajectories.jsonl`).

**IDs.** Numéricos con 3+ dígitos (`harness-`, `PAT-`, `EVAL-`, `EXP-`,
`EVT-`, `RUN-YYYYMMDD-`). `experience_id` es **derivado y determinista**:
`EXP-` + los 12 primeros hex del sha256 del envelope canónico, en decimal con
relleno a 9 dígitos — mismo envelope, mismo id, sin contadores.

## Semántica por artefacto

- **TrajectoryEnvelope** — registro bruto, agnóstico del runner
  (`runner.name` slug libre: `claude-code`, `codex`, `opencode`,
  `synthetic-fixture`, …). Contenido solo por referencia (`input_ref` /
  `output_ref`); `events[].kind` es string libre en v1 (la taxonomía se fija
  con el primer adaptador real). `usage` separa tokens cacheados de frescos.
- **ExecutionExperience** — derivada determinísticamente del envelope.
  `diagnosis` opcional (el incremento 1 ingiere sin diagnosticar).
  `validity.referenced_symbols` SOLO contiene ids que resolvieron contra el
  índice Grafos citado; lo no resoluble queda en la cuarentena del almacén y,
  adjunto a la experiencia, en `validity.quarantined_refs` — poner en
  cuarentena nunca borra. `status` usa los estados
  `current/suspect/stale/invalidated/revalidated`; la política de transición
  entre ellos es de fases posteriores.
- **TaskFeatures** — declarativo y determinista; vocabularios cerrados
  (`low/medium/high`, buckets con `unknown`). `budget` exige enteros ≥1.
- **HarnessBundle** — política inmutable con linaje (`parent_harness_id`) y
  `status ∈ {candidate, active, deprecated, rejected}`. El vocabulario interno
  de política (`workflow`, `retry_policy`, …) es string libre en v1: fijarlo
  hoy sería inventar el vocabulario que el registro/selector (fase posterior)
  debe decidir con datos. Se acepta también en YAML
  (`persistence.load_yaml_model`).

## Ingesta (contrato operativo del CLI)

`run ingest TRAJECTORY --task-features F --outcome O [--ref SYM]…
[--code-root DIR]`:

1. valida el envelope, las features y el outcome (error de dominio → exit 2,
   cero escrituras);
2. exige en `--code-root` un checkout Git y un índice Grafos **fresco**
   (ausente/obsoleto → exit 3, cero escrituras); negocia por capacidades y
   verifica los `--ref` en un único `query batch` (op `memory`) — un solo
   snapshot para toda la ingesta;
3. normaliza a `ExecutionExperience` (coste derivado de `usage`;
   `monetary_cost` ausente = desconocido) y escribe: blob de features
   (content-addressed) → línea de trayectoria → líneas de cuarentena → línea
   de experiencia;
4. re-ingesta idéntica → idempotente (devuelve la experiencia almacenada con
   warning); mismo `run_id` con contenido distinto → conflicto, exit 2.
