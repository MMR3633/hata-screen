FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright PORT=8080
WORKDIR /app
COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends libatomic1 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r requirements.txt && playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/*
COPY hub ./hub
COPY screen ./screen
COPY main.py .
EXPOSE 8080
CMD ["python", "main.py"]
