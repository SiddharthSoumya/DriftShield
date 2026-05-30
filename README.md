# DriftShield 🛡️
### DriftShield helps detect abnormal system behavior and potential security threats before they escalate into critical failures.

DriftShield is an autonomous anomaly detection and security monitoring platform designed to identify suspicious behavioral drift in applications and systems. It continuously analyzes incoming activity patterns, detects abnormal behavior using intelligent backend processing, and provides real-time visibility through a modern monitoring dashboard.
It autonomuosly remediates the system and revert back to trusted baseline configuration within a specific time-interval.

Built with a scalable containerized architecture, DriftShield combines a Spring Boot backend, PostgreSQL database, Dockerized deployment, and a lightweight frontend dashboard to create a secure and developer-friendly monitoring system.

---

# 🚀 Features

* Real-time anomaly and drift detection
* AI-assisted behavioral monitoring
* RESTful backend APIs
* PostgreSQL-powered persistent storage
* Dockerized full-stack deployment
* Lightweight frontend dashboard
* Scalable backend architecture
* Clean separation of frontend and backend services

---

# 🏗️ Tech Stack

## Backend

* Java 21
* Spring Boot
* Spring Data JPA
* Hibernate
* PostgreSQL
* Maven

## Frontend

* React
* Vite
* JavaScript

## DevOps & Infrastructure

* Docker
* Docker Compose

---

# ⚡ System Architecture

```text id="ew6vyj"
                ┌──────────────────┐
                │    Frontend UI   │
                │  React + Vite    │
                └────────┬─────────┘
                         │ REST API
                         ▼
                ┌──────────────────┐
                │  Spring Boot API │
                │ Drift Detection  │
                │ Business Logic   │
                └────────┬─────────┘
                         │ JPA/Hibernate
                         ▼
                ┌──────────────────┐
                │   PostgreSQL DB  │
                │ Persistent Store │
                └──────────────────┘
```

---

# 📂 Project Structure

```bash id="n57yhz"
DriftShield/
│
├── backend/
│   ├── src/
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── pom.xml
│
├── frontend/
│   ├── src/
│   ├── Dockerfile
│   └── package.json
│
└── README.md
```

---

# ⚙️ Prerequisites

Before running the project, ensure the following are installed:

* Docker
* Docker Compose

---

# ▶️ Running the Backend

Navigate to the backend directory and start the backend services:

```bash id="f59jx8"
docker-compose up --build
```

This will:

* Build the backend container
* Start Spring Boot services
* Start PostgreSQL database
* Configure networking automatically

Backend server will run on:

```bash id="6e7r4g"
http://localhost:8080
```

---

# ▶️ Running the Frontend

Run the frontend container using:

```bash id="4cbp77"
docker run -p 5173:5173 driftshield-frontend
```

Frontend dashboard will be available at:

```bash id="94c8hk"
http://localhost:5173
```

---

# 📸 Screenshots / Demo

## Dashboard Preview

*Add project screenshots here*

```md id="f3kqwf"
![Dashboard Screenshot](https://github.com/SiddharthSoumya/DriftShield/blob/main/DriftShield_Dashboard.png)
```

## Demo Video

*Add your demo video link here*

```md id="9z5q4d"
[Watch Demo Video](https://youtu.be/d1kuxFRCc8o)
```

---

# 🧠 How DriftShield Works

1. Incoming system or application activity is received by the backend APIs.
2. Spring Boot services process and analyze the activity patterns.
3. Drift detection logic identifies suspicious or abnormal behavior.
4. Processed data is stored in PostgreSQL.
5. Frontend dashboard displays monitoring insights and detection results in real time.

---

# 🛠️ Development Notes

* Docker daemon must be running before starting containers.
* First build may take several minutes depending on internet speed and image caching.
* Environment variables can be configured for production deployment.
* PostgreSQL data persistence depends on Docker volume configuration.

---

# 🔥 Future Improvements

* Advanced AI/ML drift prediction
* Real-time alert notifications
* Authentication & RBAC
* Kubernetes deployment
* Cloud-native monitoring support
* Historical analytics dashboard

---

# 👨‍💻 Contributors

* Siddharth Soumya
  
---

# 📜 License

This project was built for educational and hackathon purposes.
