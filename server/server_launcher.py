import threading
import time
import socket
import sys
import os

import webview

import central_server

DEFAULT_CENTRAL_PORT = int(os.environ.get("CENTRAL_SERVER_PORT", "5000"))

def wait_for_port(host, port, timeout=5.0):
    """Wait until the server is accepting connections, return True if ok."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except Exception:
            time.sleep(0.1)
    return False

def run_server(port):
    try:
        central_server.init_server_state()
    except Exception as e:
        print("Warning: init_server_state failed:", e)

    central_server.app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

def read_cookies(window):
    try:
        cookies = window.get_cookies()  
        print("=== webview cookies ===")
        for c in cookies:
            try:
                print(c.output())
            except Exception:
                print(c)
        print("=======================")
    except Exception as e:
        print("read_cookies error:", e)

class Api:
    def clearCookies(self):
        try:
            webview.windows[0].clear_cookies()
            return True
        except Exception as e:
            print("clear_cookies error:", e)
            return False

if __name__ == "__main__":
    port = DEFAULT_CENTRAL_PORT
    t = threading.Thread(target=run_server, args=(port,), daemon=True)
    t.start()

    ok = wait_for_port('127.0.0.1', port, timeout=10.0)
    if not ok:
        print("Server did not start in time. Check logs.")
        sys.exit(1)

    url = f'http://127.0.0.1:{port}/login.html'

    window = webview.create_window('Drone Detector', url, js_api=Api())
    webview.start(read_cookies, window, private_mode=False, http_server=False)
