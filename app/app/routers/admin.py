import datetime
import logging
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, status

from app.auth.deps import require_admin_local
from app.database import execute, hash_password, query
from app.services.telegram_notifier import send_telegram_sync

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])

_ACTION_OUTPUT: dict[str, dict] = {}
_ACTION_OUTPUT_MAX_ENTRIES = 100
_ACTION_OUTPUT_MAX_LINES_PER_ENTRY = 5000
_BACKUP_RUNNING = False
_BACKUP_RUNNING_LOCK = threading.Lock()


def _trim_action_output():
    if len(_ACTION_OUTPUT) > _ACTION_OUTPUT_MAX_ENTRIES:
        # Remove oldest completed entries
        completed = [(k, v) for k, v in _ACTION_OUTPUT.items() if v.get("done")]
        to_remove = len(_ACTION_OUTPUT) - _ACTION_OUTPUT_MAX_ENTRIES
        for k, _ in sorted(completed, key=lambda x: x[1].get("timestamp", ""))[:to_remove]:
            _ACTION_OUTPUT.pop(k, None)


def _truncate_lines(lines: list[str]) -> list[str]:
    if len(lines) > _ACTION_OUTPUT_MAX_LINES_PER_ENTRY:
        lines = lines[-_ACTION_OUTPUT_MAX_LINES_PER_ENTRY:]
    return lines

def _run_action_worker(action_id: str, script: str):
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        proc = subprocess.Popen(
            script, shell=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            startupinfo=si,
            text=True, bufsize=1,
        )
        lines: list[str] = []
        for raw in iter(proc.stdout.readline, ""):
            line = raw.rstrip("\r\n")
            lines.append(line)
            _ACTION_OUTPUT[action_id] = {"lines": _truncate_lines(lines.copy()), "done": False}
            _trim_action_output()
        proc.wait()
        script_name = Path(script).stem
        lines = _truncate_lines(lines)
        _ACTION_OUTPUT[action_id] = {"lines": lines.copy(), "done": True, "returncode": proc.returncode, "timestamp": datetime.datetime.utcnow().isoformat()}
        _trim_action_output()
        if proc.returncode == 0:
            send_telegram_sync(f"<b>Action Complete</b>\n{script_name}\nExit code: {proc.returncode}")
    except Exception as e:
        _ACTION_OUTPUT[action_id] = {"lines": [f"Error: {e}"], "done": True, "returncode": -1, "timestamp": datetime.datetime.utcnow().isoformat()}

# Dynamic paths derived from script location (works for both dev and live)
_PROJECT_ROOT = Path(__file__).parent.parent.parent
_PROJECT_PARENT = _PROJECT_ROOT.parent
HOME_DIR = Path(os.environ.get("USERPROFILE", str(Path.home())))
LIVE_DIR = Path("D:/DivyaDrishti/DDTools-live")
DEV_DIR = _PROJECT_ROOT
LOG_DIR = _PROJECT_PARENT / "logs"
BACKUP_DIR = _PROJECT_PARENT / "backups" / "daily"
BACKUP_LIVE_DIR = _PROJECT_PARENT / "backups" / "DD-live"
BACKUP_DEV_DIR = _PROJECT_PARENT / "backups" / "DD-dev"
SERVICE_DIR = _PROJECT_ROOT / "services"


def _run_cmd(cmd: list[str], cwd=None) -> tuple[str, str, int]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=cwd, shell=True)
        return p.stdout or "", p.stderr or "", p.returncode
    except Exception as e:
        return "", str(e), 1


def _calculate_next_run(frequency: str, scheduled_time: str, day_of_week=None, day_of_month=None) -> str | None:
    if frequency == 'manual':
        return None
    now = datetime.datetime.now()
    try:
        parts = scheduled_time.split(':')
        hour, minute = int(parts[0]), int(parts[1])
    except Exception:
        hour, minute = 2, 0
    if frequency == 'daily':
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += datetime.timedelta(days=1)
        return candidate.strftime('%Y-%m-%d %H:%M')
    elif frequency == 'weekly':
        if day_of_week is None:
            return None
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        for d in range(8):
            test = candidate + datetime.timedelta(days=d)
            if test.weekday() == day_of_week and test > now:
                return test.strftime('%Y-%m-%d %H:%M')
        return None
    elif frequency == 'monthly':
        if day_of_month is None:
            return None
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        for m in range(13):
            test = candidate.replace(day=min(day_of_month, 28))
            test += datetime.timedelta(days=m * 30)
            if test.day != day_of_month and day_of_month <= 28:
                pass
            if test > now:
                return test.strftime('%Y-%m-%d %H:%M')
        return None
    return None


