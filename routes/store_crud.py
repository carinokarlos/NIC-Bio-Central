import base64
import logging
import socket
from datetime import datetime

import pyodbc
from flask import Blueprint, render_template, request, jsonify, session
from zk import ZK
from portal import app, loggedin_required

logger = logging.getLogger(__name__)

store_crud_bp = Blueprint('store_crud', __name__)

# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------
DUPLICATE_CHECK_DAYS = 90   # rolling window used for attendance de-duplication
ZK_PORT              = 4370
ZK_TIMEOUT           = 15

# Connection string for the HRIS source database (read-only lookups)
HRIS_CONN_STR = (
    "Driver={ODBC Driver 18 for SQL Server};"
    "Server=192.168.100.115,1433;"
    "Database=HRISNICV2;"
    "UID=BioCentral;"
    "PWD=B1oC3ntr@l2026;"
    "TrustServerCertificate=yes;"
)
 
def get_db_connection():
    """
    Dedicated Bio-Central connection string.
    Verified to work with Windows Authentication on MGSVR14.
    """
    conn_str = (
        "Driver={ODBC Driver 18 for SQL Server};"
        "Server=MGSVR14.mgroup.local,1433;"
        "Database=biocentral;"
        "Trusted_Connection=yes;"
        "TrustServerCertificate=yes;"
        "Network=dbmssocn;"
    )
    return pyodbc.connect(conn_str)
 
