import socket
import sys
import threading
import time

import webview

import client_app


def find_free_port():
    sock = socket.socket()
    sock.bind(("", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def wait_for_port(host, port, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except Exception:
            time.sleep(0.1)
    return False


def run_server(port):
    client_app.run_detector_node(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    port = find_free_port()
    thread = threading.Thread(target=run_server, args=(port,), daemon=True)
    thread.start()

    if not wait_for_port("127.0.0.1", port, timeout=10.0):
        print("Detector node did not start in time.")
        sys.exit(1)

    url = f"http://127.0.0.1:{port}/login.html"
    window = webview.create_window("Drone Detector Node", url)
    webview.start(private_mode=False, http_server=False)
