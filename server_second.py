import subprocess
import sys

# Inicia o webservice na porta 8081
subprocess.run([sys.executable, "webservice.py", "--port", "8081", "--role", "backup"])