def _update_schedule_next_run(schedule_id: int):
    rows = query("SELECT * FROM backup_schedules WHERE id = ?", (schedule_id,))
    if not rows:
        return
    s = rows[0]
    next_run = _calculate_next_run(s['frequency'], s['scheduled_time'], s['day_of_week'], s['day_of_month'])
    execute("UPDATE backup_schedules SET next_run = ? WHERE id = ?", (next_run, schedule_id))


def _recalculate_all_schedules():
    rows = query("SELECT * FROM backup_schedules WHERE enabled = 1")
    for r in rows:
        _update_schedule_next_run(r['id'])


def _find_pm2() -> str:
    try:
        r = subprocess.run(["where", "pm2"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip().splitlines()[0]
    except Exception:
        pass
    return "pm2"

def _run_backup_worker(action_id: str, label: str, src: str, backup_dir: Path, process_name: str, prefix: str, gdrive_folder: str, destination: str = 'both'):
    import shutil
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        lines: list[str] = []
        def emit(msg: str):
            lines.append(msg)
            _ACTION_OUTPUT[action_id] = {"lines": _truncate_lines(lines.copy()), "done": False}
            _trim_action_output()

        pm2 = _find_pm2()

        emit(f"=== {label} Backup ===")
        emit("")

        emit(f"Step 1: Stopping {process_name}...")
        p = subprocess.run(
            ["cmd", "/c", pm2, "stop", process_name],
            capture_output=True, text=True, timeout=15, startupinfo=si,
        )
        for l in p.stdout.splitlines():
            emit(l)
        emit("")

        emit("Step 2: Creating timestamp...")
        p = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Date -Format yyyy-MM-dd_HH-mm-ss"],
            capture_output=True, text=True, timeout=10, startupinfo=si,
        )
        timestamp = p.stdout.strip()
        emit(f"Timestamp: {timestamp}")
        emit("")

        backup_dir.mkdir(parents=True, exist_ok=True)
        temp_dir = backup_dir / "backup-temp"

        emit("Step 3: Copying files to temp...")
        if temp_dir.exists():
            shutil.rmtree(str(temp_dir))
        rc = subprocess.run(
            ["cmd", "/c", "robocopy", src, str(temp_dir), "/MIR", "/XF", "nul", "/NFL", "/NDL", "/NJH", "/NJS", "/NP"],
            capture_output=True, text=True, timeout=120, startupinfo=si,
        )
        for l in rc.stdout.splitlines():
            if l.strip():
                emit(l)
        emit("")

        emit("Step 4: Creating zip archive...")
        zip_name = f"{prefix}-{timestamp}.zip"
        zip_path = backup_dir / zip_name
        p = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Compress-Archive -Path '{temp_dir}\\*' -DestinationPath '{zip_path}' -Force"],
            capture_output=True, text=True, timeout=120, startupinfo=si,
        )
        emit(f"Created: {zip_name}")
        emit("")

        emit("Step 5: Cleaning temp directory...")
        if temp_dir.exists():
            shutil.rmtree(str(temp_dir))
        emit("Done")
        emit("")

        if destination in ('drive', 'both'):
            emit("Step 6: Uploading to Google Drive...")
            rclone = f'"{HOME_DIR}\\rclone\\rclone.exe"'
            p = subprocess.run(
                ["cmd", "/c", f"{rclone} copy \"{zip_path}\" \"gdrive:/{gdrive_folder}/\""],
                capture_output=True, text=True, timeout=180, startupinfo=si,
            )
            for l in p.stdout.splitlines():
                if l.strip():
                    emit(l)
            for l in p.stderr.splitlines():
                if l.strip():
                    emit(l)
            emit("")
        else:
            emit("Step 6: Skipping Google Drive upload (local-only backup)")
            emit("")

        emit(f"Step 7: Starting {process_name}...")
        p = subprocess.run(
            ["cmd", "/c", pm2, "start", process_name],
            capture_output=True, text=True, timeout=15, startupinfo=si,
        )
        for l in p.stdout.splitlines():
            emit(l)
        emit("")

        emit("Step 8: Saving PM2 state...")
        p = subprocess.run(
            ["cmd", "/c", pm2, "save"],
            capture_output=True, text=True, timeout=15, startupinfo=si,
        )
        for l in p.stdout.splitlines():
            emit(l)
        emit("")

        emit("Step 9: Cleaning old backups (>30 days)...")
        cutoff = datetime.datetime.now() - datetime.timedelta(days=30)
        for f in backup_dir.glob(f"{prefix}-*.zip"):
            try:
                if datetime.datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                    f.unlink()
                    emit(f"  Deleted: {f.name}")
            except Exception:
                pass
        emit("")

        emit(f"=== {label} Backup Complete ===")
        _ACTION_OUTPUT[action_id] = {"lines": _truncate_lines(lines.copy()), "done": True, "returncode": 0, "timestamp": datetime.datetime.utcnow().isoformat()}
        send_telegram_sync(f"<b>Backup Complete</b>\n{label}\n{zip_name}")
    except subprocess.TimeoutExpired as e:
        emit(f"TIMEOUT: {e}")
        _ACTION_OUTPUT[action_id] = {"lines": _truncate_lines(lines.copy()), "done": True, "returncode": -1, "timestamp": datetime.datetime.utcnow().isoformat()}
    except Exception as e:
        emit(f"Error: {e}")
        _ACTION_OUTPUT[action_id] = {"lines": _truncate_lines(lines.copy()), "done": True, "returncode": -1, "timestamp": datetime.datetime.utcnow().isoformat()}


