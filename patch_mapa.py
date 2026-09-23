#!/usr/bin/env python3
"""
Script para adicionar as funcionalidades do mapa ao app.py existente
Executar: python patch_mapa.py
"""

import re
import os

APP_FILE = 'app.py'
BACKUP_FILE = 'app.py.backup_mapa'

# 1. Faça backup do arquivo original
print("📦 Criando backup do app.py original...")
os.system(f'copy {APP_FILE} {BACKUP_FILE}' if os.name == 'nt' else f'cp {APP_FILE} {BACKUP_FILE}')
print(f"✅ Backup salvo como {BACKUP_FILE}")

# Lê o arquivo original
with open(APP_FILE, 'r', encoding='utf-8') as f:
    content = f.read()

# ===== MODIFICAÇÃO 1: CORS - Adicionar X-Tecnico-Token =====
print("\n🔧 1/9 - Atualizando CORS...")
old_cors = '"allow_headers": ["Content-Type", "Authorization"]'
new_cors = '"allow_headers": ["Content-Type", "Authorization", "X-Tecnico-Token"]'
if old_cors in content:
    content = content.replace(old_cors, new_cors)
    print("✅ CORS atualizado")
else:
    print("⚠️ CORS não encontrado (pode já estar atualizado)")

# ===== MODIFICAÇÃO 2: Tabela contratos - Adicionar endereco_cliente =====
print("\n🔧 2/9 - Adicionando endereco_cliente na criação da tabela...")
old_table = 'descricao_servico TEXT, dados_os_completos TEXT, ocorrencias_texto TEXT\n    )'''
new_table = 'descricao_servico TEXT, dados_os_completos TEXT, ocorrencias_texto TEXT,\n        endereco_cliente TEXT\n    )'''
if old_table in content:
    content = content.replace(old_table, new_table)
    print("✅ Coluna adicionada na criação da tabela")
else:
    print("⚠️ Padrão não encontrado, verificando alternativa...")
    # Tenta outro padrão
    alt_old = 'ocorrencias_texto TEXT\n    )'''
    alt_new = 'ocorrencias_texto TEXT,\n        endereco_cliente TEXT\n    )'''
    if alt_old in content:
        content = content.replace(alt_old, alt_new)
        print("✅ Coluna adicionada (padrão alternativo)")

# ===== MODIFICAÇÃO 3: init_db - Adicionar endereco_cliente na migração =====
print("\n🔧 3/9 - Adicionando migração para endereco_cliente...")
old_migration = "for col in ['descricao_servico', 'dados_os_completos', 'ocorrencias_texto']:"
new_migration = "for col in ['descricao_servico', 'dados_os_completos', 'ocorrencias_texto', 'endereco_cliente']:"
if old_migration in content:
    content = content.replace(old_migration, new_migration)
    print("✅ Migração atualizada")
else:
    print("⚠️ Migração não encontrada, tentando padrão alternativo...")
    alt_migration = "['descricao_servico', 'dados_os_completos', 'ocorrencias_texto']"
    if alt_migration in content:
        content = content.replace(alt_migration, 
            "['descricao_servico', 'dados_os_completos', 'ocorrencias_texto', 'endereco_cliente']")
        print("✅ Migração atualizada (padrão alternativo)")

# ===== MODIFICAÇÃO 4: Adicionar rota do mapa =====
print("\n🔧 4/9 - Adicionando rota /mapa.html...")
rota_mapa = '''
# 🆕 ROTA DO MAPA
@app.route('/mapa.html')
def mapa_tecnico():
    """Serve o arquivo do mapa para os técnicos"""
    return send_from_directory('.', 'mapa.html')
'''
# Procura o local para inserir (após a rota sign.html)
insert_marker = "@app.route('/sign.html')"
if insert_marker in content:
    # Encontra o final da função sign_page
    sign_func_end = content.find("def sign_page():")
    if sign_func_end > 0:
        # Procura o próximo @app.route ou def após sign_page
        next_route = content.find("@app.route", sign_func_end + 50)
        if next_route > 0:
            content = content[:next_route] + rota_mapa + "\n" + content[next_route:]
            print("✅ Rota do mapa adicionada")
        else:
            print("⚠️ Não foi possível encontrar ponto de inserção")
    else:
        print("⚠️ Função sign_page não encontrada")
else:
    print("⚠️ Rota sign.html não encontrada")

# ===== MODIFICAÇÃO 5: salvar_contrato - Capturar endereco_cliente =====
print("\n🔧 5/9 - Adicionando captura de endereco_cliente...")
old_salvar = "cliente_cpf = dados.get('cliente_cpf', '').strip()\n        template_id = dados.get('template_id')"
new_salvar = "cliente_cpf = dados.get('cliente_cpf', '').strip()\n        # 🆕 Capturar endereço do cliente\n        endereco_cliente = dados.get('endereco_cliente', '').strip()\n        template_id = dados.get('template_id')"
if old_salvar in content:
    content = content.replace(old_salvar, new_salvar)
    print("✅ Captura de endereço adicionada")
else:
    print("⚠️ Padrão não encontrado para captura de endereço")

# ===== MODIFICAÇÃO 6: salvar_contrato - Adicionar endereco_cliente no INSERT =====
print("\n🔧 6/9 - Atualizando INSERT para incluir endereco_cliente...")
old_insert = "descricao_servico, ocorrencias_texto, dados_os_completos"
new_insert = "descricao_servico, ocorrencias_texto, dados_os_completos, endereco_cliente"
if old_insert in content:
    content = content.replace(old_insert, new_insert)
    # Agora atualiza os VALUES
    old_values = "descricao_servico, ocorrencias_texto, dados_os_completos)"
    new_values = "descricao_servico, ocorrencias_texto, dados_os_completos, endereco_cliente)"
    if old_values in content:
        content = content.replace(old_values, new_values)
        print("✅ INSERT atualizado")
    else:
        print("⚠️ VALUES não encontrados para atualização")
