from zk import ZK
import pyodbc
from datetime import datetime

# ==========================================
# DATABASE CONNECTION
# ==========================================
BIOCENTRAL_CONN = (
    "Driver={ODBC Driver 18 for SQL Server};"
    "Server=MGSVR14.mgroup.local,1433;"
    "Database=biocentral;"
    "Trusted_Connection=yes;"
    "Network=dbmssocn;"
    "TrustServerCertificate=yes;"
)

# ==========================================
# FETCH ALL DEVICES
# ==========================================
def get_devices():
    conn = None
    cursor = None

    try:
        conn = pyodbc.connect(BIOCENTRAL_CONN + "app=FetchDevices;")
        cursor = conn.cursor()

        cursor.execute("""
            SELECT 
                device_id,
                bcc,
                ip_address,
                chain_type
            FROM device_registry
            ORDER BY bcc ASC
        """)

        rows = cursor.fetchall()

        devices = []

        for row in rows:
            devices.append({
                "device_id": row.device_id,
                "bcc": row.bcc,
                "ip_address": row.ip_address,
                "chain_type": row.chain_type
            })

        return devices

    except Exception as e:
        print(f"[ERROR] Database Error: {e}")
        return []

    finally:
        if cursor:
            cursor.close()

        if conn:
            conn.close()


# ==========================================
# PING DEVICE
# ==========================================
def ping_device(ip):
    zk = ZK(
        ip,
        port=4370,
        timeout=5,
        password=0,
        force_udp=False,
        ommit_ping=False
    )

    conn = None

    try:
        conn = zk.connect()

        print(f"[SUCCESS] Connected to {ip}")
        return True

    except Exception as e:
        print(f"[FAILED] {ip} -> {e}")
        return False

    finally:
        if conn:
            conn.disconnect()


# ==========================================
# MAIN
# ==========================================
if __name__ == "__main__":

    print("=" * 60)
    print("FETCHING REGISTERED DEVICES")
    print("=" * 60)

    devices = get_devices()

    if not devices:
        print("No devices found.")
        exit()

    print(f"TOTAL DEVICES: {len(devices)}")
    print()

    for device in devices:

        print("-" * 60)
        print(f"Device ID : {device['device_id']}")
        print(f"BCC       : {device['bcc']}")
        print(f"IP        : {device['ip_address']}")
        print(f"Type      : {device['chain_type']}")

        ping_device(device['ip_address'])

    print()
    print("=" * 60)
    print("DONE")
    print("=" * 60)