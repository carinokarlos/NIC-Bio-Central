import base64
from datetime import datetime
from zk import ZK
import mysql.connector

# --- CONFIGURATION ---
DB_HOST = '127.0.0.1'
DB_USER = 'root'
DB_PASS = ''
DB_NAME = 'biocentral'

def sync_device_users_to_sql(device_ip, port=4370):
    print(f"[*] Starting User & Fingerprint Sync for {device_ip}...")
    
    # 1. Connect to MySQL
    try:
        db = mysql.connector.connect(host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME)
        cursor = db.cursor()
    except Exception as e:
        print(f"[!] Database Connection Failed: {e}")
        return

    # 2. Connect to ZK Device
    zk = ZK(device_ip, port=port, timeout=15, force_udp=False)
    conn = None
    
    try:
        conn = zk.connect()
        conn.disable_device()
        
        # --- FETCH USERS ---
        print("[-] Fetching Users from terminal...")
        users = conn.get_users()
        
        # Upsert query: Inserts new users, or updates existing ones based on employee_code + device_ip
        # Upsert query: access_number is now set to NULL
        user_query = """
            INSERT INTO backup_device_users 
            (device_ip, employee_code, access_number, employee_name, privilege_level, pin_password, backup_timestamp)
            VALUES (%s, %s, NULL, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE 
            employee_name = VALUES(employee_name),
            privilege_level = VALUES(privilege_level),
            pin_password = VALUES(pin_password),
            backup_timestamp = VALUES(backup_timestamp)
        """
        
        user_count = 0
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        for u in users:
            emp_id = str(u.user_id).strip()
            # We only pass emp_id once (for employee_code). 
            # The query itself handles the NULL for access_number.
            cursor.execute(user_query, (
                device_ip, 
                emp_id,    # employee_code
                u.name, 
                u.privilege, 
                u.password,
                current_time
            ))
            user_count += 1
            
        db.commit()
        print(f"[+] Successfully synced {user_count} Users to SQL.")

        # --- FETCH FINGERPRINTS ---
        print("[-] Fetching Fingerprints...")
        templates = []
        try:
            templates = conn.get_templates()
        except Exception as e:
            print(f"    [!] Bulk read blocked, attempting Deep Extraction...")
            # Fallback for VX10+ Firmware
            for u in users:
                for fid in range(10):
                    try:
                        finger = conn.get_user_template(uid=u.uid, temp_id=fid, user_id=str(u.user_id))
                        if finger and getattr(finger, 'template', None):
                            templates.append(finger)
                    except Exception:
                        continue

        # Upsert query for fingerprints (Requires a UNIQUE index on device_ip + employee_code + finger_index)
        # If you didn't add a unique index to the fingerprints table yet, we will just delete the old ones and insert the new ones.
        
        # Delete old fingerprints for this device to prevent duplicates during sync
        cursor.execute("DELETE FROM backup_fingerprints WHERE device_ip = %s", (device_ip,))
        
        finger_query = """
            INSERT INTO backup_fingerprints 
            (device_ip, employee_code, finger_index, finger_template, backup_timestamp)
            VALUES (%s, %s, %s, %s, %s)
        """
        
        # Create a UID map to find the employee_code for each template
        uid_to_emp = {u.uid: str(u.user_id).strip() for u in users}
        
        finger_count = 0
        for t in templates:
            emp_id = uid_to_emp.get(t.uid, "Unknown")
            if emp_id == "Unknown": continue
            
            # Encode raw bytes to Base64 text
            if isinstance(t.template, (bytes, bytearray)):
                encoded_template = base64.b64encode(t.template).decode('utf-8')
            else:
                encoded_template = str(t.template)
                
            cursor.execute(finger_query, (
                device_ip,
                emp_id,
                t.fid,
                encoded_template,
                current_time
            ))
            finger_count += 1
            
        db.commit()
        print(f"[+] Successfully synced {finger_count} Fingerprints to SQL.")
        print(f"\n[SUCCESS] Entire device ({device_ip}) is fully backed up to the 'biocentral' database.")

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"\n[ERROR] Sync failed: {e}")
    finally:
        if conn:
            conn.enable_device()
            conn.disconnect()
        if 'cursor' in locals(): cursor.close()
        if 'db' in locals(): db.close()

if __name__ == "__main__":
    TARGET_IP = "192.168.10.100"
    sync_device_users_to_sql(TARGET_IP)