import json
import logging
import os
import socket
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_APP_DATA = Path(os.environ.get("APPDATA", str(Path.home() / ".config"))) / "DivyaDrishti"
_APP_DATA.mkdir(parents=True, exist_ok=True)
PORT_FILE = str(_APP_DATA / "current_port.json")


def _log_configure():
    log_path = _APP_DATA / "helper.log"
    handler = logging.FileHandler(str(log_path), mode="a", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(handler)
    # Also log to console via stream handler if not already present
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        root.addHandler(ch)


def _wait_for_port(port: int, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(("127.0.0.1", port))
            s.close()
            return
        except OSError:
            time.sleep(0.5)
    raise TimeoutError(f"Port {port} not free within {timeout}s")


def _write_port(port: int) -> None:
    tmp = PORT_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"port": port, "pid": os.getpid()}, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, PORT_FILE)


def _pid_on_port(port: int) -> int | None:
    """Return the PID holding the given TCP port, or None."""
    if sys.platform == "win32":
        import subprocess
        try:
            out = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True, text=True, timeout=5,
            ).stdout
            for line in out.splitlines():
                line = line.strip()
                if not line or line.startswith("Proto"):
                    continue
                parts = line.split()
                if len(parts) >= 4:
                    # Local address is typically column 1
                    for p in parts:
                        if ":" in p and p.endswith(f":{port}"):
                            try:
                                pid = int(parts[-1])
                                if pid != 0:
                                    return pid
                            except (ValueError, IndexError):
                                pass
        except Exception:
            return None
    else:
        try:
            import psutil
            for conn in psutil.net_connections():
                if conn.laddr.port == port and conn.status == "LISTEN":
                    return conn.pid
        except Exception:
            pass
    return None


def _kill_stale_server(port: int) -> None:
    """Kill any process holding the given port."""
    pid = _pid_on_port(port)
    if not pid:
        return
    logger.info("Killing PID %d on port %d", pid, port)
    if sys.platform == "win32":
        import subprocess
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            capture_output=True, timeout=5,
        )
    else:
        try:
            import signal
            os.kill(pid, signal.SIGTERM)
            time.sleep(2)
            if _pid_on_port(port) == pid:
                os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def _acquire_port(preferred: int = 8000) -> int:
    """Kill any stale process on preferred port and bind to it."""
    _kill_stale_server(preferred)
    _wait_for_port(preferred)
    _write_port(preferred)
    logger.info("Acquired port %d", preferred)
    return preferred


if __name__ == "__main__":
    _log_configure()

    preferred = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    logger.info("Starting helper: preferred=%d", preferred)

    actual = _acquire_port(preferred)
    logger.info("Starting uvicorn on port %d", actual)

    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=actual, log_level="info")
