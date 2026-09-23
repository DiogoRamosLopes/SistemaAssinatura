import sqlite3
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'database', 'contratos.db')

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Verifica e adiciona cliente_nome na tabela templates
cursor.execute("PRAGMA table_info(templates)")
colunas = [col[1] for col in cursor.fetchall()]

if 'cliente_nome' not in colunas:
    cursor.execute("ALTER TABLE templates ADD COLUMN cliente_nome TEXT")
    print("✅ Coluna 'cliente_nome' adicionada à tabela templates.")
else:
    print("ℹ️ Coluna 'cliente_nome' já existe.")

conn.commit()
conn.close()
print("Migração concluída.")