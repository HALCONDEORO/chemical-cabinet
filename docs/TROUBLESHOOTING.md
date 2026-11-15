# Troubleshooting

These checks describe the current main branch (version 0.1.1 plus later changes listed in the changelog). Keep the original data folder and any known
backups while investigating. Do not edit the SQLite database or delete files to
make an error disappear. For ordinary use, see [Using the cabinet](USER-GUIDE.md).

## An empty cabinet appeared

Check the exact `--data` argument and the directory from which Python was started.
Relative paths depend on that directory. A new data path creates a new cabinet;
it does not find or migrate the previous one. Stop the new instance and restart
with the original path. Do not overwrite either folder while identifying them.

## The browser cannot connect

- Check that the Python process is still running and use the port passed to
  `--port` (8080 by default).
- On the same computer, start with `http://localhost:8080`, not HTTPS unless you
  have set up HTTPS separately.
- `127.0.0.1` is local to each device. A phone's `localhost` is the phone, not the
  computer running the cabinet. See [Using it from a phone](../README.md#using-it-from-a-phone).
- If the terminal says the address is already in use, check whether your intended
  instance is already running. Otherwise choose an unused port. Do not start a
  second writer on the same data folder just to work around the error.

## `host not allowed` (403)

A custom name needs an explicit `--allowed-host`, for example
`--allowed-host cabinet.local`. Use only the name, without `http://` or a path.
The option can be repeated for names you actually use. It is a request check, not
login or a firewall. Do not disable it or allow arbitrary names as a workaround.

## `cross-site request refused` or `missing or wrong form token` (403)

Open the form directly through the cabinet's own address, allow its cookie, and
load a fresh form before re-entering the change. Do not submit from a saved HTML
file, another site's form or a different cabinet address. Check that any reverse
proxy preserves the intended host and origin. Do not remove request checks.

A form with an error is not proof of a saved change. Check the record first to
avoid adding it twice.

## `unsupported file type` or `too large` (413)

- Photos must be JPEG, PNG or WebP; a phone may produce HEIC, which is not accepted.
  Export a supported copy. Renaming its extension does not convert its contents.
- A safety data sheet must be a PDF. The declared type and the file's opening
  bytes are checked; this is not a malware scan or a full document validation.
- The request limit is **25 MiB (26,214,400 bytes) for the entire submitted form**,
  including both files, text and multipart overhead, not 25 MiB per file.
  Use smaller copies with room below that total.

Keep the originals elsewhere if needed. After a successful replacement, the old
attachment can be deleted by the app; it does not retain previous versions.

## An item disappeared or an edit says `not found`

Clear the cabinet filters first. A change can move a record out of a filtered
view. Check **Disposal log** too: retired records are not in the active list and
cannot be edited through the web form. There is no web undo for retirement.
Do not add a duplicate until you know which of these happened.

## CSV or Print sheet shows more records than the current page

The CSV always includes every active record. The navigation link **Print sheet**
also includes all active records. **Print this view** on a filtered cabinet passes
its search, attention filter, location and order, but not the page number. Check
the filter summary on the sheet itself. Both outputs omit retired
records and do not contain the attachment files. See [output contents](USER-GUIDE.md#export-and-print-know-what-is-included).

## Backup or restore refused

Stop the web app and every MCP process using the data before backing up. Their
attachment changes are not locked together with the database snapshot. Backups
are offline, local and unencrypted ZIP files: protect the archive as you would
the original inventory. The app does not upload or schedule them.

The parent directory of an output path must already exist and be writable.
Choose a **new filename** for `create` and a **new directory name** for `restore`.
Even an existing empty restore directory is refused. Do not delete the live data
folder to make room for a restore.

| Error or condition | Next check |
| --- | --- |
| Output already exists | Pick a new output path; commands do not overwrite it. |
| `expected regular database and non-symlink files directory` | Check the data path and that `cabinet.sqlite3` exists. Do not substitute an empty database. |
| Missing attachment, checksum mismatch or database integrity failure | Preserve the source and archive. Find another verified backup or investigate the missing data; do not skip verification. |
| `snapshot timed out` or attachment changed/disappeared | Check that every writer is stopped before making a fresh backup at a new path. |
| `backup too large`, `archive too large` or too many files | This version has 1 GiB uncompressed and 10,000-entry archive limits. Do not remove safety checks or split/edit an archive by hand and assume it will restore. |
| Hard-link operation not supported or not permitted | `create` uses a hard link to publish the ZIP without overwriting an existing path. Try an appropriate local filesystem with hard-link support; permission and free-space checks still apply. |
| No space left | Creating/verifying/restoring needs temporary disk space as well as the final output. Preserve the source and known backups; choose storage with enough free space. |

Successful backup CLI commands exit with status 0 and print no success message.
A handled refusal prints an `error:` line to standard error and exits with status
1. Read the result of each step before starting the next. File existence alone
is not verification.

After creating an archive, run `verify`, restore into a new path, and inspect the
restored cabinet and its attachments before relying on it. Verification checks
archive structure, checksums, SQLite integrity and attachment references. It does
not establish that the entered inventory is accurate or that every desired file
was attached in the first place.

### `RESTORE_INCOMPLETE`

An interrupted restore may leave this marker. **Do not remove the marker to force
the app to start. Do not use MCP to read or write that folder either.** The marker
means the restore has not been confirmed complete, not that only the marker is
wrong. Preserve the failed destination for investigation if needed, and restore
the known archive into a different new directory. Point the app and MCP at it
only after a successful restore and checks.

## MCP says there is no database

Use the same absolute `--data` path as the web app. On a new cabinet, run the web
app once to create its database before starting MCP. Do not initialize over an
incomplete restore. MCP uses standard input/output, not a browser URL. See
[AI assistants](AI-AGENTS.md) for configuration and paging limits.

Read tools are available by default. Write tools require `--allow-write`; this
switch does not add authentication or decide whether a requested change is
appropriate. Stop MCP along with the web app for an offline backup.

## Reporting a problem

Include the app version, Python version, operating system, command with private
paths redacted, exact error text, and steps using invented `(demo)` data. Do not
attach your database, backup ZIP, real photos, safety data sheets or inventory
exports to a public issue. Follow [SECURITY.md](../SECURITY.md) for security reports.
