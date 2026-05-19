from flask import Flask, jsonify, request, render_template, redirect, make_response
import socket
import sys
import os
from threading import Thread, Lock
from database import (
    inicializar_banco,
    criar_usuario,
    autenticar_usuario,
    criar_sessao,
    buscar_sessao,
    deletar_sessao,
    adicionar_amigo,
    listar_amigos,
    buscar_historico,
    criar_grupo,
    adicionar_membro_grupo,
    listar_grupos_com_info,
    listar_membros_do_grupo_por_id,
    buscar_historico_grupo,
    usuario_em_grupo_por_id,
)

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from Server import Server

app = Flask(__name__)

HOST = "127.0.0.1"
PORT = 12345

# ── Estado global ─────────────────────────────────────────────────────────────

# nome_usuario -> socket TCP com o servidor de chat
client_sockets = {}
client_sockets_lock = Lock()

# nome_usuario -> lista de mensagens do chat geral/sistema
messages = {}
messages_lock = Lock()

# ── Helpers ───────────────────────────────────────────────────────────────────

def get_usuario_logado():
    token = request.cookies.get("sessao")
    if not token:
        return None
    return buscar_sessao(token)

def receive_message(nome_usuario):
    """
    Thread dedicada a receber mensagens do servidor TCP para um usuário.

    Filtragem:
    - [Privado de X]: mensagem privada — já está no banco, ignora aqui
      para não duplicar no histórico.
    - [grupo:<id>] remetente: texto — mensagem de grupo em tempo real.
      Também já está no banco; ignoramos aqui porque o frontend usa
      polling em /historico-grupo/<id>. Se quisermos notificar o usuário
      de que chegou mensagem num grupo que não está aberto, poderíamos
      guardar aqui — mas por ora ignoramos para manter simples.
    - Qualquer outra coisa (mensagens de sistema, etc.) vai para messages[].
    """
    with client_sockets_lock:
        sock = client_sockets.get(nome_usuario)

    print(f"[DEBUG] Thread de recepção iniciada para: {nome_usuario}")

    while True:
        try:
            msg = sock.recv(1024).decode()
            if not msg:
                break

            print(f"[DEBUG] Recebido para {nome_usuario}: {msg}")

            # mensagens privadas e de grupo já estão no banco — não duplicar
            if msg.startswith("[Privado") or msg.startswith("[grupo:"):
                continue

            with messages_lock:
                messages[nome_usuario].append(msg)

        except Exception as e:
            print(f"[DEBUG] Erro na thread de recepção de {nome_usuario}: {e}")
            break

# ── Autenticação ──────────────────────────────────────────────────────────────

@app.route("/cadastro", methods=["GET"])
def pagina_cadastro():
    return render_template("cadastro.html")

@app.route("/cadastro", methods=["POST"])
def post_cadastro():
    data  = request.json
    nome  = data.get("nome", "").strip()
    senha = data.get("senha", "").strip()

    if not nome or not senha:
        return jsonify({"ok": False, "erro": "Nome e senha são obrigatórios."}), 400

    resultado = criar_usuario(nome, senha)
    if not resultado["ok"]:
        return jsonify(resultado), 409

    return jsonify({"ok": True})

@app.route("/login", methods=["GET"])
def pagina_login():
    return render_template("login.html")

@app.route("/login", methods=["POST"])
def post_login():
    data  = request.json
    nome  = data.get("nome", "").strip()
    senha = data.get("senha", "").strip()

    usuario = autenticar_usuario(nome, senha)
    if not usuario:
        return jsonify({"ok": False, "erro": "Nome ou senha incorretos."}), 401

    # fecha socket antigo se existir (re-login)
    with client_sockets_lock:
        sock_antigo = client_sockets.get(nome)
        if sock_antigo:
            try:
                sock_antigo.close()
            except Exception:
                pass

    # conecta ao servidor TCP
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((HOST, PORT))
        sock.send(nome.encode())
    except Exception as e:
        return jsonify({"ok": False, "erro": f"Não foi possível conectar ao servidor de chat: {e}"}), 503

    with client_sockets_lock:
        client_sockets[nome] = sock

    with messages_lock:
        messages[nome] = []

    Thread(target=receive_message, args=(nome,), daemon=True).start()

    token = criar_sessao(usuario["id"])
    response = make_response(jsonify({"ok": True}))
    response.set_cookie("sessao", token, httponly=True, samesite="Lax")
    return response

