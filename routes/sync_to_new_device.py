"""
sync_to_new_device.py
─────────────────────────────────────────────────────────────────────────────
Blueprint: sync_to_new_device_bp

Reads backed-up users and fingerprints from SQL Server (biocentral on
MGSVR14) and pushes them to a target ZK biometric terminal.
Attendance logs are NOT pushed — ZK devices do not support writing them.

Flow:
  1. Pre-flight  — TCP ping target device
  2. Push Users  — read dbo.backup_device_users WHERE device_id = source_device_id,
                   set_user() on target preserving uid slot where free
  3. Push FP     — read dbo.backup_fingerprints WHERE device_id = source_device_id,
                   save_user_template() on target
  4. Audit       — write result to dbo.biocentral_audit_logs

Real-time progress is exposed via GET /api/sync/status/<task_id> polling.

Dependencies: flask, pyodbc, pyzk, portal
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import base64
import logging
import socket
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Lock
from typing import Any

import pyodbc
from flask import Blueprint, jsonify, render_template, request, session
from zk import ZK
from portal import loggedin_required

# ─────────────────────────────────────────────────────────────────────────────
# Blueprint
# ─────────────────────────────────────────────────────────────────────────────
sync_to_new_device_bp = Blueprint("sync_to_new_device_bp", __name__)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────
ZK_PORT     = 4370
ZK_TIMEOUT  = 15
MAX_RETRIES = 3
RETRY_DELAY = 2

# ─────────────────────────────────────────────────────────────────────────────
# Database connection string  (single SQL Server — same as store_crud.py)
# ─────────────────────────────────────────────────────────────────────────────
BIOCENTRAL_CONN_STR = (
    "Driver={ODBC Driver 18 for SQL Server};"
    "Server=MGSVR17.mgroup.local,1433;"
    "Database=biocentral;"
    "Trusted_Connection=yes;"
    "TrustServerCertificate=yes;"
    "Network=dbmssocn;"
)

# ─────────────────────────────────────────────────────────────────────────────
# In-memory task store
# ─────────────────────────────────────────────────────────────────────────────
_tasks: dict[str, dict[str, Any]] = {}
_tasks_lock = Lock()
_executor   = ThreadPoolExecutor(max_workers=4)


# ═════════════════════════════════════════════════════════════════════════════
# ── CONNECTION HELPERS ───────────────────────────────────────────────────────
# ═════════════════════════════════════════════════════════════════════════════

def get_db_connection() -> pyodbc.Connection:
    """SQL Server — BioCentral (device registry, backup tables, audit logs)."""
    return pyodbc.connect(BIOCENTRAL_CONN_STR)


def _ping(ip: str) -> tuple[bool, str]:
    """TCP-only handshake — never mutates ZK state."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(3)
            s.connect((ip, ZK_PORT))
        return True, "Reachable"
    except Exception as exc:
        return False, str(exc)


def _zk_connect(ip: str, retries: int = MAX_RETRIES):
    """Return an active ZK connection, retrying on transient failures."""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            zk   = ZK(ip, port=ZK_PORT, timeout=ZK_TIMEOUT, force_udp=False)
            conn = zk.connect()
            return conn
        except Exception as exc:
            last_err = exc
            if attempt < retries:
                time.sleep(RETRY_DELAY)
    raise ConnectionError(f"Cannot connect to {ip} after {retries} attempts: {last_err}")


# ═════════════════════════════════════════════════════════════════════════════
# ── TASK STATE HELPERS ───────────────────────────────────────────────────────
# ═════════════════════════════════════════════════════════════════════════════

def _new_task(source_device_id: int, source_ip: str, target_ip: str, operator: str) -> str:
    task_id = str(uuid.uuid4())
    with _tasks_lock:
        _tasks[task_id] = {
            "task_id":          task_id,
            "status":           "pending",
            "source_device_id": source_device_id,
            "source_ip":        source_ip,   # display only
            "target_ip":        target_ip,
            "operator":         operator,
            "started_at":  datetime.now().isoformat(),
            "finished_at": None,
            "progress":    0,
            "phase":       "Initialising…",
            "logs":        [],
            "summary": {
                "users_pushed":         0,
                "users_skipped":        0,
                "fingerprints_pushed":  0,
                "fingerprints_skipped": 0,
                "errors":               0,
            },
        }
    return task_id


