# 1. Use a specific, slim version of the official Python image
FROM python:3.11-slim

# 2. Set environment variables to optimize Python inside a container
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# 3. Set the working directory inside the container
WORKDIR /app

# 4. Create output directory for harvested metadata
RUN mkdir -p /app/data

# 5. Copy only requirements first to leverage container caching layers
COPY requirements.txt .

# 6. Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# 7. Copy the rest of your application code
COPY . .

# 7. Create a non-privileged user and switch to it for runtime security
RUN useradd -u 10001 harvester && chown -R harvester:harvester /app
USER harvester

# 8. Define the command to run your script/app
CMD ["python", "FedHarv.src/fedharv/core.py"]