@app.route("/logout", methods=["POST"])
def logout():
    token   = request.cookies.get("sessao")
    usuario = None

    if token:
        usuario = buscar_sessao(token)
        deletar_sessao(token)

    if usuario:
        nome = usuario["nome"]
        with client_sockets_lock:
            sock = client_sockets.pop(nome, None)
        if sock:
            try:
                sock.close()
            except Exception:
                pass
        with messages_lock:
            messages.pop(nome, None)

    response = make_response(jsonify({"ok": True}))
    response.delete_cookie("sessao")
    return response

# ── Chat geral ────────────────────────────────────────────────────────────────

@app.route("/")
def home():
    usuario = get_usuario_logado()
    if not usuario:
        return redirect("/login")
    return render_template("index.html", nome=usuario["nome"])

@app.route("/messages")
def get_messages():
    usuario = get_usuario_logado()
    if not usuario:
        return jsonify({"ok": False, "erro": "Não autenticado."}), 401

    nome = usuario["nome"]
    with messages_lock:
        msgs = list(messages.get(nome, []))

    return jsonify(msgs)

@app.route("/send", methods=["POST"])
def send_message():
    usuario = get_usuario_logado()
    if not usuario:
        return jsonify({"ok": False, "erro": "Não autenticado."}), 401

    data     = request.json
    mensagem = data.get("mensagem", "").strip()
    if not mensagem:
        return jsonify({"ok": False, "erro": "Mensagem vazia."}), 400

    nome = usuario["nome"]
    with client_sockets_lock:
        sock = client_sockets.get(nome)

    if not sock:
        return jsonify({"ok": False, "erro": "Sem conexão com o servidor de chat."}), 503

    try:
        sock.send(mensagem.encode())
    except Exception as e:
        return jsonify({"ok": False, "erro": f"Erro ao enviar: {e}"}), 500

    return jsonify({"ok": True})

# ── Amigos ────────────────────────────────────────────────────────────────────

@app.route("/amigos")
def get_amigos():
    usuario = get_usuario_logado()
    if not usuario:
        return jsonify({"ok": False, "erro": "Não autenticado."}), 401

    amigos = listar_amigos(usuario["nome"])

    with client_sockets_lock:
        online_set = set(client_sockets.keys())

    return jsonify([
        {"nome": a, "online": a in online_set}
        for a in amigos
    ])

@app.route("/amigos/adicionar", methods=["POST"])
def post_adicionar_amigo():
    usuario = get_usuario_logado()
    if not usuario:
        return jsonify({"ok": False, "erro": "Não autenticado."}), 401

    nome_amigo = request.json.get("nome", "").strip()
    if not nome_amigo:
        return jsonify({"ok": False, "erro": "Nome do amigo é obrigatório."}), 400

    if nome_amigo == usuario["nome"]:
        return jsonify({"ok": False, "erro": "Você não pode se adicionar."}), 400

    resultado = adicionar_amigo(usuario["nome"], nome_amigo)
    if not resultado["ok"]:
        return jsonify(resultado), 400

    return jsonify({"ok": True})

@app.route("/historico/<nome_amigo>")
def get_historico(nome_amigo):
    usuario = get_usuario_logado()
    if not usuario:
        return jsonify({"ok": False, "erro": "Não autenticado."}), 401

    historico = buscar_historico(usuario["nome"], nome_amigo)
    return jsonify(historico)

# ── Grupos ────────────────────────────────────────────────────────────────────

@app.route("/grupos")
def get_grupos():
    """
    Retorna os grupos do usuário logado com id, nome e criador.
    Usado pela sidebar para listar os grupos.
    """
    usuario = get_usuario_logado()
    if not usuario:
        return jsonify({"ok": False, "erro": "Não autenticado."}), 401

    grupos = listar_grupos_com_info(usuario["nome"])
    return jsonify(grupos)