def _log(task_id: str, msg: str, level: str = "info") -> None:
    entry = {"ts": datetime.now().strftime("%H:%M:%S"), "msg": msg, "level": level}
    with _tasks_lock:
        if task_id in _tasks:
            _tasks[task_id]["logs"].append(entry)
    if level == "error":
        logger.error("[%s] %s", task_id[:8], msg)
    else:
        logger.info("[%s] %s", task_id[:8], msg)


def _set_progress(task_id: str, pct: int, phase: str) -> None:
    with _tasks_lock:
        if task_id in _tasks:
            _tasks[task_id]["progress"] = pct
            _tasks[task_id]["phase"]    = phase


def _finish(task_id: str, success: bool, message: str = "") -> None:
    with _tasks_lock:
        if task_id in _tasks:
            _tasks[task_id]["status"]      = "done" if success else "failed"
            _tasks[task_id]["finished_at"] = datetime.now().isoformat()
            _tasks[task_id]["progress"]    = 100 if success else _tasks[task_id]["progress"]
            if message:
                _tasks[task_id]["phase"]   = message


def _bump(task_id: str, key: str, amount: int = 1) -> None:
    with _tasks_lock:
        if task_id in _tasks:
            _tasks[task_id]["summary"][key] = _tasks[task_id]["summary"].get(key, 0) + amount


# ═════════════════════════════════════════════════════════════════════════════
# ── SYNC ENGINE ──────────────────────────────────────────────────────────────
# ═════════════════════════════════════════════════════════════════════════════

