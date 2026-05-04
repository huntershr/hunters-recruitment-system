FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Set environment variables
ENV PORT=8000
ENV GOOGLE_API_KEY=""

EXPOSE 8000

CMD ["python", "-m", "app.main"]
