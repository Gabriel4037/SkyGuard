import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path


# Optional Windows setup helper. It installs pywebview plus the native runtimes
# needed for the desktop launchers when a clean machine is missing them.
WEBVIEW2_BOOTSTRAPPER_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
VC_REDIST_X64_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
VC_REDIST_X86_URL = "https://aka.ms/vs/17/release/vc_redist.x86.exe"


def run(cmd, check=True):
    """Run a command and show it before execution."""
    print(f"\n> {' '.join(cmd)}")
    return subprocess.run(cmd, check=check)


def run_capture(cmd):
    """Run a command and capture output for troubleshooting."""
    print(f"\n> {' '.join(cmd)}")
    return subprocess.run(cmd, check=False, capture_output=True, text=True)


def is_uv_managed_pip_error(result):
    """Detect pip errors caused by uv-managed Python environments."""
    text = f"{result.stdout}\n{result.stderr}".lower()
    return "externally-managed-environment" in text or "managed by uv" in text


def install_with_uv(package_name):
    """Install a Python package through uv when normal pip is blocked."""
    uv = shutil.which("uv")
    if not uv:
        print("\n`uv` is not available in PATH, so the uv fallback cannot be used.")
        return False

    result = run_capture([uv, "pip", "install", "--python", sys.executable, package_name])
    if result.returncode == 0:
        print(f"\nInstalled {package_name} with uv successfully.")
        return True

    print(f"\nuv install failed for {package_name}.")
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)
    return False


def install_python_package():
    """
    The import name is `webview`, but the pip package is `pywebview`.
    """
    pip_install = run_capture([sys.executable, "-m", "pip", "install", "--upgrade", "pywebview"])
    if pip_install.returncode == 0:
        print("\nInstalled pywebview with pip successfully.")
        return True

    if is_uv_managed_pip_error(pip_install):
        print("\nDetected a uv-managed Python environment. Trying `uv pip install` instead...")
        return install_with_uv("pywebview")

    print("\npip install failed for pywebview.")
    if pip_install.stdout:
        print(pip_install.stdout)
    if pip_install.stderr:
        print(pip_install.stderr)
    return False


def import_check():
    """Confirm that pywebview can be imported by this Python interpreter."""
    try:
        import webview  # noqa: F401

        print("\nPython package check: OK (`import webview` works)")
        return True
    except Exception as exc:
        print(f"\nPython package check failed: {exc}")
        return False


def detect_webview2_runtime():
    """Check whether Microsoft Edge WebView2 Runtime is installed."""
    if platform.system() != "Windows":
        return True

    candidates = [
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft" / "EdgeWebView" / "Application",
        Path(os.environ.get("ProgramFiles", "")) / "Microsoft" / "EdgeWebView" / "Application",
    ]
    for base in candidates:
        if base.exists() and any(base.iterdir()):
            return True
    return False


def detect_vc_redist():
    """Check whether the Microsoft Visual C++ runtime is installed."""
    if platform.system() != "Windows":
        return True

    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    candidates = [
        system_root / "System32" / "vcruntime140.dll",
        system_root / "System32" / "msvcp140.dll",
        system_root / "SysWOW64" / "vcruntime140.dll",
        system_root / "SysWOW64" / "msvcp140.dll",
    ]
    return any(path.exists() for path in candidates)


def install_webview2_with_winget():
    """Try installing WebView2 through Windows Package Manager."""
    winget = shutil.which("winget")
    if not winget:
        return False

    try:
        run(
            [
                winget,
                "install",
                "--exact",
                "--id",
                "Microsoft.EdgeWebView2Runtime",
                "--accept-package-agreements",
                "--accept-source-agreements",
            ]
        )
        return True
    except Exception as exc:
        print(f"\nwinget install failed: {exc}")
        return False


def install_webview2_with_bootstrapper():
    """Download and run Microsoft's WebView2 bootstrapper."""
    tmp_dir = Path(tempfile.gettempdir())
    installer = tmp_dir / "MicrosoftEdgeWebView2Setup.exe"

    try:
        print(f"\nDownloading WebView2 runtime bootstrapper to {installer}")
        urllib.request.urlretrieve(WEBVIEW2_BOOTSTRAPPER_URL, installer)
        run([str(installer), "/silent", "/install"])
        return True
    except Exception as exc:
        print(f"\nBootstrapper install failed: {exc}")
        return False