def _run_sync(task_id: str, source_device_id: int, target_ip: str, operator: str, only_codes: list | None = None) -> None:
    """
    Background thread.
    Resolves source_device_id → source_ip from dbo.device_registry, then reads
    dbo.backup_device_users and dbo.backup_fingerprints for that device_id
    and pushes the data to the target ZK device.

    only_codes: if provided, only push users whose employee_code is in this list.
    Progress milestones: 5 → 10 → 20 → 30–60 → 60–90 → 95 → 100
    """
    db        = None
    zk_target = None

    try:
        with _tasks_lock:
            _tasks[task_id]["status"] = "running"

        # ── Pre-flight ────────────────────────────────────────────────────────
        _set_progress(task_id, 5, "Verifying devices…")

        # ── Open SQL Server connection ─────────────────────────────────────────
        _set_progress(task_id, 10, "Reading backup data from SQL Server…")
        db  = get_db_connection()
        cur = db.cursor()

        # Resolve source IP from device_id (used only for ZK connection & display)
        cur.execute(
            "SELECT ip_address, bcc FROM dbo.device_registry WHERE device_id = ?",
            (source_device_id,),
        )
        reg_row = cur.fetchone()
        if not reg_row:
            raise RuntimeError(f"Device ID {source_device_id} not found in device_registry.")
        source_ip  = (reg_row.ip_address or "").strip()
        source_bcc = reg_row.bcc or f"Device {source_device_id}"

        _log(task_id, f"Sync started by {operator}: {source_bcc} (device_id={source_device_id}) → {target_ip}")

        tgt_ok, tgt_msg = _ping(target_ip)
        if not tgt_ok:
            raise RuntimeError(f"Target device {target_ip} unreachable: {tgt_msg}")
        _log(task_id, f"Target device {target_ip} is reachable.", "success")

        # ── Fetch users from dbo.backup_device_users ──────────────────────────
        cur.execute(
            """
            SELECT employee_code, employee_name, privilege_level, pin_password
            FROM   dbo.backup_device_users
            WHERE  device_ip = ?
            ORDER  BY employee_code
            """,
            (source_ip,),
        )
        source_users = cur.fetchall()

        if not source_users:
            raise RuntimeError(
                f"No backed-up users found for device ID {source_device_id} ({source_bcc}, {source_ip}). "
                "Run a backup first from the Device Manager."
            )

        # Filter to specific codes if this is a selective push
        if only_codes:
            only_set     = {c.strip().upper() for c in only_codes}
            source_users = [u for u in source_users if str(u.employee_code).strip().upper() in only_set]
            if not source_users:
                raise RuntimeError("None of the selected employee codes were found in the backup.")

        _log(task_id, f"Found {len(source_users)} user(s) to push for {source_ip}.")

        # ── Fetch fingerprints from dbo.backup_fingerprints ───────────────────
        cur.execute(
            """
            SELECT employee_code, finger_index, finger_template
            FROM   dbo.backup_fingerprints
            WHERE  device_ip = ?
            ORDER  BY employee_code, finger_index
            """,
            (source_ip,),
        )
        source_fps = cur.fetchall()
        _log(task_id, f"Found {len(source_fps)} fingerprint template(s) in backup.")

        # ── Connect to target ZK device ───────────────────────────────────────
        _set_progress(task_id, 20, f"Connecting to target device {target_ip}…")
        zk_target = _zk_connect(target_ip)
        _log(task_id, f"Connected to target device {target_ip}.")
        zk_target.disable_device()
        time.sleep(0.5)

        # Load existing users on target — to skip duplicates and resolve uid conflicts
        existing_target  = zk_target.get_users()
        existing_by_code = {str(u.user_id).strip().upper(): u for u in existing_target}
        max_uid          = max((u.uid for u in existing_target), default=0)

        # ── Push users ────────────────────────────────────────────────────────
        total_users = len(source_users)
        _set_progress(task_id, 30, f"Pushing {total_users} user(s)…")
        _log(task_id, f"Pushing {total_users} user(s) to target…")

        pushed_users  = 0
        skipped_users = 0
        failed_users  = 0

        for i, user in enumerate(source_users):
            emp_code  = str(user.employee_code).strip().upper()
            name      = user.employee_name or ""
            privilege = int(user.privilege_level or 0)
            pin_pwd   = user.pin_password or ""

            # Update progress smoothly across the 30–60 range
            pct = 30 + int((i / total_users) * 30)
            _set_progress(task_id, pct, f"Pushing users… ({i + 1}/{total_users})")

            if emp_code in existing_by_code:
                skipped_users += 1
                _bump(task_id, "users_skipped")
                continue

            # Assign a fresh sequential uid slot (1–65535).
            max_uid   += 1
            target_uid = max_uid

            try:
                zk_target.set_user(
                    uid=target_uid,
                    user_id=emp_code,
                    name=name,
                    privilege=privilege,
                    password=pin_pwd,
                    card=0,
                )
                time.sleep(0.1)
                existing_target  = zk_target.get_users()
                existing_by_code = {str(u.user_id).strip().upper(): u for u in existing_target}
                pushed_users += 1
                _bump(task_id, "users_pushed")
            except Exception as exc:
                _log(task_id, f"Failed to push user {emp_code}: {exc}", "error")
                failed_users += 1
                _bump(task_id, "errors")

        _log(
            task_id,
            f"Users — pushed: {pushed_users}, "
            f"skipped (already on device): {skipped_users}, failed: {failed_users}.",
            "success" if not failed_users else "warn",
        )

        # ── Push fingerprints ─────────────────────────────────────────────────
        total_fps = len(source_fps)
        _set_progress(task_id, 60, f"Pushing {total_fps} fingerprint(s)…")
        _log(task_id, f"Pushing {total_fps} fingerprint template(s) to target…")

        pushed_fp  = 0
        skipped_fp = 0
        failed_fp  = 0

        for i, fp in enumerate(source_fps):
            emp_code = str(fp.employee_code).strip().upper()
            fid      = int(fp.finger_index)
            encoded  = fp.finger_template

            pct = 60 + int((i / max(total_fps, 1)) * 30)
            _set_progress(task_id, pct, f"Pushing fingerprints… ({i + 1}/{total_fps})")

            target_user = existing_by_code.get(emp_code)
            if not target_user:
                skipped_fp += 1
                _bump(task_id, "fingerprints_skipped")
                continue

            try:
                raw_template = base64.b64decode(encoded)
            except Exception:
                raw_template = encoded.encode("utf-8") if isinstance(encoded, str) else encoded

            # Build Finger object — fall back to plain namespace if pyzk build differs
            try:
                from zk.finger import Finger
                t         = Finger(uid=target_user.uid, fid=fid, valid=1, template=raw_template)
                t.user_id = emp_code
            except Exception:
                class _T:
                    pass
                t          = _T()
                t.uid      = target_user.uid
                t.fid      = fid
                t.valid    = 1
                t.template = raw_template
                t.user_id  = emp_code

            try:
                try:
                    zk_target.save_user_template(target_user, t)
                except TypeError:
                    zk_target.save_user_template(t)
                pushed_fp += 1
                _bump(task_id, "fingerprints_pushed")
            except Exception as exc:
                _log(task_id, f"Fingerprint push failed {emp_code} fid={fid}: {exc}", "warn")
                failed_fp += 1
                _bump(task_id, "errors")

        _log(
            task_id,
            f"Fingerprints — pushed: {pushed_fp}, "
            f"skipped: {skipped_fp}, failed: {failed_fp}.",
            "success" if not failed_fp else "warn",
        )

        # Hardware buffer refresh
        try:
            zk_target.refresh_data()
            time.sleep(1)
        except Exception:
            pass

        # ── Audit log ─────────────────────────────────────────────────────────
        _set_progress(task_id, 95, "Writing audit log…")
        with _tasks_lock:
            s = _tasks[task_id]["summary"]

        detail = (
            f"Sync device_id={source_device_id} ({source_bcc}) → {target_ip} | "
            f"Users: {s['users_pushed']} pushed, {s['users_skipped']} skipped | "
            f"Fingerprints: {s['fingerprints_pushed']} pushed, {s['fingerprints_skipped']} skipped | "
            f"Errors: {s['errors']}"
        )
        try:
            cur.execute(
                """
                INSERT INTO dbo.biocentral_audit_logs
                    (module, target, action, action_details, action_by, action_at)
                VALUES ('SYNC', ?, 'SYNC_COMPLETE', ?, ?, GETDATE())
                """,
                (f"{source_device_id}→{target_ip}", detail, operator),
            )
            db.commit()
        except Exception as audit_exc:
            _log(task_id, f"Audit log write failed (non-fatal): {audit_exc}", "warn")

        _finish(task_id, True, "Sync completed successfully.")
        _log(task_id, "✓ Sync completed successfully.", "success")

    except Exception as exc:
        _log(task_id, f"Sync FAILED: {exc}", "error")
        _finish(task_id, False, f"Sync failed: {exc}")

        # Best-effort failure audit
        try:
            err_db  = get_db_connection()
            err_cur = err_db.cursor()
            err_cur.execute(
                """
                INSERT INTO dbo.biocentral_audit_logs
                    (module, target, action, action_details, action_by, action_at)
                VALUES ('SYNC', ?, 'SYNC_FAILED', ?, ?, GETDATE())
                """,
                (f"{source_device_id}→{target_ip}", str(exc), operator),
            )
            err_db.commit()
            err_cur.close()
            err_db.close()
        except Exception:
            pass

    finally:
        if zk_target:
            try:
                zk_target.enable_device()
            except Exception:
                pass
            try:
                zk_target.disconnect()
            except Exception:
                pass
        if db:
            try:
                db.close()
            except Exception:
                pass


