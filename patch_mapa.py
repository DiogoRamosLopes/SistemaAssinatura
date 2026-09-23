"""
Script para adicionar as funcionalidades do mapa ao app.py existente
Executar: python patch_mapa.py
"""

import re
import os

APP_FILE = 'app.py'
BACKUP_FILE = 'app.py.backup_mapa'

print("Criando backup do app.py original...")
os.system(f'copy {APP_FILE} {BACKUP_FILE}' if os.name == 'nt' else f'cp {APP_FILE} {BACKUP_FILE}')
print(f"Backup salvo como {BACKUP_FILE}")

with open(APP_FILE, 'r', encoding='utf-8') as f:
    content = f.read()

print("\n1/9 - Atualizando CORS...")
old_cors = '"allow_headers": ["Content-Type", "Authorization"]'
new_cors = '"allow_headers": ["Content-Type", "Authorization", "X-Tecnico-Token"]'
if old_cors in content:
    content = content.replace(old_cors, new_cors)
    print("CORS atualizado")
else:
    print("CORS nao encontrado (pode ja estar atualizado)")

# ===== MODIFICACAO 2: Tabela contratos - Adicionar endereco_cliente =====
print("\n2/9 - Adicionando endereco_cliente na criacao da tabela...")
old_table = 'descricao_servico TEXT, dados_os_completos TEXT, ocorrencias_texto TEXT\n    )\'\'\''
new_table = 'descricao_servico TEXT, dados_os_completos TEXT, ocorrencias_texto TEXT,\n        endereco_cliente TEXT\n    )\'\'\''
if old_table in content:
    content = content.replace(old_table, new_table)
    print("Coluna adicionada na criacao da tabela")
else:
    print("Padrao nao encontrado, verificando alternativa...")
    alt_old = 'ocorrencias_texto TEXT\n    )\'\'\''
    alt_new = 'ocorrencias_texto TEXT,\n        endereco_cliente TEXT\n    )\'\'\''
    if alt_old in content:
        content = content.replace(alt_old, alt_new)
        print("Coluna adicionada (padrao alternativo)")

print("\n3/9 - Adicionando migracao para endereco_cliente...")
old_migration = "for col in ['descricao_servico', 'dados_os_completos', 'ocorrencias_texto']:"
new_migration = "for col in ['descricao_servico', 'dados_os_completos', 'ocorrencias_texto', 'endereco_cliente']:"
if old_migration in content:
    content = content.replace(old_migration, new_migration)
    print("Migracao atualizada")
else:
    print("Migracao nao encontrada, tentando padrao alternativo...")
    alt_migration = "['descricao_servico', 'dados_os_completos', 'ocorrencias_texto']"
    if alt_migration in content:
        content = content.replace(alt_migration, 
            "['descricao_servico', 'dados_os_completos', 'ocorrencias_texto', 'endereco_cliente']")
        print("Migracao atualizada (padrao alternativo)")

print("\n4/9 - Adicionando rota /mapa.html...")
rota_mapa = '''
# ROTA DO MAPA
@app.route('/mapa.html')
def mapa_tecnico():
    """Serve o arquivo do mapa para os tecnicos"""
    return send_from_directory('.', 'mapa.html')
'''
insert_marker = "@app.route('/sign.html')"
if insert_marker in content:
    sign_func_end = content.find("def sign_page():")
    if sign_func_end > 0:
        next_route = content.find("@app.route", sign_func_end + 50)
        if next_route > 0:
            content = content[:next_route] + rota_mapa + "\n" + content[next_route:]
            print("Rota do mapa adicionada")
        else:
            print("Nao foi possivel encontrar ponto de insercao")
    else:
        print("Funcao sign_page nao encontrada")
else:
    print("Rota sign.html nao encontrada")

print("\n5/9 - Adicionando captura de endereco_cliente...")
old_salvar = "cliente_cpf = dados.get('cliente_cpf', '').strip()\n        template_id = dados.get('template_id')"
new_salvar = "cliente_cpf = dados.get('cliente_cpf', '').strip()\n        # Capturar endereco do cliente\n        endereco_cliente = dados.get('endereco_cliente', '').strip()\n        template_id = dados.get('template_id')"
if old_salvar in content:
    content = content.replace(old_salvar, new_salvar)
    print("Captura de endereco adicionada")
else:
    print("Padrao nao encontrado para captura de endereco")

print("\n6/9 - Atualizando INSERT para incluir endereco_cliente...")
old_insert = "descricao_servico, ocorrencias_texto, dados_os_completos"
new_insert = "descricao_servico, ocorrencias_texto, dados_os_completos, endereco_cliente"
if old_insert in content:
    content = content.replace(old_insert, new_insert)
    old_values = "descricao_servico, ocorrencias_texto, dados_os_completos)"
    new_values = "descricao_servico, ocorrencias_texto, dados_os_completos, endereco_cliente)"
    if old_values in content:
        content = content.replace(old_values, new_values)
        print("INSERT atualizado")
    else:
        print("VALUES nao encontrados para atualizacao")
else:
    print("Padrao INSERT nao encontrado")

