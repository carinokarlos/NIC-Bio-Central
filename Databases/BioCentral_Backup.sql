CREATE DATABASE IF NOT EXISTS Biocentral_Backup;
USE Biocentral_Backup;

-- =====================================================
-- 1. EMPLOYEE USERS (PIN + ACCESS CODE)
-- =====================================================
CREATE TABLE backup_device_users (
    backup_id INT AUTO_INCREMENT PRIMARY KEY,
    device_ip VARCHAR(20) NOT NULL,
    employee_code VARCHAR(50) NOT NULL,
    access_number INT NULL,                  -- 🔥 RESTORED (device access code)
    employee_name VARCHAR(100) NOT NULL,
    privilege_level INT DEFAULT 0,
    pin_password VARCHAR(255) NULL,
    backup_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    -- 🔥 NEW: Required for ON DUPLICATE KEY UPDATE to work in the Python script
    UNIQUE KEY unique_user_device (device_ip, employee_code) 
);

CREATE INDEX idx_users_empcode ON backup_device_users(employee_code);
CREATE INDEX idx_users_device ON backup_device_users(device_ip);
CREATE INDEX idx_users_access ON backup_device_users(access_number);

-- =====================================================
-- 2. FINGERPRINTS (BIOMETRIC DATA)
-- =====================================================
CREATE TABLE backup_fingerprints (
    backup_id INT AUTO_INCREMENT PRIMARY KEY,
    device_ip VARCHAR(20) NOT NULL,
    employee_code VARCHAR(50) NOT NULL,
    finger_index TINYINT NOT NULL,
    finger_template LONGTEXT NOT NULL,
    is_valid TINYINT(1) DEFAULT 1,
    backup_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    -- 🔥 NEW: Prevents saving the exact same finger index twice for the same user on the same device
    UNIQUE KEY unique_fingerprint (device_ip, employee_code, finger_index)
);

CREATE INDEX idx_finger_empcode ON backup_fingerprints(employee_code);
CREATE INDEX idx_finger_device ON backup_fingerprints(device_ip);

-- =====================================================
-- 3. ATTENDANCE LOGS
-- =====================================================
CREATE TABLE backup_attendance_logs (
    log_id INT AUTO_INCREMENT PRIMARY KEY,
    device_ip VARCHAR(20) NOT NULL,
    employee_code VARCHAR(50) NOT NULL,
    punch_time DATETIME NOT NULL,
    -- Updated comment to reflect that your device might send '5'
    punch_type TINYINT NOT NULL COMMENT '0=Time In, 1=Time Out, 5=Custom State', 
    verify_type TINYINT NULL COMMENT '1=Fingerprint, 3=Password',
    backup_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    -- 🔥 NEW: Completely prevents duplicate punch entries down to the exact second
    UNIQUE KEY unique_punch (device_ip, employee_code, punch_time)
);

CREATE INDEX idx_logs_empcode ON backup_attendance_logs(employee_code);
CREATE INDEX idx_logs_time ON backup_attendance_logs(punch_time);
CREATE INDEX idx_logs_device_time ON backup_attendance_logs(device_ip, punch_time);