else:
    print("⚠️ Padrão INSERT não encontrado")

# ===== MODIFICAÇÃO 7: Adicionar endpoints do mapa =====
print("\n🔧 7/9 - Adicionando endpoints do mapa...")
endpoints_mapa = '''
# 🆕 NOVOS ENDPOINTS DO MAPA
@app.route('/api/contrato/<contrato_id>/endereco', methods=['GET'])
@tecnico_auth_required
def obter_endereco_contrato(contrato_id):
    """
    Retorna o endereço de um contrato específico para uso no mapa.
    Não expõe outros dados do contrato por segurança.
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
                'erro': 'Contrato não encontrado'
            }), 404
        
        if not row['endereco_cliente']:
            return jsonify({
                'sucesso': False, 
                'erro': 'Endereço não cadastrado para este contrato',
                'cliente_nome': row['cliente_nome']
            }), 404
        
        return jsonify({
            'sucesso': True, 
            'endereco': row['endereco_cliente'], 
            'cliente_nome': row['cliente_nome']
        })
        
    except Exception as e:
        logger.error(f"Erro ao buscar endereço do contrato {contrato_id}: {e}", exc_info=True)
        return jsonify({'sucesso': False, 'erro': 'Erro interno ao buscar endereço'}), 500

@app.route('/api/contrato/<contrato_id>/endereco', methods=['PUT'])
@tecnico_auth_required
def atualizar_endereco_contrato(contrato_id):
    """Atualiza o endereço do cliente em um contrato existente"""
    try:
        data = request.get_json()
        endereco = data.get('endereco', '').strip()
        
        if not endereco:
            return jsonify({'sucesso': False, 'erro': 'Endereço é obrigatório'}), 400
        
        db = get_db()
        contrato = db.execute('SELECT id FROM contratos WHERE id = ?', (contrato_id,)).fetchone()
        
        if not contrato:
            db.close()
            return jsonify({'sucesso': False, 'erro': 'Contrato não encontrado'}), 404
        
        db.execute('UPDATE contratos SET endereco_cliente = ? WHERE id = ?', (endereco, contrato_id))
        db.commit()
        db.close()
        
        logger.info(f"✅ Endereço atualizado para o contrato {contrato_id}")
        return jsonify({'sucesso': True, 'mensagem': 'Endereço atualizado com sucesso!'})
        
    except Exception as e:
        logger.error(f"Erro ao atualizar endereço: {e}", exc_info=True)
        return jsonify({'sucesso': False, 'erro': 'Erro interno'}), 500
'''
# Insere antes da rota baixar_txt_contrato
marker_txt = "@app.route('/api/contrato/<contrato_id>/txt', methods=['GET'])"
if marker_txt in content:
    content = content.replace(marker_txt, endpoints_mapa + "\n" + marker_txt)
    print("✅ Endpoints do mapa adicionados")
else:
    print("⚠️ Não foi possível inserir endpoints do mapa")

# ===== MODIFICAÇÃO 8: listar_contratos - Adicionar endereco_cliente =====
print("\n🔧 8/9 - Atualizando listar_contratos...")
# Primeira query
old_query1 = "radiusnet_finalizado, radiusnet_finalizado_data, radiusnet_os_status\n                FROM contratos ORDER BY numero_seq DESC"
new_query1 = "radiusnet_finalizado, radiusnet_finalizado_data, radiusnet_os_status,\n                endereco_cliente\n                FROM contratos ORDER BY numero_seq DESC"
if old_query1 in content:
    content = content.replace(old_query1, new_query1)
    print("✅ Query 1 atualizada")

# Segunda query
old_query2 = "radiusnet_finalizado, radiusnet_finalizado_data, radiusnet_os_status\n                FROM contratos ORDER BY numero_seq DESC"
if old_query2 in content:
    content = content.replace(old_query2, new_query1)
    print("✅ Query 2 atualizada")

# Query fallback
old_fallback = "radiusnet_id_os, descricao_servico, ocorrencias_texto\n            FROM contratos ORDER BY numero_seq DESC"
new_fallback = "radiusnet_id_os, descricao_servico, ocorrencias_texto,\n            endereco_cliente\n            FROM contratos ORDER BY numero_seq DESC"
if old_fallback in content:
    content = content.replace(old_fallback, new_fallback)
    print("✅ Query fallback atualizada")

# ===== MODIFICAÇÃO 9: Mensagem de inicialização =====
print("\n🔧 9/9 - Atualizando mensagem de inicialização...")
old_msg = "print(f'Login admin: admin / [variável ADMIN_SENHA_PADRAO]')"
new_msg = "print(f'🗺️  Mapa de rotas: http://{local_ip}:5000/mapa.html')\n    print(f'Login admin: admin / [variável ADMIN_SENHA_PADRAO]')"
if old_msg in content:
    content = content.replace(old_msg, new_msg)
    print("✅ Mensagem de inicialização atualizada")
else:
    print("⚠️ Mensagem não encontrada")

# Salva o arquivo modificado
with open(APP_FILE, 'w', encoding='utf-8') as f:
    f.write(content)

print("\n" + "="*50)
print("✅ PATCH APLICADO COM SUCESSO!")
print("="*50)
print(f"📁 Arquivo original: {BACKUP_FILE}")
print(f"📁 Arquivo modificado: {APP_FILE}")
print("\n🔍 Verifique as alterações com:")
print(f"  diff {BACKUP_FILE} {APP_FILE}" if os.name != 'nt' else f"  fc {BACKUP_FILE} {APP_FILE}")
print("\n🚀 Agora reinicie o servidor Flask!")