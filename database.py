import sqlite3
import hashlib
import secrets
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "chat.db")

def get_connection():
    """Retorna uma conexão com o banco de dados."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row #permite acessar coluna pelo nome
    return conn

def inicializar_banco():
    """Cria as tabelas se ainda não existirem."""
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id    INTEGER PRIMARY KEY AUTOINCREMENT,
                nome  TEXT NOT NULL UNIQUE,
                senha TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessoes (
                token      TEXT PRIMARY KEY,
                id_usuario INTEGER NOT NULL,
                criado_em  DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (id_usuario) REFERENCES usuarios(id)
            );

            CREATE TABLE IF NOT EXISTS grupos (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                nome       TEXT NOT NULL UNIQUE,
                criado_por TEXT NOT NULL,
                criado_em  DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS grupo_membros (
                id_grupo   INTEGER NOT NULL,
                id_usuario INTEGER NOT NULL,
                entrou_em  DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id_grupo, id_usuario),
                FOREIGN KEY (id_grupo)   REFERENCES grupos(id),
                FOREIGN KEY (id_usuario) REFERENCES usuarios(id)
            );

            CREATE TABLE IF NOT EXISTS amigos (
                id_usuario1 INTEGER NOT NULL,
                id_usuario2 INTEGER NOT NULL,
                PRIMARY KEY (id_usuario1, id_usuario2),
                FOREIGN KEY (id_usuario1) REFERENCES usuarios(id),
                FOREIGN KEY (id_usuario2) REFERENCES usuarios(id)
            );

            CREATE TABLE IF NOT EXISTS mensagens_privadas (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                remetente    TEXT NOT NULL,
                destinatario TEXT NOT NULL,
                conteudo     TEXT NOT NULL,
                criado_em    DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS mensagens_grupo (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                id_grupo  INTEGER NOT NULL,
                remetente TEXT NOT NULL,
                conteudo  TEXT NOT NULL,
                criado_em DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (id_grupo) REFERENCES grupos(id)
            );
        """)

# ── Usuários ──────────────────────────────────────────────────────────────────

def hash_senha(senha: str) -> str:
    return hashlib.sha256(senha.encode()).hexdigest()

def criar_usuario(nome: str, senha: str) -> dict:
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO usuarios (nome, senha) VALUES (?, ?)",
                (nome, hash_senha(senha))
            )
        return {"ok": True}
    except sqlite3.IntegrityError:
        return {"ok": False, "erro": "Nome de usuário já existe."}

def autenticar_usuario(nome: str, senha: str):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM usuarios WHERE nome = ? AND senha = ?",
            (nome, hash_senha(senha))
        ).fetchone()

def buscar_usuario_por_nome(nome: str):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM usuarios WHERE nome = ?", (nome,)
        ).fetchone()

# ── Sessões ───────────────────────────────────────────────────────────────────

def criar_sessao(id_usuario: int) -> str:
    token = secrets.token_hex(32)
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO sessoes (token, id_usuario) VALUES (?, ?)",
            (token, id_usuario)
        )
    return token

