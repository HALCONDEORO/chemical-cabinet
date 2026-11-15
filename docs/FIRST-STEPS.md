# Your first cabinet session

You need a computer with Python 3.9 or newer and a web browser. This is a local
application, not a website where you create an account. Someone must start it on
that computer before the browser can use it. You do not need an AI assistant.

If Python and the project files are not ready, follow
[Local installation](LOCAL-INSTALL.md) first. Ask someone you trust for help with that step
if running a command is unfamiliar. Keep the default local-only address for your
first session. There is no login; do not share the port on the internet.

## The simpler local start

If you already have a built `chemical-cabinet.pyz` from the reviewed source, run:

```text
python /path/to/chemical-cabinet.pyz start
```

Python is still required. This uses `chemical-cabinet-data` in your home folder,
prints the actual data folder and local URL, then asks a browser to open after
successfully binding the port. If it cannot open one, copy the printed URL into
your browser; `start --no-browser` skips the browser request. Keep the command
window open. There is no universal double-click installer.

Already have a cabinet? Use `start --data "/absolute/path/to/your cabinet"`
instead, or you may open an empty default cabinet. For an isolated practice
session use a **different absolute** `--data` folder. Do not practise retirement
or edits on your real records. See [Local installation](LOCAL-INSTALL.md) for
building the portable file and the tested-platform limits. The source command
below remains an alternative; its relative-path behavior differs.

## Before adding your real records

Decide which folder will hold your cabinet. Write down its full path somewhere
private. The browser does not store the inventory: the computer running Python
does, in the folder passed to `--data`. A different data folder means a different
cabinet. Bookmarks alone are not a backup.

To learn without changing real records, start a separate practice cabinet from
the project directory:

```text
python -m chemical_cabinet --data ./practice-cabinet-demo --port 8080
```

Use an unused port if another cabinet is already running. If your system uses
`python3` or `py` instead of `python`, use the command that worked during setup.
This example creates a practice folder relative to the project directory; it
does not find or copy any existing cabinet.

Keep the command window open. Open `http://localhost:8080` in the browser on that
same computer. If it does not connect, use
[Troubleshooting](TROUBLESHOOTING.md#the-browser-cannot-connect).

## Try one invented record

1. Choose **Add**.
2. Enter `Example cleaner B (demo)` as the name, `Shelf B (demo)` as the location,
   `1 bottle` as the quantity, and `Invented training record (demo)` as the notes.
   Leave dates and files empty. This is not a real product or chemical advice.
3. Choose **Save**. Check the name, location and quantity on **Cabinet**.
4. Missing-date, missing-photo and missing-sheet flags are expected for this
   example. They are field reminders, not a safety verdict.
5. Choose **Edit**, change the quantity to `2 bottles`, and save. Quantity is text;
   the app does not automatically track use or subtract stock.
6. Search for `Example cleaner B` and choose **Apply**. If you later cannot find
   a record, **Clear** removes the filters.
7. Choose **Print this view** beside the match count to check the filtered print
   sheet. The navigation link **Print sheet** instead opens all active records.
   The CSV link always exports all active records, even from a filtered sheet.

Stop here if you are only exploring. Do not copy practice records into a real
cabinet. For photos, dates, editing and retirement, use the
[User guide](USER-GUIDE.md). Retiring a record has no web undo.

## Stop and return later

In the command window running the app, press **Ctrl+C** to stop it. Closing the
browser tab alone does not stop the app. The saved data remains in the data
folder. To return, start it again with the same data path and port, then open the
browser address again. Do not start several copies on the same data folder.

Before relying on real records, make an offline backup and test a restore into a
new folder. Follow [Backups](../README.md#backups); stop web and MCP processes
first. CSV and printouts are reports, not full backups. Keep backup ZIPs private:
they contain inventory data and referenced attachments and are not encrypted.

## Optional: connect an assistant

[Useful assistant workflows](AI-WORKFLOWS.md) gives read-only examples after a
compatible local MCP client has been configured. Connecting a tool to a remote
AI service can send inventory text to that service. Local MCP transport alone
does not keep a remote assistant's processing local. Choose what you share.
