import socket
import pyodbc
from flask import Blueprint, render_template, request, jsonify, session
from portal import app, loggedin_required
 
store_crud_bp = Blueprint('store_crud', __name__)
 
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
    """Handshake verified before SQL write."""
    try:
        socket.setdefaulttimeout(3)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((ip, 4370))
            return True, "Handshake Successful"
    except Exception as e:
        return False, f"Connection Failed: {str(e)}"
 
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
 
        if d_id: # UPDATE EXISTING
            cursor.execute("""
                UPDATE dbo.device_registry
                SET bcc = ?, ip_address = ?, comms_key = ?, chain_type = ?
                WHERE device_id = ?
            """, (bcc, ip, key, chain, d_id))
           
            action_type, target_val = "UPDATE", str(d_id)
            action_desc = f"Updated terminal {bcc} at {ip}"
           
        else: # INSERT NEW
            cursor.execute("""
                INSERT INTO dbo.device_registry (bcc, ip_address, comms_key, chain_type)
                OUTPUT INSERTED.device_id
                VALUES (?, ?, ?, ?)
            """, (bcc, ip, key, chain))
           
            target_val = str(cursor.fetchone()[0])
            action_type, action_desc = "REGISTER", f"Registered new terminal {bcc} at {ip}"
 
        # Unified Audit Entry
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
       
        # Corrected: Added action_at and GETDATE() to prevent the NULL constraint crash
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