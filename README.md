# chemical-cabinet

A small, free, self-hosted inventory for the chemical products kept in a cupboard or store room by an association or a small business.

For each product you can record a photo, the safety data sheet (PDF), where it is stored, quantity, opened and expiry dates. The cabinet page flags items that are expired, expire within 30 days, or have no expiry date, safety data sheet or photo. When something is thrown away you retire it, and the reason and method go into a disposal log.

Standard library Python only. Site and comparison with other tools: see `docs/`. No accounts, no external services, no tracking.

## What this is not

This is a tracking aid. It does **not** check legal compliance, does not tell you which products are safe to store together, and does not replace the manufacturer's safety data sheet, a written risk assessment or advice from your national regulator. Check the rules that apply to you.

## Run it

```
python -m chemical_cabinet --data ./data --port 8080
```

Open `http://localhost:8080` on the computer that runs it. ### Using it from a phone

By default the app only listens on `127.0.0.1`, so a phone cannot reach it. To use it from a phone on the same Wi-Fi, run it on a computer or small server in the building with `--host 0.0.0.0` (or that machine's LAN address) and open `http://<that machine's address>:8080` on the phone. Only do this on a network you trust, and prefer a reverse proxy that adds a password and HTTPS (taking photos from the browser camera usually needs HTTPS). Never forward the port to the internet.

The app has **no login**. Do not expose it to the internet directly: keep it on a trusted network or put it behind a reverse proxy that does authentication and HTTPS. Data (SQLite database and uploads) lives in the `--data` folder, so backing it up is copying that folder.

Needs Python 3.9 or newer.

## Other tools

Free chemical inventory tools already exist, mostly for university labs and under GPL/AGPL licenses. See [docs/ALTERNATIVES.md](docs/ALTERNATIVES.md). No code from them was copied.

## Test

```
python -m unittest discover -s tests -v
```

## Status

Early version (0.1). Ideas welcome as issues: export to CSV, a printable review sheet, translations, optional login.

## License

MIT, see `LICENSE`.
