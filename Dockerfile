FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY data ./data
# UN solo worker: el worker de jobs vive en el proceso de la API (ver README)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
