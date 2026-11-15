"""Start one local cabinet with an optional browser, using the existing HTTP app."""
import argparse
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
import threading
import webbrowser

from .app import make_handler


def open_browser(url):
    """Browser availability never decides whether the local server can run."""
    try:
        opened = webbrowser.open(url, new=2)
    except Exception:
        opened = False
    if not opened:
        print('A browser could not be opened. Copy the local URL above into your browser.',
              file=sys.stderr, flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=Path.home() / 'chemical-cabinet-data',
                        help='data folder (default: chemical-cabinet-data in your home folder)')
    parser.add_argument('--port', type=int, default=8080, help='localhost port (default: 8080)')
    parser.add_argument('--no-browser', action='store_true', help='print the URL without opening a browser')
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error('--port must be between 0 and 65535 (0 chooses an available port).')
    # The easy launcher has one stable default. Explicit relative paths are rejected
    # rather than silently selecting a different cabinet after a shortcut/update.
    if not args.data.is_absolute():
        parser.error('--data must be an absolute path.')
    data = args.data.resolve()
    try:
        # Open the browser only after binding, never on a port owned by another app.
        # make_handler keeps the existing restore, Host, Origin and token guards.
        with ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(str(data))) as server:
            url = 'http://127.0.0.1:%d/' % server.server_address[1]
            print('Data folder: %s' % data, flush=True)
            print('Open %s' % url, flush=True)
            print('Local computer only. No login. Keep this window open; press Ctrl+C to stop.', flush=True)
            if not args.no_browser:
                threading.Thread(target=partial(open_browser, url), daemon=True).start()
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                print('\nStopped. Your data folder was not removed.', flush=True)
    except (OSError, ValueError) as exc:
        parser.exit(2, 'Could not start the local cabinet: %s\nCheck the data folder and port; no browser was opened.\n' % exc)


if __name__ == '__main__':
    main()
