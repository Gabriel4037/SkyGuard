import socket
import os
import sys
import threading
import time

import webview

import central_server

DEFAULT_CENTRAL_PORT = int(os.environ.get("CENTRAL_SERVER_PORT", "5000"))


def wait_for_port(host, port, timeout=5.0):
    """Wait until the Flask server is ready before opening the webview."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except Exception:
            time.sleep(0.1)
    return False


def run_server(port):
    """Start the central Flask app in a background thread for the desktop window."""
    try:
        central_server.init_server_state()
    except Exception as e:
        print("Warning: init_server_state failed:", e)

    central_server.app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    # The launcher starts Flask first, then opens the local admin login page
    # inside pywebview to make the prototype feel like a desktop application.
    port = DEFAULT_CENTRAL_PORT
    t = threading.Thread(target=run_server, args=(port,), daemon=True)
    t.start()

    ok = wait_for_port("127.0.0.1", port, timeout=30.0)
    if not ok:
        print("Server did not start in time. Check logs.")
        sys.exit(1)

    url = f"http://127.0.0.1:{port}/login.html"

    webview.create_window("Drone Detector", url)
    webview.start(private_mode=False, http_server=False)
