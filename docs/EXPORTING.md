# Exporting the inventory

Open `/export.csv` in the running application to download a UTF-8 CSV of **all active products**, not just the current search, filter or page. The export is read-only and does not change the inventory.

## Fields and characters

The ten columns are `id`, `name`, `location`, `quantity`, `opened_on`, `expires_on`, `has_photo`, `has_safety_data_sheet`, `flags` and `notes`. Dates use `YYYY-MM-DD`; missing dates are empty. Attachment columns contain `yes` or `no`, not the files themselves. Flags are calculated at export time, joined by `; `, and are reminders rather than a compliance verdict.

The file has a UTF-8 byte order mark to help spreadsheet programs recognise accented characters. Fields containing commas, quotes or line breaks are quoted as CSV. When reading it in Python, use `encoding="utf-8-sig"` and `newline=""` with `csv.reader` or `csv.DictReader`. Splitting into lines first can lose line breaks inside notes.

## Spreadsheet formula precautions

User-entered text can begin with characters a spreadsheet treats as formulas. The exporter adds a leading apostrophe to text beginning with `=`, `+`, `-` or `@`, including when preceded by ASCII whitespace or a byte order mark. Text beginning with a tab, carriage return or line feed is also prefixed. Original characters remain after that prefix.

This deliberately changes those CSV values, including legitimate text such as a negative quantity. Other ordinary text, accents, quotes and internal line breaks are preserved. Spreadsheet behaviour varies: import untrusted columns as text and do not enable external links or content. The prefix is a precaution, not a guarantee that every spreadsheet will treat every input safely. Saving the file again from a spreadsheet can change its contents and protection.

## What this export does not preserve

CSV is a convenient inventory report, **not a restorable backup**. It excludes retired products, retirement records, creation dates and attachment files. A leading apostrophe in an exported cell may have been original text or a protective prefix, so do not strip apostrophes automatically to reconstruct source data. Use the documented backup workflow when you need a recoverable copy, including photos and safety data sheets.
