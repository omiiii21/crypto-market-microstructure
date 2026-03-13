# Deploying to Railway

This guide walks through deploying the full surveillance stack on [Railway](https://railway.app).
The deployment mirrors the `docker-compose.yml` topology exactly, using Railway services
instead of containers.

## Architecture on Railway

```
GitHub repo (monorepo)
        │
        ├── [timescaledb]   Custom Docker service  ← DB + schema init
        ├── [redis]         Railway Redis plugin
        ├── [data-ingestion]  Worker service
        ├── [metrics-engine]  Worker service
        ├── [anomaly-detector] Worker service
        └── [dashboard]     Web service  ← public URL exposed
```

All six services live in **one Railway project** and communicate over Railway's
private network.

---

## Prerequisites

- A Railway account and the [Railway CLI](https://docs.railway.app/develop/cli) installed
- Your repo pushed to GitHub and connected to Railway
- (Optional) A Slack webhook URL for alert notifications

---

## Step 1 – Create a Railway Project

```bash
railway login
railway init          # creates a new project, follow the prompts
```

Or create the project in the Railway dashboard at [railway.app](https://railway.app).

---

## Step 2 – Add the Redis Plugin

In the Railway dashboard:
1. Click **New** → **Database** → **Redis**
2. Railway provisions Redis and exposes `$REDIS_URL` as a reference variable.

Note the reference variable name (typically `${{Redis.REDIS_URL}}`).

---

## Step 3 – Add the TimescaleDB Service

TimescaleDB is not a managed Railway plugin, so deploy it as a custom Docker service:

1. Click **New** → **GitHub Repo** → select this repo
2. Set **Root Directory** to `/` (repo root)
3. Set **Dockerfile Path** to `services/timescaledb/Dockerfile`
4. Name the service **timescaledb**
5. Add the following environment variables:

| Variable            | Value                     |
|---------------------|---------------------------|
| `POSTGRES_DB`       | `surveillance`            |
| `POSTGRES_USER`     | `surveillance`            |
| `POSTGRES_PASSWORD` | *(strong secret)*         |
| `TIMESCALEDB_TELEMETRY` | `off`               |

6. Add a **TCP Proxy** on port `5432` so other services can connect.
7. Railway will build the image, run `01-init.sql` and `02-seed.sql` on first boot,
   and create all TimescaleDB hypertables automatically.

**Get the private hostname** from the service's **Variables** tab:
```
RAILWAY_PRIVATE_DOMAIN=timescaledb.railway.internal
```

---

## Step 4 – Add the Application Services

Repeat this process for each of the four application services.
For each one:
1. Click **New** → **GitHub Repo** → select this repo
2. Set **Root Directory** to `/` (repo root)
3. Set **Dockerfile Path** to the value in the table below
4. Name the service as shown
5. Set the environment variables listed

### Service Table

| Service name       | Dockerfile Path                             | Exposed port |
|--------------------|---------------------------------------------|:------------:|
| `data-ingestion`   | `services/data-ingestion/Dockerfile`        | —            |
| `metrics-engine`   | `services/metrics-engine/Dockerfile`        | —            |
| `anomaly-detector` | `services/anomaly-detector/Dockerfile`      | —            |
| `dashboard`        | `services/dashboard/Dockerfile`             | **public**   |

### Common Environment Variables (all four services)

| Variable      | Value                                                  |
|---------------|--------------------------------------------------------|
| `REDIS_URL`   | `${{Redis.REDIS_URL}}`  *(Railway reference variable)* |
| `CONFIG_PATH` | `config`                                               |
| `LOG_LEVEL`   | `INFO`                                                 |
| `LOG_FORMAT`  | `json`                                                 |

### Services that also need DATABASE_URL

`metrics-engine`, `anomaly-detector`, and `dashboard` additionally need:

| Variable       | Value                                                                                    |
|----------------|------------------------------------------------------------------------------------------|
| `DATABASE_URL` | `postgresql://surveillance:<password>@${{timescaledb.RAILWAY_PRIVATE_DOMAIN}}:5432/surveillance` |

Replace `<password>` with the `POSTGRES_PASSWORD` you set in Step 3.

### anomaly-detector only

| Variable           | Value                                  |
|--------------------|----------------------------------------|
| `SLACK_WEBHOOK_URL` | `https://hooks.slack.com/...` (optional) |

### dashboard only

Railway automatically injects `PORT`.  The Dockerfile CMD reads `${PORT:-8050}`,
so **no extra port variable is needed**.  The service name for the railway.toml
healthcheck path is `/api/health`.

---

## Step 5 – Set Deployment Order

Railway does not natively enforce startup ordering like Docker Compose's
`depends_on`, but you can approximate it by deploying in this order and
waiting for each service to become healthy before starting the next:

1. `redis` (plugin – already running)
2. `timescaledb` – wait until the health check passes
3. `data-ingestion`
4. `metrics-engine`
5. `anomaly-detector`
6. `dashboard`

Each application service retries on failure (`restartPolicyMaxRetries = 10`)
so temporary ordering issues self-heal.

---

## Step 6 – Access the Dashboard

Once the `dashboard` service is running:

1. Open the **dashboard** service in the Railway dashboard
2. Click **Settings** → **Networking** → **Generate Domain**
3. Railway gives you a public HTTPS URL such as `https://dashboard-production-xxxx.up.railway.app`
4. Open it in a browser – the REST API docs are at `/docs`

---

## Environment Variable Reference

| Variable            | Used by                            | Description                             |
|---------------------|------------------------------------|-----------------------------------------|
| `REDIS_URL`         | all app services                   | Redis connection string                 |
| `DATABASE_URL`      | metrics-engine, anomaly-detector, dashboard | PostgreSQL (TimescaleDB) URL  |
| `POSTGRES_DB`       | timescaledb                        | Database name (`surveillance`)          |
| `POSTGRES_USER`     | timescaledb                        | DB user (`surveillance`)                |
| `POSTGRES_PASSWORD` | timescaledb                        | DB password (keep secret)               |
| `CONFIG_PATH`       | all app services                   | Path to YAML configs (`config`)         |
| `LOG_LEVEL`         | all app services                   | `DEBUG` / `INFO` / `WARNING`            |
| `LOG_FORMAT`        | all app services                   | `json` for structured logs              |
| `SLACK_WEBHOOK_URL` | anomaly-detector                   | Slack incoming webhook (optional)       |
| `PORT`              | dashboard                          | Injected by Railway automatically       |

---

## Updating Deployments

Railway redeploys automatically on every push to the connected branch.
Because each service has its own Dockerfile, only changed layers are rebuilt.

To trigger a manual redeploy:

```bash
railway up --service dashboard
```

---

## Persistent Storage

Railway volumes are not enabled by default for custom Docker services.
Add a **volume** to the `timescaledb` service via **Settings → Volumes**
and mount it at `/var/lib/postgresql/data` to persist data across restarts.

Without a volume, the database is **ephemeral** – data is lost on redeploy.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `data-ingestion` keeps restarting | Redis not ready | Check Redis plugin is healthy; retries will self-heal |
| `metrics-engine` exits with DB error | `DATABASE_URL` misconfigured | Verify private hostname and password |
| Dashboard returns 500 | Redis or DB unreachable | Check env vars; visit `/api/health` for details |
| TimescaleDB init scripts not run | Data directory already exists | Delete the volume and redeploy to re-run init |
| `Port already in use` | Stale process | Railway handles this – the container is restarted cleanly |
