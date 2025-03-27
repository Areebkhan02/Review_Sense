import subprocess
import os

print("== DEBUGGING CHROME INSTALLATION ==")
print("Listing /usr/bin:")
try:
    result = subprocess.run(["ls", "-la", "/usr/bin"], capture_output=True, text=True)
    print(result.stdout)
except Exception as e:
    print(f"Error listing directory: {e}")

print("\nChecking for chromium:")
try:
    result = subprocess.run(["which", "chromium-browser"], capture_output=True, text=True)
    print(f"Chromium path: {result.stdout}")
except Exception as e:
    print(f"Error finding chromium: {e}")

print("\nEnvironment variables:")
for key, value in os.environ.items():
    print(f"{key}: {value}")