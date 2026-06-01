import csv
import io
import pyodbc
from collections import defaultdict
from flask import Blueprint, render_template, request, jsonify, session, Response
from portal import app, loggedin_required
from zk import ZK, const

get_employees_bp = Blueprint('get_employees', __name__)

def get_db_connection():
    """Verified Bio-Central connection string for MGSVR17."""
    conn_str = (
        "Driver={ODBC Driver 18 for SQL Server};"
        "Server=MGSVR17.mgroup.local,1433;"
        "Database=biocentral;"
        "Trusted_Connection=yes;"
        "TrustServerCertificate=yes;"
        "Network=dbmssocn;"
    )
    return pyodbc.connect(conn_str)

@get_employees_bp.route('/get-employee', methods=['GET'])
@loggedin_required()
def get_employee_page():
    return render_template('get_employee.html')

@get_employees_bp.route('/api/fetch-devices', methods=['GET'])
@loggedin_required()
def fetch_devices():
    """Dynamically fetches all registered devices from the database."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT device_id, bcc, ip_address, chain_type FROM dbo.device_registry")
        
        devices = []
        for row in cursor.fetchall():
            devices.append({
                "device_id": row.device_id,
                "bcc": row.bcc,
                "ip_address": row.ip_address.strip(),
                "chain_type": row.chain_type
            })
            
        return jsonify({"status": "success", "data": devices})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})
    finally:
        if 'conn' in locals(): conn.close()

@get_employees_bp.route('/api/fetch-employees', methods=['POST'])
@loggedin_required()
def fetch_employees():
    """Fetches employees live from the physical ZKTeco device."""
    try:
        device_id = request.form.get('device_id')
        
        conn_db = get_db_connection()
        cursor = conn_db.cursor()
        cursor.execute("SELECT ip_address, comms_key FROM dbo.device_registry WHERE device_id = ?", (device_id,))
        device_row = cursor.fetchone()
        conn_db.close()

        if not device_row:
            return jsonify({"status": "error", "message": "Device not found in SQL registry."})

        ip = device_row.ip_address.strip()
        key = int(device_row.comms_key) if device_row.comms_key.isdigit() else 0

        zk = ZK(ip, port=4370, timeout=5, password=key, force_udp=False, ommit_ping=False)
        conn_zk = None
        
        try:
            conn_zk = zk.connect()
            users = conn_zk.get_users()
            
            employee_data = []
            for user in users:
                display_name = user.name if user.name else "UNNAMED FINGERPRINT"
                employee_data.append({
                    "id": str(user.user_id),
                    "name": display_name,
                    "status": "Enrolled"
                })
            
            return jsonify({"status": "success", "data": employee_data})
        except Exception as e:
            return jsonify({"status": "error", "message": f"Hardware Connection Error: {str(e)}"})
        finally:
            if conn_zk: conn_zk.disconnect()
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@get_employees_bp.route('/api/fetch-logs', methods=['POST'])
@loggedin_required()
def fetch_logs():
    """Fetches attendance logs live from the ZKTeco device for a specific employee."""
    try:
        device_id = request.form.get('device_id')
        emp_id = request.form.get('emp_id')
        
        conn_db = get_db_connection()
        cursor = conn_db.cursor()
        cursor.execute("SELECT ip_address, comms_key FROM dbo.device_registry WHERE device_id = ?", (device_id,))
        device_row = cursor.fetchone()
        conn_db.close()

        if not device_row:
            return jsonify({"status": "error", "message": "Device not found in SQL registry."})

        ip = device_row.ip_address.strip()
        key = int(device_row.comms_key) if device_row.comms_key.isdigit() else 0

        zk = ZK(ip, port=4370, timeout=5, password=key, force_udp=False, ommit_ping=False)
        conn_zk = None
        
        try:
            conn_zk = zk.connect()
            attendances = conn_zk.get_attendance()
            
            logs_data = []
            for att in attendances:
                if str(att.user_id) == str(emp_id):
                    punch_type = "TIME IN" if getattr(att, 'punch', -1) == 0 else "TIME OUT" if getattr(att, 'punch', -1) == 1 else "LOGGED"
                    
                    logs_data.append({
                        "date": att.timestamp.strftime('%B %d, %Y'),
                        "time": att.timestamp.strftime('%I:%M %p'),
                        "type": punch_type,
                        "raw_time": att.timestamp 
                    })
            logs_data.sort(key=lambda x: x['raw_time'], reverse=True)
            for log in logs_data:
                del log['raw_time']
            return jsonify({"status": "success", "data": logs_data[:50]})
            
        except Exception as e:
            return jsonify({"status": "error", "message": f"Hardware Connection Error: {str(e)}"})
        finally:
            if conn_zk: conn_zk.disconnect()
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@get_employees_bp.route('/api/backup-logs', methods=['GET'])
@loggedin_required()
def backup_logs():
    """Fetches logs and users from hardware and returns a formatted CSV with Time In/Out."""
    try:
        device_id = request.args.get('device_id')
        conn_db = get_db_connection()
        cursor = conn_db.cursor()
        cursor.execute("SELECT bcc, ip_address, comms_key FROM dbo.device_registry WHERE device_id = ?", (device_id,))
        device_row = cursor.fetchone()
        conn_db.close()

        if not device_row:
            return "Device not found in SQL registry.", 404

        ip = device_row.ip_address.strip()
        key = int(device_row.comms_key) if device_row.comms_key.isdigit() else 0
        device_name = device_row.bcc.replace(" ", "_")

        zk = ZK(ip, port=4370, timeout=10, password=key, force_udp=False, ommit_ping=False)
        conn_zk = None
        try:
            conn_zk = zk.connect()
            users = conn_zk.get_users()
            user_map = {str(u.user_id): (u.name if u.name else "UNNAMED") for u in users}
            attendances = conn_zk.get_attendance()
            grouped_logs = defaultdict(list)
            for att in attendances:
                uid = str(att.user_id)
                date_str = att.timestamp.strftime('%Y-%m-%d')
                time_obj = att.timestamp.time()
                grouped_logs[(uid, date_str)].append(time_obj)
            
            output = io.StringIO()
            writer = csv.writer(output)
            
            writer.writerow(['Device Location', 'Employee Name', 'Employee ID', 'Date', 'Time In', 'Time Out'])
            
            for (uid, date_str), times in grouped_logs.items():
                emp_name = user_map.get(uid, "UNKNOWN")
                times.sort()
                time_in = times[0].strftime('%I:%M %p')
                time_out = times[-1].strftime('%I:%M %p') if len(times) > 1 else "--"
                
                writer.writerow([
                    device_row.bcc, 
                    emp_name, 
                    uid, 
                    date_str, 
                    time_in, 
                    time_out
                ])

            output.seek(0)
            return Response(
                output.getvalue(),
                mimetype="text/csv",
                headers={"Content-disposition": f"attachment; filename={device_name}_backup.csv"}
            )
        except Exception as e:
            return f"Hardware Connection Error: {str(e)}", 500
        finally:
            if conn_zk: conn_zk.disconnect()
    except Exception as e:
        return f"Backup failed: {str(e)}", 500

@get_employees_bp.route('/api/purge-logs', methods=['POST'])
@loggedin_required()
def purge_logs():
    """Wipes the attendance buffer on the physical ZKTeco device."""
    try:
        device_id = request.form.get('device_id')
        password = request.form.get('password')

        if password != "123123":
            return jsonify({"status": "error", "message": "Unauthorized: Invalid PIN."})

        conn_db = get_db_connection()
        cursor = conn_db.cursor()
        cursor.execute("SELECT ip_address, comms_key FROM dbo.device_registry WHERE device_id = ?", (device_id,))
        device_row = cursor.fetchone()
        conn_db.close()

        if not device_row:
            return jsonify({"status": "error", "message": "Device not found in SQL registry."})

        ip = device_row.ip_address.strip()
        key = int(device_row.comms_key) if device_row.comms_key.isdigit() else 0

        zk = ZK(ip, port=4370, timeout=15, password=key, force_udp=False, ommit_ping=False)
        conn_zk = None
        try:
            conn_zk = zk.connect()
            conn_zk.clear_attendance() 
            return jsonify({"status": "success", "message": "Device storage cleared successfully."})
        except Exception as e:
            return jsonify({"status": "error", "message": f"Hardware Connection Error: {str(e)}"})
        finally:
            if conn_zk: conn_zk.disconnect()
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})