FROM python:3.12-slim

WORKDIR /app

COPY gateway.py /app/gateway.py
COPY warmup.pdf /app/warmup.pdf

# The gateway only uses the standard library; no pip install is needed.
EXPOSE 18000 18003

CMD ["python3", "/app/gateway.py"]
