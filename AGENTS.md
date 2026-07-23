# Instrucciones del repositorio

- Python mínimo: 3.12. Gestión con uv (`uv sync --dev`, `uv run …`).
- La lógica productiva vive bajo `src/proof_harness` y las pruebas bajo `tests`.
- `schemas/` es la fuente canónica de los 4 artefactos; los modelos Pydantic
  los implementan y los tests de contrato verifican que no derivan. Cambiar un
  esquema exige cambiar modelo, ejemplos y tests en el mismo commit.
- Los almacenes bajo `.proof-harness/` son append-only y deterministas: nada
  de timestamps en líneas persistidas, JSON canónico (claves ordenadas), la
  misma entrada produce los mismos bytes.
- Grafos solo por su CLI JSON pública (`--json --read-only`, `--require-fresh`);
  jamás leer `.grafos/grafos.db`. Este repo nunca escribe en Grafos.
- Nada de llamadas a modelos LLM en este repositorio.
- Idioma: README, código, docstrings y CLI en inglés; `docs/` en español.
- Antes de cerrar un cambio: `uv run ruff check .`, `uv run mypy src` y
  `uv run pytest` en verde.
- Conventional Commits; sin artefactos derivados ni secretos en el árbol.
