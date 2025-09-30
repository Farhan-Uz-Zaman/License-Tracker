FROM python:3.10-slim

# Set timezone to Bangladesh
ENV TZ=Asia/Dhaka

# Set working directory
WORKDIR /app

# Copy application files
COPY app/ .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose Flask port
EXPOSE 5000

# Run the application
CMD ["python", "app.py"]
