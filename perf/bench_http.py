"""End-to-end HTTP timing of GET / on synthetic (demo) data, old vs new path."""
import http.client
import os
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from chemical_cabinet import app, db
from perf.bench_synthetic import fill


def serve_and_time(n, path_qs, repeats=5):
    tmp = tempfile.TemporaryDirectory()
    con = db.connect(os.path.join(tmp.name, "cabinet.sqlite3"))
    fill(con, n, seed=n)
    con.close()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), app.make_handler(tmp.name))
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    best = float("inf")
    for _ in range(repeats):
        c = http.client.HTTPConnection("127.0.0.1", port)
        t0 = time.perf_counter()
        c.request("GET", path_qs, headers={"Host": "localhost:%d" % port})
        body = c.getresponse().read()
        best = min(best, time.perf_counter() - t0)
        c.close()
    srv.shutdown()
    tmp.cleanup()
    return best * 1000.0, len(body)


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
    for qs in ("/", "/?flagged=1", "/?q=shelf+3"):
        ms, size = serve_and_time(n, qs)
        print("GET %-14s %8.1f ms  (%d bytes, %d synthetic demo items)" % (qs, ms, size, n))