# ═════════════════════════════════════════════════════════════════════════════
# ── API ROUTES ───────────────────────────────────────────────────────────────
# ═════════════════════════════════════════════════════════════════════════════

# ── Dashboard ─────────────────────────────────────────────────────────────────
@sync_to_new_device_bp.route("/sync-to-new-device")
@loggedin_required()
def sync_to_new_device():
    return render_template("sync_to_new_device.html")


# ── GET /api/devices ──────────────────────────────────────────────────────────
@sync_to_new_device_bp.route("/sync/api/devices", methods=["GET"])
@loggedin_required()
def api_get_devices():
    """
    Return all registered ZK terminals instantly — no ping, online=null.
    Each device includes last_backup: the most recent BACKUP action timestamp
    from biocentral_audit_logs (module='DEVICE', action='BACKUP') for that
    device, resolved via device_registry.device_id.
    Devices are sorted by last_backup descending (most recently backed-up first),
    with devices that have never been backed up sorted last.
    Call GET /sync/api/devices/status separately to get live online status.
    """
    try:
        conn = get_db_connection()
        cur  = conn.cursor()
        cur.execute(
            """
            SELECT
                dr.device_id,
                dr.bcc,
                dr.ip_address,
                dr.comms_key,
                dr.chain_type,
                MAX(al.action_at) AS last_backup
            FROM dbo.device_registry dr
            LEFT JOIN dbo.biocentral_audit_logs al
                ON  al.module = 'DEVICE'
                AND al.action = 'BACKUP'
                AND TRY_CAST(al.target AS INT) = dr.device_id
            GROUP BY dr.device_id, dr.bcc, dr.ip_address, dr.comms_key, dr.chain_type
            ORDER BY last_backup DESC, dr.bcc ASC
            """
        )
        devices = []
        for row in cur.fetchall():
            ip = row.ip_address.strip() if row.ip_address else ""
            last_backup = (
                row.last_backup.strftime("%Y-%m-%d")
                if row.last_backup else None
            )
            devices.append({
                "device_id":   row.device_id,
                "bcc":         row.bcc,
                "ip":          ip,
                "comms_key":   row.comms_key,
                "chain_type":  row.chain_type,
                "online":      None,   # populated by /devices/status
                "last_backup": last_backup,
            })
        cur.close()
        conn.close()
        return jsonify({"status": "success", "devices": devices})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


