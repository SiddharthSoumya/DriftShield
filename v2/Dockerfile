FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home --uid 10001 driftshield && mkdir /data && chown driftshield:driftshield /data
COPY driftshield ./driftshield
USER driftshield
ENV DRIFTSHIELD_HOST=0.0.0.0 DRIFTSHIELD_DB=/data/driftshield.db PYTHONUNBUFFERED=1
EXPOSE 8000
VOLUME ["/data"]
CMD ["python", "-m", "driftshield"]
