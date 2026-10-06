#!/bin/sh
set -eu

psql -v ON_ERROR_STOP=1 \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    --set=app_password="$KD_APP_PASSWORD" <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;
CREATE ROLE knowledgedesk LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEDB NOCREATEROLE;
GRANT CONNECT ON DATABASE knowledgedesk TO knowledgedesk;
GRANT USAGE, CREATE ON SCHEMA public TO knowledgedesk;
SQL