# ── GET /api/devices/status ───────────────────────────────────────────────────
@sync_to_new_device_bp.route("/sync/api/devices/status", methods=["GET"])
@loggedin_required()
def api_devices_status():
    """
    Ping all devices concurrently and return { ip: online } map.
    Called by the frontend after the table is already rendered so the page
    never blocks waiting for timeouts.
    """
    try:
        conn = get_db_connection()
        cur  = conn.cursor()
        cur.execute("SELECT ip_address FROM dbo.device_registry")
        ips = [
            row.ip_address.strip()
            for row in cur.fetchall()
            if row.ip_address and row.ip_address.strip()
        ]
        cur.close()
        conn.close()

        def _check(ip: str) -> tuple[str, bool]:
            ok, _ = _ping(ip)
            return ip, ok

        statuses: dict[str, bool] = {}
        with ThreadPoolExecutor(max_workers=min(len(ips), 20)) as pool:
            for ip, online in pool.map(_check, ips):
                statuses[ip] = online

        return jsonify({"status": "success", "statuses": statuses})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


# ── GET /api/device/<device_id>/backup-summary ────────────────────────────────
@sync_to_new_device_bp.route("/sync/api/device/<int:device_id>/backup-summary", methods=["GET"])
@loggedin_required()
def api_backup_summary(device_id: int):
    """
    Returns counts of backed-up users, fingerprints, and attendance records
    stored in SQL Server for the given device_id, plus the last backup timestamp.
    Used to populate the source device preview panel.
    """
    try:
        conn = get_db_connection()
        cur  = conn.cursor()

        # Resolve IP from device_registry — backup tables are keyed by device_ip
        cur.execute(
            "SELECT ip_address FROM dbo.device_registry WHERE device_id = ?", (device_id,)
        )
        reg = cur.fetchone()
        if not reg:
            return jsonify({"status": "error", "message": f"Device ID {device_id} not found."}), 404
        device_ip = (reg.ip_address or "").strip()

        cur.execute(
            "SELECT COUNT(*) FROM dbo.backup_device_users WHERE device_ip = ?", (device_ip,)
        )
        user_count = cur.fetchone()[0]

        cur.execute(
            "SELECT COUNT(*) FROM dbo.backup_fingerprints WHERE device_ip = ?", (device_ip,)
        )
        fp_count = cur.fetchone()[0]

        cur.execute(
            "SELECT COUNT(*) FROM dbo.backup_attendance_logs WHERE device_ip = ?", (device_ip,)
        )
        att_count = cur.fetchone()[0]

        cur.execute(
            "SELECT MAX(backup_timestamp) FROM dbo.backup_device_users WHERE device_ip = ?", (device_ip,)
        )
        row         = cur.fetchone()
        last_backup = str(row[0]) if row and row[0] else "Never"

        cur.close()
        conn.close()
        return jsonify({
            "status":      "success",
            "user_count":  user_count,
            "fp_count":    fp_count,
            "att_count":   att_count,
            "last_backup": last_backup,
        })
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


