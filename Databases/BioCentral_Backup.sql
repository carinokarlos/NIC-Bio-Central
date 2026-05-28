CREATE TABLE dbo.backup_device_users (     
    backup_id INT IDENTITY(1,1) PRIMARY KEY,     
    device_ip VARCHAR(20) NOT NULL,           -- Which terminal this came from     
    employee_code VARCHAR(50) NOT NULL,       -- The Employee ID (mapped from HRIS)     
    access_number INT NULL,                   -- Added: The hardware Access Number
    employee_name VARCHAR(100) NOT NULL,     
    privilege_level INT DEFAULT 0,            -- 0 = User, 14 = Super Admin     
    pin_password VARCHAR(50) NULL,            -- The keypad PIN (if any)     
    backup_timestamp DATETIME DEFAULT GETDATE() -- When the backup was run 
);

-- Indexing for fast lookups by employee or by device 
CREATE NONCLUSTERED INDEX IX_BackupUsers_EmpCode ON dbo.backup_device_users(employee_code); 
CREATE NONCLUSTERED INDEX IX_BackupUsers_Device ON dbo.backup_device_users(device_ip);
CREATE NONCLUSTERED INDEX IX_BackupUsers_Time ON dbo.backup_device_users(backup_timestamp);


CREATE TABLE dbo.backup_fingerprints (     
    backup_id INT IDENTITY(1,1) PRIMARY KEY,     
    device_ip VARCHAR(20) NOT NULL,     
    employee_code VARCHAR(50) NOT NULL,     
    finger_index INT NOT NULL,                -- 0 to 9 (representing which finger)     
    finger_template VARCHAR(MAX) NOT NULL,    -- The massive base64 biometric template     
    backup_timestamp DATETIME DEFAULT GETDATE() 
);

-- Indexing to quickly find all fingerprints for a specific employee on a specific device 
CREATE NONCLUSTERED INDEX IX_BackupFingers_EmpCode ON dbo.backup_fingerprints(employee_code); 
CREATE NONCLUSTERED INDEX IX_BackupFingers_Device ON dbo.backup_fingerprints(device_ip);


CREATE TABLE dbo.backup_attendance_logs (     
    log_id INT IDENTITY(1,1) PRIMARY KEY,     
    device_ip VARCHAR(20) NOT NULL,     
    employee_code VARCHAR(50) NOT NULL,     
    punch_time DATETIME NOT NULL,             -- The exact time they clocked in/out     
    punch_type INT NULL,                      -- e.g., 0=Check-in, 1=Check-out (from device)     
    verify_type INT NULL,                     -- e.g., 1=Fingerprint, 3=Password     
    backup_timestamp DATETIME DEFAULT GETDATE() 
);


-- 1. Index for pulling a specific employee's logs 
CREATE NONCLUSTERED INDEX IX_BackupLogs_EmpCode ON dbo.backup_attendance_logs(employee_code);
-- 2. Index for filtering logs by date (Critical for payroll/reporting speeds) 
CREATE NONCLUSTERED INDEX IX_BackupLogs_PunchTime ON dbo.backup_attendance_logs(punch_time);
-- 3. Composite index to quickly see who clocked into a specific store on a specific day 
CREATE NONCLUSTERED INDEX IX_BackupLogs_Device_Time ON dbo.backup_attendance_logs(device_ip, punch_time);