def install_vc_redist_with_winget():
    """Try installing Visual C++ Redistributable through winget."""
    winget = shutil.which("winget")
    if not winget:
        return False

    package_ids = [
        "Microsoft.VCRedist.2015+.x64",
        "Microsoft.VCRedist.2015+.x86",
    ]

    ok = True
    for package_id in package_ids:
        try:
            run(
                [
                    winget,
                    "install",
                    "--exact",
                    "--id",
                    package_id,
                    "--accept-package-agreements",
                    "--accept-source-agreements",
                ]
            )
        except Exception as exc:
            print(f"\nwinget VC++ install failed for {package_id}: {exc}")
            ok = False
    return ok


def install_vc_redist_with_bootstrapper():
    """Download and run the Visual C++ Redistributable installers."""
    tmp_dir = Path(tempfile.gettempdir())
    installers = [
        (VC_REDIST_X64_URL, tmp_dir / "vc_redist.x64.exe"),
        (VC_REDIST_X86_URL, tmp_dir / "vc_redist.x86.exe"),
    ]

    ok = True
    for url, installer in installers:
        try:
            print(f"\nDownloading VC++ runtime installer to {installer}")
            urllib.request.urlretrieve(url, installer)
            run([str(installer), "/install", "/quiet", "/norestart"])
        except Exception as exc:
            print(f"\nVC++ bootstrapper install failed for {installer.name}: {exc}")
            ok = False
    return ok


def install_windows_runtime():
    """Install WebView2 on Windows if it is missing."""
    if platform.system() != "Windows":
        print("\nNon-Windows OS detected: no WebView2 runtime installation needed.")
        return True

    if detect_webview2_runtime():
        print("\nWindows WebView2 runtime already appears to be installed.")
        return True

    print("\nWindows WebView2 runtime not found. Trying winget first...")
    if install_webview2_with_winget():
        return True

    print("\nTrying Microsoft bootstrapper fallback...")
    return install_webview2_with_bootstrapper()


def install_windows_cpp_runtime():
    """Install Visual C++ runtime libraries on Windows if missing."""
    if platform.system() != "Windows":
        print("\nNon-Windows OS detected: no VC++ runtime installation needed.")
        return True

    if detect_vc_redist():
        print("\nMicrosoft Visual C++ runtime already appears to be installed.")
        return True

    print("\nMicrosoft Visual C++ runtime not found. Trying winget first...")
    if install_vc_redist_with_winget():
        return True

    print("\nTrying Microsoft VC++ bootstrapper fallback...")
    return install_vc_redist_with_bootstrapper()


def main():
    """Run all setup checks required by the desktop webview launchers."""
    print("Installing support for `import webview`...")
    print(f"Python: {sys.executable}")
    print(f"Platform: {platform.platform()}")
    print(f"Python version: {platform.python_version()}")

    if sys.version_info >= (3, 14):
        print("\nWarning: Python 3.14+ may have weaker compatibility with pywebview and other native GUI packages.")
        print("Recommendation: prefer Python 3.11 or 3.12 if installation fails.")

    python_install_ok = install_python_package()
    python_ok = python_install_ok and import_check()

    webview_runtime_ok = install_windows_runtime()
    cpp_runtime_ok = install_windows_cpp_runtime()

    if platform.system() == "Windows":
        webview_runtime_ok = webview_runtime_ok and detect_webview2_runtime()
        cpp_runtime_ok = cpp_runtime_ok and detect_vc_redist()

    print("\nSummary")
    print(f"- Python package installed: {'yes' if python_ok else 'no'}")
    print(f"- WebView2 runtime ready: {'yes' if webview_runtime_ok else 'no'}")
    print(f"- VC++ runtime ready: {'yes' if cpp_runtime_ok else 'no'}")

    if not python_ok or not webview_runtime_ok or not cpp_runtime_ok:
        print("\nSome dependencies may still be missing.")
        if not python_ok:
            print("\nPython package troubleshooting:")
            print("- Confirm `uv` is installed: uv --version")
            print(f"- Try manually: uv pip install --python {sys.executable} pywebview")
            print(f"- Test import: {sys.executable} -c \"import webview; print(webview.__file__)\"")
            if sys.version_info >= (3, 14):
                print("- If this still fails, switch to Python 3.11 or 3.12 for better pywebview compatibility")
        print("If this is a locked-down machine, try:")
        print("1. Run this script as Administrator")
        print("2. If using uv Python, run: uv pip install --python <your-python> pywebview")
        print("3. Install Microsoft Edge WebView2 Runtime manually")
        print("4. Install Microsoft Visual C++ 2015-2022 Redistributable manually")
        print("5. Use the browser fallback instead of embedded webview")
        sys.exit(1)

    print("\nDone. `import webview` should now be available.")


if __name__ == "__main__":
    main()