# ── GET /api/device/<device_id>/users ─────────────────────────────────────────
@sync_to_new_device_bp.route("/sync/api/device/<int:device_id>/users", methods=["GET"])
@loggedin_required()
def api_device_users(device_id: int):
    """
    Returns the backed-up user list from dbo.backup_device_users for the
    given device_id.  Used for the source device preview panel (Users tab).
    """
    try:
        conn = get_db_connection()
        cur  = conn.cursor()

        # Resolve IP from device_registry — backup tables are keyed by device_ip
        cur.execute(
            "SELECT ip_address FROM dbo.device_registry WHERE device_id = ?", (device_id,)
        )
        reg = cur.fetchone()
        if not reg:
            return jsonify({"status": "error", "message": f"Device ID {device_id} not found."}), 404
        device_ip = (reg.ip_address or "").strip()

        cur.execute(
            """
            SELECT employee_code, employee_name, access_number,
                   privilege_level, pin_password, backup_timestamp
            FROM   dbo.backup_device_users
            WHERE  device_ip = ?
            ORDER  BY employee_code
            """,
            (device_ip,),
        )
        users = []
        for row in cur.fetchall():
            users.append({
                "employee_code":    str(row.employee_code),
                "employee_name":    row.employee_name or "",
                "access_number":    row.access_number,
                "privilege_level":  row.privilege_level,
                "has_pin":          bool(row.pin_password),
                "backup_timestamp": str(row.backup_timestamp),
            })
        cur.close()
        conn.close()
        return jsonify({"status": "success", "users": users, "count": len(users)})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


# ── GET /api/device/<device_id>/attendance ────────────────────────────────────
@sync_to_new_device_bp.route("/sync/api/device/<int:device_id>/attendance", methods=["GET"])
@loggedin_required()
def api_device_attendance(device_id: int):
    """
    Returns the backed-up attendance count from dbo.backup_attendance_logs
    for the given device_id.  Shown for reference only — not pushed to target.
    """
    try:
        conn = get_db_connection()
        cur  = conn.cursor()

        # Resolve IP from device_registry — backup tables are keyed by device_ip
        cur.execute(
            "SELECT ip_address FROM dbo.device_registry WHERE device_id = ?", (device_id,)
        )
        reg = cur.fetchone()
        if not reg:
            return jsonify({"status": "error", "message": f"Device ID {device_id} not found."}), 404
        device_ip = (reg.ip_address or "").strip()

        cur.execute(
            "SELECT COUNT(*) FROM dbo.backup_attendance_logs WHERE device_ip = ?", (device_ip,)
        )
        count = cur.fetchone()[0]
        cur.close()
        conn.close()
        return jsonify({"status": "success", "count": count})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