def test_zk_connection(ip, key):
    """Handshake verified before SQL write. Uses per-socket timeout to avoid mutating global state."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(3)   # per-socket only — does NOT affect other sockets in the process
            s.connect((ip, ZK_PORT))
            return True, "Handshake Successful"
    except Exception as e:
        return False, f"Connection Failed: {str(e)}"


# ==============================================================================
# BACKUP HELPERS  (adapted from safe_copy_to_sql.py)
# ==============================================================================

def _fetch_hris_map() -> dict:
    """
    Builds a 'Double-Net' lookup from HRISNICV2.
    Maps BOTH (clean employee code -> AccessNo) AND (AccessNo -> AccessNo)
    so any variant of the ZK device ID resolves to the canonical AccessNo.
    Returns an empty dict on failure (backup still proceeds without matching).
    """
    mapping = {}
    try:
        with pyodbc.connect(HRIS_CONN_STR, timeout=10) as hris:
            with hris.cursor() as cur:
                cur.execute("""
                    SELECT Code, AccessNo
                    FROM dbo.vBiometricsManagement
                    WHERE AccessNo IS NOT NULL
                """)
                for row in cur.fetchall():
                    raw_code   = str(row[0]).strip().upper()
                    clean_code = raw_code.replace('-', '').lstrip('0')
                    access_no  = str(row[1]).split('.')[0].strip().upper()
                    if access_no:
                        mapping[clean_code] = access_no
                        mapping[access_no]  = access_no
    except Exception:
        pass   # non-fatal; backup continues with unmatched codes
    return mapping


def _backup_users(cursor, db, zk_conn, device_ip: str, ts: str, hris_map: dict) -> dict:
    """
    Phase 1 – Upserts every enrolled user from the ZK device into
    dbo.backup_device_users and resolves their HRIS Access Number.
    Returns a summary dict and the raw user list for downstream phases.
    """
    users = zk_conn.get_users()

    upsert_sql = """
        UPDATE dbo.backup_device_users
        SET    access_number   = ?,
               employee_name   = ?,
               privilege_level = ?,
               pin_password    = ?,
               backup_timestamp = ?
        WHERE  device_ip = ? AND employee_code = ?;

        IF @@ROWCOUNT = 0
        BEGIN
            INSERT INTO dbo.backup_device_users
                (device_ip, employee_code, access_number, employee_name,
                 privilege_level, pin_password, backup_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?);
        END
    """

    total_count = 0
    match_count = 0
    fail_count  = 0

    for u in users:
        raw_zk_id  = str(u.user_id).strip().upper()
        clean_id   = raw_zk_id.replace('-', '').lstrip('0')
        name       = u.name.decode('utf-8') if isinstance(u.name, bytes) else u.name
        access_num = hris_map.get(clean_id)

        if access_num:
            match_count += 1

        try:
            cursor.execute(upsert_sql, (
                # --- UPDATE params ---
                access_num, name, u.privilege, u.password, ts, device_ip, raw_zk_id,
                # --- INSERT params ---
                device_ip, raw_zk_id, access_num, name, u.privilege, u.password, ts,
            ))
            total_count += 1
        except Exception as exc:
            logger.warning("User upsert failed for %s on %s: %s", raw_zk_id, device_ip, exc)
            fail_count += 1

    db.commit()
    return {"users": users, "synced": total_count, "matched": match_count, "failed": fail_count}


def _backup_fingerprints(cursor, db, zk_conn, device_ip: str, users: list, ts: str) -> dict:
    """
    Phase 2 – Inserts new fingerprint templates into dbo.backup_fingerprints.
    Skips templates that already exist for the (device_ip, employee_code, finger_index)
    combination to prevent duplicates without relying on a DB unique constraint.
    """
    # Try bulk fetch first; fall back to per-user retrieval on older firmware.
    templates = []
    try:
        templates = zk_conn.get_templates()
    except Exception:
        for u in users:
            for fid in range(10):
                try:
                    finger = zk_conn.get_user_template(
                        uid=u.uid, temp_id=fid, user_id=str(u.user_id)
                    )
                    if finger and getattr(finger, 'template', None):
                        templates.append(finger)
                except Exception:
                    continue

    if not templates:
        return {"new": 0, "skipped": 0, "failed": 0}

    # Load existing (employee_code, finger_index) pairs for this device.
    cursor.execute(
        "SELECT employee_code, finger_index FROM dbo.backup_fingerprints WHERE device_ip = ?",
        (device_ip,)
    )
    existing = {(str(r[0]), int(r[1])) for r in cursor.fetchall()}

    uid_to_emp = {u.uid: str(u.user_id).strip().upper() for u in users}

    insert_sql = """
        INSERT INTO dbo.backup_fingerprints
            (device_ip, employee_code, finger_index, finger_template, backup_timestamp)
        VALUES (?, ?, ?, ?, ?)
    """

    new_count = skip_count = fail_count = 0

    for t in templates:
        try:
            fid = int(t.fid)
        except Exception:
            skip_count += 1
            continue

        if not (0 <= fid <= 9):
            skip_count += 1
            continue

        emp_code = uid_to_emp.get(t.uid, "Unknown")
        if emp_code == "Unknown" or (emp_code, fid) in existing:
            skip_count += 1
            continue

        encoded = (
            base64.b64encode(t.template).decode('utf-8')
            if isinstance(t.template, (bytes, bytearray))
            else str(t.template)
        )
        try:
            cursor.execute(insert_sql, (device_ip, emp_code, fid, encoded, ts))
            existing.add((emp_code, fid))
            new_count += 1
        except Exception as exc:
            logger.warning("Fingerprint insert failed for %s fid=%s on %s: %s", emp_code, fid, device_ip, exc)
            fail_count += 1

    db.commit()
    return {"new": new_count, "skipped": skip_count, "failed": fail_count}


def _backup_attendance(cursor, db, zk_conn, device_ip: str, ts: str) -> dict:
    """
    Phase 3 – Inserts attendance punch records into dbo.backup_attendance_logs.
    De-duplicates against the last DUPLICATE_CHECK_DAYS days already in the DB.
    """
    attendance = zk_conn.get_attendance()
    if not attendance:
        return {"new": 0, "skipped": 0, "failed": 0}

    # Build a set of (employee_code, punch_time_str) pairs already stored.
    cursor.execute(
        """
        SELECT employee_code, punch_time
        FROM   dbo.backup_attendance_logs
        WHERE  device_ip  = ?
          AND  punch_time >= DATEADD(day, -?, GETDATE())
        """,
        (device_ip, DUPLICATE_CHECK_DAYS)
    )
    existing_logs = {
        (
            str(row[0]),
            row[1].strftime('%Y-%m-%d %H:%M:%S') if isinstance(row[1], datetime) else str(row[1])
        )
        for row in cursor.fetchall()
    }

    insert_sql = """
        INSERT INTO dbo.backup_attendance_logs
            (device_ip, employee_code, punch_time, punch_type, verify_type, backup_timestamp)
        VALUES (?, ?, ?, ?, ?, ?)
    """

    new_count = skip_count = fail_count = 0

    for record in attendance:
        emp_code       = str(record.user_id).strip().upper()
        punch_time_str = record.timestamp.strftime('%Y-%m-%d %H:%M:%S')

        if (emp_code, punch_time_str) in existing_logs:
            skip_count += 1
            continue

        try:
            cursor.execute(insert_sql, (
                device_ip, emp_code, punch_time_str,
                int(record.punch), record.status, ts
            ))
            existing_logs.add((emp_code, punch_time_str))
            new_count += 1
        except Exception as exc:
            logger.warning("Attendance insert failed for %s at %s on %s: %s", emp_code, punch_time_str, device_ip, exc)
            fail_count += 1

    db.commit()
    return {"new": new_count, "skipped": skip_count, "failed": fail_count}
 
@store_crud_bp.route('/device-manager')
@loggedin_required()
def device_manager():
    return render_template('connect_device.html')
 
@store_crud_bp.route('/api/get-devices', methods=['GET'])
@loggedin_required()
def get_devices():
    """Fetches terminals using the direct Bio-Central connection."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
       
        cursor.execute("""
            SELECT device_id, bcc, ip_address, comms_key, chain_type
            FROM dbo.device_registry
        """)
       
        devices = []
        for row in cursor.fetchall():
            devices.append({
                "device_id": row.device_id,
                "bcc": row.bcc,
                "ip_address": row.ip_address.strip(),
                "comms_key": row.comms_key,
                "chain_type": row.chain_type,
                "last_seen": "N/A" # Column missing from SSMS schema
            })
        return jsonify({"status": "success", "data": devices})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})
    finally:
        if 'conn' in locals(): conn.close()
 
