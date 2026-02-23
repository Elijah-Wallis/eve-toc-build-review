import os
import subprocess
import sys
import time

# --- [CONFIGURATION: EVE_SWARM_V1] ---
PROJECT_NAME = "eve_autonomous_node"
DOCKER_COMPOSE_CONTENT = f"""
services:
  {PROJECT_NAME}:
    build: .
    container_name: {PROJECT_NAME}
    restart: always
    environment:
      - LOG_LEVEL=DEBUG
    volumes:
      - .:/app
    entrypoint: []
    command: ["python", "main.py"]
"""

DOCKERFILE_CONTENT = """
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "main.py"]
"""

def log(message, level="INFO"):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] :: {message}")

def execute_command(command):
    try:
        result = subprocess.run(
            command, shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        return result.stdout.decode().strip()
    except subprocess.CalledProcessError as e:
        log(f"Command Failed: {command}\\nError: {e.stderr.decode().strip()}", "CRITICAL")
        sys.exit(1)

def verify_substrate():
    log("Verifying Substrate (Docker Engine)...", "SCAN")
    try:
        execute_command("docker info")
        log("Substrate Active.", "SUCCESS")
    except:
        log("Substrate Failure. Is Docker running?", "FATAL")
        sys.exit(1)

def inject_infrastructure():
    log("Scanning for Protocol Infrastructure...", "SCAN")
    if not os.path.exists("requirements.txt"):
        with open("requirements.txt", "w") as f:
            f.write("requests\npython-dotenv\nopenai\nfastapi\nuvicorn")
    if not os.path.exists("Dockerfile"):
        with open("Dockerfile", "w") as f:
            f.write(DOCKERFILE_CONTENT)
    if not os.path.exists("docker-compose.yml"):
        with open("docker-compose.yml", "w") as f:
            f.write(DOCKER_COMPOSE_CONTENT)
    if not os.path.exists("main.py"):
        with open("main.py", "w") as f:
            f.write("import time\\nprint('Eve Protocol Active.')\\nwhile True: time.sleep(10)")

def kill_latency():
    log("Purging legacy instances...", "CLEAN")
    try:
        execute_command(f"docker rm -f {PROJECT_NAME}")
    except:
        pass

def initiate_swarm():
    log("Compiling Swarm Binary...", "BUILD")
    execute_command("docker-compose build")
    log("Deploying Agent (Detached Mode)...", "DEPLOY")
    execute_command("docker-compose up -d")
    log(f"Swarm Active. Container ID: {PROJECT_NAME}", "SUCCESS")

if __name__ == "__main__":
    print("--- [ARCHITECT PROTOCOL: AUTO-DEPLOY] ---")
    verify_substrate()
    inject_infrastructure()
    kill_latency()
    initiate_swarm()
    print("--- [SYSTEM OPTIMAL] ---")
