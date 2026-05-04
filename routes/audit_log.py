from flask import Blueprint, render_template, session, jsonify, flash, request
import pyodbc

# Connection String
BIOCENTRAL_CONN = (
    "Driver={ODBC Driver 18 for SQL Server};"
    "Server=MGSVR14.mgroup.local,1433;"
    "Database=biocentral;"
    "Trusted_Connection=yes;"
    "Network=dbmssocn;"
    "TrustServerCertificate=yes;"
)

audit_log_bp = Blueprint('audit_log', __name__)

@audit_log_bp.route('/audit-logs')
def view_audit_logs():
    # Import inside the function to prevent "ModuleNotFoundError" or Circular Imports
    from portal import loggedin_required
    
    @loggedin_required()
    def wrapped_view():
        logs = []
        try:
            conn = pyodbc.connect(BIOCENTRAL_CONN + "app=AuditViewer;")
            cursor = conn.cursor()

            cursor.execute("""
                SELECT TOP 100
                    audit_id, action_at, action_by, module, target, action, action_details
                FROM biocentral_audit_logs
                WHERE module != 'AUTH' 
                ORDER BY action_at DESC
            """)
            raw = cursor.fetchall()

            # Mapping logic
            device_ids = list({
                int(row[4]) for row in raw
                if row[3] == 'DEVICE' and row[4] is not None and str(row[4]).strip().isdigit()
            })

            device_map = {}
            if device_ids:
                placeholders = ','.join('?' * len(device_ids))
                cursor.execute(
                    f"SELECT device_id, bcc, ip_address, chain_type FROM device_registry WHERE device_id IN ({placeholders})",
                    device_ids
                )
                for d in cursor.fetchall():
                    device_map[d[0]] = (d[1], d[2], d[3])

            for row in raw:
                action_at = row[1].strftime('%Y-%m-%d %H:%M:%S') if row[1] else 'N/A'
                dev = device_map.get(int(row[4]), (None, None, None)) if row[3] == 'DEVICE' else (None, None, None)
                logs.append([row[0], action_at, row[2], row[3], row[4], row[5], row[6], dev[0], dev[1], dev[2]])

        except Exception as e:
            flash(f"Error fetching logs: {e}")
        finally:
            if 'cursor' in locals(): cursor.close()
            if 'conn' in locals(): conn.close()

        return render_template('audit_logs.html', logs=logs)
    
    return wrapped_view()

@audit_log_bp.route('/audit-logs/device/<int:device_id>')
def audit_device_detail(device_id):
    from portal import loggedin_required
    
    @loggedin_required()
    def wrapped_detail(device_id):
        try:
            conn = pyodbc.connect(BIOCENTRAL_CONN + "app=AuditViewer;")
            cursor = conn.cursor()
            cursor.execute("SELECT device_id, bcc, ip_address, comms_key, chain_type FROM device_registry WHERE device_id = ?", (device_id,))
            row = cursor.fetchone()
            if not row: return jsonify({'error': 'Not found'}), 404
            return jsonify({
                'device_id': row[0], 
                'bcc': row[1], 
                'ip_address': row[2].strip(), 
                'comms_key': row[3], 
                'chain_type': row[4]
            })
        except Exception as e:
            return jsonify({'error': str(e)}), 500
        finally:
            if 'cursor' in locals(): cursor.close()
            if 'conn' in locals(): conn.close()
            
    return wrapped_detail(device_id)