import pyodbc

def get_standalone_ips():
    conn_str = (
        "Driver={ODBC Driver 18 for SQL Server};"
        "Server=MGSVR14.mgroup.local,1433;"
        "Database=biocentral;"
        "Trusted_Connection=yes;"
        "TrustServerCertificate=yes;"
        "Network=dbmssocn;"
    )
    
    try:
        with pyodbc.connect(conn_str) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT ip_address FROM dbo.device_registry")
                ips = [row[0].strip() for row in cursor.fetchall() if row[0]]
                
                print("Extracted IPs:")
                for ip in ips:
                    print(ip)
                return ips
    except Exception as e:
        print(f"Database error: {e}")

if __name__ == "__main__":
    get_standalone_ips()