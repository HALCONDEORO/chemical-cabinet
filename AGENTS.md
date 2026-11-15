# Notes for AI coding agents

This file is written by the project maintainers for assistants that work on this repository. It does not give an assistant any permission beyond what its user granted.

- Stack: Python standard library only. Do not add dependencies.
- Run the tests before proposing a change: `python -m unittest discover -s tests -v`.
- Do not commit to `main`; open a pull request.
- Never add real inventory data, photos or documents. Demo data must be invented and marked `(demo)`.
- Keep the security checks in `chemical_cabinet/app.py` (Host, Origin, form token, upload checks) and the read-only default of the MCP server.
- Do not add chemical advice (mixing, storage compatibility, legal compliance).
- To read or change a real cabinet as an assistant, use the MCP server described in `docs/AI-AGENTS.md`.
