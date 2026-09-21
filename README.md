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

# 📸 Dashboard Preview

![Dashboard Screenshot](https://raw.githubusercontent.com/SiddharthSoumya/DriftShield/main/DriftShield_Dashboard.png)

---

# 🎥 Demo Video

[![Watch the Demo](https://img.youtube.com/vi/d1kuxFRCc8o/maxresdefault.jpg)](https://youtu.be/d1kuxFRCc8o)

---
# 🖼️ Project Poster

[![DriftShield Poster](https://raw.githubusercontent.com/SiddharthSoumya/DriftShield/main/poster-preview.png)](https://canva.link/ln4opcenpf5iu1m)

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

# � Render Deployment Guide (Free Tier)

This project is ready for a free Render deployment with a managed Postgres database and secure environment variables.

## 1) Create a free Postgres database

1. Sign in to Render.
2. Click New + -> Postgres.
3. Pick a name like `driftshield-db`.
4. Choose the Free plan.
5. Select a region close to your users.
6. Create the database.

Render will provide your database connection details. Keep the generated connection string private.

## 2) Set secure environment variables

Use the Render dashboard for each web service instead of hardcoding secrets in source control.

Backend service env vars:

- `PORT=8080`
- `DB_URL=<your-render-postgres-connection-string>`
- `CORS_ALLOWED_ORIGINS=https://*.onrender.com,https://*.netlify.app,https://*.vercel.app,https://*.github.dev`

Frontend service env vars:

- `VITE_API_BASE_URL=https://<your-backend-service>.onrender.com`

Important:
- Never commit `.env` files with real secrets.
- Use Render env vars or a local `.env` file only for development.
- Keep the database password and connection string private.

## 3) One-click deploy with Render Blueprint

This repo includes a Render blueprint at [render.yaml](render.yaml).

You can deploy immediately by clicking:

https://render.com/deploy?repo=https://github.com/<your-github-username>/DriftShield

If you prefer the dashboard flow:

1. Push this repository to GitHub.
2. In Render, click New + -> Blueprint.
3. Connect the repository.
4. Render will create the backend, frontend, and free Postgres database automatically.

## 4) Local development

Backend:

```bash
cd backend/backend
./mvnw spring-boot:run
```

Frontend:

```bash
cd frontend
npm install
npm run dev -- --host
```

## 5) Production notes

- Backend uses H2 by default for local development when no DB is configured.
- In Render, the app prefers `DB_URL` / `DATABASE_URL` and will use the Postgres connection string automatically.
- Frontend is served as a static build through Nginx for a lightweight production deployment.

## 6) Secure GitHub Actions deploy workflow

This repo includes a CI/CD workflow at [.github/workflows/render-deploy.yml](.github/workflows/render-deploy.yml) that validates the app and deploys both services to Render.

Secrets to add in GitHub:

- `RENDER_API_KEY`
- `RENDER_SERVICE_ID_BACKEND`
- `RENDER_SERVICE_ID_FRONTEND`

How to get them:

1. In Render, open your account settings and create an API key.
2. Copy the service IDs from the Render dashboard URLs for each service.
3. Add the values as GitHub repository secrets under Settings -> Secrets and variables -> Actions.

Once configured, pushes to `main` will run the validation build and trigger both Render deployments automatically.

# 📜 License

This project was built for educational and hackathon purposes.
