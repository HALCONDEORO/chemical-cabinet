# Import products from CSV

Imports add new active products to an existing cabinet. They do not match names,
update IDs, retire products or restore photos and safety data sheets. **Importing
the same file twice adds duplicates.** Use a backup, not CSV, to restore a cabinet.

Use Python 3.9 or newer from the project directory. No extra packages are needed.
First validate the file without opening or changing a cabinet:

```
python -m chemical_cabinet.csv_import ./products.csv
```

Check the reported product count. When ready, stop the web and MCP writers, make
a backup, then add the products to the intended data folder explicitly:

```
python -m chemical_cabinet.backup create ./data ./before-import.zip
python -m chemical_cabinet.csv_import ./products.csv --data ./data --apply
```

The data folder must already contain `cabinet.sqlite3` from a working cabinet.
An unfinished restore marker refuses import. The marker is checked before opening
the database and again after acquiring the SQLite write lock. This is not a lock
against a restore started after the check; keep all other writers stopped. The command never creates a cabinet
or copies attachments. Restart the app after a successful import and attach the
photos and safety data sheets to the new products separately.

## Format

Use comma-separated UTF-8 CSV. UTF-8 with a byte-order mark (BOM), CRLF, Unicode,
quoted commas and quoted multiline notes are supported. A spreadsheet's
semicolon-separated or legacy Windows encoding must be saved as CSV UTF-8 with
commas first. Do not replace commas blindly inside quoted notes.

Only `name` is required. Other supported columns are `location`, `quantity`,
`notes`, `opened_on`, and `expires_on`. Column order does not matter. Headers
ignore case and surrounding spaces; duplicate or unknown headers are refused.
Use empty date cells when unknown, or real dates written as `YYYY-MM-DD`.
Quantities are text (for example `2 bottles`), not converted between units.

[Download an invented example](examples/import-demo.csv), or start with:

```csv
name,location,quantity,notes,opened_on,expires_on
Sample cleaner (demo),Shelf A (demo),2 bottles,Invented example only,,2028-12-31
```

Limits per file: 2 MiB and 1,000 products. Limits per cell: name 200 characters,
location 500, quantity 200, notes 10,000, dates 10. Blank records are ignored;
an import with no products is refused. Leading and trailing cell whitespace is
trimmed. Embedded note line breaks are preserved.

The cabinet's export CSV is a **report, not this import format**. Its IDs,
attachment-status and flag columns are deliberately refused: they cannot safely
recreate those records. Copy the supported columns into a separate file if you
intend to add products, and check the results. Spreadsheet formula-protection
apostrophes from an export are not automatically removed on import. Text is
never executed as a spreadsheet formula or SQL.

## Errors and integrity

A bad header, malformed CSV, invalid date or oversized cell stops the import.
Errors name the field and physical line where possible, without printing cell
contents. The line points to the start of a CSV record; quoted multiline notes
can make this differ from the spreadsheet row number. Fix the file and validate
again.

All rows are checked before writing. The products are then inserted in a single
SQLite transaction with foreign-key enforcement enabled: if an insert fails, no products from that import are
committed. Existing products, photos, safety data sheets and disposal records
are not updated. This is not a transaction over the filesystem or an online
restore mechanism. The command does not reserve the cabinet against writers
started by another process.
