# Contributing to ddogctl

¡Gracias por tu interés en `ddogctl`! / Thanks for your interest in `ddogctl`!

This guide is intentionally short. Both English and Spanish are welcome in
issues, PRs and commit messages — the project itself ships bilingual help text.

## Ground rules

- Be respectful and constructive in reviews and discussions.
- Prefer small, focused PRs over large ones.
- Don't break the public CLI surface without discussion (flag names, JSON shape).
- **Never commit secrets.** `DD_API_KEY` / `DD_APP_KEY` belong in env vars or
  `~/.config/ddctl/config.yaml`, never in code, examples or tests.

## Development setup

```bash
git clone https://github.com/45p1d4/ddogctl.git
cd ddogctl

python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate

pip install -e .
pip install pytest
pytest -q
```

A guardrail test (`tests/test_debug_help.py`) ensures every leaf command
exposes `--debug`. If you add a new command, make sure that test still passes.

## Architecture cheat sheet

- **`ddctl/cli.py`** — Typer root app, global flags, sub‑app registration.
- **`ddctl/commands/*.py`** — one module per Datadog domain (monitors, apm, logs,
  metrics, rum, hosts, downtimes, services, incidents, dashboards, synthetics,
  service, url, auth).
- **`ddctl/api.py`** — thin `requests` wrapper that injects `DD-API-KEY` /
  `DD-APPLICATION-KEY` headers and raises a structured `ApiError`.
- **`ddctl/config.py`** — env‑var → YAML context resolution.
- **`ddctl/normalize.py`** — pure functions that turn Datadog payloads into
  the compact dicts shipped via `--json`. `DEFAULT_FIELDS` is the per‑command
  whitelist.
- **`ddctl/ui.py`** — `emit()` is the central output helper: Rich tables for
  humans, compact JSON `{cmd, n, data, meta}` for `--json`, raw payload with
  `--full`, and structured errors via `emit_error()`.
- **`ddctl/i18n.py`** — `t("es", "en")` selects help text based on
  `DDOGCTL_LANG` (defaults to `es`).
- **`ddctl/options.py`** — shared `DebugOption` annotation.

## Adding a new command

1. Pick the right module under `ddctl/commands/` (or create a new sub‑app and
   register it in `ddctl/cli.py`).
2. Define a Typer command with **named flags only** (`--service`, `--from`, ...),
   bilingual help via `t("es", "en")`, and a `debug: DebugOption = False`
   parameter.
3. Call the API through `get_client_from_ctx(ctx)`.
4. Normalize the response into a small dict / list of dicts. Add a whitelist
   entry to `DEFAULT_FIELDS` in `ddctl/normalize.py`.
5. Render output through `emit(ctx, "<cmd.key>", data, raw=resp,
   table_renderer=_render)` so tables, `--json` and `--full` all work for free.
6. Add or extend tests under `tests/`.

## Output contract (don't break this)

- `--json` emits **one line** of compact JSON to stdout:
  `{"cmd":"...","n":N,"data":<...>,"meta":{...}}`.
- Errors with `--json`: `{"cmd":"...","error":{"type","message","status","payload"}}`.
- Without `--json`, render a Rich table. Auto‑hide empty columns and lift
  constants (`service`, `env`, `cluster`) into the title via `build_title()`.

## Style

- Python 3.10+. Type hints required for public functions.
- Keep modules small and dependency‑light; the runtime stack is
  Typer + Rich + requests + pyyaml + python-dateutil. Please discuss before
  adding new dependencies.
- Use `from __future__ import annotations` at the top of every module.
- Comments should explain **why**, not narrate **what**.

## Tests

```bash
pytest -q
```

Add tests for any new normalizer, time parser change, or command behavior.
Network calls in tests must be mocked.

## Commit / PR conventions

- Conventional Commit prefixes are appreciated but not required:
  `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`.
- PR description should answer:
  1. What does it change?
  2. Why is it needed?
  3. How was it tested? (commands run, expected output)
- Update both `README.md` and `README.es.md` if user‑facing behavior changes.

## Releasing (maintainers)

1. Bump `version` in `pyproject.toml`.
2. Update READMEs if there are new commands.
3. Tag: `git tag vX.Y.Z && git push --tags`.

## License

By contributing you agree that your contributions will be licensed under the
[MIT License](LICENSE).
