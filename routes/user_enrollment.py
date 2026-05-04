import pyodbc
from flask import Blueprint, request, jsonify, render_template
from zk import ZK, const

enroll_bp = Blueprint('enroll_bp', __name__)

# --- Enterprise DB Credentials (HRIS Employee Profiles) ---
DB_SERVER = '192.168.100.115'
DB_NAME = 'HRISNICV2'
DB_UID = 'BioCentral'
DB_PWD = 'B1oC3ntr@l2026'

def fetch_all_devices():
    """
    Fetches all store devices and their IPs directly from the Bio-Central registry.
    Uses the connection logic verified in store_crud.py.
    """
    conn_str = (
        "Driver={ODBC Driver 18 for SQL Server};"
        "Server=MGSVR14.mgroup.local,1433;"
        "Database=biocentral;"
        "Trusted_Connection=yes;"
        "TrustServerCertificate=yes;"
        "Network=dbmssocn;"
    )
    
    query = "SELECT bcc, ip_address FROM dbo.device_registry"
    
    devices = []
    try:
        with pyodbc.connect(conn_str) as conn:
            with conn.cursor() as cursor:
                cursor.execute(query)
                for row in cursor.fetchall():
                    devices.append({
                        "name": row.bcc,
                        "ip": row.ip_address.strip()
                    })
    except pyodbc.Error as e:
        print(f"Database device lookup failed: {e}")
        
    return devices

@enroll_bp.route('/new_fingerprint', methods=['GET'])
def new_fingerprint_page():
    # Fetch devices from Bio-Central DB before rendering the page
    devices = fetch_all_devices()
    return render_template('new_fingerprint.html', devices=devices)


def fetch_employee_info(search_query):
    """Fetches the official employee name and AccessNo from the vBiometricsManagement view."""
    conn_str = (
        f"DRIVER={{ODBC Driver 18 for SQL Server}};"
        f"SERVER={DB_SERVER};"
        f"DATABASE={DB_NAME};"
        f"UID={DB_UID};"
        f"PWD={DB_PWD};"
        f"TrustServerCertificate=yes;"
    )
    
    # Parameterized query to check either Code or Name
    query = "SELECT [Name], [AccessNo] FROM [dbo].[vBiometricsManagement] WHERE [Code] = ? OR [Name] = ?"
    
    try:
        with pyodbc.connect(conn_str) as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (search_query, search_query))
                row = cursor.fetchone()
                if row:
                    return row[0], row[1] # Returns Name, AccessNo
    except pyodbc.Error as e:
        print(f"Database lookup failed: {e}")
        
    return None, None


# --- POST Route: Hardware API Endpoint ---
@enroll_bp.route('/api/enroll_fingerprint', methods=['POST'])
def enroll_fingerprint():
    data = request.json
    ip = data.get('ip')
    port = int(data.get('port', 4370))
    search_query = data.get('search_query') 
    temp_id = data.get('temp_id')
    
    if not ip or not search_query or temp_id is None:
        return jsonify({"status": "error", "message": "Store IP, Search Query, and Finger Selection are required."}), 400

    employee_name, access_no = fetch_employee_info(search_query)
    if not employee_name or not access_no:
         return jsonify({"status": "error", "message": f"Employee '{search_query}' not found."}), 404

    zk = ZK(ip, port=port, timeout=5, password=0, force_udp=False, ommit_ping=False)
    conn = None
    try:
        conn = zk.connect()
        conn.disable_device() 
        
        device_users = conn.get_users()
        target_uid = None
        
        for u in device_users:
            if str(u.user_id) == str(access_no):
                target_uid = u.uid
                break
                
        if target_uid is None:
            if device_users:
                target_uid = max([u.uid for u in device_users]) + 1
            else:
                target_uid = 1
                
        if target_uid > 65535:
             raise Exception("Scanner's internal index is full (> 65535).")

        conn.set_user(uid=target_uid, name=employee_name, privilege=const.USER_DEFAULT, password='', group_id='', user_id=str(access_no))
        
        # ⚠️ Blocking call: Waits for success, timeout, or duplicate rejection.
        conn.enroll_user(uid=target_uid, temp_id=int(temp_id), user_id=str(access_no))
        
        # --- EXPERT FIX: Hardware Verification ---
        # Prove the device actually stored the template in memory.
        templates = conn.get_templates()
        is_saved = False
        
        for t in templates:
            # Match the exact internal UID and the specific finger index (fid)
            if str(t.uid) == str(target_uid) and str(t.fid) == str(temp_id):
                is_saved = True
                break
                
        if not is_saved:
            return jsonify({
                "status": "error", 
                "message": "Device rejected the enrollment. The fingerprint is a DUPLICATE, the scanner timed out, or the user cancelled."
            }), 400
        # -----------------------------------------

        return jsonify({
            "status": "success", 
            "emp_name": employee_name,
            "access_no": access_no,
            "message": "Physical fingerprint verified and saved to hardware."
        })
        
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
        
    finally:
        if conn:
            conn.enable_device() 
            conn.disconnect()

@enroll_bp.route('/api/live_search_employee', methods=['GET'])
def live_search_employee():
    """Returns top 10 employee matches as the user types."""
    search_term = request.args.get('q', '').strip()
    if not search_term or len(search_term) < 2:
        return jsonify([])

    conn_str = (
        f"DRIVER={{ODBC Driver 18 for SQL Server}};"
        f"SERVER={DB_SERVER};" # Using your HRIS credentials
        f"DATABASE={DB_NAME};"
        f"UID={DB_UID};"
        f"PWD={DB_PWD};"
        f"TrustServerCertificate=yes;"
    )
    
    # Use LIKE operator for partial, case-insensitive matching
    query = """
        SELECT TOP 10 [Name], [Code], [AccessNo] 
        FROM [dbo].[vBiometricsManagement] 
        WHERE [Name] LIKE ? OR [Code] LIKE ?
    """
    wildcard_term = f"%{search_term}%"
    
    results = []
    try:
        with pyodbc.connect(conn_str) as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (wildcard_term, wildcard_term))
                for row in cursor.fetchall():
                    results.append({
                        "name": row[0],
                        "code": row[1],
                        "access_no": str(row[2])
                    })
    except pyodbc.Error as e:
        print(f"Live search failed: {e}")
        
    return jsonify(results)