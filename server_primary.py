import subprocess
import sys

# Inicia o webservice na porta 8080
subprocess.run([sys.executable, "webservice.py", "--port", "8080", "--role", "primario"])