def _run_restore_worker(action_id: str, source: str, backup_path_str: str):
    import shutil
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        lines: list[str] = []
        def emit(msg: str):
            lines.append(msg)
            _ACTION_OUTPUT[action_id] = {"lines": _truncate_lines(lines.copy()), "done": False}
            _trim_action_output()

        pm2 = _find_pm2()
        backup_path = Path(backup_path_str)
        filename = backup_path.name

        if source == "live":
            src_path = str(LIVE_DIR)
            process_name = "divyadrishti-live"
            label = "Live"
        else:
            src_path = str(DEV_DIR)
            process_name = "divyadrishti-dev"
            label = "Dev"

        emit(f"=== Restore {label}: {filename} ===")
        emit("")

        emit(f"Step 1: Stopping {process_name}...")
        p = subprocess.run(
            ["cmd", "/c", pm2, "stop", process_name],
            capture_output=True, text=True, timeout=15, startupinfo=si,
        )
        for l in p.stdout.splitlines():
            if l.strip():
                emit(l)
        emit("")

        emit("Step 2: Extracting backup to project directory...")
        emit(f"  Source: {filename}")
        emit(f"  Target: {src_path}")
        p = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Expand-Archive -Path '{backup_path}' -DestinationPath '{src_path}' -Force"],
            capture_output=True, text=True, timeout=180, startupinfo=si,
        )
        for l in p.stdout.splitlines():
            if l.strip():
                emit(l)
        for l in p.stderr.splitlines():
            if l.strip():
                emit(l)
        emit("Extraction complete")
        emit("")

        emit("Step 3: Installing dependencies...")
        p = subprocess.run(
            ["cmd", "/c", f"pip install -r \"{src_path}/requirements.txt\""],
            capture_output=True, text=True, timeout=120, startupinfo=si,
        )
        for l in p.stdout.splitlines():
            if l.strip():
                emit(l)
        emit("")

        emit(f"Step 4: Starting {process_name}...")
        p = subprocess.run(
            ["cmd", "/c", pm2, "start", process_name],
            capture_output=True, text=True, timeout=15, startupinfo=si,
        )
        for l in p.stdout.splitlines():
            if l.strip():
                emit(l)
        emit("")

        emit("Step 5: Saving PM2 state...")
        p = subprocess.run(
            ["cmd", "/c", pm2, "save"],
            capture_output=True, text=True, timeout=15, startupinfo=si,
        )
        for l in p.stdout.splitlines():
            if l.strip():
                emit(l)
        emit("")

        emit(f"=== Restore Complete: {filename} ===")
        _ACTION_OUTPUT[action_id] = {"lines": _truncate_lines(lines.copy()), "done": True, "returncode": 0, "timestamp": datetime.datetime.utcnow().isoformat()}
        send_telegram_sync(f"<b>Restore Complete</b>\n{label}: {filename}")
    except subprocess.TimeoutExpired as e:
        emit(f"TIMEOUT: {e}")
        _ACTION_OUTPUT[action_id] = {"lines": _truncate_lines(lines.copy()), "done": True, "returncode": -1, "timestamp": datetime.datetime.utcnow().isoformat()}
    except Exception as e:
        emit(f"Error: {e}")
        _ACTION_OUTPUT[action_id] = {"lines": _truncate_lines(lines.copy()), "done": True, "returncode": -1, "timestamp": datetime.datetime.utcnow().isoformat()}


