"""Service manager for deepresearch — installs systemd/launchd/NSSM services."""

from __future__ import annotations
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

SERVICE_NAME = "deepresearch"


def _detect_platform() -> str:
    if sys.platform == "linux":
        return "linux"
    elif sys.platform == "darwin":
        return "macos"
    elif sys.platform == "win32":
        return "windows"
    return sys.platform


def _get_installation_paths() -> dict[str, Path]:
    """Detect the deepresearch installation paths."""
    venv_dir = Path(sys.prefix)
    # If we're in a venv, use that; otherwise assume system install
    if hasattr(sys, "real_prefix") or (
        hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix
    ):
        python_bin = venv_dir / "bin" / "python"
        # service_manager.py is at src/deepresearch/service_manager.py
        project_dir = Path(__file__).resolve().parent.parent.parent
    else:
        python_bin = Path(sys.executable)
        project_dir = Path(__file__).resolve().parent.parent.parent

    return {
        "venv": venv_dir,
        "python": python_bin,
        "project": project_dir,
    }


def cmd_install() -> int:
    """Install deepresearch as a system service."""
    platform = _detect_platform()
    paths = _get_installation_paths()

    print(f"Installing {SERVICE_NAME} service for {platform}...")
    print(f"  Python: {paths['python']}")
    print(f"  Project: {paths['project']}")

    if platform == "linux":
        return _install_systemd(paths)
    elif platform == "macos":
        return _install_launchd(paths)
    elif platform == "windows":
        return _install_nssm(paths)
    else:
        print(f"Unsupported platform: {platform}")
        return 1


def cmd_uninstall() -> int:
    """Remove the deepresearch service."""
    platform = _detect_platform()
    print(f"Uninstalling {SERVICE_NAME} service...")

    if platform == "linux":
        return _uninstall_systemd()
    elif platform == "macos":
        return _uninstall_launchd()
    elif platform == "windows":
        return _uninstall_nssm()
    else:
        print(f"Unsupported platform: {platform}")
        return 1


def cmd_status() -> int:
    """Show service status."""
    platform = _detect_platform()

    if platform == "linux":
        return _run_cmd(["systemctl", "status", SERVICE_NAME], sudo=True)
    elif platform == "macos":
        return _run_cmd(["launchctl", "list", f"dk.kiffnet.{SERVICE_NAME}"])
    elif platform == "windows":
        return _run_cmd(["sc", "query", SERVICE_NAME])
    else:
        print(f"Unsupported platform: {platform}")
        return 1


def cmd_start() -> int:
    """Start the service."""
    platform = _detect_platform()

    if platform == "linux":
        return _run_cmd(["systemctl", "start", SERVICE_NAME], sudo=True)
    elif platform == "macos":
        return _run_cmd(["launchctl", "start", f"dk.kiffnet.{SERVICE_NAME}"])
    elif platform == "windows":
        return _run_cmd(["net", "start", SERVICE_NAME])
    else:
        print(f"Unsupported platform: {platform}")
        return 1


def cmd_stop() -> int:
    """Stop the service."""
    platform = _detect_platform()

    if platform == "linux":
        return _run_cmd(["systemctl", "stop", SERVICE_NAME], sudo=True)
    elif platform == "macos":
        return _run_cmd(["launchctl", "stop", f"dk.kiffnet.{SERVICE_NAME}"])
    elif platform == "windows":
        return _run_cmd(["net", "stop", SERVICE_NAME])
    else:
        print(f"Unsupported platform: {platform}")
        return 1


def cmd_restart() -> int:
    """Restart the service."""
    platform = _detect_platform()

    if platform == "linux":
        return _run_cmd(["systemctl", "restart", SERVICE_NAME], sudo=True)
    elif platform == "macos":
        _run_cmd(["launchctl", "stop", f"dk.kiffnet.{SERVICE_NAME}"])
        return _run_cmd(["launchctl", "start", f"dk.kiffnet.{SERVICE_NAME}"])
    elif platform == "windows":
        return _run_cmd(["net", "stop", SERVICE_NAME]) or _run_cmd(
            ["net", "start", SERVICE_NAME]
        )
    else:
        print(f"Unsupported platform: {platform}")
        return 1


def cmd_logs() -> int:
    """Show service logs."""
    platform = _detect_platform()

    if platform == "linux":
        return _run_cmd(["journalctl", "-u", SERVICE_NAME, "-f", "-n", "50"], sudo=True)
    elif platform == "macos":
        log_path = Path.home() / "Library" / "Logs" / f"{SERVICE_NAME}.log"
        return _run_cmd(["tail", "-f", "-n", "50", str(log_path)])
    elif platform == "windows":
        return _run_cmd(
            ["wevtutil", "qe", f"{SERVICE_NAME}", "/c:50", "/rd:true", "/f:text"]
        )
    else:
        print(f"Unsupported platform: {platform}")
        return 1


# ── Platform-specific installers ───────────────────────────────────