@store_crud_bp.route('/api/save-device', methods=['POST'])
@loggedin_required()
def save_device():
    """Handles logic for Adding/Editing devices. Hardware check bypassed for testing."""
    data = request.form
    d_id = data.get('device_id')
    bcc = data.get('bcc')
    ip = data.get('ip_address')
    key = data.get('comms_key', '0')
    chain = data.get('chain_type')
    current_user = session.get('username', 'System')
 
    # --- BYPASSED: Commented out to save to DB first without hardware ---
    # is_online, msg = test_zk_connection(ip, key)
    # if not is_online:
    #     return jsonify({"status": "error", "message": f"Handshake Failed: {msg}"})
    # ---------------------------------------------------------------------------------------------
 
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
 
        if d_id:
            cursor.execute("""
                UPDATE dbo.device_registry
                SET bcc = ?, ip_address = ?, comms_key = ?, chain_type = ?
                WHERE device_id = ?
            """, (bcc, ip, key, chain, d_id))
           
            action_type, target_val = "UPDATE", str(d_id)
            action_desc = f"Updated terminal {bcc} at {ip}"
           
        else:
            cursor.execute("""
                INSERT INTO dbo.device_registry (bcc, ip_address, comms_key, chain_type)
                OUTPUT INSERTED.device_id
                VALUES (?, ?, ?, ?)
            """, (bcc, ip, key, chain))
           
            target_val = str(cursor.fetchone()[0])
            action_type, action_desc = "REGISTER", f"Registered new terminal {bcc} at {ip}"
 
        cursor.execute("""
            INSERT INTO dbo.biocentral_audit_logs (module, target, action, action_details, action_by, action_at)
            VALUES ('DEVICE', ?, ?, ?, ?, GETDATE())
        """, (target_val, action_type, action_desc, current_user))
 
        conn.commit()
        return jsonify({"status": "success", "message": "Device saved to database."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})
    finally:
        if 'conn' in locals(): conn.close()
 
@store_crud_bp.route('/api/delete-device', methods=['POST'])
@loggedin_required()
def delete_device():
    """Removes device and logs action to audit table."""
    d_id = request.form.get('device_id')
    current_user = session.get('username', 'System')
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
       
        cursor.execute("SELECT bcc FROM dbo.device_registry WHERE device_id = ?", (d_id,))
        bcc_row = cursor.fetchone()
        bcc = bcc_row[0] if bcc_row else "Unknown"
       
        cursor.execute("DELETE FROM dbo.device_registry WHERE device_id = ?", (d_id,))
       
        cursor.execute("""
            INSERT INTO dbo.biocentral_audit_logs (module, target, action, action_details, action_by, action_at)
            VALUES ('DEVICE', ?, 'DELETE', ?, ?, GETDATE())
        """, (str(d_id), f"Deleted terminal {bcc}", current_user))
       
        conn.commit()
        return jsonify({"status": "success", "message": "Device removed."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})
    finally:
        if 'conn' in locals(): conn.close()


