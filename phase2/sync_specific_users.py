import base64
from datetime import datetime
from zk import ZK
import mysql.connector

# --- CONFIGURATION ---
DB_CONFIG = {
    'host': '127.0.0.1',
    'user': 'root',
    'password': '',
    'database': 'biocentral'
}
DEVICE_IP = '192.168.100.162'
TARGET_EMPLOYEES = ['11111512', '40626479', '11110048', '12925456']

def sync_specific_users():
    db = mysql.connector.connect(**DB_CONFIG)
    cursor = db.cursor()
    zk = ZK(DEVICE_IP, port=4370, timeout=15)
    
    try:
        conn = zk.connect()
        conn.disable_device()
        
        # 1. FETCH USERS & CREATE UID MAP
        users = conn.get_users()
        # This map translates the internal UID to your Employee Code
        uid_to_emp_code = {u.uid: str(u.user_id).strip() for u in users}
        
        for u in users:
            emp_code = str(u.user_id).strip()
            if emp_code in TARGET_EMPLOYEES:
                print(f"[*] Syncing user: {u.name} ({emp_code})")
                cursor.execute("""
                    INSERT INTO backup_device_users 
                    (device_ip, employee_code, access_number, employee_name, privilege_level, pin_password)
                    VALUES (%s, %s, NULL, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE 
                    employee_name = VALUES(employee_name), pin_password = VALUES(pin_password)
                """, (DEVICE_IP, emp_code, u.name, u.privilege, u.password))
        
        # 2. SYNC FINGERPRINTS (Fixed: Use uid_to_emp_code map)
        print("[*] Syncing fingerprints...")
        templates = conn.get_templates()
        for t in templates:
            # Look up the employee_code using the template's UID
            emp_code = uid_to_emp_code.get(t.uid)
            
            if emp_code and emp_code in TARGET_EMPLOYEES:
                template_b64 = base64.b64encode(t.template).decode('utf-8')
                cursor.execute("""
                    INSERT INTO backup_fingerprints 
                    (device_ip, employee_code, finger_index, finger_template)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE finger_template = VALUES(finger_template)
                """, (DEVICE_IP, emp_code, t.fid, template_b64))

        # 3. SYNC ATTENDANCE LOGS
        print("[*] Syncing logs...")
        logs = conn.get_attendance()
        for log in logs:
            emp_code = str(log.user_id).strip()
            if emp_code in TARGET_EMPLOYEES:
                cursor.execute("""
                    INSERT IGNORE INTO backup_attendance_logs 
                    (device_ip, employee_code, punch_time, punch_type, verify_type)
                    VALUES (%s, %s, %s, %s, %s)
                """, (DEVICE_IP, emp_code, log.timestamp, log.punch, log.status))

        db.commit()
        print(f"[+] Sync successful for the 4 target employees.")

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[!] Error: {e}")
    finally:
        if 'conn' in locals() and conn: conn.disconnect()
        db.close()

if __name__ == "__main__":
    sync_specific_users()