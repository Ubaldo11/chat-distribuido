import socket
import sys
import os
from threading import Thread, Lock

# permite importar database.py que está na pasta app/
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app"))
from database import (
    sao_amigos,
    salvar_mensagem_privada,
    salvar_mensagem_grupo,
    listar_membros_do_grupo_por_id,
    usuario_em_grupo_por_id,
    listar_grupos_do_usuario,
    listar_todos_grupos,
    sair_grupo,
)


class Server:
    def __init__(self, HOST, PORT):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.bind((HOST, PORT))
        self.socket.listen()
        print("Servidor esperando por conexão ...")

        # nome -> socket dos clientes conectados agora
        self.clients = {}
        self.lock = Lock()

    def listen(self):
        while True:
            client_socket, address = self.socket.accept()
            client_name = client_socket.recv(1024).decode().strip()
            print(f"Conexão com {client_name}: {address}")

            with self.lock:
                self.clients[client_name] = client_socket

            Thread(
                target=self.handle_client,
                args=(client_name, client_socket),
                daemon=True
            ).start()

            self.send_system_message(f"{client_name} entrou no servidor.")
            self._avisar_grupos(client_name, client_socket)

    def _avisar_grupos(self, client_name, client_socket):
        """Ao conectar, informa o usuário em quais grupos ele já está."""
        grupos = listar_grupos_do_usuario(client_name)
        if grupos:
            try:
                client_socket.send(
                    f"[Servidor]: Você está nos grupos: {', '.join(grupos)}".encode()
                )
            except Exception:
                pass

    def handle_client(self, client_name, client_socket):
        while True:
            try:
                data = client_socket.recv(1024).decode().strip()
                if not data:
                    break

                # ── /msg <usuario> <mensagem> — mensagem privada ──────────────
                if data.startswith("/msg "):
                    parts = data.split(" ", 2)
                    if len(parts) < 3:
                        self._send(client_socket, "Uso: /msg <usuario> <mensagem>")
                    else:
                        to_user, message = parts[1], parts[2]
                        self.private_message(client_name, to_user, message)

                # ── /msg-grupo <id_grupo> <mensagem> — mensagem de grupo ──────
                elif data.startswith("/msg-grupo "):
                    parts = data.split(" ", 2)
                    if len(parts) < 3:
                        self._send(client_socket, "Uso: /msg-grupo <id_grupo> <mensagem>")
                    else:
                        try:
                            id_grupo = int(parts[1])
                            message  = parts[2]
                            self.group_message(client_name, id_grupo, message)
                        except ValueError:
                            self._send(client_socket, "ID de grupo inválido.")

                # ── /leave <grupo> — sair do grupo ───────────────────────────
                elif data.startswith("/leave "):
                    nome_grupo = data.split(" ", 1)[1]
                    self.leave_group(client_name, nome_grupo)

                # ── /groups — listar meus grupos ─────────────────────────────
                elif data == "/groups":
                    grupos = listar_grupos_do_usuario(client_name)
                    msg = ", ".join(grupos) if grupos else "Você não está em nenhum grupo."
                    self._send(client_socket, f"[Servidor]: Seus grupos: {msg}")

                # ── /allgroups — listar todos os grupos ───────────────────────
                elif data == "/allgroups":
                    grupos = listar_todos_grupos()
                    msg = ", ".join(grupos) if grupos else "Nenhum grupo criado."
                    self._send(client_socket, f"[Servidor]: Grupos disponíveis: {msg}")

                # ── /users — listar usuários online ───────────────────────────
                elif data == "/users":
                    with self.lock:
                        users = ", ".join(self.clients.keys())
                    self._send(client_socket, f"[Servidor]: Usuários online: {users}")

                # ── /sair — desconectar ───────────────────────────────────────
                elif data == "/sair":
                    self.disconnect(client_name)
                    break

                # ── /help ─────────────────────────────────────────────────────
                elif data == "/help":
                    self._send(client_socket,
                        "--> Comandos disponíveis:\n"
                        "/msg <usuario> <mensagem>           - mensagem privada\n"
                        "/msg-grupo <id_grupo> <mensagem>    - mensagem de grupo\n"
                        "/leave <grupo>                      - sair de um grupo\n"
                        "/groups                             - listar seus grupos\n"
                        "/allgroups                          - listar todos os grupos\n"
                        "/users                              - listar usuários online\n"
                        "/sair                               - sair do chat\n"
                        "/help                               - lista comandos"
                    )

                # ── mensagem sem comando (ignorada no novo fluxo) ─────────────
                else:
                    self._send(client_socket,
                        "[Servidor]: Use /msg-grupo <id> <mensagem> para enviar em grupo."
                    )

            except Exception as e:
                print(f"[DEBUG] Erro em handle_client({client_name}): {e}")
                self.disconnect(client_name)
                break

    # ── Mensagem privada ──────────────────────────────────────────────────────

    def private_message(self, sender, receiver, message):
        """
        Envia mensagem privada entre dois usuários.
        Só permite se os dois forem amigos.
        Salva no banco independente de o receptor estar online.
        """
        if not sao_amigos(sender, receiver):
            with self.lock:
                sock = self.clients.get(sender)
            if sock:
                self._send(sock, f"[Servidor]: Você e {receiver} não são amigos.")
            return

        # persiste no banco (fonte da verdade)
        salvar_mensagem_privada(sender, receiver, message)
        print(f"[DEBUG] Mensagem privada salva: {sender} -> {receiver}: {message}")

        # entrega em tempo real se o receptor estiver online
        with self.lock:
            receiver_sock = self.clients.get(receiver)

        if receiver_sock:
            try:
                receiver_sock.send(f"[Privado de {sender}]: {message}".encode())
            except Exception:
                pass

    # ── Mensagem de grupo ─────────────────────────────────────────────────────

    def group_message(self, sender, id_grupo, message):
        """
        Envia mensagem para todos os membros online de um grupo.
        Verifica se o remetente é membro antes de aceitar.
        Salva no banco para persistência do histórico.
        """
        # verifica se o remetente é membro do grupo
        if not usuario_em_grupo_por_id(sender, id_grupo):
            with self.lock:
                sock = self.clients.get(sender)
            if sock:
                self._send(sock, f"[Servidor]: Você não é membro deste grupo.")
            return

        # persiste no banco
        salvar_mensagem_grupo(id_grupo, sender, message)
        print(f"[DEBUG] Mensagem de grupo salva: grupo={id_grupo} | {sender}: {message}")

        # entrega em tempo real para membros online
        membros = listar_membros_do_grupo_por_id(id_grupo)

        with self.lock:
            sockets_online = [
                (nome, self.clients[nome])
                for nome in membros
                if nome in self.clients
            ]

        for nome, sock in sockets_online:
            try:
                # o prefixo [grupo:<id>] permite o frontend identificar
                # a qual grupo a mensagem pertence e renderizá-la corretamente
                sock.send(
                    f"[grupo:{id_grupo}] {sender}: {message}".encode()
                )
            except Exception:
                pass

    # ── Sair de grupo ─────────────────────────────────────────────────────────

    def leave_group(self, user, nome_grupo):
        resultado = sair_grupo(user, nome_grupo)
        with self.lock:
            sock = self.clients.get(user)
        if sock:
            if resultado["ok"]:
                self._send(sock, f"[Servidor]: Você saiu do grupo '{nome_grupo}'.")
            else:
                self._send(sock, f"[Servidor]: {resultado['erro']}")

    # ── Utilitários ───────────────────────────────────────────────────────────

    def _send(self, sock, message: str):
        """Envia uma mensagem para um socket, ignorando erros silenciosamente."""
        try:
            sock.send(message.encode())
        except Exception:
            pass

    def send_system_message(self, message):
        """Envia mensagem de sistema para todos os clientes conectados."""
        with self.lock:
            sockets = list(self.clients.values())
        for sock in sockets:
            self._send(sock, f"[Servidor]: {message}")

    def disconnect(self, user):
        """Desconecta o usuário — mantém grupos e amizades no banco."""
        with self.lock:
            if user in self.clients:
                try:
                    self.clients[user].close()
                except Exception:
                    pass
                del self.clients[user]
        self.send_system_message(f"{user} desconectou-se.")