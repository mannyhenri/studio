# Working on Studio in the sandbox

Each task is done in the offline sandbox (see [../sandbox.md](../sandbox.md)). Before you start:

1. `npm run sandbox` (or `bash scripts/setup-linux.sh`, then `python3 scripts/sandbox.py demo`) and open
   http://localhost:8188/studio. The demo has the pictures and clips of a small piece of the film “The Glass Harbour”
   and nothing else; import `screenplays/The Glass Harbour.pdf` in Projects to make a project from it.
2. Read `README.md` for how Studio is laid out: the node pack in `ComfyUI-Studio/` (server routes in `__init__.py`
   and its modules, the web app in `web/app.js`), the apps in `workflows/*.app.json` and their builders in `builders/`.
3. Run `python3 scripts/sandbox.py check` once, so you know it passes before you change anything.

While working:

- Renders don't happen here. After a Generate, `python3 scripts/sandbox.py last-job` shows the graph Studio sent (app,
  models, size, prompt); `jobs` lists them all; `sandbox/jobs/*.json` has each one in full.
- Add tests that run offline: pure logic in `tests/unit`, anything that needs Studio running in `tests/sandbox` (the
  `demo` and `empty` fixtures start a sandbox on a free port with its own folders).
- A new model or workflow: run `python sandbox/collect_models.py` so the sandbox has its (empty) model file.
- `python3 scripts/studio-call.py <tool> '<json>'` calls Studio's MCP tools against the running sandbox.
- Keep `tests/live` working: those scripts run against the real PC and Mac workers (`STUDIO_LIVE=1`).

When you finish, `python3 scripts/sandbox.py check` must pass: `pytest tests/unit tests/sandbox` and `ruff check .`.
