# TimescaleDB Service Dockerfile
# Extends the official TimescaleDB image with our schema initialization scripts.
#
# Build context must be the repo root so COPY can reach db/:
#   docker build -t crypto-timescaledb -f services/timescaledb/Dockerfile .
#
# On first startup PostgreSQL runs every script in /docker-entrypoint-initdb.d/
# in alphabetical order.  Subsequent restarts skip initialization (data dir
# already populated), so the schema is safe to redeploy without data loss.

FROM timescale/timescaledb:latest-pg15

# Disable telemetry
ENV TIMESCALEDB_TELEMETRY=off

# Default credentials – override via Railway environment variables:
#   POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD
ENV POSTGRES_DB=surveillance \
    POSTGRES_USER=surveillance \
    POSTGRES_PASSWORD=surveillance_dev

# Copy init scripts from repo root
COPY db/init.sql /docker-entrypoint-initdb.d/01-init.sql
COPY db/seed.sql /docker-entrypoint-initdb.d/02-seed.sql

EXPOSE 5432