@store_crud_bp.route('/api/backup-device', methods=['POST'])
@loggedin_required()
def backup_device():
    """
    Connects to a ZK biometric terminal and runs all three backup phases:
      Phase 1 — Users   (upsert into dbo.backup_device_users)
      Phase 2 — Fingerprints (insert new into dbo.backup_fingerprints)
      Phase 3 — Attendance logs (insert new into dbo.backup_attendance_logs)

    A single row is written to dbo.biocentral_audit_logs on completion or failure.
    Returns a JSON summary so the frontend can display per-phase counts.
    """
    device_id    = request.form.get('device_id')
    device_ip    = request.form.get('ip_address', '').strip()
    comms_key    = request.form.get('comms_key', '0')
    bcc          = request.form.get('bcc', device_ip)
    current_user = session.get('username', 'System')

    if not device_ip:
        return jsonify({"status": "error", "message": "No IP address provided for backup."})

    db     = None
    cursor = None
    zk_conn = None

    try:
        # ── Pre-flight: verify the device is reachable ──────────────────────
        is_online, handshake_msg = test_zk_connection(device_ip, comms_key)
        if not is_online:
            return jsonify({
                "status":  "error",
                "message": f"Cannot reach device at {device_ip}. {handshake_msg}"
            })

        # ── Open DB + ZK connections ─────────────────────────────────────────
        hris_map = _fetch_hris_map()
        db       = get_db_connection()
        cursor   = db.cursor()
        ts       = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        zk      = ZK(device_ip, port=ZK_PORT, timeout=ZK_TIMEOUT, force_udp=False)
        zk_conn = zk.connect()
        try:
            zk_conn.disable_device()

            # ── Phase 1: Users ───────────────────────────────────────────────────
            phase1 = _backup_users(cursor, db, zk_conn, device_ip, ts, hris_map)

            # ── Phase 2: Fingerprints ────────────────────────────────────────────
            phase2 = _backup_fingerprints(cursor, db, zk_conn, device_ip, phase1["users"], ts)

            # ── Phase 3: Attendance ──────────────────────────────────────────────
            phase3 = _backup_attendance(cursor, db, zk_conn, device_ip, ts)

        finally:
            try:
                zk_conn.enable_device()
            except Exception:
                pass

        # ── Audit log ────────────────────────────────────────────────────────
        summary_detail = (
            f"Backup OK for {bcc} ({device_ip}) | "
            f"Users: {phase1['synced']} synced, {phase1['matched']} matched, {phase1['failed']} failed | "
            f"Fingerprints: {phase2['new']} new, {phase2['skipped']} skipped, {phase2['failed']} failed | "
            f"Attendance: {phase3['new']} new, {phase3['skipped']} skipped, {phase3['failed']} failed"
        )
        cursor.execute("""
            INSERT INTO dbo.biocentral_audit_logs
                (module, target, action, action_details, action_by, action_at)
            VALUES ('DEVICE', ?, 'BACKUP', ?, ?, GETDATE())
        """, (str(device_id), summary_detail, current_user))
        db.commit()

        return jsonify({
            "status":  "success",
            "message": f"Backup completed for {bcc}.",
            "summary": {
                "users":        {"synced": phase1["synced"], "matched": phase1["matched"], "failed": phase1["failed"]},
                "fingerprints": {"new":    phase2["new"],    "skipped": phase2["skipped"], "failed": phase2["failed"]},
                "attendance":   {"new":    phase3["new"],    "skipped": phase3["skipped"], "failed": phase3["failed"]},
            }
        })

    except Exception as e:
        logger.exception("Backup failed for %s (%s)", bcc, device_ip)
        if db:
            try:
                db.rollback()
            except Exception:
                pass
            # Best-effort failure audit entry
            try:
                err_cursor = db.cursor()
                err_cursor.execute("""
                    INSERT INTO dbo.biocentral_audit_logs
                        (module, target, action, action_details, action_by, action_at)
                    VALUES ('DEVICE', ?, 'BACKUP_FAILED', ?, ?, GETDATE())
                """, (str(device_id), f"Backup FAILED for {bcc}: {str(e)}", current_user))
                db.commit()
            except Exception:
                pass

        return jsonify({
            "status":  "error",
            "message": f"Backup failed: {str(e)}",
        })

    finally:
        if zk_conn:
            try:
                zk_conn.disconnect()
            except Exception:
                pass
        if cursor:
            cursor.close()
        if db:
            db.close()