def _run_backup_live_worker(action_id: str, destination: str = 'both'):
    _run_backup_worker(action_id, "Live",
        src=str(LIVE_DIR),
        backup_dir=BACKUP_LIVE_DIR,
        process_name="divyadrishti-live",
        prefix="DDlive",
        gdrive_folder="DD-live backups",
        destination=destination)


def _run_backup_dev_worker(action_id: str, destination: str = 'both'):
    _run_backup_worker(action_id, "Dev",
        src=str(DEV_DIR),
        backup_dir=BACKUP_DEV_DIR,
        process_name="divyadrishti-dev",
        prefix="DDdev",
        gdrive_folder="DD-dev backups",
        destination=destination)


def _run_backup_both_worker(action_id: str, destination: str = 'both'):
    lines: list[str] = []
    def emit(msg: str):
        lines.append(msg)
        _ACTION_OUTPUT[action_id] = {"lines": _truncate_lines(lines.copy()), "done": False}
        _trim_action_output()
    try:
        emit("=== Backup Sequence: Live + Dev ===")
        emit("")
        emit("--- Starting Live Backup ---")
        live_id = str(uuid.uuid4())
        _ACTION_OUTPUT[live_id] = {"lines": [], "done": False}
        _run_backup_live_worker(live_id, destination)
        live_data = _ACTION_OUTPUT.get(live_id, {})
        for l in live_data.get("lines", []):
            if l.strip():
                emit(l)
        emit("")
        emit("--- Starting Dev Backup ---")
        dev_id = str(uuid.uuid4())
        _ACTION_OUTPUT[dev_id] = {"lines": [], "done": False}
        _run_backup_dev_worker(dev_id, destination)
        dev_data = _ACTION_OUTPUT.get(dev_id, {})
        for l in dev_data.get("lines", []):
            if l.strip():
                emit(l)
        emit("")
        emit("=== Both Backups Complete ===")
        _ACTION_OUTPUT[action_id] = {"lines": _truncate_lines(lines.copy()), "done": True, "returncode": 0, "timestamp": datetime.datetime.utcnow().isoformat()}
    except Exception as e:
        emit(f"Error: {e}")
        _ACTION_OUTPUT[action_id] = {"lines": _truncate_lines(lines.copy()), "done": True, "returncode": -1, "timestamp": datetime.datetime.utcnow().isoformat()}


@router.get("/system/status")
async def system_status(_admin: dict = Depends(require_admin_local)):
    processes = []
    site_status = "offline"
    disk_total = 0
    disk_used = 0

    try:
        import json as _json
        CREATE_NO_WINDOW = 0x08000000
        pm2 = _find_pm2()
        p = subprocess.run(
            ["cmd", "/c", pm2 + " jlist"],
            capture_output=True, text=True, timeout=10,
            creationflags=CREATE_NO_WINDOW,
        )
        stdout = p.stdout or ""
        if p.returncode == 0 and stdout.strip():
            processes = _json.loads(stdout)
    except Exception:
        pass

    def proc_info(p):
        return {
            "name": p.get("name", ""),
            "status": p.get("pm2_env", {}).get("status", "unknown"),
            "uptime": p.get("pm2_env", {}).get("pm_uptime", 0),
            "cpu": p.get("monit", {}).get("cpu", 0),
            "memory": p.get("monit", {}).get("memory", 0),
            "restarts": p.get("pm2_env", {}).get("restart_time", 0),
            "pid": p.get("pid", 0),
        }

    try:
        import shutil
        usage = shutil.disk_usage(_PROJECT_ROOT.anchor + "/")
        disk_total = usage.total
        disk_used = usage.used
    except Exception:
        pass

    proc_map = {p.get("name"): p.get("pm2_env", {}).get("status") for p in processes}
    if proc_map.get("divyadrishti-live") == "online" and proc_map.get("divyadrishti-tunnel") == "online":
        site_status = "online"
    elif proc_map.get("divyadrishti-dev") == "online":
        site_status = "online"
    else:
        site_status = "offline"

    return {
        "success": True,
        "processes": [proc_info(p) for p in processes],
        "site": site_status,
        "disk_total": disk_total,
        "disk_used": disk_used,
        "timestamp": datetime.datetime.utcnow().isoformat(),
    }


