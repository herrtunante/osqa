FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    default-libmysqlclient-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt /app/

# Install Python dependencies
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Copy application files
COPY . /app/

# Create necessary directories
RUN mkdir -p /app/log /app/cache /app/upfiles

# Expose port
EXPOSE 8000

# Run Django check command
CMD ["python", "manage.py", "check", "--deploy"]