@app.route("/grupos/criar", methods=["POST"])
def post_criar_grupo():
    """
    Cria um novo grupo.
    Body: { "nome": "nome do grupo", "membros": ["user1", "user2"] }
    O criador é adicionado automaticamente — não precisa estar em membros[].
    """
    usuario = get_usuario_logado()
    if not usuario:
        return jsonify({"ok": False, "erro": "Não autenticado."}), 401

    data       = request.json
    nome_grupo = data.get("nome", "").strip()
    membros    = data.get("membros", [])

    if not nome_grupo:
        return jsonify({"ok": False, "erro": "Nome do grupo é obrigatório."}), 400

    resultado = criar_grupo(nome_grupo, usuario["nome"], membros)
    if not resultado["ok"]:
        return jsonify(resultado), 400

    return jsonify({"ok": True, "id_grupo": resultado["id_grupo"]})

@app.route("/grupos/<int:id_grupo>/membros", methods=["GET"])
def get_membros_grupo(id_grupo):
    """Retorna a lista de membros de um grupo."""
    usuario = get_usuario_logado()
    if not usuario:
        return jsonify({"ok": False, "erro": "Não autenticado."}), 401

    if not usuario_em_grupo_por_id(usuario["nome"], id_grupo):
        return jsonify({"ok": False, "erro": "Você não é membro deste grupo."}), 403

    membros = listar_membros_do_grupo_por_id(id_grupo)
    return jsonify(membros)

@app.route("/grupos/<int:id_grupo>/adicionar-membro", methods=["POST"])
def post_adicionar_membro(id_grupo):
    """
    Adiciona um novo membro ao grupo.
    Qualquer membro pode convidar alguém.
    Body: { "nome": "nome do usuario" }
    """
    usuario = get_usuario_logado()
    if not usuario:
        return jsonify({"ok": False, "erro": "Não autenticado."}), 401

    nome_novo = request.json.get("nome", "").strip()
    if not nome_novo:
        return jsonify({"ok": False, "erro": "Nome do usuário é obrigatório."}), 400

    resultado = adicionar_membro_grupo(id_grupo, usuario["nome"], nome_novo)
    if not resultado["ok"]:
        return jsonify(resultado), 400

    return jsonify({"ok": True})

@app.route("/historico-grupo/<int:id_grupo>")
def get_historico_grupo(id_grupo):
    """
    Retorna o histórico de mensagens do grupo para o usuário logado.
    Filtra apenas mensagens a partir do momento em que o usuário entrou.
    """
    usuario = get_usuario_logado()
    if not usuario:
        return jsonify({"ok": False, "erro": "Não autenticado."}), 401

    if not usuario_em_grupo_por_id(usuario["nome"], id_grupo):
        return jsonify({"ok": False, "erro": "Você não é membro deste grupo."}), 403

    historico = buscar_historico_grupo(id_grupo, usuario["nome"])
    return jsonify(historico)

# ── Inicialização ─────────────────────────────────────────────────────────────

def iniciar_servidor_chat():
    server = Server(HOST, PORT)
    server.listen()


if __name__ == "__main__":
    # inicializa o banco de dados
    inicializar_banco()

    # sobe o servidor de chat numa thread daemon em background
    # daemon=True garante que a thread encerra junto com o processo principal
    Thread(target=iniciar_servidor_chat, daemon=True).start()
    print(f"Servidor de chat rodando em {HOST}:{PORT}")

    # sobe o servidor web Flask
    # use_reloader=False evita que o Flask tente subir o servidor de chat duas vezes
    # Define a porta para o Fly.io (8080) ou local (5000)
    import os
    port = int(os.environ.get("PORT", 8080))  # Fly.io usa 8080, local usa 8080 também
    host = "0.0.0.0"  # Aceita conexões externas

    app.run(host=host, port=port, debug=False, use_reloader=False)