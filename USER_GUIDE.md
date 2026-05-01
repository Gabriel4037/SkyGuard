# SkyGuard User Guide

This guide explains how to download, set up, and run SkyGuard from GitHub on a fresh computer.

SkyGuard has two applications:

- `server/`: central admin server for login, users, logs, camera monitoring, model management, and threat policy
- `client/`: detector node for camera input, YOLO drone detection, local logs, clip recording, and sync

## 1. Requirements

Recommended environment:

- Windows 10 or Windows 11
- Python `3.11.9`
- Git
- Internet connection for installing Python packages and downloading the YOLO model
- Camera or video file for detection testing

Python packages are listed in:

```text
requirements.txt
```

## 2. Download the Project from GitHub

Open PowerShell or Command Prompt and run:

```bash
git clone <YOUR_GITHUB_REPOSITORY_URL>
cd SkyGuard
```

If the project was already cloned before, update it with:

```bash
git pull
```

## 3. Create a Python Virtual Environment

From the project root folder:

```bash
python -m venv .venv
```

Activate it on Windows:

```bash
.venv\Scripts\activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

## 4. Download the YOLO Model

The YOLO `.pt` model file is not included in this repository because it is a large external model artifact.

Download the YOLOv11x drone detection weights from:

```text
https://huggingface.co/doguilmak/Drone-Detection-YOLOv11x/tree/main/weight
```

After downloading the `.pt` file, rename it to:

```text
best_v11.pt
```

Place it in:

```text
client/models/best_v11.pt
```

Final expected path:

```text
SkyGuard/client/models/best_v11.pt
```

Important: do not commit the `.pt` model file to GitHub. It is intentionally ignored by `.gitignore`.

## 5. Start the Central Server

From the project root folder, run:

```bash
python server/server_launcher.py
```

The central server runs at:

```text
http://127.0.0.1:5000
```

The launcher opens the admin interface in a desktop webview window.

Default first-run admin account:

```text
Username: admin
Password: admin
```

For a real demonstration, change this password after first login.

## 6. Start the Client Detector

Open a second PowerShell or Command Prompt window.

Go to the same project folder:

```bash
cd SkyGuard
```

Activate the virtual environment:

```bash
.venv\Scripts\activate
```

Start the client:

```bash
python client/client_launcher.py
```

The client detector opens in its own desktop webview window. The local client Flask service uses an available local port automatically.

## 7. Connect Client to Server

On the client login page:

1. Enter the central server address.
2. If server and client are on the same computer, use:

```text
127.0.0.1
```

3. If server and client are on different computers, use the server computer's LAN IP address, for example:

```text
192.168.1.20
```

The client converts this into:

```text
http://192.168.1.20:5000
```

4. Click the connection test button.
5. Log in with a valid central server account.

## 8. Basic Admin Workflow

In the central server application, the admin can:

- View dashboard summary
- Manage users
- Enable or disable client self-registration
- View central detection logs
- View or download uploaded event clips
- Monitor live client cameras
- Upload and activate model releases
- Update threat-policy settings

Useful admin pages:

```text
/
/users.html
/admin_logs.html
/admin_monitor.html
/model_manager.html
```

## 9. Basic Client Workflow

In the client detector application, a user can:

- Log in through the central server
- Register local cameras
- Start live camera detection
- Run video/file-based detection
- Save event clips
- View local logs
- Sync logs and clips to the central server
- Download model updates from the central server

Detection uses:

```text
client/models/best_v11.pt
```

If this file is missing, the client interface can still open, but detection will fail when a frame is processed.

## 10. Model Management

There are two model setup methods:

### Method A: Local Default Model

Place the model at:

```text
client/models/best_v11.pt
```

This allows the client to run detection directly.

### Method B: Central Model Release

An admin can upload a `.pt` model from the central server Model Manager page.

After upload:

1. The server stores the model in `server/models/`.
2. The client checks for model updates.
3. The client downloads the active server model.
4. The client applies the new model when detection is idle.

## 11. Log and Clip Sync

Detection events are first saved locally on the client.

The client then syncs pending logs and clips to the central server in the background. Manual sync can also be triggered from the client interface.

Central logs can be reviewed from:

```text
admin_logs.html
```

## 12. Live Camera Monitoring

Live camera monitoring works through the central admin monitor page.

Process:

1. Client registers and starts a camera.
2. Admin opens the central monitor page.
3. Client detects that an admin monitor is active.
4. Client uploads camera frames to the central server.
5. Admin sees the latest camera frames in the monitor page.

Monitor page:

```text
admin_monitor.html
```

## 13. Runtime Files

SkyGuard creates runtime files while running.

Client runtime files:

```text
client/data/
client/clips/
client/models/
```

Server runtime files:

```text
server/data/
server/clips/
server/models/
```

These are ignored by Git because they contain local databases, clips, settings, and large model files.

## 14. Troubleshooting

### Problem: Detection does not start

Check that the model exists at:

```text
client/models/best_v11.pt
```

### Problem: GitHub does not contain the model

This is expected. Download the model separately from:

```text
https://huggingface.co/doguilmak/Drone-Detection-YOLOv11x/tree/main/weight
```

### Problem: Client cannot connect to server

Check that:

- The central server is running
- The server IP address is correct
- Both computers are on the same LAN
- Firewall allows the server port `5000`

### Problem: Login fails

Check that:

- The username and password are correct
- The central server is running
- The client has the correct central server address

### Problem: Package installation fails

Check that:

- Python `3.11.9` is installed
- The virtual environment is activated
- Internet connection is available
- `pip install -r requirements.txt` was run from the project root

## 15. Model Attribution

SkyGuard uses a YOLOv11x drone detection model based on:

Doguilmak, `Drone-Detection-YOLOv11x`  
https://github.com/doguilmak/Drone-Detection-YOLOv11x

Model weights download page:

```text
https://huggingface.co/doguilmak/Drone-Detection-YOLOv11x/tree/main/weight
```

The original project is released under the MIT License. SkyGuard uses the model for academic demonstration and prototype drone-detection purposes.
