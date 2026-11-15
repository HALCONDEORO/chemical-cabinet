# Changelog

## Unreleased

- Uploads: a photo or data sheet is now written to a new file that cannot replace an existing one, and a failed write removes only the partial file it created, so a name collision can no longer overwrite or delete another product's sheet. Serving `/files/` refuses a symlinked or non-regular upload folder and does not follow a symlink as the last path part (checked on POSIX; this is not a defence against a local user who can change the folders).

- Docs: a user guide (docs/USER-GUIDE.md), troubleshooting (docs/TROUBLESHOOTING.md), synthetic demo records (docs/examples/demo-records.json) with a test that checks them, and a clearer MCP guide.

- Cabinet page: filter by exact location, sort by expiry, location, name or newest, and the printed review sheet keeps the same filters. The list is now read with one SQL query per page (search, filter, sort and paging in the database), with indexes on active products and on the disposal log. Dates that are not ISO (for example `2030-06` or text) are flagged "invalid expiry date" and kept as typed; nothing is converted or rewritten. Speed numbers in the project notes come from synthetic data, not real use.

- Cabinet page: each product is an article with its own heading; the Edit, Safety data sheet and Retire controls get names that include the product name; the skip link moves focus to the main content. Checked once in headless Chrome and with structural tests; not tested with screen readers, and not a WCAG audit.

- MCP server: refuses to start on a data folder with a `RESTORE_INCOMPLETE` marker or a missing or dangling database path, opens only an existing database and never creates the schema, turns storage errors into tool errors instead of ending the session, rejects item ids outside the SQLite integer range, and turns SQLite foreign keys on for its connections. Stop the web app and MCP before restoring a backup; there is no lock against concurrent use.

- CSV export: cells that start with a formula character after spaces, line breaks or a byte order mark are now prefixed with an apostrophe too. The original text is kept. This is a conservative defence, not a promise for every spreadsheet program. See docs/EXPORTING.md.

- Backups: archives and restored folders are private to the owner (zip 0600, folders 0700, files 0600). Backup creation refuses a source folder that still has the `RESTORE_INCOMPLETE` marker, including a dangling symlink. The size limit now counts the manifest, so verify and restore accept a backup at exactly the limit. Backup creation also checks that the core inventory tables and columns exist; this is a structural check only, not a check that the inventory is sound. No encryption and no locking against concurrent use.

## 0.1.1 - 2026-11-15

- Editing a product from a search, filter or page now returns to that same view after saving, and the edit page has a "Back to the list" link.
- The cabinet page shows 100 products per page with Previous/Next links that keep the search, filter and sort; it says which range is shown.
- The backup command prints one clear `error:` line and exits with status 1 when it refuses something (for example restoring onto a folder that exists), instead of a Python traceback.
- Filter checkboxes and the Clear link now have 44px touch targets.
- Tests that read every page's HTML for keyboard basics: skip link first, no positive tabindex, one h1, language set, every control and image named. It does not drive a real browser.
- Project site: new captures (filters, current form borders) and text for search, backups and editing.
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