# ===== MODIFICACAO 7: Adicionar endpoints do mapa =====
print("\n7/9 - Adicionando endpoints do mapa...")
endpoints_mapa = '''
# NOVOS ENDPOINTS DO MAPA
@app.route('/api/contrato/<contrato_id>/endereco', methods=['GET'])
@tecnico_auth_required
def obter_endereco_contrato(contrato_id):
    """
    Retorna o endereco de um contrato especifico para uso no mapa.
    Nao expoe outros dados do contrato por seguranca.
    """
    try:
        db = get_db()
        row = db.execute(
            'SELECT endereco_cliente, cliente_nome FROM contratos WHERE id = ?', 
            (contrato_id,)
        ).fetchone()
        db.close()
        
        if not row:
            return jsonify({
                'sucesso': False, 
                'erro': 'Contrato nao encontrado'
            }), 404
        
        if not row['endereco_cliente']:
            return jsonify({
                'sucesso': False, 
                'erro': 'Endereco nao cadastrado para este contrato',
                'cliente_nome': row['cliente_nome']
            }), 404
        
        return jsonify({
            'sucesso': True, 
            'endereco': row['endereco_cliente'], 
            'cliente_nome': row['cliente_nome']
        })
        
    except Exception as e:
        logger.error(f"Erro ao buscar endereco do contrato {contrato_id}: {e}", exc_info=True)
        return jsonify({'sucesso': False, 'erro': 'Erro interno ao buscar endereco'}), 500

@app.route('/api/contrato/<contrato_id>/endereco', methods=['PUT'])
@tecnico_auth_required
def atualizar_endereco_contrato(contrato_id):
    """Atualiza o endereco do cliente em um contrato existente"""
    try:
        data = request.get_json()
        endereco = data.get('endereco', '').strip()
        
        if not endereco:
            return jsonify({'sucesso': False, 'erro': 'Endereco e obrigatorio'}), 400
        
        db = get_db()
        contrato = db.execute('SELECT id FROM contratos WHERE id = ?', (contrato_id,)).fetchone()
        
        if not contrato:
            db.close()
            return jsonify({'sucesso': False, 'erro': 'Contrato nao encontrado'}), 404
        
        db.execute('UPDATE contratos SET endereco_cliente = ? WHERE id = ?', (endereco, contrato_id))
        db.commit()
        db.close()
        
        logger.info(f"Endereco atualizado para o contrato {contrato_id}")
        return jsonify({'sucesso': True, 'mensagem': 'Endereco atualizado com sucesso!'})
        
    except Exception as e:
        logger.error(f"Erro ao atualizar endereco: {e}", exc_info=True)
        return jsonify({'sucesso': False, 'erro': 'Erro interno'}), 500
'''
# Insere antes da rota baixar_txt_contrato
marker_txt = "@app.route('/api/contrato/<contrato_id>/txt', methods=['GET'])"
if marker_txt in content:
    content = content.replace(marker_txt, endpoints_mapa + "\n" + marker_txt)
    print("Endpoints do mapa adicionados")
else:
    print("Nao foi possivel inserir endpoints do mapa")

# ===== MODIFICACAO 8: listar_contratos - Adicionar endereco_cliente =====
print("\n8/9 - Atualizando listar_contratos...")
# Primeira query
old_query1 = "radiusnet_finalizado, radiusnet_finalizado_data, radiusnet_os_status\n                FROM contratos ORDER BY numero_seq DESC"
new_query1 = "radiusnet_finalizado, radiusnet_finalizado_data, radiusnet_os_status,\n                endereco_cliente\n                FROM contratos ORDER BY numero_seq DESC"
if old_query1 in content:
    content = content.replace(old_query1, new_query1)
    print("Query 1 atualizada")

# Segunda query
old_query2 = "radiusnet_finalizado, radiusnet_finalizado_data, radiusnet_os_status\n                FROM contratos ORDER BY numero_seq DESC"
if old_query2 in content:
    content = content.replace(old_query2, new_query1)
    print("Query 2 atualizada")

# Query fallback
old_fallback = "radiusnet_id_os, descricao_servico, ocorrencias_texto\n            FROM contratos ORDER BY numero_seq DESC"
new_fallback = "radiusnet_id_os, descricao_servico, ocorrencias_texto,\n            endereco_cliente\n            FROM contratos ORDER BY numero_seq DESC"
if old_fallback in content:
    content = content.replace(old_fallback, new_fallback)
    print("Query fallback atualizada")

# ===== MODIFICACAO 9: Mensagem de inicializacao =====
print("\n9/9 - Atualizando mensagem de inicializacao...")
old_msg = "print(f'Login admin: admin / [variavel ADMIN_SENHA_PADRAO]')"
new_msg = "print(f'Mapa de rotas: http://{local_ip}:5000/mapa.html')\n    print(f'Login admin: admin / [variavel ADMIN_SENHA_PADRAO]')"
if old_msg in content:
    content = content.replace(old_msg, new_msg)
    print("Mensagem de inicializacao atualizada")
else:
    print("Mensagem nao encontrada")

# Salva o arquivo modificado
with open(APP_FILE, 'w', encoding='utf-8') as f:
    f.write(content)

print("\n" + "="*50)
print("PATCH APLICADO COM SUCESSO!")
print("="*50)
print(f"Arquivo original: {BACKUP_FILE}")
print(f"Arquivo modificado: {APP_FILE}")
print("\nVerifique as alteracoes com:")
print(f"  diff {BACKUP_FILE} {APP_FILE}" if os.name != 'nt' else f"  fc {BACKUP_FILE} {APP_FILE}")
print("\nAgora reinicie o servidor Flask!")