SYSTEMD_SERVICE_TEMPLATE = """[Unit]
Description=DeepeResearch Multi-Agent Research System
Documentation=https://github.com/Acharnite/deepresearch
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=simple
User={user}
Group={group}
WorkingDirectory={project_dir}
Environment=PATH={venv_dir}/bin:/usr/local/bin:/usr/bin:/bin
ExecStart={python_bin} -m deepresearch serve --host 0.0.0.0 --port {port}
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
KillMode=mixed

# Security hardening
NoNewPrivileges=true
ProtectSystem=full
PrivateTmp=true

[Install]
WantedBy=multi-user.target
"""


def _install_systemd(paths: dict) -> int:
    """Create and enable systemd service."""
    service_path = Path("/etc/systemd/system") / f"{SERVICE_NAME}.service"

    # Get user/group
    user = os.environ.get("SUDO_USER") or os.environ.get("USER", "nobody")
    import grp

    try:
        group = grp.getgrgid(os.getgid()).gr_name
    except Exception:
        group = user

    service_content = SYSTEMD_SERVICE_TEMPLATE.format(
        user=user,
        group=group,
        project_dir=paths["project"],
        venv_dir=paths["venv"],
        python_bin=paths["python"],
        port=os.environ.get("DEEPRESEARCH_PORT", "7500"),
    )

    try:
        service_path.write_text(service_content)
        print(f"  Service file created: {service_path}")
    except PermissionError:
        print(f"  Need sudo: could not write to {service_path}")
        print("  Run: sudo mkdir -p /etc/systemd/system")
        print(f"  Run: sudo tee {service_path} << 'EOF'")
        print(service_content)
        print("EOF")
        return 1

    # Enable and start
    ret = _run_cmd(["systemctl", "daemon-reload"], sudo=True)
    ret |= _run_cmd(["systemctl", "enable", SERVICE_NAME], sudo=True)
    ret |= _run_cmd(["systemctl", "start", SERVICE_NAME], sudo=True)

    if ret == 0:
        print(f"  Service {SERVICE_NAME} installed and started!")
        _run_cmd(["systemctl", "status", SERVICE_NAME, "--no-pager"], sudo=True)

    return ret


def _uninstall_systemd() -> int:
    """Stop, disable, and remove systemd service."""
    _run_cmd(["systemctl", "stop", SERVICE_NAME], sudo=True)
    _run_cmd(["systemctl", "disable", SERVICE_NAME], sudo=True)
    service_path = Path("/etc/systemd/system") / f"{SERVICE_NAME}.service"
    if service_path.exists():
        service_path.unlink()
        print(f"  Removed: {service_path}")
    _run_cmd(["systemctl", "daemon-reload"], sudo=True)
    return 0


def _install_launchd(paths: dict) -> int:
    """Create and load launchd plist (macOS)."""
    plist_path = (
        Path.home() / "Library" / "LaunchAgents" / f"dk.kiffnet.{SERVICE_NAME}.plist"
    )
    plist_path.parent.mkdir(parents=True, exist_ok=True)

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>dk.kiffnet.{SERVICE_NAME}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{paths["python"]}</string>
        <string>-m</string>
        <string>deepresearch</string>
        <string>serve</string>
        <string>--host</string>
        <string>0.0.0.0</string>
        <string>--port</string>
        <string>{os.environ.get("DEEPRESEARCH_PORT", "7500")}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{paths["project"]}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{Path.home() / "Library" / "Logs" / f"{SERVICE_NAME}.log"}</string>
    <key>StandardErrorPath</key>
    <string>{Path.home() / "Library" / "Logs" / f"{SERVICE_NAME}.log"}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>{paths["venv"]}/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>"""

    plist_path.write_text(plist_content)
    print(f"  Plist created: {plist_path}")
    return _run_cmd(["launchctl", "load", str(plist_path)])


def _uninstall_launchd() -> int:
    plist_path = (
        Path.home() / "Library" / "LaunchAgents" / f"dk.kiffnet.{SERVICE_NAME}.plist"
    )
    _run_cmd(["launchctl", "unload", str(plist_path)])
    if plist_path.exists():
        plist_path.unlink()
    return 0


def _install_nssm(paths: dict) -> int:
    """Install as Windows service using NSSM."""
    print("  Windows NSSM installation requires nssm.exe")
    print(f"  nssm install {SERVICE_NAME} {paths['python']} -m deepresearch serve")
    print(f"  nssm start {SERVICE_NAME}")
    return 1  # Manual for now


def _uninstall_nssm() -> int:
    print(f"  nssm stop {SERVICE_NAME}")
    print(f"  nssm remove {SERVICE_NAME} confirm")
    return 1


# ── Helpers ──────────────────────────────────────────────────────


def _run_cmd(cmd: list[str], sudo: bool = False) -> int:
    """Run a command, optionally with sudo."""
    import subprocess

    if sudo and os.geteuid() != 0:
        cmd = ["sudo"] + cmd
    try:
        return subprocess.call(cmd)
    except FileNotFoundError:
        print(f"  Command not found: {cmd[0]}")
        return 1
    except Exception as e:
        print(f"  Error running {' '.join(cmd)}: {e}")
        return 1
