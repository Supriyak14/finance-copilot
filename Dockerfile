# One image serving the FastAPI API and the dashboard it renders.
FROM python:3.13-slim

# Unbuffered so a hosting platform's log view updates live rather than in bursts.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first: this layer is cached and only rebuilt when they change.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copied by name rather than "COPY . ." so .env, the local database and the
# virtualenv cannot end up in the image even by accident.
COPY app ./app
COPY scripts ./scripts
COPY static ./static
COPY templates ./templates

RUN mkdir -p data

# The demo configuration. No API key is baked in and none is needed:
# AI_DEMO_MODE=always means the AI features answer from the built-in stand-in
# and never call the Anthropic API, so the deployment cannot incur any cost.
ENV AI_DEMO_MODE=always \
    ANTHROPIC_API_KEY="" \
    SEED_DEMO_DATA=true \
    DATABASE_URL="sqlite:///./data/finance.db" \
    PORT=8000

EXPOSE 8000

# Shell form, so $PORT supplied by the host platform is expanded at run time.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