@router.get("/system/logs")
async def system_logs(
    service: str = "divyadrishti-live",
    lines: int = 50,
    _admin: dict = Depends(require_admin_local),
):
    if service not in ("divyadrishti-live", "divyadrishti-dev", "divyadrishti-tunnel"):
        raise HTTPException(status_code=400, detail="Invalid service")
    pm2_log_dir = HOME_DIR / ".pm2" / "logs"
    log_file = pm2_log_dir / f"{service}-out.log"
    if not log_file.exists():
        log_file = pm2_log_dir / f"{service}-error.log"
    if not log_file.exists():
        return {"success": True, "lines": [], "service": service}
    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        recent = all_lines[-lines:]
        return {"success": True, "lines": [l.rstrip() for l in recent], "service": service}
    except Exception as e:
        return {"success": True, "lines": [f"Error reading log: {e}"], "service": service}


@router.get("/system/backups")
async def list_backups(_admin: dict = Depends(require_admin_local)):
    backups = []
    for d in (BACKUP_DIR, BACKUP_LIVE_DIR, BACKUP_DEV_DIR):
        if d.exists():
            for f in d.glob("*.zip"):
                try:
                    mtime = datetime.datetime.fromtimestamp(f.stat().st_mtime)
                    backups.append({
                        "name": f.name,
                        "size": f.stat().st_size,
                        "date": mtime.strftime("%Y-%m-%d %H:%M"),
                        "age_days": (datetime.datetime.now() - mtime).days,
                    })
                except Exception:
                    pass
    backups.sort(key=lambda x: x["date"], reverse=True)
    return {"success": True, "backups": backups}


@router.post("/system/action")
async def system_action(
    body: dict = Body(...),
    _admin: dict = Depends(require_admin_local),
):
    action = body.get("action", "")
    destination = body.get("destination", "both")
    if destination not in ('local', 'drive', 'both'):
        destination = 'both'
    if action not in ("restart", "start", "stop", "backup", "deploy", "backup-live", "backup-dev"):
        raise HTTPException(status_code=400, detail="Invalid action")
    action_id = str(uuid.uuid4())
    _ACTION_OUTPUT[action_id] = {"lines": [], "done": False}
    if action == "backup-live":
        threading.Thread(target=_run_backup_live_worker, args=(action_id, destination), daemon=True).start()
    elif action == "backup-dev":
        threading.Thread(target=_run_backup_dev_worker, args=(action_id, destination), daemon=True).start()
    else:
        allowed = {
            "restart": str(SERVICE_DIR / "system_restart_live.bat"),
            "start": str(SERVICE_DIR / "system_start_live.bat"),
            "stop": str(SERVICE_DIR / "system_stop_live.bat"),
            "backup": str(SERVICE_DIR / "backup_live.bat"),
            "deploy": str(SERVICE_DIR / "update_live.bat"),
        }
        script = allowed[action]
        threading.Thread(target=_run_action_worker, args=(action_id, script), daemon=True).start()
    return {"success": True, "action_id": action_id, "action": action}


@router.post("/backup/run")
async def backup_run(
    body: dict = Body(...),
    _admin: dict = Depends(require_admin_local),
):
    source = body.get("source", "live")
    destination = body.get("destination", "both")
    if source not in ("live", "dev", "both"):
        raise HTTPException(status_code=400, detail="Invalid source")
    if destination not in ("local", "drive", "both"):
        destination = "both"
    action_id = str(uuid.uuid4())
    _ACTION_OUTPUT[action_id] = {"lines": [], "done": False}
    if source == "both":
        threading.Thread(target=_run_backup_both_worker, args=(action_id, destination), daemon=True).start()
    elif source == "live":
        threading.Thread(target=_run_backup_live_worker, args=(action_id, destination), daemon=True).start()
    else:
        threading.Thread(target=_run_backup_dev_worker, args=(action_id, destination), daemon=True).start()
    return {"success": True, "action_id": action_id, "source": source, "destination": destination}


@router.delete("/backup/delete")
async def delete_backup(
    body: dict = Body(...),
    _admin: dict = Depends(require_admin_local),
):
    filename = body.get("filename", "")
    if not filename:
        raise HTTPException(status_code=400, detail="Filename required")
    deleted = False
    for d in (BACKUP_DIR, BACKUP_LIVE_DIR, BACKUP_DEV_DIR):
        target = d / filename
        if target.exists() and target.is_file() and target.suffix == ".zip":
            try:
                target.unlink()
                deleted = True
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to delete: {e}")
    if not deleted:
        raise HTTPException(status_code=404, detail="Backup file not found")
    return {"success": True, "filename": filename}


