# Local installation and updates

The existing source command still works from the checkout root:

```sh
python -m chemical_cabinet --data /absolute/path/to/cabinet --port 8080
```

Use Python 3.9 or newer with SQLite support. The app needs no packages, account,
service, administrator privileges or internet connection at runtime. `python`
may be named `python3` on your computer. Check `python --version` first.

## One portable file, built from reviewed source

From the root of your reviewed source checkout:

```sh
python tools/build_zipapp.py --output ./dist/chemical-cabinet.pyz
python ./dist/chemical-cabinet.pyz --version
python ./dist/chemical-cabinet.pyz --help
```

The builder uses Python's standard-library `zipapp`. The archive includes only
Python files inside `chemical_cabinet`, its launcher and the MIT license. It does
not include the database, uploads, tests, documentation or your inventory. No
Python interpreter is bundled: keep Python installed. The archive is executable
code, not a sandbox or an encrypted container. Only run code from a source you
trust. There is no installer or automatic download/update service.

### Easiest local start

```sh
python /path/to/chemical-cabinet.pyz start
```

This starts the existing app on **localhost only** and asks your default browser
to open the address, after the local port has been bound successfully. Your data
folder is `chemical-cabinet-data` in your home folder, not the working directory.
The actual absolute folder and local URL are printed every time. Keep the
terminal window open. Press Ctrl+C there to stop the app; closing the browser tab
does not stop it. `start --no-browser` prints the URL without asking a browser.
If no browser is available, copy the printed URL manually. If port 8080 is busy,
stop the other copy or choose another port: `start --port 8081`. The launcher will
not open a busy port that might belong to a different application. It does not
check for another copy of this inventory using a *different* port; run one copy.

Already have data? Do **not** start a new empty default cabinet. Point at your
existing absolute data folder instead:

```sh
python /path/to/chemical-cabinet.pyz start --data "/absolute/path/to/existing cabinet"
```

A source-checkout equivalent is `python -m chemical_cabinet.launcher`. There is
no universal double-click installer. Browser availability/file associations vary
by operating system. The browser-opening call and its failure fallback were
checked with test doubles; the real Linux localhost start/HTTP/Ctrl+C path was
exercised without launching an installed desktop browser.

Copy the `.pyz` file to a convenient local folder. You can run it from a different
working folder, without keeping the source checkout there. Always start it with
Python, not by assuming double-click or file associations work:

```sh
python /path/to/chemical-cabinet.pyz web --data /absolute/path/to/cabinet --port 8080
```

Open `http://localhost:8080`. The default binding remains `127.0.0.1`; this launcher
does not open a firewall or make the app reachable from another computer. There
is no login. Keep the existing network/security guidance in the main README.
Stop the foreground app with Ctrl+C before updating or backing up.

The portable launcher requires an **absolute** `--data` folder for `web` and
`mcp`. This prevents a shortcut, terminal or update from accidentally opening a
new empty cabinet in a different working directory. Quote paths that contain
spaces. Do not put inventory data inside the source or beside code that you
routinely delete during updates. An empty cabinet after an update may mean the
wrong `--data` path: stop and check the path before importing or recreating data.

The advanced `web` command does not open a browser. Other included commands use the existing modules and their existing safeguards:

```sh
python /path/to/chemical-cabinet.pyz mcp --data /absolute/path/to/cabinet
python /path/to/chemical-cabinet.pyz backup --help
```

MCP remains read-only by default and uses stdio, not a network port. It needs a
cabinet that has been opened by the web app first. Backup positional paths keep
the original module's behavior; use absolute paths there too. This launcher does
not add import commands or change any future import module's CLI.

## Update without replacing the data folder

1. Stop the web app and every MCP process that can use this cabinet. Keep the
   exact absolute data path. Do not run an old and new writer together.
2. Create and verify a backup with the existing backup commands. Keep it outside
   the data and code folders. See the main README's backup instructions and any
   version-specific release notes. Copying just `cabinet.sqlite3` is not a full
   backup because attachments live beside it.
3. Get the reviewed source version you intend to run. Keep a copy of the old
   `.pyz` and record its version. Build the new archive at a **new** output path:

   ```sh
   python tools/build_zipapp.py --output /path/to/chemical-cabinet-next.pyz
   python /path/to/chemical-cabinet-next.pyz --version
   ```

4. Start the new archive with the **same absolute data path**, initially on
   localhost. Check records and attachments before treating the update as done.
   Building or replacing the archive never copies or migrates your inventory.
5. Keep the prior archive and verified backup until your checks pass. Do not
   assume old code can read a database modified by a future version. Follow that
   version's migration/rollback notes. If restoration is needed, restore into a
   new folder and point the app there only after verified success.

The builder refuses to replace an existing output unless you explicitly pass
`--overwrite`. It rejects symlink package members and symlink output files, and
replaces a finished archive atomically on the same filesystem. This is not a
concurrent-build lock or a security boundary against someone who can modify your
source/build directory. Build from a private, trusted local checkout.

## Verification and limits

`tests/test_install_zipapp.py` exercises an archive after removing the copied
source, help/version, a real localhost startup from another directory, stdio
initialization, explicit data-path errors, data-file hashes across a code-only
update, and output/symlink exclusions using invented data. It does not certify
backup recovery, browser behavior, schema migration or any external deployment.

Linux with Python 3.10 was exercised for this change, including source and portable startup. Windows, macOS and other
Python versions were not exercised here. Paths and quoting vary by shell. On
Windows pass an absolute path such as `"C:\Users\YourName\Cabinet Data"`; on
macOS/Linux use an absolute path such as `"/home/yourname/Cabinet Data"`. These are
syntax examples, not claims that those systems were tested. If Python lacks
SQLite support, install a Python distribution with SQLite before trying again.
