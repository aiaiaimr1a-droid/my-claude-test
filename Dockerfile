FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY deadline_bot ./deadline_bot
ENV DB_PATH=/app/data/tasks.db
VOLUME /app/data
CMD ["python", "-m", "deadline_bot"]
