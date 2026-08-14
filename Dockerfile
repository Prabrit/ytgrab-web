FROM python:3.12-slim

# Install Node.js (for JS challenge solving) and FFmpeg (for media merging)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    nodejs \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Ensure start script is executable
RUN chmod +x start.sh

EXPOSE 10000
CMD ["./start.sh"]