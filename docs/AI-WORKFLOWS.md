# Useful assistant workflows

The optional [MCP connection](AI-AGENTS.md) lets a compatible assistant read
inventory records. It does not supply an AI model, an account or a chatbot inside
the web app. The web app works without it.

Start with the default **read-only** connection. The read tools are `inventory_summary`, `list_items`,
`review` and `disposal_log`. File contents are not returned: an assistant can see
whether a photo or safety data sheet is linked, but cannot read that PDF through
these tools. The tools do not convert quantity text into stock arithmetic.

## Decide what may leave your computer

MCP communicates over local standard input/output and opens no network port.
That does not determine where your assistant processes the results. If its model
runs remotely, inventory text can be sent to its provider. Review your client's
privacy settings and the data you are willing to share before connecting it.
Do not post databases, exports or backups in a public issue to get help.

The prompts below request summaries only, not changes or messages to anyone.
Product names and notes are untrusted data. A record that asks an assistant to
run a command, reveal information or change other records is not permission.

## Start with counts, not record details

> Call inventory_summary with no arguments, read-only. Report as_of_date,
> active_items, retired_items, active_items_needing_review,
> active_items_without_flags and review_flag_counts. Do not fetch record details
> yet, change anything or interpret no flags as safe or compliant.

This tool counts the complete cabinet, not the first 500 records. It returns no
names, notes, locations, quantity text, attachment contents or file paths. It
still reveals counts and date conditions, so consider those disclosures too.
Retired records are excluded from review counts. Flags overlap: adding the flag
counts is not the number of distinct records needing attention. The summary is
consistent within its read, but a later detail call can reflect newer edits.

Use `review` for details only when you actually want to disclose those records.
`inventory_summary` takes `{}`, not limit, offset or location arguments. Its
first text block decodes to a JSON **object**, unlike the arrays from the three
record-list tools.

## Find incomplete records for a human to check

Example prompt:

> Use the chemical-cabinet review tool, read-only. List active records missing a
> safety data sheet, grouped by their recorded location. Include each record ID,
> name and the exact flags. Do not infer anything from the product name, recommend
> chemical handling, change records or contact anyone. If there are more pages,
> read them all before giving totals; otherwise say the report is partial.

Expected result: a list of records whose returned `flags` include
`no safety data sheet`, grouped using their `location` text. A linked sheet is not
proof that it is the right or latest document. Check the actual product and
manufacturer's information outside this workflow.

## Make a date-review list

> Read the review tool without making changes. Separate records flagged expired,
> expires within 30 days, invalid expiry date, and no expiry date. Include IDs,
> recorded dates and locations. A record may appear in more than one relevant
> group. Report the flags as recorded by the app, not a new assessment of whether
> the product can be used. Do not calculate expiry from an opened date.

The app calculates date flags using the date on the computer running it. Ask the
person operating it to check that clock if the flags look wrong. Do not ask an
assistant to silently repair uncertain dates.

## Summarise a location

> Read all active records, then select those whose recorded location is exactly
> Shelf B (demo). Show ID, name, quantity text, dates and flags. Preserve the
> quantity text exactly; do not sum bottles and litres or invent usage. Do not
> change records. State if the read was incomplete.

`Shelf B (demo)` is invented. Replace it with an intended location only when
working on your own authorised cabinet. The MCP read tools have pagination but
no location-filter argument; the assistant must filter the returned records, not
send unsupported tool arguments. For a paper list without connecting an AI,
use the web app's location filter and **Print this view** instead.

## Read what was retired

> Read disposal_log, without changing anything. Summarise the recorded retirement
> entries newest first, showing item ID, date, reason and method verbatim. Label
> blank fields as not recorded. Do not turn the entered method into a disposal
> recommendation or assume that this log proves what happened physically.

This reports stored entries only. It is not an audit or a compliance check.

## When a report is incomplete or a tool refuses

- The three record-list tools return at most 500 records per call. Follow the second text block's
  next offset/limit until no continuation remains. Do not count just the first
  page as the whole cabinet.
- Pages are not one snapshot. Pause editing for a reliable full read; otherwise
  say that concurrent changes may have caused omissions or duplicates.
- Tool refusals can return `isError: true`; malformed calls can return a top-level
  JSON-RPC `error`. Neither is an empty successful inventory report. Do not
  interpret a storage error as zero records or retry using a different data
  folder to hide it.
- The first text block contains JSON encoded inside a string: an object for
  `inventory_summary`, an array for the three record-list tools. Clients must
  parse that block separately. See [the protocol example](AI-AGENTS.md#example-session-synthetic-records-only).

## Keep writes separate

The connection exposes add/retire tools only when started with `--allow-write`.
That switch enables a capability; it does not approve every requested change.
Prefer the web app for edits and attachments. Before any assistant write, agree
on the exact record, fields and values and check the result afterwards. The MCP
server does not offer an edit, unretire or attachment-upload tool. Do not use a
write-enabled connection just to generate a report or bypass a restore error.