# ── POST /api/sync/start ──────────────────────────────────────────────────────
@sync_to_new_device_bp.route("/sync/api/start", methods=["POST"])
@loggedin_required()
def api_sync_start():
    """
    Kick off a background sync task.
    Body JSON: { source_device_id, target_ip }
    Resolves source IP from device_registry; reads backup tables by device_id.
    """
    data             = request.get_json(silent=True) or {}
    source_device_id = data.get("source_device_id")
    target_device_id = data.get("target_device_id")
    target_ip        = (data.get("target_ip") or "").strip()
    operator         = session.get("username", "System")

    if not source_device_id or not target_device_id or not target_ip:
        return jsonify({"status": "error", "message": "source_device_id, target_device_id and target_ip are required."}), 400

    try:
        source_device_id = int(source_device_id)
        target_device_id = int(target_device_id)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "device IDs must be integers."}), 400

    if source_device_id == target_device_id:
        return jsonify({"status": "error", "message": "Source and target devices must be different."}), 400

    # Resolve source IP for display
    try:
        conn = get_db_connection()
        cur  = conn.cursor()
        cur.execute("SELECT ip_address FROM dbo.device_registry WHERE device_id = ?", (source_device_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            return jsonify({"status": "error", "message": f"Device ID {source_device_id} not found."}), 404
        source_ip = (row.ip_address or "").strip()
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500

    task_id = _new_task(source_device_id, source_ip, target_ip, operator)
    _executor.submit(_run_sync, task_id, source_device_id, target_ip, operator)

    return jsonify({"status": "success", "task_id": task_id})


# ── GET /api/sync/status/<task_id> ───────────────────────────────────────────
@sync_to_new_device_bp.route("/sync/api/status/<task_id>", methods=["GET"])
@loggedin_required()
def api_sync_status(task_id: str):
    """Return current state, progress %, phase, summary, and log tail."""
    with _tasks_lock:
        task = _tasks.get(task_id)

    if not task:
        return jsonify({"status": "error", "message": "Task not found."}), 404

    return jsonify({
        "status":      task["status"],
        "progress":    task["progress"],
        "phase":       task["phase"],
        "summary":     task["summary"],
        "logs":        task["logs"][-60:],
        "started_at":  task["started_at"],
        "finished_at": task["finished_at"],
    })


# ── GET /api/sync/logs ────────────────────────────────────────────────────────
@sync_to_new_device_bp.route("/sync/api/logs", methods=["GET"])
@loggedin_required()
def api_sync_logs():
    """Return the last 20 sync task summaries (in-memory, resets on restart)."""
    with _tasks_lock:
        recent = sorted(
            _tasks.values(),
            key=lambda t: t["started_at"],
            reverse=True,
        )[:20]

    return jsonify({
        "status": "success",
        "tasks": [
            {
                "task_id":          t["task_id"],
                "status":           t["status"],
                "source_device_id": t["source_device_id"],
                "source_ip":        t["source_ip"],
                "target_ip":        t["target_ip"],
                "operator":         t["operator"],
                "progress":         t["progress"],
                "started_at":       t["started_at"],
                "summary":          t["summary"],
            }
            for t in recent
        ],
    })


# ── POST /sync/api/push-selected ─────────────────────────────────────────────
@sync_to_new_device_bp.route("/sync/api/push-selected", methods=["POST"])
@loggedin_required()
def api_push_selected():
    """
    Push only the specified employee_codes from the source device backup
    to the target ZK device.
    Body JSON: { source_device_id, target_device_id, target_ip, employee_codes: [...] }
    """
    data             = request.get_json(silent=True) or {}
    source_device_id = data.get("source_device_id")
    target_device_id = data.get("target_device_id")
    target_ip        = (data.get("target_ip") or "").strip()
    codes            = [str(c).strip().upper() for c in (data.get("employee_codes") or []) if c]
    operator         = session.get("username", "System")

    if not source_device_id or not target_device_id or not target_ip:
        return jsonify({"status": "error", "message": "source_device_id, target_device_id and target_ip are required."}), 400

    try:
        source_device_id = int(source_device_id)
        target_device_id = int(target_device_id)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "device IDs must be integers."}), 400

    if not codes:
        return jsonify({"status": "error", "message": "No employee codes provided."}), 400

    if source_device_id == target_device_id:
        return jsonify({"status": "error", "message": "Source and target devices must be different."}), 400

    # Resolve source IP for display
    try:
        conn = get_db_connection()
        cur  = conn.cursor()
        cur.execute("SELECT ip_address FROM dbo.device_registry WHERE device_id = ?", (source_device_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            return jsonify({"status": "error", "message": f"Device ID {source_device_id} not found."}), 404
        source_ip = (row.ip_address or "").strip()
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500

    task_id = _new_task(source_device_id, source_ip, target_ip, operator)
    _executor.submit(_run_sync, task_id, source_device_id, target_ip, operator, codes)
    return jsonify({"status": "success", "task_id": task_id})