@router.post("/backup/restore")
async def restore_backup(
    body: dict = Body(...),
    _admin: dict = Depends(require_admin_local),
):
    filename = body.get("filename", "")
    if not filename:
        raise HTTPException(status_code=400, detail="Filename required")
    if filename.startswith("DDlive-") or filename.startswith("DDTools-live-"):
        source = "live"
    elif filename.startswith("DDdev-"):
        source = "dev"
    else:
        raise HTTPException(status_code=400, detail="Cannot determine backup source from filename")
    backup_path = None
    for d in (BACKUP_DIR, BACKUP_LIVE_DIR, BACKUP_DEV_DIR):
        target = d / filename
        if target.exists() and target.is_file():
            backup_path = target
            break
    if not backup_path:
        raise HTTPException(status_code=404, detail="Backup file not found")
    action_id = str(uuid.uuid4())
    _ACTION_OUTPUT[action_id] = {"lines": [], "done": False}
    threading.Thread(target=_run_restore_worker, args=(action_id, source, str(backup_path)), daemon=True).start()
    return {"success": True, "action_id": action_id, "filename": filename, "source": source}


@router.get("/backup/schedules")
async def list_schedules(_admin: dict = Depends(require_admin_local)):
    rows = query("SELECT * FROM backup_schedules ORDER BY id")
    return {
        "success": True,
        "schedules": [
            {
                "id": r["id"],
                "source": r["source"],
                "frequency": r["frequency"],
                "destination": r["destination"],
                "scheduled_time": r["scheduled_time"],
                "day_of_week": r["day_of_week"],
                "day_of_month": r["day_of_month"],
                "enabled": bool(r["enabled"]),
                "last_run": r["last_run"],
                "next_run": r["next_run"],
            }
            for r in rows
        ],
    }


@router.post("/backup/schedules")
async def create_schedule(
    body: dict = Body(...),
    _admin: dict = Depends(require_admin_local),
):
    source = body.get("source", "live")
    frequency = body.get("frequency", "manual")
    destination = body.get("destination", "both")
    scheduled_time = body.get("scheduled_time", "02:00")
    day_of_week = body.get("day_of_week")
    day_of_month = body.get("day_of_month")
    enabled = body.get("enabled", True)

    if source not in ("live", "dev", "both"):
        raise HTTPException(status_code=400, detail="Invalid source")
    if frequency not in ("manual", "daily", "weekly", "monthly"):
        raise HTTPException(status_code=400, detail="Invalid frequency")
    if destination not in ("local", "drive", "both"):
        raise HTTPException(status_code=400, detail="Invalid destination")

    sid = execute(
        "INSERT INTO backup_schedules (source, frequency, destination, scheduled_time, day_of_week, day_of_month, enabled) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (source, frequency, destination, scheduled_time, day_of_week, day_of_month, 1 if enabled else 0),
    )
    _update_schedule_next_run(sid)
    _ensure_scheduler()
    rows = query("SELECT * FROM backup_schedules WHERE id = ?", (sid,))
    if not rows:
        raise HTTPException(status_code=500, detail="Failed to create schedule")
    r = rows[0]
    return {
        "success": True,
        "schedule": {
            "id": r["id"],
            "source": r["source"],
            "frequency": r["frequency"],
            "destination": r["destination"],
            "scheduled_time": r["scheduled_time"],
            "day_of_week": r["day_of_week"],
            "day_of_month": r["day_of_month"],
            "enabled": bool(r["enabled"]),
            "last_run": r["last_run"],
            "next_run": r["next_run"],
        },
    }


