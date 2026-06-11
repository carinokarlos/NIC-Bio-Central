# NIC Synapse (Biometric Service Gateway)

![Version](https://img.shields.io/badge/version-1.0.0-blue.svg)
![Build](https://img.shields.io/badge/build-passing-brightgreen.svg)
![Python](https://img.shields.io/badge/python-3.11+-blue.svg)

**NIC Synapse** (internally facing as *NIC BioCentral*) is a custom-built middleware application developed by the MIS Department at Newtrends International Corporation. It serves as the dedicated communication layer between central HR Master Data and decentralized biometric hardware (ZKTeco TX628) located across nationwide branch stores.

## 🏗️ Architecture & Purpose

Historically, managing biometric devices over a hub-and-spoke VPN introduces severe latency and thread-locking issues. Synapse solves this by decoupling the web UI from the hardware execution layer. 

Built on **Flask**, the application uses a "Fail-Fast" TCP validation strategy combined with an asynchronous **Celery/Redis** task queue. This ensures that the central server remains highly responsive even when remote store networks are degraded, while providing HR administrators with real-time transaction updates via **WebSockets**.

## ✨ Key Features

* **Asynchronous Remote Enrollment:** Trigger physical hardware prompts (beeps/screen changes) at remote stores instantly from the HQ dashboard without blocking web threads.
* **Master Data Reflection:** Connects to the existing HR database via SQLAlchemy reflection (Read-Only) to validate employee IDs and store routing seamlessly.
* **Fail-Fast Network Validation:** Pre-checks VPN/TCP routing on port `4370` within 2 seconds before committing to heavy hardware protocol handshakes.
* **Real-Time State Management:** Pushes live transaction statuses (e.g., *Submitted*, *Validating*, *Timeout*, *Successful*) to the HR frontend using `Flask-SocketIO`.
* **Hardware Safe-Locks:** Implements strict device locking/unlocking during profile pushes to prevent physical race conditions and hardware corruption.

## 🛠️ Tech Stack

* **Backend Framework:** Flask 3.0+
* **Hardware Protocol:** `pyzk` (ZKTeco Standalone SDK / UDP-TCP wrapper)
* **Task Queue:** Celery + Redis
* **Database ORM:** SQLAlchemy (with Database Reflection)
* **Real-time Comms:** Flask-SocketIO (Eventlet)
* **Access Control:** Active Directory (LDAP) Integration

## 🔒 Security & Authorization

This repository contains critical infrastructure code. The web application is strictly gated via Active Directory. Only users belonging to the designated `App_Biometric_Admins` security group may access the enrollment endpoints. The application interacts with corporate Master Data using the Principle of Least Privilege (Read-Only service accounts).

---
*Developed and maintained by the MIS Department Interns Carl Cabral & Carlos Cariño - Newtrends International Corporation.*