def buscar_sessao(token: str):
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT u.id, u.nome
            FROM sessoes s
            JOIN usuarios u ON u.id = s.id_usuario
            WHERE s.token = ?
            """,
            (token,)
        ).fetchone()

def deletar_sessao(token: str):
    with get_connection() as conn:
        conn.execute("DELETE FROM sessoes WHERE token = ?", (token,))

# ── Amigos ────────────────────────────────────────────────────────────────────

def adicionar_amigo(nome_solicitante: str, nome_amigo: str) -> dict:
    try:
        with get_connection() as conn:
            u1 = conn.execute(
                "SELECT id FROM usuarios WHERE nome = ?", (nome_solicitante,)
            ).fetchone()
            u2 = conn.execute(
                "SELECT id FROM usuarios WHERE nome = ?", (nome_amigo,)
            ).fetchone()

            if not u2:
                return {"ok": False, "erro": f"Usuário '{nome_amigo}' não encontrado."}
            if u1["id"] == u2["id"]:
                return {"ok": False, "erro": "Você não pode se adicionar."}

            id_menor = min(u1["id"], u2["id"])
            id_maior = max(u1["id"], u2["id"])

            conn.execute(
                "INSERT OR IGNORE INTO amigos (id_usuario1, id_usuario2) VALUES (?, ?)",
                (id_menor, id_maior)
            )
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "erro": str(e)}

def sao_amigos(nome_a: str, nome_b: str) -> bool:
    with get_connection() as conn:
        u_a = conn.execute(
            "SELECT id FROM usuarios WHERE nome = ?", (nome_a,)
        ).fetchone()
        u_b = conn.execute(
            "SELECT id FROM usuarios WHERE nome = ?", (nome_b,)
        ).fetchone()

        if not u_a or not u_b:
            return False

        id_menor = min(u_a["id"], u_b["id"])
        id_maior = max(u_a["id"], u_b["id"])

        resultado = conn.execute(
            "SELECT 1 FROM amigos WHERE id_usuario1 = ? AND id_usuario2 = ?",
            (id_menor, id_maior)
        ).fetchone()

    return resultado is not None

def listar_amigos(nome_usuario: str) -> list:
    with get_connection() as conn:
        u = conn.execute(
            "SELECT id FROM usuarios WHERE nome = ?", (nome_usuario,)
        ).fetchone()

        if not u:
            return []

        id_usuario = u["id"]

        rows = conn.execute(
            """
            SELECT u.nome
            FROM usuarios u
            JOIN amigos a ON (
                (a.id_usuario1 = ? AND a.id_usuario2 = u.id)
                OR
                (a.id_usuario2 = ? AND a.id_usuario1 = u.id)
            )
            WHERE u.id != ?
            """,
            (id_usuario, id_usuario, id_usuario)
        ).fetchall()

    return [row["nome"] for row in rows]

# ── Grupos ────────────────────────────────────────────────────────────────────

def criar_grupo(nome_grupo: str, criado_por: str, membros: list) -> dict:
    """
    Cria um grupo e adiciona o criador + membros iniciais.
    membros é uma lista de nomes (não precisa incluir o criador).
    Retorna {"ok": True, "id_grupo": id} ou {"ok": False, "erro": "..."}.
    """
    try:
        with get_connection() as conn:
            cursor = conn.execute(
                "INSERT INTO grupos (nome, criado_por) VALUES (?, ?)",
                (nome_grupo, criado_por)
            )
            id_grupo = cursor.lastrowid

            # adiciona o criador
            id_criador = conn.execute(
                "SELECT id FROM usuarios WHERE nome = ?", (criado_por,)
            ).fetchone()["id"]

            conn.execute(
                "INSERT OR IGNORE INTO grupo_membros (id_grupo, id_usuario) VALUES (?, ?)",
                (id_grupo, id_criador)
            )

            # adiciona os membros convidados
            for nome_membro in membros:
                u = conn.execute(
                    "SELECT id FROM usuarios WHERE nome = ?", (nome_membro,)
                ).fetchone()
                if u:
                    conn.execute(
                        "INSERT OR IGNORE INTO grupo_membros (id_grupo, id_usuario) VALUES (?, ?)",
                        (id_grupo, u["id"])
                    )

        return {"ok": True, "id_grupo": id_grupo}
    except sqlite3.IntegrityError:
        return {"ok": False, "erro": f"Já existe um grupo com o nome '{nome_grupo}'."}
    except Exception as e:
        return {"ok": False, "erro": str(e)}

def adicionar_membro_grupo(id_grupo: int, nome_solicitante: str, nome_novo_membro: str) -> dict:
    """
    Adiciona um novo membro ao grupo.
    Qualquer membro pode convidar alguém.
    """
    try:
        with get_connection() as conn:
            u_sol = conn.execute(
                "SELECT id FROM usuarios WHERE nome = ?", (nome_solicitante,)
            ).fetchone()

            if not u_sol:
                return {"ok": False, "erro": "Solicitante não encontrado."}

            eh_membro = conn.execute(
                "SELECT 1 FROM grupo_membros WHERE id_grupo = ? AND id_usuario = ?",
                (id_grupo, u_sol["id"])
            ).fetchone()

            if not eh_membro:
                return {"ok": False, "erro": "Você não é membro deste grupo."}

            u_novo = conn.execute(
                "SELECT id FROM usuarios WHERE nome = ?", (nome_novo_membro,)
            ).fetchone()

            if not u_novo:
                return {"ok": False, "erro": f"Usuário '{nome_novo_membro}' não encontrado."}

            conn.execute(
                "INSERT OR IGNORE INTO grupo_membros (id_grupo, id_usuario) VALUES (?, ?)",
                (id_grupo, u_novo["id"])
            )

        return {"ok": True}
    except Exception as e:
        return {"ok": False, "erro": str(e)}

def listar_grupos_do_usuario(nome_usuario: str) -> list:
    """Retorna lista de nomes dos grupos que o usuário é membro."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT g.nome
            FROM grupos g
            JOIN grupo_membros gm ON g.id = gm.id_grupo
            JOIN usuarios u       ON u.id = gm.id_usuario
            WHERE u.nome = ?
            """,
            (nome_usuario,)
        ).fetchall()
    return [row["nome"] for row in rows]

def listar_grupos_com_info(nome_usuario: str) -> list:
    """
    Retorna os grupos do usuário com id, nome e criador.
    Usado pelo webservice para montar a sidebar.
    """
    with get_connection() as conn:
        u = conn.execute(
            "SELECT id FROM usuarios WHERE nome = ?", (nome_usuario,)
        ).fetchone()

        if not u:
            return []

        rows = conn.execute(
            """
            SELECT g.id, g.nome, g.criado_por
            FROM grupos g
            JOIN grupo_membros gm ON g.id = gm.id_grupo
            WHERE gm.id_usuario = ?
            ORDER BY g.nome ASC
            """,
            (u["id"],)
        ).fetchall()

    return [
        {"id": row["id"], "nome": row["nome"], "criado_por": row["criado_por"]}
        for row in rows
    ]

def listar_membros_do_grupo(nome_grupo: str) -> list:
    """Retorna lista de nomes dos membros de um grupo pelo nome."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT u.nome
            FROM usuarios u
            JOIN grupo_membros gm ON u.id = gm.id_usuario
            JOIN grupos g         ON g.id = gm.id_grupo
            WHERE g.nome = ?
            """,
            (nome_grupo,)
        ).fetchall()
    return [row["nome"] for row in rows]

def listar_membros_do_grupo_por_id(id_grupo: int) -> list:
    """Retorna lista de nomes dos membros de um grupo pelo id."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT u.nome
            FROM usuarios u
            JOIN grupo_membros gm ON u.id = gm.id_usuario
            WHERE gm.id_grupo = ?
            """,
            (id_grupo,)
        ).fetchall()
    return [row["nome"] for row in rows]

def listar_todos_grupos() -> list:
    with get_connection() as conn:
        rows = conn.execute("SELECT nome FROM grupos").fetchall()
    return [row["nome"] for row in rows]

def usuario_em_grupo_por_id(nome_usuario: str, id_grupo: int) -> bool:
    """Verifica se o usuário é membro do grupo pelo id."""
    with get_connection() as conn:
        u = conn.execute(
            "SELECT id FROM usuarios WHERE nome = ?", (nome_usuario,)
        ).fetchone()
        if not u:
            return False
        resultado = conn.execute(
            "SELECT 1 FROM grupo_membros WHERE id_grupo = ? AND id_usuario = ?",
            (id_grupo, u["id"])
        ).fetchone()
    return resultado is not None

def sair_grupo(nome_usuario: str, nome_grupo: str) -> dict:
    try:
        with get_connection() as conn:
            g = conn.execute(
                "SELECT id FROM grupos WHERE nome = ?", (nome_grupo,)
            ).fetchone()

            if not g:
                return {"ok": False, "erro": f"Grupo '{nome_grupo}' não existe."}

            u = conn.execute(
                "SELECT id FROM usuarios WHERE nome = ?", (nome_usuario,)
            ).fetchone()

            conn.execute(
                "DELETE FROM grupo_membros WHERE id_grupo = ? AND id_usuario = ?",
                (g["id"], u["id"])
            )
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "erro": str(e)}

# ── Mensagens de grupo ────────────────────────────────────────────────────────

def salvar_mensagem_grupo(id_grupo: int, remetente: str, conteudo: str):
    """Persiste uma mensagem de grupo no banco."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO mensagens_grupo (id_grupo, remetente, conteudo)
            VALUES (?, ?, ?)
            """,
            (id_grupo, remetente, conteudo)
        )

def buscar_historico_grupo(id_grupo: int, nome_usuario: str) -> list:
    """
    Retorna o histórico de mensagens do grupo para um usuário.
    Filtra apenas mensagens a partir do momento em que o usuário entrou,
    garantindo que novos membros não vejam conversas anteriores à sua entrada.
    """
    with get_connection() as conn:
        u = conn.execute(
            "SELECT id FROM usuarios WHERE nome = ?", (nome_usuario,)
        ).fetchone()

        if not u:
            return []

        entrada = conn.execute(
            """
            SELECT entrou_em
            FROM grupo_membros
            WHERE id_grupo = ? AND id_usuario = ?
            """,
            (id_grupo, u["id"])
        ).fetchone()

        if not entrada:
            return []

        entrou_em = entrada["entrou_em"]

        rows = conn.execute(
            """
            SELECT remetente, conteudo, criado_em
            FROM mensagens_grupo
            WHERE id_grupo = ?
              AND criado_em >= ?
            ORDER BY criado_em ASC, id ASC
            """,
            (id_grupo, entrou_em)
        ).fetchall()

    return [
        {
            "remetente": row["remetente"],
            "conteudo":  row["conteudo"],
            "criado_em": row["criado_em"]
        }
        for row in rows
    ]

# ── Mensagens privadas ────────────────────────────────────────────────────────

def salvar_mensagem_privada(remetente: str, destinatario: str, conteudo: str):
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO mensagens_privadas (remetente, destinatario, conteudo)
            VALUES (?, ?, ?)
            """,
            (remetente, destinatario, conteudo)
        )

def buscar_historico(usuario_a: str, usuario_b: str) -> list:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT remetente, destinatario, conteudo, criado_em
            FROM mensagens_privadas
            WHERE (remetente = ? AND destinatario = ?)
               OR (remetente = ? AND destinatario = ?)
            ORDER BY criado_em ASC, id ASC
            """,
            (usuario_a, usuario_b, usuario_b, usuario_a)
        ).fetchall()

    return [
        {
            "remetente":    row["remetente"],
            "destinatario": row["destinatario"],
            "conteudo":     row["conteudo"],
            "criado_em":    row["criado_em"]
        }
        for row in rows
    ]