FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY Source ./Source
ENV PYTHONPATH=/app
EXPOSE 8080
CMD ["uvicorn","Source.Jobs.app_main:app","--host","0.0.0.0","--port","8080"]
