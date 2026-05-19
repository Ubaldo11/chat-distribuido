import requests
import time
import webbrowser
import os

class MonitorFlyio:
    def __init__(self):
        self.primario = "https://distribuidos.fly.dev"
        self.backup = "https://distribuidos-backup.fly.dev:8081"
        self.primario_ativo = True
        
    def verificar_servidor(self, url):
        try:
            response = requests.get(url, timeout=5)
            return response.status_code == 200
        except:
            return False
    
    def executar(self):
        print("="*50)
        print("MONITOR DE TOLERANCIA A FALHAS - FLY.IO")
        print("="*50)
        print(f"Primario: {self.primario}")
        print(f"Backup: {self.backup}")
        print("-"*50)
        
        # Abre o primario no navegador
        webbrowser.open(self.primario)
        
        try:
            while True:
                if self.verificar_servidor(self.primario):
                    if not self.primario_ativo:
                        print("[RECUPERADO] Primario voltou ao ar!")
                        webbrowser.open(self.primario)
                        self.primario_ativo = True
                    print(f"[OK] Primario ativo - {time.strftime('%H:%M:%S')}")
                else:
                    if self.primario_ativo:
                        print("[FALHA] Primario caiu!")
                        print("[FAILOVER] Abrindo backup...")
                        webbrowser.open(self.backup)
                        self.primario_ativo = False
                    else:
                        print("[AGUARDANDO] Primario ainda inativo...")
                
                time.sleep(10)
                
        except KeyboardInterrupt:
            print("\n[ENCERRANDO] Monitor finalizado!")

if __name__ == "__main__":
    monitor = MonitorFlyio()
    monitor.executar()