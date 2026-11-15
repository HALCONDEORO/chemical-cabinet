# Using the cabinet

This guide describes the current main branch (version 0.1.1 plus later changes listed in the changelog). The cabinet records what you enter. It does not
recommend chemical handling, storage, mixing or disposal methods.

For startup options, see [Run it](../README.md#run-it). For errors, see
[Troubleshooting](TROUBLESHOOTING.md). For assistant access, see
[AI assistants](AI-AGENTS.md).

## Keep the data folder in view

The `--data` folder holds `cabinet.sqlite3` and the `files/` attachments directory.
A relative path such as `./data` is relative to the directory where you start
Python. Starting from another directory can open a different, empty cabinet.
Use the same absolute data path each time if that is a risk.

There is no login or per-person permission system. Anyone who can reach the web
app can read and change the cabinet. The local default is the simplest starting
point. Do not expose its port to the internet.

## Add a record

1. Open **Add**.
2. Enter a name. Location, quantity, notes, dates and files are optional.
3. Use the dates on the record you are transcribing. A blank date stays unknown;
   the app does not calculate an expiry date from the opened date.
4. Add a JPEG, PNG or WebP photo and a PDF safety data sheet if available.
5. Choose **Save**, then check the saved record in the cabinet.

These are invented examples, not product instructions:

| Field | Example A | Example B |
| --- | --- | --- |
| Name | Example cleaner A (demo) | Example cleaner B (demo) |
| Location | Shelf A (demo) | Shelf B (demo) |
| Quantity | 2 x 5 L | 1 bottle |
| Opened on | 2026-01-10 | Leave blank |
| Expires on | 2026-02-01 | Leave blank |
| Notes | Invented training record (demo) | Invented training record (demo) |

Machine-readable copies of these example fields are in
[demo-records.json](examples/demo-records.json), for documentation tests. This is
not an import file or a command to load data into your cabinet. Do not add demo
records to a real inventory.

Quantity is free text, not a measured balance. For example, `2 x 5 L` is displayed
as entered. There is no automatic deduction when a bottle is used, unit conversion,
low-stock calculation or movement ledger. Edit the quantity yourself when needed.

## Find and review records

On **Cabinet**, search matches name, location or notes, ignoring letter case. It
does not search quantity, the disposal log or the contents of attached PDFs.
**Only products that need attention** keeps records with one or more review flags.
**Soonest expiry first** puts dated records before records without an expiry date.
Choose **Apply** after changing these controls; **Clear** resets the view.

The cabinet shows up to 100 matching records per page. Previous/Next links keep
the search, filter and sort. Editing from a filtered page returns to that view
after saving; a changed record may no longer match the filter.

Review flags use the server computer's current date:

| Flag | Meaning in the app |
| --- | --- |
| `expired` | The entered expiry date is before today. |
| `expires within 30 days` | The expiry date is today or within the next 30 days, including day 30. |
| `no expiry date` | The expiry field is blank. |
| `no safety data sheet` | No PDF attachment is linked to the record. |
| `no photo` | No photo attachment is linked to the record. |

A flag records a missing field or date condition. Absence of flags is not a safety
assessment. The app does not read a safety data sheet to confirm that it belongs
to the product or that its contents are current.

## Edit without losing an attachment

Choose **Edit** on an active record. Leaving a file chooser empty keeps that
attachment. Selecting a new file replaces the old one; the old file can be deleted
by the app. This is not a document version history. Make an offline backup before
changes you may need to undo.

If a save is refused, do not assume that your typed text or selected files were
retained in the form. Check the cabinet before trying again. Avoid editing the
same record from two windows at once.

## Retire a record

Open **Retire** on its card, enter the reason and the method actually used, then
choose **Retire**. This logs a record; it does not tell you how to dispose of a
product. Check **Disposal log** afterwards.

Retired records leave the active cabinet. There is no web undo, unretire or edit
screen for a retired record. Do not use retirement just to reduce a quantity.

## Export and print: know what is included

**Print sheet** opens `/review`; use the browser's Print command. Its checkboxes
are spaces on the printed sheet, not saved review approvals.

The Download CSV link on that page opens `/export.csv`.

| Output | Included | Not included |
| --- | --- | --- |
| CSV | All active records, attachment yes/no fields and current flags | Retired records, actual photos and PDFs |
| Print sheet | The active records that match the search, attention filter, location and order you chose on **Cabinet** (all active records if none), and current flags | Retired records, file contents, a saved history of checks |
| Offline backup ZIP | Database and referenced attachment files | An online service or an automatic backup schedule |

The print sheet follows the search, attention filter, location and order you chose on
**Cabinet**, and says so at the top. It does not depend on the page you were on. The CSV
always has every active record. Check the output before sharing it. CSV text that could start a
spreadsheet formula is prefixed with an apostrophe. Keep that protection rather
than enabling formulas in untrusted inventory text.

CSV is not a full backup. For recovery commands and their limits, see
[Backups](../README.md#backups) and
[backup troubleshooting](TROUBLESHOOTING.md#backup-or-restore-refused).