@router.put("/backup/schedules/{schedule_id}")
async def update_schedule(
    schedule_id: int,
    body: dict = Body(...),
    _admin: dict = Depends(require_admin_local),
):
    existing = query("SELECT * FROM backup_schedules WHERE id = ?", (schedule_id,))
    if not existing:
        raise HTTPException(status_code=404, detail="Schedule not found")
    s = existing[0]
    source = body.get("source", s["source"])
    frequency = body.get("frequency", s["frequency"])
    destination = body.get("destination", s["destination"])
    scheduled_time = body.get("scheduled_time", s["scheduled_time"])
    day_of_week = body.get("day_of_week", s["day_of_week"])
    day_of_month = body.get("day_of_month", s["day_of_month"])
    enabled = body.get("enabled", bool(s["enabled"]))

    if source not in ("live", "dev", "both"):
        raise HTTPException(status_code=400, detail="Invalid source")
    if frequency not in ("manual", "daily", "weekly", "monthly"):
        raise HTTPException(status_code=400, detail="Invalid frequency")
    if destination not in ("local", "drive", "both"):
        raise HTTPException(status_code=400, detail="Invalid destination")

    execute(
        "UPDATE backup_schedules SET source=?, frequency=?, destination=?, scheduled_time=?, day_of_week=?, day_of_month=?, enabled=? WHERE id=?",
        (source, frequency, destination, scheduled_time, day_of_week, day_of_month, 1 if enabled else 0, schedule_id),
    )
    _update_schedule_next_run(schedule_id)
    _ensure_scheduler()
    rows = query("SELECT * FROM backup_schedules WHERE id = ?", (schedule_id,))
    if not rows:
        raise HTTPException(status_code=500, detail="Failed to update schedule")
    r = rows[0]
    return {
        "success": True,
        "schedule": {
            "id": r["id"],
            "source": r["source"],
            "frequency": r["frequency"],
            "destination": r["destination"],
            "scheduled_time": r["scheduled_time"],
            "day_of_week": r["day_of_week"],
            "day_of_month": r["day_of_month"],
            "enabled": bool(r["enabled"]),
            "last_run": r["last_run"],
            "next_run": r["next_run"],
        },
    }


@router.delete("/backup/schedules/{schedule_id}")
async def delete_schedule(
    schedule_id: int,
    _admin: dict = Depends(require_admin_local),
):
    execute("DELETE FROM backup_schedules WHERE id = ?", (schedule_id,))
    return {"success": True}


_scheduler_thread: threading.Thread | None = None
_scheduler_lock = threading.Lock()


def _ensure_scheduler():
    global _scheduler_thread
    with _scheduler_lock:
        if _scheduler_thread is None or not _scheduler_thread.is_alive():
            _scheduler_thread = threading.Thread(target=_backup_scheduler_loop, daemon=True)
            _scheduler_thread.start()


def _backup_scheduler_loop():
    while True:
        try:
            rows = query("SELECT * FROM backup_schedules WHERE enabled = 1 AND next_run IS NOT NULL")
            now = datetime.datetime.now()
            for r in rows:
                if r['next_run'] and r['frequency'] != 'manual':
                    try:
                        next_dt = datetime.datetime.strptime(r['next_run'], '%Y-%m-%d %H:%M')
                        if now >= next_dt:
                            with _BACKUP_RUNNING_LOCK:
                                if _BACKUP_RUNNING:
                                    logger.warning("Backup already in progress — skipping scheduled backup")
                                    continue
                                _BACKUP_RUNNING = True
                            action_id = str(uuid.uuid4())
                            _ACTION_OUTPUT[action_id] = {"lines": [], "done": False}
                            destination = r['destination']
                            src = r['source']
                            _ACTION_OUTPUT[action_id] = {"lines": [f"=== Scheduled {r['frequency'].title()} Backup ==="], "done": False}
                            threading.Thread(
                                target=_run_scheduled_backup_wrapper,
                                args=(action_id, src, destination, r['id']),
                                daemon=True,
                            ).start()
                    except Exception:
                        pass
        except Exception:
            pass
        time.sleep(60)


def _run_scheduled_backup(action_id: str, source: str, destination: str, schedule_id: int):
    try:
        if source == "both":
            _run_backup_both_worker(action_id, destination)
        elif source == "live":
            _run_backup_live_worker(action_id, destination)
        else:
            _run_backup_dev_worker(action_id, destination)
        execute("UPDATE backup_schedules SET last_run = datetime('now') WHERE id = ?", (schedule_id,))
        _update_schedule_next_run(schedule_id)
    except Exception as e:
        _ACTION_OUTPUT[action_id] = {"lines": [f"Error: {e}"], "done": True, "returncode": -1}


def _run_scheduled_backup_wrapper(action_id: str, source: str, destination: str, schedule_id: int):
    global _BACKUP_RUNNING
    try:
        _run_scheduled_backup(action_id, source, destination, schedule_id)
    finally:
        with _BACKUP_RUNNING_LOCK:
            _BACKUP_RUNNING = False


_ensure_scheduler()


