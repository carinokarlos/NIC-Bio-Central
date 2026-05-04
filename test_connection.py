import pyodbc
import re


RAW_CONFIG_STRING = (
    "Driver={SQL Server};"
    "Server=MGSVR14.mgroup.local,1433;"
    "Database=biocentral;"
    "Trusted_Connection=yes;" # This bypasses UID/PWD
    "Network=dbmssocn;"
)

def test_raw_connection():
    print("--- BIO-CENTRAL DIAGNOSTIC TOOL ---")
    
    # 1. Driver Detection
    print("\n1. Detecting Drivers...")
    drivers = [d for d in pyodbc.drivers() if 'ODBC Driver' in d]
    best_driver = 'ODBC Driver 17 for SQL Server' 
    if 'ODBC Driver 18 for SQL Server' in drivers:
        best_driver = 'ODBC Driver 18 for SQL Server'
    elif 'ODBC Driver 17 for SQL Server' in drivers:
        best_driver = 'ODBC Driver 17 for SQL Server'
    print(f"   -> Selected Driver: {best_driver}")

    # 2. String Construction
    print("\n2. Building Connection String...")
    refined_str = re.sub(r'DRIVER=\{.*?\};', f'DRIVER={{{best_driver}}};', RAW_CONFIG_STRING, flags=re.I)
    
    if 'Driver 18' in best_driver and 'TrustServerCertificate' not in refined_str:
        refined_str += "TrustServerCertificate=yes;"
    
    safe_print_str = re.sub(r'PWD=.*?;', 'PWD=********;', refined_str, flags=re.I)
    print(f"   -> Target String: {safe_print_str}")

    # 3. Connection Test
    print("\n3. Attempting SQL Server Handshake...")
    try:
        conn = pyodbc.connect(refined_str, timeout=5)
        print("   -> [SUCCESS]: Authenticated to SQL Server!")
        
        # 4. Table Access Test
        print("\n4. Testing dbo.device_registry Access...")
        cursor = conn.cursor()
        
        # Explicitly using dbo. to avoid schema ambiguity
        cursor.execute("SELECT TOP 3 device_id, bcc, ip_address FROM dbo.device_registry")
        rows = cursor.fetchall()
        
        print("   -> [SUCCESS]: Table is accessible!")
        print("   -> Data Preview:")
        if not rows:
            print("      (Table is empty but connection works perfectly)")
        else:
            for row in rows:
                print(f"      ID: {row.device_id} | BCC: {row.bcc} | IP: {row.ip_address.strip()}")
            
        conn.close()
        
    except pyodbc.Error as e:
        print(f"\n   -> [FAILED]: PyODBC SQL Error:")
        print(f"      {e}")
    except Exception as e:
        print(f"\n   -> [FAILED]: General Python Error:")
        print(f"      {e}")

if __name__ == "__main__":
    test_raw_connection()