from flask import Blueprint, render_template, jsonify, request
from datetime import datetime
from zk import ZK
import pyodbc

# Reusing your connection string format
BIOCENTRAL_CONN = (
    "Driver={ODBC Driver 18 for SQL Server};"
    "Server=MGSVR14.mgroup.local,1433;"
    "Database=biocentral;"
    "Trusted_Connection=yes;"
    "Network=dbmssocn;"
    "TrustServerCertificate=yes;"
)

sync_time_bp = Blueprint('sync_time', __name__)

@sync_time_bp.route('/reset_time')
def reset_time_page():
    from portal import loggedin_required
    
    @loggedin_required()
    def wrapped_view():
        devices = []
        try:
            conn = pyodbc.connect(BIOCENTRAL_CONN + "app=SyncTime;")
            cursor = conn.cursor()
            # Fetching registered devices
            cursor.execute("""
                SELECT device_id, bcc, ip_address, chain_type 
                FROM device_registry 
                ORDER BY bcc ASC
            """)
            
            # 1. Fetch the raw PyODBC rows
            raw_devices = cursor.fetchall()
            
            # 2. Convert them to standard Python lists for JSON serialization
            devices = [list(row) for row in raw_devices]
            
        except Exception as e:
            print(f"Error fetching devices: {e}")
        finally:
            if 'cursor' in locals(): cursor.close()
            if 'conn' in locals(): conn.close()

        return render_template('reset_time.html', devices=devices)
    
    return wrapped_view()


@sync_time_bp.route('/api/ping_device', methods=['POST'])
def ping_device():
    data = request.get_json()
    ip_address = data.get('ip')
    
    if not ip_address:
        return jsonify({"success": False, "message": "No IP address provided."})

    zk = ZK(ip_address, port=4370, timeout=5, password=0, force_udp=False, ommit_ping=False)
    conn = None
    try:
        conn = zk.connect()
        # Connection successful if no exception is thrown
        return jsonify({"success": True, "message": f"Successfully connected to {ip_address}"})
    except Exception as e:
        return jsonify({"success": False, "message": f"Connection failed: {str(e)}"})
    finally:
        if conn:
            conn.disconnect()


@sync_time_bp.route('/api/sync_device', methods=['POST'])
def sync_device():
    data = request.get_json()
    ip_address = data.get('ip')
    client_time_str = data.get('client_time') # <-- Grab the time sent by the frontend
    
    if not ip_address:
        return jsonify({"success": False, "message": "No IP address provided."})
    
    if not client_time_str:
        return jsonify({"success": False, "message": "No client PC time provided in the request payload."})

    try:
        # Convert the string sent by JavaScript into a Python datetime object
        pc_time = datetime.strptime(client_time_str, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return jsonify({"success": False, "message": "Invalid time format. Expected YYYY-MM-DD HH:MM:SS."})

    zk = ZK(ip_address, port=4370, timeout=5)
    conn = None
    try:
        conn = zk.connect()
        
        # Push the parsed PC's current time to the ZKTeco device
        conn.set_time(pc_time)
        
        return jsonify({
            "success": True, 
            "message": f"Time synced successfully to PC time: {pc_time.strftime('%Y-%m-%d %H:%M:%S')}"
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"Sync failed: {str(e)}"})
    finally:
        if conn:
            conn.disconnect()