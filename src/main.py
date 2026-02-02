import threading
import time
import socket
import sys
import os

import webview
import drone_detection

def find_free_port():
    s = socket.socket()
    s.bind(('', 0))
    port = s.getsockname()[1]
    s.close()
    return port

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
        drone_detection.load_model()
    except Exception as e:
        print("Warning: load_model failed:", e)
    try:
        drone_detection.init_db_conn()
    except Exception as e:
        print("Warning: init_db_conn failed:", e)


    drone_detection.app.run(host='127.0.0.1', port=port, debug=False, use_reloader=False)

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
    port = find_free_port()
    t = threading.Thread(target=run_server, args=(port,), daemon=True)
    t.start()

    ok = wait_for_port('127.0.0.1', port, timeout=10.0)
    if not ok:
        print("Server did not start in time. Check logs.")
        sys.exit(1)

    url = f'http://127.0.0.1:{port}/login.html'

    window = webview.create_window('Drone Detector', url, js_api=Api())
    webview.start(read_cookies, window, private_mode=False, http_server=True, http_port=port)