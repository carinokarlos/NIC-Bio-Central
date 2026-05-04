import pyodbc
import time
from flask import Blueprint, render_template, request, flash, redirect, url_for, session, jsonify
from zk import ZK

move_registration_bp = Blueprint('move_registration', __name__)

BIOCENTRAL_CONN_STR = (
    "Driver={ODBC Driver 18 for SQL Server};"
    "Server=MGSVR14.mgroup.local,1433;"
    "Database=biocentral;"
    "Trusted_Connection=yes;"
    "TrustServerCertificate=yes;"
    "Network=dbmssocn;"
)

def get_biocentral_connection():
    return pyodbc.connect(BIOCENTRAL_CONN_STR)

@move_registration_bp.route('/api/get_device_employees/<ip>')
def get_device_employees(ip):
    zk = ZK(ip, port=4370, timeout=5)
    conn = None
    device_users = []
    
    try:
        conn = zk.connect()
        users = conn.get_users()
        for u in users:
            device_users.append({
                "code": u.user_id,
                "name": u.name,
                "access_no": u.uid
            })
        return jsonify({"status": "success", "users": device_users})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})
    finally:
        if conn:
            conn.disconnect()

@move_registration_bp.route('/move_registration', methods=['GET', 'POST'])
def move_registration():
    if not session.get('sdr_loggedin'):
        if request.method == 'POST':
            return jsonify({"status": "error", "message": "Session expired. Please log in again."}), 401
        return redirect(url_for('index'))

    if request.method == 'POST':
        source_device = request.form.get('source_branch')
        employee_codes = request.form.getlist('employee_id') 
        dest_devices = request.form.getlist('dest_branch') 

        if not source_device or not employee_codes or not dest_devices:
            return jsonify({"status": "error", "message": "Please complete all fields before transferring."})

        if source_device in dest_devices:
            return jsonify({"status": "error", "message": "Origin device cannot be included in the destination devices."})

        # --- 1. PULL TEMPLATES FROM ORIGIN FOR ALL SELECTED EMPLOYEES ---
        zk_source = ZK(source_device, port=4370, timeout=5)
        conn_source = None
        origin_data = {} 

        try:
            conn_source = zk_source.connect()
            conn_source.disable_device()
            time.sleep(0.5)
            
            all_users = conn_source.get_users()
            all_templates = conn_source.get_templates()
            
            for emp_code in employee_codes:
                u_info = next((u for u in all_users if str(u.user_id) == str(emp_code)), None)
                if u_info:
                    u_temps = [t for t in all_templates if str(t.uid) == str(u_info.uid)]
                    origin_data[str(emp_code)] = {
                        'info': u_info,
                        'templates': u_temps
                    }
        except Exception as e:
            return jsonify({"status": "error", "message": f"Error connecting to origin device: {e}"})
        finally:
            if conn_source:
                conn_source.enable_device()
                conn_source.disconnect()

        if not origin_data:
            return jsonify({"status": "error", "message": "None of the selected employees were found on the origin device."})

        # --- 2. PUSH TO TARGETS ---
        success_count = 0
        total_unique_templates = sum(len(data['templates']) for data in origin_data.values())
        
        for dest_ip in dest_devices:
            zk_dest = ZK(dest_ip, port=4370, timeout=10)
            conn_dest = None
            try:
                conn_dest = zk_dest.connect()
                conn_dest.disable_device()
                time.sleep(0.5) 
                
                target_users = conn_dest.get_users()
                
                for emp_code, data in origin_data.items():
                    u_info = data['info']
                    u_temps = data['templates']

                    existing_user = next((u for u in target_users if str(u.user_id) == str(emp_code)), None)
                    
                    if not existing_user:
                        target_uid = max([u.uid for u in target_users], default=0) + 1
                        
                        user_pwd = getattr(u_info, 'password', '')
                        user_grp = getattr(u_info, 'group_id', '')
                        user_card = getattr(u_info, 'card', 0)
                        
                        conn_dest.set_user(
                            uid=target_uid, 
                            name=u_info.name, 
                            privilege=u_info.privilege, 
                            password=user_pwd,
                            group_id=user_grp,
                            user_id=str(emp_code),
                            card=user_card
                        )
                        
                        # --- BULLETPROOF FIX: Fetch the OFFICIAL User Object ---
                        # Instead of a Dummy class, we force the device to confirm the user was created
                        # and grab the exact memory object back from the device.
                        time.sleep(0.5) 
                        target_users = conn_dest.get_users()
                        existing_user = next((u for u in target_users if str(u.user_id) == str(emp_code)), None)

                    if not existing_user:
                        print(f"Warning: Hardware rejected user profile creation for {emp_code}")
                        continue

                    # Push actual fingerprints using the Official User Object
                    for t in u_temps:
                        try:
                            # Strictly bind to the verified hardware object
                            t.uid = existing_user.uid 
                            t.user_id = str(emp_code)
                            t.valid = 1  # Force the template to be active
                            
                            try:
                                # Use the official object fetched directly from the device
                                conn_dest.save_user_template(existing_user, t)
                            except TypeError:
                                conn_dest.save_user_template(t)
                                
                        except Exception as template_err:
                            print(f"Warning on {dest_ip} for template: {template_err}")

                # --- HARDWARE BUFFER REFRESH ---
                conn_dest.refresh_data()
                time.sleep(1) # Extra buffer time to allow re-indexing

                success_count += 1
            except Exception as e:
                print(f"Critical failed to copy to {dest_ip}: {e}")
            finally:
                if conn_dest:
                    conn_dest.enable_device()
                    conn_dest.disconnect()
        
        if success_count > 0:
            return jsonify({
                "status": "success", 
                "message": f"Successfully pushed {len(origin_data)} employee(s) and {total_unique_templates} fingerprint(s) to {success_count} terminal(s)."
            })
        else:
            return jsonify({"status": "error", "message": "Transfer failed on all target devices."})

    # --- GET REQUEST ---
    devices = []
    try:
        with get_biocentral_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT bcc, ip_address FROM dbo.device_registry ORDER BY bcc")
                for row in cursor.fetchall():
                    devices.append({'name': row.bcc, 'ip': row.ip_address.strip()})
    except Exception as e:
        flash("Could not load device list from the database.", "error")

    return render_template('move_registration.html', devices=devices)