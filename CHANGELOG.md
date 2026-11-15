# Changelog

## Unreleased

- MCP read tools take optional `limit` and `offset`; a second text block says when more records remain. Pages are not a snapshot (see docs/AI-AGENTS.md).
- Form field borders are darker so the boundary of each field reaches 3:1 contrast (WCAG 1.4.11), with a test.
- MCP server: malformed tool parameters (non-object params, non-string name, non-object arguments) now get a JSON-RPC -32602 error instead of ending the session.
- Search, "needs attention" filter and soonest-expiry sort on the cabinet page.
- Offline backups: `python -m chemical_cabinet.backup create|verify|restore`. Restore goes to a new folder and leaves a `RESTORE_INCOMPLETE` marker until done; the app refuses to start on a folder that still has it.
- Edit a product (name, place, quantity, dates; new photo or sheet replaces the old one only if given).
- Uploads: a failed save no longer leaves orphan files, and replacing a photo or sheet deletes the old file.
- Retiring a product is now atomic: a concurrent double retire no longer writes two log rows, and a failed log write rolls the retirement back.
- CSV export (`/export.csv`) and a printable review sheet (`/review`).
- Form fields have labels; pages have a main landmark, a named navigation and a skip link.
- Test that starts the real MCP server process and talks to it over stdio.

## 0.1.0

- Inventory with photo, safety data sheet, dates and review flags.
- Retire products with a reason and disposal method; disposal log.
- Host, Origin and form-token checks; upload type and size limits.
- Optional local MCP server for AI assistants, read-only by default.
- Project page in `docs/`.