@router.get("/system/action-status/{action_id}")
async def system_action_status(
    action_id: str,
    _admin: dict = Depends(require_admin_local),
):
    data = _ACTION_OUTPUT.get(action_id)
    if not data:
        raise HTTPException(status_code=404, detail="Action not found")
    return {"success": True, **data}


@router.post("/system/log/clear")
async def clear_logs(service: str = "divyadrishti-live", _admin: dict = Depends(require_admin_local)):
    if service not in ("divyadrishti-live", "divyadrishti-dev", "divyadrishti-tunnel"):
        raise HTTPException(status_code=400, detail="Invalid service")
    pm2_log_dir = HOME_DIR / ".pm2" / "logs"
    out_log = pm2_log_dir / f"{service}-out.log"
    err_log = pm2_log_dir / f"{service}-error.log"
    for lf in (out_log, err_log):
        try:
            with open(lf, "w") as f:
                f.write("")
        except Exception:
            pass
    return {"success": True, "message": f"Logs cleared for {service}"}


@router.get("/users")
async def list_users(_admin: dict = Depends(require_admin_local)):
    rows = query("SELECT id, username, role, is_active, created_at, expires_at, failed_attempts, last_active FROM users ORDER BY id")
    return {
        "success": True,
        "users": [
            {
                "id": r["id"],
                "username": r["username"],
                "role": r["role"],
                "is_active": bool(r["is_active"]),
                "failed_attempts": r["failed_attempts"] or 0,
                "created_at": r["created_at"],
                "expires_at": r["expires_at"],
                "last_active": r["last_active"],
            }
            for r in rows
        ],
    }


@router.post("/users")
async def create_user(body: dict, _admin: dict = Depends(require_admin_local)):
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    confirm_password = body.get("confirm_password") or ""
    role = body.get("role", "user")
    expires_at = body.get("expires_at")
    if not username or not password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username and password required")
    if password != confirm_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Passwords do not match")
    if role not in ("admin", "user"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Role must be 'admin' or 'user'")
    try:
        hash_pw = hash_password(password)
        if expires_at:
            execute(
                "INSERT INTO users (username, password, role, expires_at) VALUES (?, ?, ?, ?)",
                (username, hash_pw, role, expires_at),
            )
        else:
            execute(
                "INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
                (username, hash_pw, role),
            )
    except Exception as e:
        if "UNIQUE" in str(e):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return {"success": True, "username": username}


@router.delete("/users/{user_id}")
async def delete_user(user_id: int, _admin: dict = Depends(require_admin_local)):
    execute("DELETE FROM users WHERE id = ? AND role != 'admin'", (user_id,))
    return {"success": True}


@router.post("/users/{user_id}/expire")
async def set_expiry(user_id: int, body: dict, _admin: dict = Depends(require_admin_local)):
    expires_at = body.get("expires_at")
    if not expires_at:
        execute("UPDATE users SET expires_at = NULL WHERE id = ?", (user_id,))
    else:
        try:
            exp = datetime.datetime.fromisoformat(expires_at)
            if exp < datetime.datetime.now():
                raise HTTPException(status_code=400, detail="Expiry date cannot be in the past")
        except ValueError:
            pass
        execute("UPDATE users SET expires_at = ? WHERE id = ?", (expires_at, user_id))
    return {"success": True}


@router.post("/users/{user_id}/toggle-active")
async def toggle_user_active(user_id: int, _admin: dict = Depends(require_admin_local)):
    row = query("SELECT is_active FROM users WHERE id = ?", (user_id,))
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    new_val = 0 if row[0]["is_active"] else 1
    execute("UPDATE users SET is_active = ? WHERE id = ?", (new_val, user_id))
    return {"success": True, "is_active": bool(new_val)}


@router.post("/users/{user_id}/password")
async def set_password(user_id: int, body: dict, _admin: dict = Depends(require_admin_local)):
    password = body.get("password") or ""
    confirm_password = body.get("confirm_password") or ""
    if not password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password required")
    if password != confirm_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Passwords do not match")
    hash_pw = hash_password(password)
    execute("UPDATE users SET password = ?, failed_attempts = 0, is_active = 1 WHERE id = ?", (hash_pw, user_id))
    return {"success": True}


@router.post("/users/{user_id}/unblock")
async def unblock_user(user_id: int, _admin: dict = Depends(require_admin_local)):
    execute("UPDATE users SET failed_attempts = 0, is_active = 1 WHERE id = ?", (user_id,))
    return {"success": True, "message": "User unblocked"}