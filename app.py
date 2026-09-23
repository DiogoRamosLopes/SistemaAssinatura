# -*- coding: utf-8 -*-
# ============================================================
# SISTEMA DE ASSINATURA DIGITAL - CÓDIGO COMPLETO CORRIGIDO
# ============================================================
# DEPENDÊNCIAS ESSENCIAIS:
#   pip install flask flask-cors pypdf reportlab Pillow
#   pip install weasyprint python-dotenv requests jinja2
#   pip install werkzeug
# ============================================================

from flask import Flask, request, jsonify, send_file, send_from_directory, session
from flask_cors import CORS
import sqlite3
import os
import base64
import uuid
import re
from datetime import datetime, timedelta
from io import BytesIO
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib.utils import ImageReader
from PIL import Image as PILImage, ImageFilter, ImageEnhance
import json
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from functools import wraps
import socket
from dotenv import load_dotenv
import requests
import time
from jinja2 import Template
import concurrent.futures
from werkzeug.security import generate_password_hash, check_password_hash

# ===== VERIFICAÇÃO DO WEASYPRINT =====
try:
    from weasyprint import HTML
    WEASYPRINT_AVAILABLE = True
except ImportError:
    WEASYPRINT_AVAILABLE = False
    print("⚠️ WeasyPrint não instalado. Execute: pip install weasyprint")
    print("   O sistema usará ReportLab como fallback.")

load_dotenv()

app = Flask(__name__)

# ===== CONFIGURAÇÕES GERAIS =====
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024
app.secret_key = os.getenv('SECRET_KEY', os.urandom(24))
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = 86400

# ===== CORS =====
ALLOWED_ORIGINS = os.getenv('ALLOWED_ORIGINS', 'http://localhost:5000,http://127.0.0.1:5000').split(',')
CORS(app, resources={
    r"/api/*": {
        "origins": ALLOWED_ORIGINS,
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization", "X-Tecnico-Token"],
        "expose_headers": ["X-Tecnico-Token"],
        "supports_credentials": True
    }
})

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===== DIRETÓRIOS =====
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'database', 'contratos.db')
UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads')
PDF_DIR = os.path.join(BASE_DIR, 'signed_pdfs')
UPLOAD_TXT_DIR = os.path.join(BASE_DIR, 'uploads', 'txt')
UPLOAD_OS_DIR = os.path.join(BASE_DIR, 'uploads', 'os_pdfs')
SIGNATURE_DIR = os.path.join(BASE_DIR, 'signatures')
TEMPLATES_DIR = os.path.join(BASE_DIR, 'templates')

for d in [UPLOAD_DIR, PDF_DIR, os.path.dirname(DB_PATH), UPLOAD_TXT_DIR, UPLOAD_OS_DIR, SIGNATURE_DIR, TEMPLATES_DIR]:
    os.makedirs(d, exist_ok=True)

# ===== CONFIGURAÇÕES DE E-MAIL =====
EMAIL_CONFIG = {
    'smtp_server': os.getenv('EMAIL_SERVER', 'smtp.gmail.com'),
    'smtp_port': int(os.getenv('EMAIL_PORTA', 587)),
    'email_remetente': os.getenv('EMAIL_REMETENTE', 'sua-empresa@gmail.com'),
    'email_senha': os.getenv('EMAIL_SENHA', ''),
    'email_nome': os.getenv('EMAIL_NOME', 'Sua-empresa Internet')
}

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

BASE_URL = os.getenv('BASE_URL', f"http://{get_local_ip()}:5000")
logger.info(f"🌐 BASE_URL configurada: {BASE_URL}")

RADIUSNET_API_URL = os.getenv('RADIUSNET_API_URL')
RADIUSNET_API_TOKEN = os.getenv('RADIUSNET_API_TOKEN')

# ================================================================
# FUNÇÃO: GERAR PDF COM TIMEOUT (WeasyPrint ou Fallback)
# ================================================================
def gerar_pdf_com_timeout(html_content, timeout=30):
    """
    Gera PDF a partir de HTML usando WeasyPrint com timeout.
    Se WeasyPrint não estiver disponível, usa ReportLab simples.
    """
    if not WEASYPRINT_AVAILABLE:
        logger.warning("⚠️ WeasyPrint não disponível. Usando fallback ReportLab.")
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        buf = BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15*mm, rightMargin=15*mm,
                               topMargin=15*mm, bottomMargin=15*mm)
        styles = getSampleStyleSheet()
        story = []
        # Tenta extrair título do HTML
        title = "Documento Gerado"
        match = re.search(r'<title>(.*?)</title>', html_content, re.IGNORECASE)
        if match:
            title = match.group(1)
        story.append(Paragraph(title, styles['Title']))
        # Limpa tags HTML para texto simples
        text = re.sub(r'<[^>]+>', ' ', html_content)
        for line in text.split('\n'):
            if line.strip():
                story.append(Paragraph(line.strip(), styles['Normal']))
        doc.build(story)
        buf.seek(0)
        return buf.read()

    logger.info(f"🔄 Gerando PDF com WeasyPrint (timeout: {timeout}s)")
    try:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(HTML(string=html_content).write_pdf)
            pdf_bytes = future.result(timeout=timeout)
            logger.info(f"✅ PDF gerado com sucesso! Tamanho: {len(pdf_bytes)} bytes")
            return pdf_bytes
    except concurrent.futures.TimeoutError:
        logger.error(f"❌ Timeout ao gerar PDF após {timeout}s")
        raise TimeoutError(f"Geração do PDF excedeu {timeout} segundos")
    except Exception as e:
        logger.error(f"❌ Erro ao gerar PDF: {e}")
        raise

# ================================================================
# CONFIGURAÇÕES DE ASSINATURA - LOCALIZAÇÃO NO PDF
# ================================================================
SIG_CONFIG_PADRAO = {'quality': 100, 'dpi': 600, 'width': 180, 'height': 93}
TEXT_FIELDS_CONFIG_PADRAO = {'font': 'Helvetica-Bold', 'font_size': 13}
DATE_FIELDS_CONFIG_PADRAO = {'font': 'Helvetica-Bold', 'font_size': 13}

# ---------- Configuração para INSTALAÇÃO ----------
SIG_CONFIG_INSTALACAO = {
    'page_index': 9,
    'x': 85,
    'y': 265,
    **SIG_CONFIG_PADRAO
}
SIG_CONFIG_RESPONSAVEL_INSTALACAO = {
    'page_index': 9,
    'x': 355,
    'y': 225,
    **SIG_CONFIG_PADRAO
}
TEXT_FIELDS_CONFIG_INSTALACAO = {
    'page_index': 9,
    **TEXT_FIELDS_CONFIG_PADRAO,
    'fields': {
        'campo_cliente': {'x': 450, 'y': 702},
        'campo_ctop': {'x': 465, 'y': 680},
        'campo_db': {'x': 415, 'y': 657},
    }
}
DATE_FIELDS_CONFIG_INSTALACAO = {
    'page_index': 9,
    **DATE_FIELDS_CONFIG_PADRAO,
    'fields': {
        'data_dia': {'x': 184, 'y': 346},
        'data_mes': {'x': 300, 'y': 346},
        'data_ano': {'x': 435, 'y': 346}
    }
}

# ---------- Configuração para MUDANÇA DE ENDEREÇO ----------
SIG_CONFIG_MUDANCA = {
    'page_index': 10,
    'x': 85,
    'y': 125,
    **SIG_CONFIG_PADRAO
}
SIG_CONFIG_RESPONSAVEL_MUDANCA = {
    'page_index': 10,
    'x': 335,
    'y': 84,
    **SIG_CONFIG_PADRAO
}
TEXT_FIELDS_CONFIG_MUDANCA = {
    'page_index': 10,
    **TEXT_FIELDS_CONFIG_PADRAO,
    'fields': {
        'campo_cliente': {'x': 467, 'y': 581},
        'campo_ctop': {'x': 474, 'y': 558},
        'campo_db': {'x': 433, 'y': 534},
    }
}
DATE_FIELDS_CONFIG_MUDANCA = {
    'page_index': 10,
    **DATE_FIELDS_CONFIG_PADRAO,
    'fields': {
        'data_dia': {'x': 169, 'y': 207},
        'data_mes': {'x': 267, 'y': 209},
        'data_ano': {'x': 393, 'y': 207}
    }
}

# ---------- Configuração para MUDANÇA DE ASSINANTE ----------
SIG_CONFIG_MUDANCA_ASSINANTE = {
    'page_index': 10,
    'x': 85,
    'y': 125,
    **SIG_CONFIG_PADRAO
}
SIG_CONFIG_RESPONSAVEL_MUDANCA_ASSINANTE = {
    'page_index': 10,
    'x': 335,
    'y': 84,
    **SIG_CONFIG_PADRAO
}
TEXT_FIELDS_CONFIG_MUDANCA_ASSINANTE = {
    'page_index': 10,
    **TEXT_FIELDS_CONFIG_PADRAO,
    'fields': {
        'campo_cliente': {'x': 467, 'y': 581},
        'campo_ctop': {'x': 474, 'y': 558},
        'campo_db': {'x': 433, 'y': 534},
    }
}
DATE_FIELDS_CONFIG_MUDANCA_ASSINANTE = {
    'page_index': 10,
    **DATE_FIELDS_CONFIG_PADRAO,
    'fields': {
        'data_dia': {'x': 169, 'y': 207},
        'data_mes': {'x': 267, 'y': 209},
        'data_ano': {'x': 393, 'y': 207}
    }
}

# ---------- Configuração para ORDEM DE SERVIÇO (OS) ----------
SIG_CONFIG_OS = {
    'page_index': 0,
    'x': 50,
    'y': 430,
    'width': 180,
    'height': 85,
    'quality': 100,
    'dpi': 600,
}
TEXT_FIELDS_CONFIG_OS = {
    'page_index': 0,
    'font': 'Helvetica',
    'font_size': 10,
    'fields': {
        'os_descricao': {'x': 48, 'y': 230},
    }
}

# ---------- Configuração para RETIRADA ----------
TEXT_FIELDS_CONFIG_RETIRADA = {
    'page_index': 0,
    'font': 'Helvetica',
    'font_size': 10,
    'fields': {
        'motivo_retirada_completo': {'x': 175, 'y': 531},
        'boleto_aberto_texto': {'x': 155, 'y': 518},
        'qtd_onu': {'x': 300, 'y': 632},
        'qtd_cordao_monofibra': {'x': 300, 'y': 615},
        'qtd_pto': {'x': 300, 'y': 598},
        'qtd_roteador': {'x': 300, 'y': 581},
        'qtd_suporte_roteador': {'x': 300, 'y': 564},
        'tentativa_1_texto': {'x': 130, 'y': 438},
        'tentativa_2_texto': {'x': 130, 'y': 417},
        'tentativa_3_texto': {'x': 130, 'y': 396},
        'tecnico_nome': {'x': 130, 'y': 375},
    }
}
DATE_FIELDS_CONFIG_RETIRADA = {
    'page_index': 0,
    'font': 'Helvetica',
    'font_size': 10,
    'fields': {}
}

# ================================================================
# RADIUSNET CLIENT (com correções)
# ================================================================
# ================================================================
# RADIUSNET CLIENT (com correções)
# ================================================================
class RadiusNetClient:
    def __init__(self, api_url, api_token):
        self.api_url = api_url.rstrip('/')
        self.api_token = api_token
        self.session = requests.Session()
        self.session.headers.update({'RTOKEN': api_token})
        logger.info(f"RadiusNet client inicializado com API URL: {self.api_url}")

    def _request(self, method, endpoint, params=None, data=None, files=None):
        endpoint = endpoint.lstrip('/')
        if self.api_url.endswith('/v1'):
            url = f"{self.api_url}/{endpoint}"
        else:
            if '/api/v1' in self.api_url:
                url = f"{self.api_url}/{endpoint}"
            else:
                url = f"{self.api_url}/v1/{endpoint}"
        url = url.replace('/v1/v1/', '/v1/')
        logger.info(f"Requisição para: {url}")
        
        for attempt in range(3):
            try:
                resp = self.session.request(method, url, params=params, data=data, files=files, timeout=15)
                
                # CORREÇÃO: Trata 404 como "não encontrado" em vez de erro
                if resp.status_code == 404:
                    logger.warning(f"⚠️ Endpoint não encontrado (404): {url}")
                    return {'rows': []}
                
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get('X-Retry-After', 60))
                    logger.warning(f"Rate limit. Aguardando {retry_after}s")
                    time.sleep(retry_after)
                    continue
                
                resp.raise_for_status()
                return resp.json()
                
            except requests.exceptions.RequestException as e:
                logger.error(f"Erro (tentativa {attempt+1}): {e}")
                if attempt == 2:
                    raise
                time.sleep(1)
        return None

    def _formatar_data_hora(self, data, hora=None):
        if not data:
            return ''
        try:
            if isinstance(data, str):
                for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%d/%m/%Y %H:%M', '%d/%m/%Y']:
                    try:
                        dt = datetime.strptime(data, fmt)
                        if hora and fmt in ['%Y-%m-%d', '%d/%m/%Y']:
                            try:
                                hora_dt = datetime.strptime(hora, '%H:%M:%S')
                                dt = dt.replace(hour=hora_dt.hour, minute=hora_dt.minute)
                            except:
                                pass
                        return dt.strftime('%d/%m/%Y %H:%M')
                    except ValueError:
                        continue
                return data
            return data
        except Exception as e:
            logger.warning(f"Erro ao formatar data: {e}")
            return data

    def get_atendimentos_cliente(self, id_cliente):
        try:
            endpoint = f"atl/{id_cliente}"
            result = self._request('GET', endpoint)
            if result and result.get('rows'):
                return result['rows']
            return []
        except Exception as e:
            logger.error(f"Erro ao buscar atendimentos do cliente {id_cliente}: {e}")
            return []

    def get_atendimento_por_os(self, id_ordem_servico, id_cliente):
        try:
            atendimentos = self.get_atendimentos_cliente(id_cliente)
            if not atendimentos:
                return None
            for at in atendimentos:
                protocolo = at.get('protocolo', '')
                if protocolo and str(id_ordem_servico) in protocolo:
                    return at
                descricao = at.get('ultima_descricao', '')
                if descricao and str(id_ordem_servico) in descricao:
                    return at
            if atendimentos:
                logger.info(f"Usando primeiro atendimento como fallback: {atendimentos[0].get('id_atendimento')}")
                return atendimentos[0]
            return None
        except Exception as e:
            logger.error(f"Erro ao buscar atendimento da OS: {e}")
            return None

    def get_atendimento_por_id(self, id_atendimento):
        try:
            endpoint = f"atd/{id_atendimento}"
            result = self._request('GET', endpoint)
            if result and result.get('rows'):
                return result['rows']
            return None
        except Exception as e:
            logger.error(f"Erro ao buscar atendimento {id_atendimento}: {e}")
            return None

    def get_termo_adesao_pdf(self, id_cliente_plano, tipo_termo=0, formato='pdf'):
        try:
            endpoint = f"termo/{id_cliente_plano}/{tipo_termo}/{formato}"
            result = self._request('GET', endpoint)
            
            # Verifica se o resultado é válido
            if not result:
                raise Exception(f"Resposta vazia ao buscar termo para {id_cliente_plano}")
            
            if not result.get('rows'):
                raise Exception(f"Termo não encontrado para {id_cliente_plano}")
            
            pdf_url = result['rows']
            
            # Verifica se a URL é válida
            if not pdf_url or not isinstance(pdf_url, str):
                raise Exception(f"URL do termo inválida: {pdf_url}")
            
            logger.info(f"📄 Baixando termo de: {pdf_url}")
            resp = requests.get(pdf_url, timeout=30, verify=True)
            resp.raise_for_status()
            
            logger.info(f"✅ Termo baixado com sucesso! Tamanho: {len(resp.content)} bytes")
            return resp.content
            
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Erro ao baixar termo PDF: {e}")
            raise Exception(f"Erro ao baixar termo: {str(e)}")
        except Exception as e:
            logger.error(f"❌ Erro ao buscar termo: {e}")
            raise

    def get_ordens_servico(self, id_cliente_plano):
        """
        Busca ordens de serviço de um cliente.
        CORRIGIDO: Retorna lista vazia em vez de erro 404.
        """
        try:
            endpoint = f"cpos/{id_cliente_plano}"
            result = self._request('GET', endpoint)
            if result and result.get('rows'):
                return result['rows']
            return []
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.warning(f"⚠️ OS não encontradas para cliente plano {id_cliente_plano}")
                return []
            raise
        except Exception as e:
            logger.error(f"❌ Erro ao buscar ordens de serviço para {id_cliente_plano}: {e}")
            return []

    def get_ordens_servico_por_tipo(self, tipo_os, pagina=1):
        try:
            endpoint = f"tos/{tipo_os}/{pagina}"
            result = self._request('GET', endpoint)
            if result and result.get('rows'):
                return {'rows': result['rows'], 'count': result.get('count', 0)}
            return {'rows': [], 'count': 0}
        except Exception as e:
            logger.error(f"Erro ao buscar OS por tipo {tipo_os}: {e}")
            return {'rows': [], 'count': 0}

    def get_os_completa_por_id(self, id_ordem_servico, tipo_os=None):
        try:
            id_busca = str(id_ordem_servico)
            tipos_a_buscar = [tipo_os] if tipo_os else [1, 2, 3]
            
            for tipo in tipos_a_buscar:
                pagina = 1
                while True:
                    resultado = self.get_ordens_servico_por_tipo(tipo, pagina)
                    rows = resultado.get('rows', [])
                    
                    if not rows:
                        break
                    
                    for os_item in rows:
                        if str(os_item.get('id_ordem_servico')) == id_busca:
                            os_item['tipo_os'] = tipo
                            logger.info(f"OS {id_busca} encontrada no tipo {tipo}, pagina {pagina}")
                            return os_item
                    
                    count = resultado.get('count', 0)
                    if len(rows) < count and len(rows) > 0:
                        pagina += 1
                    else:
                        break
            
            logger.warning(f"OS {id_busca} nao encontrada em nenhum tipo/pagina")
            return None
        except Exception as e:
            logger.error(f"Erro ao buscar OS {id_ordem_servico}: {e}", exc_info=True)
            return None

    def get_ctos(self):
        try:
            endpoint = "cto"
            result = self._request('GET', endpoint)
            if result and result.get('rows'):
                return result['rows']
            return []
        except Exception as e:
            logger.error(f"Erro ao buscar lista de CTOs: {e}")
            return []

    def get_cto_por_id(self, id_cto):
        try:
            endpoint = f"cto/{id_cto}"
            result = self._request('GET', endpoint)
            if result and result.get('rows'):
                rows = result['rows']
                if isinstance(rows, list) and rows:
                    return rows[0]
                elif isinstance(rows, dict):
                    return rows
            return None
        except Exception as e:
            logger.error(f"Erro ao buscar CTO {id_cto}: {e}")
            return None

    def get_dados_cadastrais_cliente(self, id_cliente_plano):
        try:
            endpoint = f"cpcd/cp/{id_cliente_plano}"
            result = self._request('GET', endpoint)
            if result and isinstance(result, dict):
                rows = result.get('rows')
                if rows:
                    if isinstance(rows, dict):
                        return rows
                    elif isinstance(rows, list) and rows:
                        return rows[0]
            return None
        except Exception as e:
            logger.error(f"Erro ao buscar dados cadastrais do cliente {id_cliente_plano}: {e}")
            return None

    def get_cliente_por_id(self, id_cliente):
        try:
            endpoint = f"cliente/{id_cliente}"
            result = self._request('GET', endpoint)
            if result and result.get('rows'):
                cliente = result['rows']
                if isinstance(cliente, list) and cliente:
                    return cliente[0]
                elif isinstance(cliente, dict):
                    return cliente
            return None
        except Exception as e:
            logger.error(f"Erro ao buscar cliente por ID {id_cliente}: {e}")
            return None

    def get_cliente_por_cpf(self, cpf):
        try:
            cpf_clean = re.sub(r'\D', '', cpf)
            endpoint = f"cp/{cpf_clean}/5"
            result = self._request('GET', endpoint)
            if result and result.get('rows') and len(result['rows']) > 0:
                data = result['rows'][0]
                return {
                    'id_cliente': data.get('id_cliente', ''),
                    'nome': data.get('nome_razao', ''),
                    'cpf': data.get('cpf_cnpj', ''),
                    'planos': data.get('planos', [])
                }
            return None
        except Exception as e:
            logger.error(f"Erro ao buscar cliente por CPF {cpf}: {e}")
            return None

    def get_dados_cadastrais_completos(self, id_cliente_plano=None, id_cliente=None):
        cliente_data = {}
        if id_cliente_plano:
            try:
                cliente_data = self.get_dados_cadastrais_cliente(id_cliente_plano)
                if cliente_data:
                    logger.info(f"✅ Dados cadastrais encontrados via id_cliente_plano: {id_cliente_plano}")
            except Exception as e:
                logger.warning(f"⚠️ Erro ao buscar dados cadastrais por id_cliente_plano: {e}")
        if id_cliente and (not cliente_data or not cliente_data.get('nome')):
            try:
                cliente_data_por_id = self.get_cliente_por_id(id_cliente)
                if cliente_data_por_id:
                    cliente_data = cliente_data_por_id
                    logger.info(f"✅ Dados do cliente encontrados via id_cliente: {id_cliente}")
            except Exception as e:
                logger.warning(f"⚠️ Erro ao buscar cliente por ID: {e}")
        return cliente_data if cliente_data else {}

    def get_plano_por_cpf(self, cpf, status_plano=None):
        try:
            cpf_clean = re.sub(r'\D', '', cpf)
            if status_plano is not None:
                statuses = [status_plano]
            else:
                statuses = [2, 5, 1, 3, 4]
            for status in statuses:
                endpoint = f"cp/{cpf_clean}/{status}"
                result = self._request('GET', endpoint)
                if result and result.get('rows') and len(result['rows']) > 0:
                    data = result['rows'][0]
                    if 'planos' in data and len(data['planos']) > 0:
                        plano = data['planos'][0]
                        logger.info(f"✅ Plano encontrado com status {status}: {plano.get('plano', '')}")
                        return {
                            'id_cliente': data.get('id_cliente', ''),
                            'nome': data.get('nome_razao', ''),
                            'cpf': data.get('cpf_cnpj', ''),
                            'id_cliente_plano': plano.get('id_cliente_plano', ''),
                            'plano': plano.get('plano', ''),
                            'login': plano.get('login', ''),
                            'senha': plano.get('senha', ''),
                            'mac': plano.get('mac_cadastro', '') or plano.get('mac_conexao', ''),
                            'ip': plano.get('ip_atual', ''),
                            'id_cto': plano.get('id_cto', ''),
                            'id_cto_porta': plano.get('id_cto_porta', ''),
                            'status_plano': plano.get('status_plano', ''),
                            'dia_vencimento': plano.get('dia_vencimento', ''),
                            'valor_plano': plano.get('valor_plano', ''),
                            'observacao': plano.get('observacao', ''),
                            'status_roteador': data.get('statusRoteador', {}).get('status', ''),
                            'ip_nas': plano.get('ip_nas', ''),
                            'data_ativacao': plano.get('data_ativacao', ''),
                        }
            logger.warning(f"⚠️ Nenhum plano encontrado para CPF {cpf} nos status testados: {statuses}")
            return {}
        except Exception as e:
            logger.error(f"Erro ao buscar plano por CPF {cpf}: {e}")
            return {}

    def get_plano_por_login(self, login):
        try:
            endpoint = f"cpl/{login}"
            result = self._request('GET', endpoint)
            if result and result.get('rows') and len(result['rows']) > 0:
                data = result['rows'][0]
                if 'planos' in data and len(data['planos']) > 0:
                    plano = data['planos'][0]
                    return {
                        'id_cliente': data.get('id_cliente', ''),
                        'nome': data.get('nome_razao', ''),
                        'cpf': data.get('cpf_cnpj', ''),
                        'id_cliente_plano': plano.get('id_cliente_plano', ''),
                        'plano': plano.get('plano', ''),
                        'login': plano.get('login', ''),
                        'senha': plano.get('senha', ''),
                        'mac': plano.get('mac_cadastro', '') or plano.get('mac_conexao', ''),
                        'ip': plano.get('ip_atual', ''),
                        'id_cto': plano.get('id_cto', ''),
                        'status_plano': plano.get('status_plano', ''),
                        'dia_vencimento': plano.get('dia_vencimento', ''),
                        'valor_plano': plano.get('valor_plano', ''),
                    }
            return {}
        except Exception as e:
            logger.error(f"Erro ao buscar plano por login {login}: {e}")
            return None

    def get_cliente_completo_por_plano(self, id_cliente_plano):
        try:
            endpoint = f"cpcd/cp/{id_cliente_plano}"
            logger.info(f"Buscando cliente completo via: {endpoint}")
            result = self._request('GET', endpoint)
            if result and isinstance(result, dict):
                rows = result.get('rows')
                if rows:
                    if isinstance(rows, dict):
                        return rows
                    elif isinstance(rows, list) and rows:
                        return rows[0]
            return None
        except Exception as e:
            logger.error(f"Erro ao buscar cliente completo por plano: {e}")
            return None

    def get_dados_cliente_por_plano(self, id_cliente_plano):
        try:
            cliente = self.get_cliente_completo_por_plano(id_cliente_plano)
            if not cliente:
                # CORREÇÃO: Tenta buscar OS, mas não falha se não encontrar
                os_list = []
                try:
                    os_list = self.get_ordens_servico(id_cliente_plano)
                except Exception as e:
                    logger.warning(f"⚠️ Não foi possível buscar OS para {id_cliente_plano}: {e}")
                
                if os_list and isinstance(os_list, list) and os_list:
                    first = os_list[0]
                    return {
                        'nome': first.get('cliente_nome', 'Cliente'),
                        'endereco': first.get('endereco_instalacao', ''),
                        'plano': first.get('plano', ''),
                        'velocidade': '',
                        'login': first.get('login', ''),
                        'cpf': first.get('cpf_cnpj', '')
                    }
                return None
            
            nome = cliente.get('nome', 'Cliente')
            cpf = cliente.get('cpf_cnpj_cliente', '') or cliente.get('cpf_cnpj', '')
            endereco = ''
            login = ''
            enderecos = cliente.get('enderecos', [])
            if isinstance(enderecos, list) and enderecos:
                e = enderecos[0]
                if isinstance(e, dict):
                    endereco = f"{e.get('logradouro','')}, {e.get('numero','')}"
                    if e.get('complemento'): endereco += f" - {e.get('complemento')}"
                    endereco += f" - {e.get('bairro','')} - {e.get('cidade','')}/{e.get('estado','')} - CEP: {e.get('cep','')}"
            telefones = cliente.get('telefones', [])
            if isinstance(telefones, list) and telefones:
                login = telefones[0]
            plano_nome = ''
            velocidade = ''
            
            # CORREÇÃO: Tenta buscar OS, mas não falha se não encontrar
            os_list = []
            try:
                os_list = self.get_ordens_servico(id_cliente_plano)
            except Exception as e:
                logger.warning(f"⚠️ Não foi possível buscar OS para detalhes: {e}")
            
            if os_list and isinstance(os_list, list) and os_list:
                first = os_list[0]
                plano_nome = first.get('plano', '')
                if not login:
                    login = first.get('login', '')
            if plano_nome:
                match = re.search(r'(\d+)\s*(MEGA|MEGAS|Mbps|MB)', plano_nome, re.IGNORECASE)
                if match:
                    velocidade = f"{match.group(1)} MEGAS"
            return {
                'nome': nome,
                'endereco': endereco,
                'plano': plano_nome,
                'velocidade': velocidade,
                'login': login,
                'cpf': cpf
            }
        except Exception as e:
            logger.error(f"❌ Erro em get_dados_cliente_por_plano: {e}", exc_info=True)
            return None

    def extrair_dados_os_completos(self, os_data, cliente_data_bruto=None, tecnico_logado=None):
        """Extrai todos os dados da OS com tratamento robusto para campos nulos."""
        try:
            protocolo = os_data.get('protocolo', '')
            id_ordem_servico = os_data.get('id_ordem_servico', '')
            id_cliente = os_data.get('id_cliente', '')
            id_cliente_plano = os_data.get('id_cliente_plano', '')
            login = os_data.get('login', '')
            senha = os_data.get('senha', '')
            status = os_data.get('status', '')
            data_abertura = os_data.get('data_abertura', '')
            data_execucao = os_data.get('data_execucao', '')
            hora_execucao = os_data.get('hora_execucao', '')
            valor_instalacao = os_data.get('valor_instalacao', '')
            tipo_os = os_data.get('tipo_os', '')
            cpf_os = os_data.get('cpf_cnpj', '')
            
            logger.info(f"🔍 CPF da OS (os_data): '{cpf_os}'")
            logger.info(f"🔍 id_cliente: {id_cliente}, id_cliente_plano: {id_cliente_plano}")
            
            descricao_completa = ''
            ocorrencias_texto = ''
            id_atendimento = None
            situacao_atendimento = ''
            servico_atendimento = ''
            
            if id_cliente:
                try:
                    atendimentos = self.get_atendimentos_cliente(id_cliente)
                    if atendimentos:
                        logger.info(f"✅ Encontrados {len(atendimentos)} atendimentos para o cliente {id_cliente}")
                        for at in atendimentos:
                            protocolo_at = at.get('protocolo', '')
                            descricao_at = at.get('ultima_descricao', '')
                            servico_at = at.get('servico', '')
                            if (protocolo_at and str(id_ordem_servico) in str(protocolo_at)) or \
                               (descricao_at and str(id_ordem_servico) in str(descricao_at)) or \
                               (servico_at and str(id_ordem_servico) in str(servico_at)):
                                descricao_completa = descricao_at
                                id_atendimento = at.get('id_atendimento')
                                ocorrencias_texto = descricao_at
                                situacao_atendimento = at.get('situacao', '')
                                servico_atendimento = at.get('servico', '')
                                logger.info(f"✅ Encontrado atendimento {id_atendimento}")
                                break
                        if not descricao_completa and atendimentos:
                            ultimo_at = atendimentos[-1]
                            descricao_completa = ultimo_at.get('ultima_descricao', '')
                            id_atendimento = ultimo_at.get('id_atendimento')
                            ocorrencias_texto = descricao_completa
                            situacao_atendimento = ultimo_at.get('situacao', '')
                            servico_atendimento = ultimo_at.get('servico', '')
                            logger.info(f"⚠️ Usando último atendimento como fallback: {id_atendimento}")
                except Exception as e:
                    logger.warning(f"⚠️ Erro ao buscar atendimentos: {e}")
            
            if not descricao_completa:
                descricao_completa = (
                    os_data.get('descricao_servico', '') or 
                    os_data.get('descricao', '') or 
                    os_data.get('observacao', '') or
                    os_data.get('ocorrencias', '')
                )
            
            if not descricao_completa and id_atendimento:
                try:
                    atd = self.get_atendimento_por_id(id_atendimento)
                    if atd:
                        descricao_completa = atd.get('descricao', descricao_completa)
                except:
                    pass
            
            cliente_data = cliente_data_bruto or {}
            
            if id_cliente_plano and not cliente_data:
                try:
                    cliente_data = self.get_cliente_completo_por_plano(id_cliente_plano)
                    if cliente_data:
                        logger.info(f"✅ Dados via cpcd/cp/plano: {cliente_data.get('nome', '')}")
                except Exception as e:
                    logger.warning(f"⚠️ Erro cpcd/cp/plano: {e}")
            
            if id_cliente and (not cliente_data or not cliente_data.get('nome')):
                try:
                    cliente_data_por_id = self.get_cliente_por_id(id_cliente)
                    if cliente_data_por_id:
                        cliente_data = cliente_data_por_id
                        logger.info(f"✅ Dados via cliente/{id_cliente}: {cliente_data.get('nome', '')}")
                except Exception as e:
                    logger.warning(f"⚠️ Erro cliente/{id_cliente}: {e}")
            
            if login and (not cliente_data or not cliente_data.get('nome')):
                try:
                    plano_data = self.get_plano_por_login(login)
                    if plano_data:
                        cliente_data['plano_nome'] = plano_data.get('plano', '')
                        cliente_data['plano'] = plano_data.get('plano', '')
                        cliente_data['login'] = plano_data.get('login', '')
                        cliente_data['senha'] = plano_data.get('senha', '')
                        cliente_data['mac'] = plano_data.get('mac', '')
                        cliente_data['ip'] = plano_data.get('ip', '')
                        if not cliente_data.get('nome') and plano_data.get('nome'):
                            cliente_data['nome'] = plano_data.get('nome')
                        logger.info(f"✅ Dados via cpl/{login}: {cliente_data.get('nome', '')}")
                except Exception as e:
                    logger.warning(f"⚠️ Erro cpl/{login}: {e}")
            
            cpf_busca = None
            
            if cliente_data:
                cpf_busca = (
                    cliente_data.get('cpf_cnpj_cliente', '') or 
                    cliente_data.get('cpf_cnpj', '') or
                    cliente_data.get('cpf', '')
                )
            
            if not cpf_busca:
                cpf_busca = cpf_os
            
            if not cpf_busca and id_cliente_plano:
                try:
                    cliente_completo = self.get_cliente_completo_por_plano(id_cliente_plano)
                    if cliente_completo:
                        cpf_busca = (
                            cliente_completo.get('cpf_cnpj_cliente', '') or 
                            cliente_completo.get('cpf_cnpj', '') or
                            cliente_completo.get('cpf', '')
                        )
                        if cpf_busca and not cliente_data:
                            cliente_data = cliente_completo
                            logger.info(f"✅ CPF encontrado via get_cliente_completo_por_plano: {cpf_busca}")
                except Exception as e:
                    logger.warning(f"⚠️ Erro ao buscar cliente completo por plano: {e}")
            
            if not cpf_busca and id_cliente:
                try:
                    cliente_por_id = self.get_cliente_por_id(id_cliente)
                    if cliente_por_id:
                        cpf_busca = cliente_por_id.get('cpf_cnpj', '') or cliente_por_id.get('cpf', '')
                        if cpf_busca and not cliente_data:
                            cliente_data = cliente_por_id
                            logger.info(f"✅ CPF encontrado via cliente/{id_cliente}: {cpf_busca}")
                except Exception as e:
                    logger.warning(f"⚠️ Erro ao buscar cliente por ID para CPF: {e}")
            
            logger.info(f"🔍 CPF efetivo para busca: '{cpf_busca}'")
            
            if not cpf_busca:
                logger.warning("⚠️ Nenhum CPF disponível para busca - pode afetar a recuperação de plano/login/senha")
            
            if cpf_busca and (not cliente_data.get('login') or not cliente_data.get('senha') or not cliente_data.get('plano_nome')):
                try:
                    logger.info(f"🔍 Buscando plano via CPF: {cpf_busca}")
                    plano_por_cpf = self.get_plano_por_cpf(cpf_busca)
                    if plano_por_cpf:
                        if not cliente_data.get('login') and plano_por_cpf.get('login'):
                            cliente_data['login'] = plano_por_cpf.get('login')
                            logger.info(f"✅ Login obtido via CPF: {plano_por_cpf.get('login')}")
                        if not cliente_data.get('senha') and plano_por_cpf.get('senha'):
                            cliente_data['senha'] = plano_por_cpf.get('senha')
                            logger.info(f"✅ Senha obtida via CPF")
                        if not cliente_data.get('plano_nome') and plano_por_cpf.get('plano'):
                            cliente_data['plano_nome'] = plano_por_cpf.get('plano')
                            cliente_data['plano'] = plano_por_cpf.get('plano')
                            logger.info(f"✅ Plano obtido via CPF: {plano_por_cpf.get('plano')}")
                        if not cliente_data.get('mac') and plano_por_cpf.get('mac'):
                            cliente_data['mac'] = plano_por_cpf.get('mac')
                        if not cliente_data.get('ip') and plano_por_cpf.get('ip'):
                            cliente_data['ip'] = plano_por_cpf.get('ip')
                        if not cliente_data.get('id_cto') and plano_por_cpf.get('id_cto'):
                            cliente_data['id_cto'] = plano_por_cpf.get('id_cto')
                        if not cliente_data.get('id_cliente_plano') and plano_por_cpf.get('id_cliente_plano'):
                            cliente_data['id_cliente_plano'] = plano_por_cpf.get('id_cliente_plano')
                        if not cliente_data.get('nome') and plano_por_cpf.get('nome'):
                            cliente_data['nome'] = plano_por_cpf.get('nome')
                        logger.info(f"✅ Dados enriquecidos via get_plano_por_cpf({cpf_busca})")
                    else:
                        logger.warning(f"⚠️ get_plano_por_cpf({cpf_busca}) vazio")
                except Exception as e:
                    logger.warning(f"⚠️ Erro get_plano_por_cpf({cpf_busca}): {e}")
            
            nome = (cliente_data.get('nome_razao', '') or 
                    cliente_data.get('nome', '') or 
                    cliente_data.get('razao_social', '') or 
                    os_data.get('cliente_nome', '') or 
                    os_data.get('cliente', '') or 
                    'Cliente não informado')
            
            logger.info(f"📌 Nome do cliente final: {nome}")
            
            cpf = (cliente_data.get('cpf_cnpj', '') or 
                   cliente_data.get('cpf_cnpj_cliente', '') or 
                   os_data.get('cpf_cnpj', '') or
                   cpf_busca or '')
            cpf_formatado = cpf
            if cpf:
                cpf_clean = re.sub(r'\D', '', cpf)
                if len(cpf_clean) == 11:
                    cpf_formatado = f"{cpf_clean[:3]}.{cpf_clean[3:6]}.{cpf_clean[6:9]}-{cpf_clean[9:]}"
                elif len(cpf_clean) == 14:
                    cpf_formatado = f"{cpf_clean[:2]}.{cpf_clean[2:5]}.{cpf_clean[5:8]}/{cpf_clean[8:12]}-{cpf_clean[12:]}"
            
            telefones = cliente_data.get('telefones', [])
            if not telefones:
                telefones = cliente_data.get('telefone', [])
            if isinstance(telefones, str):
                telefones = [telefones] if telefones else []
            telefone_principal = telefones[0] if telefones else ''
            telefone_secundario = telefones[1] if len(telefones) > 1 else ''
            telefones_str = f'Celular: {telefone_principal}'
            if telefone_secundario:
                telefones_str += f' / Celular: {telefone_secundario}'
            
            emails = cliente_data.get('emails', [])
            if not emails:
                emails = cliente_data.get('email', [])
            if isinstance(emails, str):
                emails = [emails] if emails else []
            email_principal = emails[0] if emails else ''
            
            endereco_instalacao = ''
            enderecos = cliente_data.get('enderecos', [])
            if not enderecos:
                enderecos = cliente_data.get('endereco', [])
            if isinstance(enderecos, list) and enderecos:
                e = enderecos[0]
                if isinstance(e, dict):
                    partes = []
                    if e.get('logradouro'): partes.append(e.get('logradouro'))
                    if e.get('numero'): partes.append(f", {e.get('numero')}")
                    if e.get('complemento'): partes.append(f" - {e.get('complemento')}")
                    if e.get('bairro'): partes.append(f" - {e.get('bairro')}")
                    if e.get('cep'): partes.append(f" - CEP: {e.get('cep')}")
                    if e.get('cidade'): partes.append(f" - {e.get('cidade')}")
                    if e.get('estado'): partes.append(f"/{e.get('estado')}")
                    endereco_instalacao = ''.join(partes)
            if not endereco_instalacao:
                endereco_instalacao = os_data.get('endereco_instalacao', '') or os_data.get('endereco', '')
            
            endereco_cobranca = ''
            if isinstance(enderecos, list) and len(enderecos) > 1:
                ec = enderecos[1]
                if isinstance(ec, dict):
                    partes = []
                    if ec.get('logradouro'): partes.append(ec.get('logradouro'))
                    if ec.get('numero'): partes.append(f", {ec.get('numero')}")
                    if ec.get('complemento'): partes.append(f" - {ec.get('complemento')}")
                    if ec.get('bairro'): partes.append(f" - {ec.get('bairro')}")
                    if ec.get('cep'): partes.append(f" - CEP: {ec.get('cep')}")
                    if ec.get('cidade'): partes.append(f" - {ec.get('cidade')}")
                    if ec.get('estado'): partes.append(f"/{ec.get('estado')}")
                    endereco_cobranca = ''.join(partes)
            if not endereco_cobranca:
                endereco_cobranca = endereco_instalacao
            
            referencia_instalacao = ''
            if isinstance(enderecos, list) and enderecos:
                e = enderecos[0]
                if isinstance(e, dict):
                    referencia_instalacao = e.get('referencia', '') or e.get('ponto_referencia', '')
            if not referencia_instalacao:
                referencia_instalacao = os_data.get('referencia', '')
            
            plano = cliente_data.get('plano_nome') or cliente_data.get('plano') or os_data.get('plano', '')
            
            velocidade = ''
            if plano:
                match = re.search(r'(\d+)\s*(MEGA|MEGAS|Mbps|MB)', plano, re.IGNORECASE)
                if match:
                    velocidade = f"{match.group(1)} MEGAS"
            
            id_cto = os_data.get('id_cto', '') or cliente_data.get('id_cto', '')
            caixa_cto = ''
            endereco_cto = ''
            if id_cto:
                cto_data = self.get_cto_por_id(id_cto)
                if cto_data:
                    caixa_cto = cto_data.get('nome_cto', '')
                    endereco_cto = cto_data.get('endereco_cto', '')
                    logger.info(f"✅ CTO {id_cto} encontrada: {caixa_cto}")
                else:
                    cto_nome_fallback = self.get_cto_by_id(id_cto)
                    if cto_nome_fallback:
                        caixa_cto = cto_nome_fallback
            
            porta_cto = ''
            id_cto_porta = os_data.get('id_cto_porta', '') or cliente_data.get('id_cto_porta', '')
            if id_cto and id_cto_porta:
                porta_num = self.get_porta_by_id_cto_porta(id_cto, id_cto_porta)
                if porta_num:
                    porta_cto = f"Porta {porta_num}"
            elif login and id_cto:
                porta_num = self.get_porta_from_cto(id_cto, login)
                if porta_num:
                    porta_cto = f"Porta {porta_num}"
            
            mac = os_data.get('mac_cadastro', '') or cliente_data.get('mac', '') or os_data.get('mac', '')
            ip = os_data.get('ip_atual', '') or cliente_data.get('ip', '') or os_data.get('ip', '')
            
            serial_fiberhome = os_data.get('serial_fiberhome', '') or cliente_data.get('serial_fiberhome', '')
            serial_huawei = os_data.get('serial_huawei', '') or cliente_data.get('serial_huawei', '')
            serial_outros = os_data.get('serial_outros', '') or cliente_data.get('serial_outros', '')
            serial_datacom = os_data.get('serial_datacom', '') or cliente_data.get('serial_datacom', '')
            serial_onu = os_data.get('serial_onu', '') or cliente_data.get('serial_onu', '')
            psk = os_data.get('psk', '') or cliente_data.get('psk', '')
            
            observacao = os_data.get('observacao', '') or cliente_data.get('observacao', '')
            equipamentos_comodato = os_data.get('equipamentos_comodato', '') or cliente_data.get('equipamentos_comodato', '')
            
            cliente_insc_estadual = cliente_data.get('insc_estadual', '') or cliente_data.get('ie', '')
            parametros_cliente = cliente_data.get('parametros', '') or ''
            
            if tecnico_logado and tecnico_logado.get('nome'):
                tecnico_responsavel = tecnico_logado.get('nome')
                logger.info(f"✅ Usando técnico logado: {tecnico_responsavel}")
            else:
                tecnico_responsavel = os_data.get('tecnico_nome', '') or os_data.get('tecnico', '')
            
            status_os = status or 'Aberta'
            status_class = ''
            if status_os.lower() in ['finalizada', 'concluída', 'concluida', 'closed']:
                status_class = 'finalizada'
            elif status_os.lower() in ['cancelada', 'canceled']:
                status_class = 'cancelada'
            else:
                status_class = 'aberta'
            
            data_hora_finalizacao = datetime.now().strftime('%d/%m/%Y %H:%M')
            
            data_abertura_formatada = self._formatar_data_hora(data_abertura)
            data_execucao_formatada = self._formatar_data_hora(data_execucao, hora_execucao)
            
            cliente_telefones_emails = telefones_str
            if email_principal:
                cliente_telefones_emails = f"{telefones_str} - {email_principal}"
            
            return {
                'protocolo': protocolo,
                'id_ordem_servico': id_ordem_servico,
                'id_cliente': id_cliente,
                'id_cliente_plano': id_cliente_plano,
                'login': cliente_data.get('login') or login,
                'senha': cliente_data.get('senha') or senha,
                'status': status_os,
                'status_class': status_class,
                'data_abertura': data_abertura,
                'data_execucao': data_execucao,
                'hora_execucao': hora_execucao,
                'data_abertura_formatada': data_abertura_formatada,
                'data_execucao_formatada': data_execucao_formatada,
                'descricao': descricao_completa,
                'descricao_servico': descricao_completa,
                'ocorrencias': ocorrencias_texto,
                'situacao_atendimento': situacao_atendimento,
                'servico_atendimento': servico_atendimento,
                'valor_instalacao': valor_instalacao,
                'tipo_os': tipo_os,
                'data_hora_finalizacao': data_hora_finalizacao,
                'cliente_nome': nome,
                'cliente_documento': cpf_formatado,
                'cliente_insc_estadual': cliente_insc_estadual,
                'cliente_telefones': telefones_str,
                'cliente_email': email_principal,
                'cliente_telefones_emails': cliente_telefones_emails,
                'endereco_instalacao': endereco_instalacao,
                'endereco_cobranca': endereco_cobranca,
                'referencia_instalacao': referencia_instalacao,
                'plano_nome': plano,
                'plano': plano,
                'velocidade': velocidade,
                'velocidade_valor': velocidade,
                'id_cto': id_cto,
                'caixa_cto': caixa_cto,
                'endereco_cto': endereco_cto,
                'porta_cto': porta_cto,
                'mac': mac,
                'mac_address': mac,
                'ip': ip,
                'ip_address': ip,
                'psk': psk,
                'psk_wifi': psk,
                'serial_fiberhome': serial_fiberhome,
                'serial_huawei': serial_huawei,
                'serial_outros': serial_outros,
                'serial_datacom': serial_datacom,
                'serial_onu': serial_onu,
                'observacao': observacao,
                'equipamentos_comodato': equipamentos_comodato,
                'parametros_cliente': parametros_cliente,
                'tecnico_responsavel': tecnico_responsavel,
                'tecnico_nome': tecnico_responsavel,
                'id_atendimento': id_atendimento,
            }
        except Exception as e:
            logger.error(f"❌ Erro ao extrair dados completos da OS: {e}", exc_info=True)
            return None

    def get_cto_by_id(self, id_cto):
        try:
            cto_path = os.path.join(BASE_DIR, 'ctos.json')
            if os.path.exists(cto_path):
                with open(cto_path, 'r', encoding='utf-8') as f:
                    ctos = json.load(f)
                    if str(id_cto) in ctos:
                        return ctos[str(id_cto)].get('nome', '')
            return None
        except Exception as e:
            logger.error(f"Erro ao buscar CTO {id_cto} do cache: {e}")
            return None

    def get_porta_from_cto(self, id_cto, login):
        try:
            cto_data = self.get_cto_por_id(id_cto)
            if cto_data and 'planos' in cto_data:
                for plano in cto_data['planos']:
                    if plano.get('login') == login:
                        porta = plano.get('porta')
                        logger.info(f"✅ Porta encontrada via v1/cto: {porta}")
                        return porta
            return None
        except Exception as e:
            logger.error(f"Erro ao buscar porta via v1/cto: {e}")
            return None

    def get_porta_by_id_cto_porta(self, id_cto, id_cto_porta):
        try:
            porta_id = int(id_cto_porta)
            if str(id_cto) == '199':
                porta = porta_id - 1900
                if 1 <= porta <= 8:
                    return str(porta)
            porta_str = str(porta_id)
            if len(porta_str) >= 2:
                sufixo = int(porta_str[-2:])
                total_portas = self.get_total_portas_cto(id_cto)
                if 1 <= sufixo <= total_portas:
                    return str(sufixo)
            return None
        except Exception as e:
            logger.error(f"Erro ao converter id_cto_porta {id_cto_porta}: {e}")
            return None

    def get_total_portas_cto(self, id_cto):
        try:
            cto_data = self.get_cto_por_id(id_cto)
            if cto_data and 'total_portas' in cto_data:
                return int(cto_data['total_portas'])
            return 8
        except Exception as e:
            logger.error(f"Erro ao buscar total de portas da CTO {id_cto}: {e}")
            return None

    def finalizar_atendimento(self, id_atendimento, id_resolucao, descricao, descr_resolucao=''):
        try:
            endpoint = "atf/"
            data = {
                'IDAT': id_atendimento,
                'IDR': id_resolucao,
                'DESC': descricao,
                'DESCR': descr_resolucao or descricao
            }
            logger.info(f"📤 Finalizando atendimento {id_atendimento}")
            result = self._request('POST', endpoint, data=data)
            if not result or not result.get('rows'):
                raise Exception("Erro ao finalizar atendimento - resposta vazia")
            rows = result['rows']
            if isinstance(rows, list) and rows:
                return rows[0]
            elif isinstance(rows, dict):
                return rows
            else:
                return result
        except Exception as e:
            logger.error(f"❌ Erro ao finalizar atendimento {id_atendimento}: {e}")
            raise

    def adicionar_ocorrencia(self, id_atendimento, descricao):
        try:
            endpoint = "atno/"
            data = {'IDAT': id_atendimento, 'DESC': descricao}
            result = self._request('POST', endpoint, data=data)
            if not result or not result.get('rows'):
                raise Exception("Erro ao adicionar ocorrência")
            return result['rows'][0] if isinstance(result['rows'], list) else result['rows']
        except Exception as e:
            logger.error(f"Erro ao adicionar ocorrência ao atendimento {id_atendimento}: {e}")
            raise

    def get_os_pdf(self, id_ordem_servico, id_cliente_plano=None, tipo_os=None):
        try:
            logger.info(f"🔍 Gerando PDF completo para OS {id_ordem_servico}")
            os_data = self.get_os_completa_por_id(id_ordem_servico, tipo_os)
            if not os_data and id_cliente_plano:
                os_list = self.get_ordens_servico(id_cliente_plano)
                for item in os_list:
                    if str(item.get('id_ordem_servico', '')) == str(id_ordem_servico):
                        os_data = item
                        break
            if not os_data:
                logger.warning(f"⚠️ OS {id_ordem_servico} não encontrada")
                return None
            dados = self.extrair_dados_os_completos(os_data, {})
            if not dados:
                logger.error(f"❌ Falha ao extrair dados da OS {id_ordem_servico}")
                return None
            return self._gerar_pdf_os_completo(dados)
        except Exception as e:
            logger.error(f"❌ Erro ao gerar PDF da OS {id_ordem_servico}: {e}", exc_info=True)
            return None

    def _gerar_pdf_os_completo(self, dados):
        try:
            template_path = os.path.join(BASE_DIR, 'templates', 'os_template.html')
            if not os.path.exists(template_path):
                logger.warning(f"⚠️ Template HTML não encontrado: {template_path}")
                return self._gerar_pdf_os_simples(dados)
            
            with open(template_path, 'r', encoding='utf-8') as f:
                template_html = f.read()
            
            template = Template(template_html)
            
            html_content = template.render(
                config_tamanho_fonte=10,
                protocolo=dados.get('protocolo', ''),
                id_ordem_servico=dados.get('id_ordem_servico', ''),
                codigo_cliente=dados.get('id_cliente', ''),
                codigo_cliente_plano=dados.get('id_cliente_plano', ''),
                status=dados.get('status', ''),
                status_class=dados.get('status_class', 'aberta'),
                data_hora_finalizacao=dados.get('data_hora_finalizacao', ''),
                cliente_nome=dados.get('cliente_nome', ''),
                cliente_cpf=dados.get('cliente_documento', ''),
                cliente_insc_estadual=dados.get('cliente_insc_estadual', ''),
                cliente_telefones=dados.get('cliente_telefones', ''),
                cliente_email=dados.get('cliente_email', ''),
                endereco_instalacao=dados.get('endereco_instalacao', ''),
                endereco_cobranca=dados.get('endereco_cobranca', ''),
                referencia_instalacao=dados.get('referencia_instalacao', ''),
                plano=dados.get('plano_nome', ''),
                velocidade=dados.get('velocidade', ''),
                valor_instalacao=dados.get('valor_instalacao', ''),
                caixa_cto=dados.get('caixa_cto', ''),
                endereco_cto=dados.get('endereco_cto', ''),
                porta_cto=dados.get('porta_cto', ''),
                login=dados.get('login', ''),
                senha=dados.get('senha', ''),
                mac=dados.get('mac_address', ''),
                ip=dados.get('ip_address', ''),
                psk_wifi=dados.get('psk_wifi', ''),
                serial_fiberhome=dados.get('serial_fiberhome', ''),
                serial_huawei=dados.get('serial_huawei', ''),
                serial_outros=dados.get('serial_outros', ''),
                serial_datacom=dados.get('serial_datacom', ''),
                serial_onu=dados.get('serial_onu', ''),
                observacao=dados.get('observacao', ''),
                ocorrencias=dados.get('ocorrencias', ''),
                equipamentos_comodato=dados.get('equipamentos_comodato', ''),
                parametros_cliente=dados.get('parametros_cliente', ''),
                descricao_servico=dados.get('descricao', ''),
                tipo_servico=dados.get('servico_atendimento', ''),
                id_atendimento=dados.get('id_atendimento', ''),
                data_abertura=dados.get('data_abertura_formatada', dados.get('data_abertura', '')),
                data_execucao=dados.get('data_execucao_formatada', dados.get('data_execucao', '')),
                hora_execucao=dados.get('hora_execucao', ''),
                tecnico_responsavel=dados.get('tecnico_nome', ''),
            )
            
            logger.info(f"🔄 Gerando PDF da OS com WeasyPrint (timeout: 30s)")
            pdf_bytes = gerar_pdf_com_timeout(html_content, timeout=30)
            
            logger.info(f"✅ PDF gerado para OS {dados.get('id_ordem_servico')} - Tamanho: {len(pdf_bytes)} bytes")
            return pdf_bytes
            
        except TimeoutError as e:
            logger.error(f"⏰ Timeout ao gerar PDF da OS: {e}")
            return self._gerar_pdf_os_simples(dados)
        except Exception as e:
            logger.error(f"❌ Erro ao gerar PDF da OS com WeasyPrint: {e}", exc_info=True)
            return self._gerar_pdf_os_simples(dados)

    def _gerar_pdf_os_simples(self, dados):
        """PDF simples de fallback usando ReportLab"""
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        pdf_buffer = BytesIO()
        doc = SimpleDocTemplate(pdf_buffer, pagesize=A4,
                               rightMargin=15*mm, leftMargin=15*mm,
                               topMargin=15*mm, bottomMargin=15*mm)
        styles = getSampleStyleSheet()
        story = []
        title_style = ParagraphStyle('CustomTitle', parent=styles['Title'], fontSize=16, 
                                     textColor='#CC0000', alignment=1, spaceAfter=20)
        story.append(Paragraph('SUA-EMPRESA INTERNET FIBRA OPTICA LTDA', title_style))
        story.append(Paragraph('ORDEM DE SERVIÇO', styles['Heading2']))
        story.append(Spacer(1, 20))
        story.append(Paragraph(f"<b>Protocolo:</b> {dados.get('protocolo', '')}", styles['Normal']))
        story.append(Paragraph(f"<b>Nº OS:</b> {dados.get('id_ordem_servico', '')}", styles['Normal']))
        story.append(Paragraph(f"<b>Cliente:</b> {dados.get('cliente_nome', '')}", styles['Normal']))
        story.append(Paragraph(f"<b>CPF:</b> {dados.get('cliente_documento', '')}", styles['Normal']))
        if dados.get('cliente_insc_estadual'):
            story.append(Paragraph(f"<b>Inscrição Estadual:</b> {dados.get('cliente_insc_estadual')}", styles['Normal']))
        story.append(Paragraph(f"<b>Telefone:</b> {dados.get('cliente_telefones', '')}", styles['Normal']))
        story.append(Paragraph(f"<b>E-mail:</b> {dados.get('cliente_email', '')}", styles['Normal']))
        story.append(Paragraph(f"<b>Endereço de Instalação:</b> {dados.get('endereco_instalacao', '')}", styles['Normal']))
        if dados.get('endereco_cobranca'):
            story.append(Paragraph(f"<b>Endereço de Cobrança:</b> {dados.get('endereco_cobranca')}", styles['Normal']))
        if dados.get('referencia_instalacao'):
            story.append(Paragraph(f"<b>Referência:</b> {dados.get('referencia_instalacao')}", styles['Normal']))
        story.append(Paragraph(f"<b>Plano:</b> {dados.get('plano_nome', '')}", styles['Normal']))
        story.append(Paragraph(f"<b>Login:</b> {dados.get('login', '')}", styles['Normal']))
        story.append(Paragraph(f"<b>Senha:</b> {dados.get('senha', '')}", styles['Normal']))
        story.append(Paragraph(f"<b>CTO:</b> {dados.get('caixa_cto', '')} - {dados.get('porta_cto', '')}", styles['Normal']))
        story.append(Paragraph(f"<b>Status:</b> {dados.get('status', '')}", styles['Normal']))
        story.append(Spacer(1, 10))
        story.append(Paragraph('<b>📝 DESCRIÇÃO DO SERVIÇO:</b>', styles['Normal']))
        story.append(Paragraph(dados.get('descricao', ''), styles['Normal']))
        if dados.get('ocorrencias'):
            story.append(Spacer(1, 10))
            story.append(Paragraph(f"<b>Ocorrências:</b> {dados.get('ocorrencias', '')}", styles['Normal']))
        story.append(Spacer(1, 20))
        story.append(Paragraph('<i>Documento gerado em modo de fallback.</i>', styles['Italic']))
        doc.build(story)
        pdf_buffer.seek(0)
        return pdf_buffer.read()
# ================================================================
# INICIALIZAÇÃO DO RADIUSNET CLIENT
# ================================================================
radiusnet_client = None
if RADIUSNET_API_URL and RADIUSNET_API_TOKEN:
    radiusnet_client = RadiusNetClient(RADIUSNET_API_URL, RADIUSNET_API_TOKEN)
    logger.info("✅ RadiusNet client inicializado")
else:
    logger.warning("⚠️ RadiusNet client não inicializado")

# ================================================================
# FUNÇÕES DE ASSINATURA E UTILITÁRIOS
# ================================================================
def remove_white_background_otsu(image):
    """Remove fundo branco da imagem de assinatura usando método Otsu"""
    if image.mode != 'RGBA':
        image = image.convert('RGBA')
    gray = image.convert('L')
    hist = gray.histogram()
    total = sum(hist)
    sumB = 0
    wB = 0
    maximum = 0
    threshold = 128
    for i in range(256):
        wB += hist[i]
        if wB == 0:
            continue
        wF = total - wB
        if wF == 0:
            break
        sumB += i * hist[i]
        mB = sumB / wB
        mF = (total - sumB) / wF
        between = wB * wF * (mB - mF) ** 2
        if between > maximum:
            maximum = between
            threshold = i
    mask = gray.point(lambda p: 255 if p < threshold else 0)
    result = PILImage.new('RGBA', image.size, (0, 0, 0, 0))
    result.paste(image, (0, 0), mask)
    return result

def process_signature_image(b64_str):
    """Processa a imagem da assinatura para overlay no PDF"""
    raw = b64_str.split(',')[1] if ',' in b64_str else b64_str
    pil = PILImage.open(BytesIO(base64.b64decode(raw))).convert("RGBA")
    pil = remove_white_background_otsu(pil)
    pil = pil.filter(ImageFilter.GaussianBlur(radius=0.5))
    return pil

def get_current_date_parts():
    """Retorna as partes da data atual para preencher campos no PDF"""
    now = datetime.now()
    meses_pt = {1: 'janeiro', 2: 'fevereiro', 3: 'março', 4: 'abril',
                5: 'maio', 6: 'junho', 7: 'julho', 8: 'agosto',
                9: 'setembro', 10: 'outubro', 11: 'novembro', 12: 'dezembro'}
    return {'data_dia': str(now.day), 'data_mes': meses_pt[now.month], 'data_ano': str(now.year)}

def draw_multiline_text(c, text, x, y, font_name, font_size, max_width=450):
    """Desenha texto multilinha no PDF com quebra automática"""
    c.setFont(font_name, font_size)
    lines = []
    for line in text.split('\n'):
        words = line.split(' ')
        current_line = []
        current_width = 0
        for word in words:
            word_width = c.stringWidth(word + ' ', font_name, font_size)
            if current_width + word_width <= max_width:
                current_line.append(word)
                current_width += word_width
            else:
                if current_line:
                    lines.append(' '.join(current_line))
                current_line = [word]
                current_width = c.stringWidth(word + ' ', font_name, font_size)
        if current_line:
            lines.append(' '.join(current_line))
    line_height = font_size * 1.2
    for i, line in enumerate(lines):
        c.drawString(x, y - (i * line_height), line)
    return len(lines) * line_height

def get_db():
    """Retorna conexão com o banco de dados SQLite"""
    conn = sqlite3.connect(DB_PATH, timeout=20.0)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Inicializa o banco de dados com todas as tabelas necessárias"""
    conn = get_db()
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS seq_counter (
        id INTEGER PRIMARY KEY CHECK(id=1), valor INTEGER DEFAULT 0
    )''')
    c.execute('INSERT OR IGNORE INTO seq_counter VALUES (1, 0)')
    
    c.execute('''CREATE TABLE IF NOT EXISTS templates (
        id TEXT PRIMARY KEY, nome TEXT NOT NULL, filename TEXT NOT NULL,
        descricao TEXT, ativo INTEGER DEFAULT 1, criado_em TEXT DEFAULT CURRENT_TIMESTAMP,
        txt_path TEXT, txt_original_name TEXT, tipo TEXT DEFAULT 'instalacao',
        os_pdf_path TEXT, os_pdf_original_name TEXT,
        radiusnet_id_cliente_plano TEXT, radiusnet_tipo_termo INTEGER DEFAULT 0,
        radiusnet_data_importacao TEXT,
        cliente_nome TEXT, mensagem_extra TEXT, has_retirada INTEGER DEFAULT 0,
        radiusnet_id_os TEXT,
        data_agendada TEXT
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS contratos (
        id TEXT PRIMARY KEY, template_id TEXT, contrato_id TEXT NOT NULL,
        numero_seq INTEGER, tecnico_id TEXT, tecnico_nome TEXT,
        cliente_nome TEXT, cliente_cpf TEXT, assinatura_base64 TEXT,
        data_hora TEXT NOT NULL, ip_dispositivo TEXT, pdf_path TEXT,
        campo_cliente TEXT, campo_ctop TEXT, campo_db TEXT, os_descricao TEXT,
        tipo_instalacao TEXT DEFAULT 'instalacao', criado_em TEXT DEFAULT CURRENT_TIMESTAMP,
        assinatura_responsavel_id TEXT, assinatura_responsavel_data TEXT,
        signature_status TEXT DEFAULT 'complete', signature_token TEXT,
        token_expires_at TEXT, email_sent_to TEXT, signed_at TEXT,
        radiusnet_id_cliente_plano TEXT, radiusnet_tipo_termo INTEGER DEFAULT 0,
        radiusnet_id_atendimento TEXT, radiusnet_login TEXT, radiusnet_endereco TEXT,
        radiusnet_descricao_servico TEXT, mensagem_extra TEXT,
        radiusnet_id_os TEXT, descricao_servico TEXT, dados_os_completos TEXT, ocorrencias_texto TEXT,
        arquivado INTEGER DEFAULT 0,
        historico_tentativas TEXT,
        resolucao_admin TEXT,
        resolucao_admin_data TEXT,
        resultado_final_retirada TEXT
    )''')
    
    c.execute("PRAGMA table_info(contratos)")
    colunas = c.fetchall()
    colunas_existentes = [col[1] for col in colunas]
    
    # Verifica se a coluna 'arquivado' existe
    if 'arquivado' not in colunas_existentes:
        try:
            c.execute('ALTER TABLE contratos ADD COLUMN arquivado INTEGER DEFAULT 0')
            logger.info("✅ Coluna 'arquivado' adicionada à tabela contratos")
        except sqlite3.OperationalError as e:
            logger.warning(f"⚠️ Erro ao adicionar coluna arquivado: {e}")
    
    # Verifica se a coluna 'data_agendada' existe na tabela templates
    c.execute("PRAGMA table_info(templates)")
    colunas_templates = [col[1] for col in c.fetchall()]
    if 'data_agendada' not in colunas_templates:
        try:
            c.execute('ALTER TABLE templates ADD COLUMN data_agendada TEXT')
            logger.info("✅ Coluna 'data_agendada' adicionada à tabela templates")
        except sqlite3.OperationalError as e:
            logger.warning(f"⚠️ Erro ao adicionar coluna data_agendada: {e}")
    
    colunas_retirada_novas = {
        'motivo_retirada': 'TEXT',
        'numero_tentativa': 'INTEGER',
        'resultado_tentativa': 'TEXT',
        'boleto_aberto': 'TEXT',
        'relato_tentativa': 'TEXT',
        'qtd_onu': 'INTEGER DEFAULT 0',
        'qtd_cordao_monofibra': 'INTEGER DEFAULT 0',
        'qtd_pto': 'INTEGER DEFAULT 0',
        'qtd_roteador': 'INTEGER DEFAULT 0',
        'qtd_suporte_roteador': 'INTEGER DEFAULT 0',
    }
    for col, tipo_col in colunas_retirada_novas.items():
        if col not in colunas_existentes:
            try:
                c.execute(f'ALTER TABLE contratos ADD COLUMN {col} {tipo_col}')
                logger.info(f"✅ Coluna {col} adicionada à tabela contratos")
            except sqlite3.OperationalError:
                pass
    
    for col in ['tentativa_count', 'quant_onus', 'quant_cabos', 'quant_ptos', 
                'quant_roteadores', 'quant_suportes', 'retirado_sistema_ativado',
                'retirada_realizada', 'tentativas', 'correcao_roteadores']:
        if col not in colunas_existentes:
            try:
                if col in ['tentativa_count', 'quant_onus', 'quant_cabos', 'quant_ptos', 
                          'quant_roteadores', 'quant_suportes', 'retirado_sistema_ativado']:
                    c.execute(f'ALTER TABLE contratos ADD COLUMN {col} INTEGER DEFAULT 0')
                else:
                    c.execute(f'ALTER TABLE contratos ADD COLUMN {col} TEXT')
            except sqlite3.OperationalError:
                pass
    
    for col in ['radiusnet_finalizado', 'radiusnet_finalizado_data', 'radiusnet_os_status', 'radiusnet_ocorrencia_id']:
        if col not in colunas_existentes:
            try:
                c.execute(f'ALTER TABLE contratos ADD COLUMN {col} TEXT')
            except sqlite3.OperationalError:
                pass
    
    try:
        c.execute('ALTER TABLE templates ADD COLUMN radiusnet_id_os TEXT')
    except sqlite3.OperationalError:
        pass
    
    try:
        c.execute('ALTER TABLE templates ADD COLUMN cliente_nome TEXT')
    except sqlite3.OperationalError:
        pass
    
    c.execute('''CREATE TABLE IF NOT EXISTS tecnicos (
        id TEXT PRIMARY KEY, nome TEXT NOT NULL, matricula TEXT UNIQUE NOT NULL, 
        ativo INTEGER DEFAULT 1, senha_hash TEXT
    )''')
    
    c.execute("PRAGMA table_info(tecnicos)")
    colunas_tecnicos = [col[1] for col in c.fetchall()]
    if 'senha_hash' not in colunas_tecnicos:
        c.execute('ALTER TABLE tecnicos ADD COLUMN senha_hash TEXT')
        logger.info("✅ Coluna senha_hash adicionada à tabela tecnicos")

    tecnicos_sem_senha = c.execute(
        'SELECT id, matricula FROM tecnicos WHERE senha_hash IS NULL OR senha_hash = ""'
    ).fetchall()
    for tec in tecnicos_sem_senha:
        senha_hash_padrao = generate_password_hash(tec['matricula'])
        c.execute('UPDATE tecnicos SET senha_hash = ? WHERE id = ?', (senha_hash_padrao, tec['id']))
    if tecnicos_sem_senha:
        logger.info(f"✅ {len(tecnicos_sem_senha)} técnico(s) migrado(s) com senha = matrícula")
    
    c.execute('''CREATE TABLE IF NOT EXISTS emails_enviados (
        id TEXT PRIMARY KEY, contrato_id TEXT, email_destino TEXT, data_envio TEXT, status TEXT
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS assinaturas_empresa (
        id TEXT PRIMARY KEY, responsavel_nome TEXT NOT NULL, responsavel_cargo TEXT,
        assinatura_base64 TEXT NOT NULL, data_cadastro TEXT NOT NULL, ativo INTEGER DEFAULT 1
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS usuarios_admin (
        id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, senha_hash TEXT NOT NULL,
        nome_completo TEXT NOT NULL, email TEXT, ativo INTEGER DEFAULT 1,
        criado_em TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    
    if c.execute('SELECT COUNT(*) FROM tecnicos').fetchone()[0] == 0:
        tecnicos_padrao = [('tec001', 'tecnico1', 'MAT001'), ('tec002', 'tecnico2', 'MAT002'), ('tec003', 'tecnico3', 'MAT003')]
        for tid, nome, mat in tecnicos_padrao:
            senha_hash = generate_password_hash(mat)
            c.execute('INSERT INTO tecnicos (id, nome, matricula, ativo, senha_hash) VALUES (?,?,?,1,?)',
                      (tid, nome, mat, senha_hash))
        logger.info("✅ Técnicos padrão criados com senha = matrícula")
    
    if c.execute('SELECT COUNT(*) FROM usuarios_admin').fetchone()[0] == 0:
        admin_id = str(uuid.uuid4())
        admin_senha_padrao = os.getenv('ADMIN_SENHA_PADRAO')
        if not admin_senha_padrao:
            logger.warning("⚠️ ADMIN_SENHA_PADRAO não definida. Usando 'admin123' - Mude imediatamente!")
            admin_senha_padrao = 'admin123'
        senha_hash = generate_password_hash(admin_senha_padrao)
        c.execute('INSERT INTO usuarios_admin (id, username, senha_hash, nome_completo, email, ativo) VALUES (?,?,?,?,?,1)',
                  (admin_id, "admin", senha_hash, "Administrador", "admin@suas empresa.com.br"))
        logger.info("✅ Usuário admin criado.")
    
    conn.commit()
    conn.close()
    logger.info("✅ Banco inicializado com sucesso!")

def proximo_numero(db):
    """Gera o próximo número sequencial para contratos"""
    db.execute('UPDATE seq_counter SET valor = valor + 1 WHERE id = 1')
    return db.execute('SELECT valor FROM seq_counter WHERE id = 1').fetchone()['valor']

def parse_txt_coordinates(txt_path):
    """
    Lê arquivo TXT de configuração para posicionamento de assinaturas e campos.
    """
    if not txt_path or not os.path.exists(txt_path):
        return None, None, None, None
    try:
        with open(txt_path, 'r', encoding='utf-8') as f:
            content = f.read()
        sig_config = {}
        sig_config_resp = {}
        text_config = {'fields': {}}
        date_config = {'fields': {}}
        current_section = None
        for line in content.split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('[') and line.endswith(']'):
                current_section = line[1:-1].upper()
                continue
            if '=' in line:
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip()
                if current_section == 'SIGNATURE':
                    if key in ['page','x','y','width','height']:
                        sig_config[key] = int(value)
                    elif key in ['quality','dpi']:
                        sig_config[key] = int(value)
                elif current_section == 'RESPONSIBLE_SIGNATURE':
                    if key in ['page','x','y','width','height']:
                        sig_config_resp[key] = int(value)
                    elif key in ['quality','dpi']:
                        sig_config_resp[key] = int(value)
                elif current_section == 'TEXT_FIELDS':
                    if key == 'page':
                        text_config['page_index'] = int(value)
                    elif key == 'font':
                        text_config['font'] = value
                    elif key == 'font_size':
                        text_config['font_size'] = int(value)
                    elif ',' in value:
                        coords = value.split(',')
                        if len(coords) >= 2:
                            text_config['fields'][key] = {'x': int(coords[0].strip()), 'y': int(coords[1].strip())}
                elif current_section == 'DATE_FIELDS':
                    if key == 'page':
                        date_config['page_index'] = int(value)
                    elif key == 'font':
                        date_config['font'] = value
                    elif key == 'font_size':
                        date_config['font_size'] = int(value)
                    elif ',' in value:
                        coords = value.split(',')
                        if len(coords) >= 2:
                            date_config['fields'][key] = {'x': int(coords[0].strip()), 'y': int(coords[1].strip())}
        if not sig_config or 'x' not in sig_config:
            return None, None, None, None
        
        sig_config.setdefault('page', 0)
        sig_config.setdefault('width', 180)
        sig_config.setdefault('height', 100)
        sig_config.setdefault('quality', 100)
        sig_config.setdefault('dpi', 600)
        
        if sig_config_resp:
            sig_config_resp.setdefault('page', sig_config.get('page', 0))
            sig_config_resp.setdefault('width', 180)
            sig_config_resp.setdefault('height', 100)
            sig_config_resp.setdefault('quality', 100)
            sig_config_resp.setdefault('dpi', 600)
        
        text_config.setdefault('page_index', sig_config.get('page', 0))
        text_config.setdefault('font', 'Helvetica-Bold')
        text_config.setdefault('font_size', 13)
        
        date_config.setdefault('page_index', sig_config.get('page', 0))
        date_config.setdefault('font', text_config.get('font', 'Helvetica-Bold'))
        date_config.setdefault('font_size', text_config.get('font_size', 13))
        
        return sig_config, sig_config_resp, text_config, date_config
    except Exception as e:
        logger.error(f"Erro parse TXT: {e}")
        return None, None, None, None

def overlay_signature_on_pdf(template_path, sig_b64, txt_path=None, campo_cliente='', campo_ctop='', campo_db='', os_descricao='', tipo_documento='instalacao', assinatura_responsavel_b64=None, dados_retirada=None):
    """
    Aplica assinatura e campos de texto no PDF.
    Para RETIRADA: NÃO é aplicada assinatura digital do cliente.
    """
    reader = PdfReader(template_path)
    writer = PdfWriter()
    
    sig_config, sig_config_resp, text_config, date_config = parse_txt_coordinates(txt_path) if txt_path else (None, None, None, None)
    
    if not sig_config:
        if tipo_documento == 'mudanca_endereco':
            sig_config = SIG_CONFIG_MUDANCA.copy()
            sig_config_resp = SIG_CONFIG_RESPONSAVEL_MUDANCA.copy()
            text_config = TEXT_FIELDS_CONFIG_MUDANCA.copy()
            date_config = DATE_FIELDS_CONFIG_MUDANCA.copy()
        elif tipo_documento == 'mudanca_assinante':
            sig_config = SIG_CONFIG_MUDANCA_ASSINANTE.copy()
            sig_config_resp = SIG_CONFIG_RESPONSAVEL_MUDANCA_ASSINANTE.copy()
            text_config = TEXT_FIELDS_CONFIG_MUDANCA_ASSINANTE.copy()
            date_config = DATE_FIELDS_CONFIG_MUDANCA_ASSINANTE.copy()
        elif tipo_documento == 'ordem_servico':
            sig_config = SIG_CONFIG_OS.copy()
            sig_config_resp = None
            text_config = TEXT_FIELDS_CONFIG_OS.copy()
        elif tipo_documento == 'retirada_sistema':
            sig_config = None
            sig_config_resp = None
            text_config = TEXT_FIELDS_CONFIG_RETIRADA.copy()
            date_config = DATE_FIELDS_CONFIG_RETIRADA.copy()
            logger.info(f"📌 Usando configuração de RETIRADA sem assinatura digital")
        else:
            sig_config = SIG_CONFIG_INSTALACAO.copy()
            sig_config_resp = SIG_CONFIG_RESPONSAVEL_INSTALACAO.copy()
            text_config = TEXT_FIELDS_CONFIG_INSTALACAO.copy()
            date_config = DATE_FIELDS_CONFIG_INSTALACAO.copy()
    else:
        logger.info(f"📌 Usando configurações do TXT: x={sig_config.get('x')}, y={sig_config.get('y')}")
        if not sig_config_resp:
            sig_config_resp = sig_config.copy()
            sig_config_resp['x'] = sig_config_resp.get('x', 110) + 200
    
    for page_num in range(len(reader.pages)):
        original_page = reader.pages[page_num]
        need_overlay = False
        overlay_buffer = BytesIO()
        c = None
        
        if sig_config and page_num == sig_config.get('page_index', 0) and sig_b64 and len(sig_b64) > 100 and tipo_documento != 'retirada_sistema':
            if not need_overlay:
                width = float(original_page.mediabox.width)
                height = float(original_page.mediabox.height)
                c = rl_canvas.Canvas(overlay_buffer, pagesize=(width, height))
                c.setPageCompression(0)
                need_overlay = True
            
            pil_img = process_signature_image(sig_b64)
            target_w = sig_config.get('width', 180)
            target_h = sig_config.get('height', 100)
            sig_buf = BytesIO()
            pil_img.save(sig_buf, format='PNG', optimize=False, compress_level=0)
            sig_buf.seek(0)
            
            c.drawImage(
                ImageReader(sig_buf),
                sig_config.get('x', 110),
                sig_config.get('y', 150),
                width=target_w,
                height=target_h,
                preserveAspectRatio=True,
                mask='auto'
            )
            logger.info(f"✍️ Assinatura aplicada na página {page_num} na posição x={sig_config.get('x')}, y={sig_config.get('y')}")
        
        if sig_config_resp and page_num == sig_config_resp.get('page_index', 0) and assinatura_responsavel_b64 and len(assinatura_responsavel_b64) > 50 and tipo_documento != 'retirada_sistema':
            if not need_overlay:
                width = float(original_page.mediabox.width)
                height = float(original_page.mediabox.height)
                c = rl_canvas.Canvas(overlay_buffer, pagesize=(width, height))
                c.setPageCompression(0)
                need_overlay = True
            
            pil_resp = process_signature_image(assinatura_responsavel_b64)
            target_w_resp = sig_config_resp.get('width', 180)
            target_h_resp = sig_config_resp.get('height', 100)
            sig_buf_resp = BytesIO()
            pil_resp.save(sig_buf_resp, format='PNG', optimize=False, compress_level=0)
            sig_buf_resp.seek(0)
            c.drawImage(
                ImageReader(sig_buf_resp),
                sig_config_resp.get('x', 350),
                sig_config_resp.get('y', 150),
                width=target_w_resp,
                height=target_h_resp,
                preserveAspectRatio=True,
                mask='auto'
            )
            logger.info(f"✍️ Assinatura do responsável aplicada na página {page_num} na posição x={sig_config_resp.get('x')}, y={sig_config_resp.get('y')}")
        
        if text_config and page_num == text_config.get('page_index', 0):
            if not need_overlay:
                width = float(original_page.mediabox.width)
                height = float(original_page.mediabox.height)
                c = rl_canvas.Canvas(overlay_buffer, pagesize=(width, height))
                c.setPageCompression(0)
                need_overlay = True
            
            font_name = text_config.get('font', 'Helvetica-Bold')
            font_size = text_config.get('font_size', 13)
            valores = {
                'campo_cliente': campo_cliente,
                'campo_ctop': campo_ctop,
                'campo_db': campo_db,
                'os_descricao': os_descricao
            }
            
            if dados_retirada:
                valores.update(dados_retirada)
            
            for field, coords in text_config.get('fields', {}).items():
                valor = valores.get(field, '')
                if valor is not None:
                    valor_str = str(valor).strip()
                    if field.startswith('qtd_') and valor_str == '0':
                        continue
                    if valor_str:
                        if field == 'os_descricao':
                            c.setFont(font_name, font_size)
                            draw_multiline_text(c, valor_str, coords['x'], coords['y'], font_name, font_size, max_width=450)
                        else:
                            c.setFont(font_name, font_size)
                            c.drawString(coords['x'], coords['y'], valor_str[:50])
        
        if date_config and page_num == date_config.get('page_index', 0):
            if not need_overlay:
                width = float(original_page.mediabox.width)
                height = float(original_page.mediabox.height)
                c = rl_canvas.Canvas(overlay_buffer, pagesize=(width, height))
                c.setPageCompression(0)
                need_overlay = True
            
            date_font_name = date_config.get('font', 'Helvetica-Bold')
            date_font_size = date_config.get('font_size', 13)
            c.setFont(date_font_name, date_font_size)
            c.setFillColorRGB(0, 0, 0)
            date_parts = get_current_date_parts()
            for field_name, coords in date_config.get('fields', {}).items():
                if field_name in date_parts:
                    c.drawString(coords['x'], coords['y'], date_parts[field_name])
        
        if need_overlay:
            c.save()
            overlay_buffer.seek(0)
            overlay_pdf = PdfReader(overlay_buffer)
            if overlay_pdf.pages:
                original_page.merge_page(overlay_pdf.pages[0])
        writer.add_page(original_page)
    
    output = BytesIO()
    writer.write(output)
    output.seek(0)
    return output.read()

def gerar_pdf_fallback(rid, dados, tec_nome, data_hora, ip, contrato_id, tipo_documento='instalacao', assinatura_responsavel_b64=None):
    """Gera um PDF de fallback quando o template não está disponível"""
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image as RLImage
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20*mm, rightMargin=20*mm, topMargin=20*mm, bottomMargin=20*mm)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('CustomTitle', parent=styles['Title'], fontSize=18, textColor='#CC0000', alignment=1, spaceAfter=20)
    titulo = 'SUA-EMPRESA INTERNET FIBRA OPTICA LTDA'
    if tipo_documento == 'mudanca_endereco':
        subtitulo = 'TERMO DE MUDANCA DE ENDERECO/ASSINANTE'
    elif tipo_documento == 'mudanca_assinante':
        subtitulo = 'TERMO DE MUDANCA DE ASSINANTE'
    elif tipo_documento == 'ordem_servico':
        subtitulo = 'ORDEM DE SERVICO - Registro de Atendimento Tecnico'
    elif tipo_documento == 'retirada_sistema':
        subtitulo = 'TERMO DE RETIRADA DO SISTEMA'
    else:
        subtitulo = 'CONTRATO DE PRESTACAO DE SERVICOS'
    date_parts = get_current_date_parts()
    data_atual = f"{date_parts['data_dia']} de {date_parts['data_mes']} de {date_parts['data_ano']}"
    cliente_nome = dados.get('cliente_nome') or 'Cliente nao informado'
    story = [Paragraph(titulo, title_style), Paragraph(subtitulo, styles['Heading2']), Spacer(1,20),
             Paragraph(f'<b>Documento Nº:</b> {contrato_id}', styles['Normal']),
             Paragraph(f'<b>Data:</b> {data_atual}', styles['Normal']),
             Paragraph(f'<b>Cliente:</b> {cliente_nome}', styles['Normal']),
             Paragraph(f'<b>CPF:</b> {dados.get("cliente_cpf", "—")}', styles['Normal']),
             Paragraph(f'<b>Tecnico:</b> {tec_nome}', styles['Normal']),
             Paragraph(f'<b>Data/Hora:</b> {data_hora}', styles['Normal']), Spacer(1,12)]
    
    if tipo_documento == 'ordem_servico':
        if dados.get('os_descricao'):
            story.append(Paragraph('<b>Descricao do Servico:</b>', styles['Normal']))
            story.append(Paragraph(dados['os_descricao'].replace('\n','<br/>'), styles['Normal']))
            story.append(Spacer(1,12))
    elif tipo_documento == 'retirada_sistema':
        if dados.get('motivo_retirada'):
            story.append(Paragraph(f'<b>Motivo da Retirada:</b> {dados.get("motivo_retirada")}', styles['Normal']))
        if dados.get('boleto_aberto'):
            story.append(Paragraph(f'<b>Boleto em Aberto:</b> {dados.get("boleto_aberto")}', styles['Normal']))
        
        if dados.get('relato_tentativa'):
            story.append(Paragraph(f'<b>Relato da Tentativa:</b> {dados.get("relato_tentativa")}', styles['Normal']))
        
        for i in range(1, 4):
            tentativa_key = f'tentativa_{i}'
            if dados.get(tentativa_key):
                story.append(Paragraph(f'<b>Tentativa {i}:</b> {dados.get(tentativa_key)}', styles['Normal']))
        
        if dados.get('equipamentos_retirados'):
            eq = dados.get('equipamentos_retirados', {})
            story.append(Spacer(1,6))
            story.append(Paragraph('<b>Equipamentos Retirados:</b>', styles['Normal']))
            story.append(Paragraph(f'ONU: {eq.get("onu",0)}', styles['Normal']))
            story.append(Paragraph(f'Cordão Monofibra: {eq.get("cordao_monofibra",0)}', styles['Normal']))
            story.append(Paragraph(f'PTO: {eq.get("pto",0)}', styles['Normal']))
            story.append(Paragraph(f'Roteador: {eq.get("roteador",0)}', styles['Normal']))
            story.append(Paragraph(f'Suporte de Roteador: {eq.get("suporte_roteador",0)}', styles['Normal']))
        
        story.append(Spacer(1,10))
        story.append(Paragraph(f'<b>Técnico Responsável:</b> {tec_nome}', styles['Normal']))
        
    else:
        for label, key in [('Nº Cliente','campo_cliente'), ('Caixa CTOP','campo_ctop'), ('DB','campo_db')]:
            val = dados.get(key,'').strip()
            if val:
                story.append(Paragraph(f'<b>{label}:</b> {val}', styles['Normal']))
                story.append(Spacer(1,6))
    
    if tipo_documento != 'retirada_sistema':
        if dados.get('assinatura_base64') and len(dados['assinatura_base64'])>100:
            pil = process_signature_image(dados['assinatura_base64'])
            pil = pil.resize((180,100), PILImage.Resampling.LANCZOS)
            enhancer = ImageEnhance.Sharpness(pil)
            pil = enhancer.enhance(1.6)
            enhancer = ImageEnhance.Contrast(pil)
            pil = enhancer.enhance(1.4)
            sig_buf = BytesIO()
            pil.save(sig_buf,'PNG', optimize=False, compress_level=0)
            sig_buf.seek(0)
            story.extend([Spacer(1,20), Paragraph('<b>Assinatura do Cliente:</b>', styles['Normal']), Spacer(1,10), RLImage(sig_buf, width=180, height=100)])
        
        if assinatura_responsavel_b64 and len(assinatura_responsavel_b64)>50:
            pil_resp = process_signature_image(assinatura_responsavel_b64)
            pil_resp = pil_resp.resize((180,100), PILImage.Resampling.LANCZOS)
            enhancer = ImageEnhance.Sharpness(pil_resp)
            pil_resp = enhancer.enhance(1.6)
            enhancer = ImageEnhance.Contrast(pil_resp)
            pil_resp = enhancer.enhance(1.4)
            sig_buf_resp = BytesIO()
            pil_resp.save(sig_buf_resp,'PNG', optimize=False, compress_level=0)
            sig_buf_resp.seek(0)
            story.extend([Spacer(1,30), Paragraph('<b>Assinatura do Responsavel:</b>', styles['Normal']), Spacer(1,10), RLImage(sig_buf_resp, width=180, height=100)])
    
    story.append(Spacer(1,20))
    story.append(Paragraph('<i>Documento assinado digitalmente conforme Lei 14.063/2020</i>', styles['Italic']))
    doc.build(story)
    buf.seek(0)
    return buf.read()

def overlay_text_on_pdf(template_path, text_values, txt_path=None):
    """Sobrescreve texto em um PDF (usado para retirada multi-etapas)"""
    reader = PdfReader(template_path)
    writer = PdfWriter()
    _, _, text_config, _ = parse_txt_coordinates(txt_path) if txt_path else (None, None, None, None)
    if not text_config:
        text_config = {'page_index': 0, 'font': 'Helvetica', 'font_size': 12, 'fields': {}}
    for page_num in range(len(reader.pages)):
        original_page = reader.pages[page_num]
        if page_num == text_config.get('page_index', 0):
            width = float(original_page.mediabox.width)
            height = float(original_page.mediabox.height)
            packet = BytesIO()
            c = rl_canvas.Canvas(packet, pagesize=(width, height))
            font_name = text_config.get('font', 'Helvetica')
            font_size = text_config.get('font_size', 12)
            c.setFont(font_name, font_size)
            c.setFillColorRGB(0, 0, 0)
            for field_name, coords in text_config.get('fields', {}).items():
                valor = text_values.get(field_name, '')
                if valor:
                    c.drawString(coords['x'], coords['y'], str(valor))
            c.save()
            packet.seek(0)
            overlay_pdf = PdfReader(packet)
            if overlay_pdf.pages:
                original_page.merge_page(overlay_pdf.pages[0])
        writer.add_page(original_page)
    output = BytesIO()
    writer.write(output)
    output.seek(0)
    return output.read()

def gerar_pdf_retirada_fallback(contrato, text_values):
    """Gera PDF de retirada sem template - NÃO TEM ASSINATURA DIGITAL"""
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20*mm, rightMargin=20*mm, topMargin=20*mm, bottomMargin=20*mm)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('CustomTitle', parent=styles['Title'], fontSize=16, alignment=1, spaceAfter=20)
    story = [Paragraph("TERMO DE RETIRADA DO SISTEMA", title_style), Spacer(1,12),
             Paragraph(f"Protocolo: {contrato['contrato_id']}", styles['Normal']),
             Paragraph(f"Data da retirada: {text_values.get('data_retirada') or '—'}", styles['Normal']),
             Paragraph(f"Cliente: {text_values.get('cliente_nome') or '—'}", styles['Normal']),
             Paragraph(f"CPF: {text_values.get('cliente_cpf') or '—'}", styles['Normal'])]
    
    if text_values.get('descricao_servico'):
        story.append(Paragraph(f"Descricao do servico: {text_values['descricao_servico']}", styles['Normal']))
    
    story.append(Spacer(1,20))
    story.append(Paragraph("<b>Quantidades retiradas:</b>", styles['Normal']))
    story.append(Paragraph(f"ONUs: {text_values.get('quant_onus', 0)}", styles['Normal']))
    story.append(Paragraph(f"Cabos monofibra: {text_values.get('quant_cabos', 0)}", styles['Normal']))
    story.append(Paragraph(f"PTOs: {text_values.get('quant_ptos', 0)}", styles['Normal']))
    story.append(Paragraph(f"Roteadores: {text_values.get('quant_roteadores', 0)}", styles['Normal']))
    story.append(Paragraph(f"Suportes de roteador: {text_values.get('quant_suportes', 0)}", styles['Normal']))
    if text_values.get('correcao_roteadores'):
        story.append(Spacer(1,5))
        story.append(Paragraph(f"<b>Correcao/adicao (roteadores):</b>", styles['Normal']))
        story.append(Paragraph(text_values['correcao_roteadores'], styles['Normal']))
    
    story.append(Spacer(1,10))
    story.append(Paragraph("<b>Tentativas do tecnico:</b>", styles['Normal']))
    for i, txt in enumerate([text_values.get('tentativa1', ''), text_values.get('tentativa2', ''), text_values.get('tentativa3', '')], 1):
        story.append(Paragraph(f"<b>Tentativa {i}:</b> {txt if txt else '—'}", styles['Normal']))
    
    story.append(Spacer(1,10))
    story.append(Paragraph(f"<b>Técnico Responsável:</b> {text_values.get('tecnico_nome', '')}", styles['Normal']))
    
    story.append(Spacer(1,20))
    story.append(Paragraph("Documento gerado eletronicamente.", styles['Italic']))
    doc.build(story)
    buf.seek(0)
    return buf.read()

def send_remote_signature_email(to_email, client_name, sign_url, documento_tipo, documento_id):
    """
    Envia e-mail com link de assinatura remota.
    Retorna True se enviado com sucesso, False caso contrário.
    """
    try:
        if not EMAIL_CONFIG.get('email_senha'):
            logger.warning("⚠️ Email não configurado - senha vazia")
            return False

        tipo_texto = {
            'instalacao': 'Contrato de Instalação',
            'mudanca_endereco': 'Termo de Mudança de Endereço',
            'mudanca_assinante': 'Termo de Mudança de Assinante',
            'ordem_servico': 'Ordem de Serviço'
        }.get(documento_tipo, 'Documento')

        msg = MIMEMultipart("alternative")
        msg["From"] = f"{EMAIL_CONFIG['email_nome']} <{EMAIL_CONFIG['email_remetente']}>"
        msg["To"] = to_email
        msg["Subject"] = f"Ação necessária: assine seu {tipo_texto} - Sua-empresa Internet"

        html_body = f"""
<!DOCTYPE html>
<html lang="pt-BR">

<head>
<meta charset="UTF-8">
<title>Assinatura Digital</title>
</head>

<body style="margin:0;padding:0;background:#f3f4f6;font-family:Arial,Helvetica,sans-serif;">

<table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:30px 0;">
<tr>
<td align="center">

<table width="600" cellpadding="0" cellspacing="0"
style="background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e5e7eb;">

<tr>
<td align="center"
style="background:linear-gradient(135deg,#dc2626,#991b1b);padding:30px;color:#ffffff;">

<h1 style="margin:0;font-size:28px;">Sua-empresa Internet</h1>

<p style="margin-top:8px;font-size:15px;">
Documento para Assinatura Digital
</p>

</td>
</tr>

<tr>
<td style="padding:35px;">

<p style="font-size:16px;color:#111827;">
Olá, <strong>{client_name}</strong>,
</p>

<p style="font-size:15px;line-height:1.7;color:#4b5563;">
Recebemos uma solicitação para assinatura do documento abaixo.
Para concluir o processo, basta acessar o link e realizar sua assinatura digital.
O procedimento é simples, rápido e seguro.
</p>

<table width="100%" cellpadding="12"
style="background:#f9fafb;border:1px solid #e5e7eb;border-left:5px solid #dc2626;border-radius:6px;margin:30px 0;">

<tr>
<td>

<p style="margin:6px 0;">
<strong>Documento:</strong> {tipo_texto}
</p>

<p style="margin:6px 0;">
<strong>Data de emissão:</strong> {datetime.now().strftime('%d/%m/%Y às %H:%M')}
</p>

</td>
</tr>

</table>

<div style="text-align:center;margin:40px 0;">

<a href="{sign_url}"
style="
background:#dc2626;
color:#ffffff;
padding:16px 40px;
font-size:17px;
font-weight:bold;
text-decoration:none;
border-radius:8px;
display:inline-block;
box-shadow:0 4px 10px rgba(0,0,0,.15);
">

ASSINAR DOCUMENTO

</a>

</div>

<p style="font-size:14px;color:#374151;line-height:1.8;">

✅ O link é exclusivo para você.<br>
✅ A assinatura possui validade jurídica.<br>
✅ Todo o processo é realizado de forma eletrônica e segura.<br>
✅ O link permanece válido por <strong>24 horas</strong>.

</p>

<hr style="margin:35px 0;border:none;border-top:1px solid #e5e7eb;">

<p style="font-size:14px;color:#4b5563;">
Caso o botão acima não funcione, copie e cole o endereço abaixo no navegador:
</p>

<p style="word-break:break-all;font-size:13px;">
<a href="{sign_url}">
{sign_url}
</a>
</p>

<div style="
margin-top:35px;
padding:16px;
background:#fff7ed;
border-left:5px solid #f59e0b;
">

<p style="margin:0;font-size:13px;color:#92400e;line-height:1.6;">

<strong>Importante</strong><br><br>

Se você não reconhece esta solicitação ou acredita que recebeu este e-mail por engano,
desconsidere esta mensagem ou entre em contato com a equipe da Sua-empresa Internet.

</p>

</div>

</td>
</tr>

<tr>

<td style="
background:#f9fafb;
padding:25px;
text-align:center;
font-size:12px;
color:#6b7280;
">

<strong>Sua-empresa Internet</strong><br>
Este é um e-mail automático. Por favor, não responda esta mensagem.

</td>

</tr>

</table>

</td>
</tr>
</table>

</body>
</html>
"""

        msg.attach(MIMEText(html_body, "html", "utf-8"))

        server = smtplib.SMTP(
            EMAIL_CONFIG["smtp_server"],
            EMAIL_CONFIG["smtp_port"]
        )

        server.starttls()

        server.login(
            EMAIL_CONFIG["email_remetente"],
            EMAIL_CONFIG["email_senha"]
        )

        server.send_message(msg)
        server.quit()

        logger.info(f"✅ E-mail enviado para {to_email}")
        return True

    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"❌ Erro de autenticação SMTP: {e}")
        return False

    except smtplib.SMTPException as e:
        logger.error(f"❌ Erro SMTP: {e}")
        return False

    except Exception as e:
        logger.error(f"❌ Erro ao enviar e-mail: {e}")
        return False
    # ================================================================
# DECORATORS DE AUTENTICAÇÃO
# ================================================================
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' in session:
            return f(*args, **kwargs)
        return jsonify({'erro': 'Autenticacao necessaria'}), 401
    return decorated

def tecnico_auth_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' in session:
            return f(*args, **kwargs)
        token = request.headers.get('X-Tecnico-Token')
        if not token:
            return jsonify({'erro': 'Autenticacao necessaria'}), 401
        db = get_db()
        tecnico = db.execute('SELECT id, nome FROM tecnicos WHERE id = ? AND ativo = 1', (token,)).fetchone()
        db.close()
        if not tecnico:
            return jsonify({'erro': 'Token invalido'}), 401
        request.tecnico = {'id': tecnico['id'], 'nome': tecnico['nome']}
        return f(*args, **kwargs)
    return decorated

# ================================================================
# ROTAS PÚBLICAS
# ================================================================
@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/admin')
def admin():
    return send_from_directory('.', 'admin.html')

@app.route('/admin-signature')
def admin_signature():
    return send_from_directory('.', 'admin_signature.html')

@app.route('/sign.html')
def sign_page():
    return send_from_directory('.', 'sign.html')

# ================================================================
# ROTAS DE AUTENTICAÇÃO
# ================================================================
@app.route('/api/login', methods=['POST'])
def login_tecnico():
    try:
        data = request.get_json()
        usuario = data.get('usuario', '').strip().lower()
        senha = data.get('senha', '').strip()
        if not usuario or not senha:
            return jsonify({'ok': False, 'erro': 'Usuário e senha obrigatórios'}), 400
        db = get_db()
        tecnico = db.execute(
            'SELECT id, nome, matricula, ativo, senha_hash FROM tecnicos WHERE LOWER(nome) = ? AND ativo = 1',
            (usuario,)
        ).fetchone()
        db.close()
        if not tecnico:
            return jsonify({'ok': False, 'erro': 'Técnico não encontrado'}), 401
        if not check_password_hash(tecnico['senha_hash'], senha):
            return jsonify({'ok': False, 'erro': 'Senha incorreta'}), 401
        token = tecnico['id']
        return jsonify({
            'ok': True, 
            'tecnico': {
                'id': tecnico['id'], 
                'nome': tecnico['nome'], 
                'matricula': tecnico['matricula']
            }, 
            'token': token
        })
    except Exception as e:
        logger.error(f"Erro no login do técnico: {e}", exc_info=True)
        return jsonify({'ok': False, 'erro': 'Erro interno'}), 500

@app.route('/api/logout', methods=['POST'])
def logout_tecnico():
    return jsonify({'ok': True})

@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    try:
        data = request.get_json()
        username = data.get('username') or data.get('usuario')
        senha = data.get('senha') or data.get('password')
        if not username or not senha:
            return jsonify({'sucesso': False, 'erro': 'Credenciais obrigatorias'}), 400
        db = get_db()
        user = db.execute('SELECT id, username, senha_hash, nome_completo FROM usuarios_admin WHERE username = ? AND ativo = 1', (username,)).fetchone()
        db.close()
        if not user:
            return jsonify({'sucesso': False, 'erro': 'Usuario nao encontrado'}), 401
        if not check_password_hash(user['senha_hash'], senha):
            return jsonify({'sucesso': False, 'erro': 'Senha incorreta'}), 401
        session.clear()
        session.permanent = True
        session['user_id'] = user['id']
        session['username'] = user['username']
        session['nome_completo'] = user['nome_completo']
        return jsonify({'sucesso': True, 'usuario': user['username'], 'nome': user['nome_completo']})
    except Exception as e:
        logger.error(f"Erro no login: {e}", exc_info=True)
        return jsonify({'sucesso': False, 'erro': 'Erro interno'}), 500

@app.route('/api/admin/logout', methods=['POST'])
def admin_logout():
    session.clear()
    return jsonify({'sucesso': True})

@app.route('/api/admin/check-auth', methods=['GET'])
def check_auth():
    if 'user_id' in session:
        return jsonify({'autenticado': True, 'usuario': session.get('username'), 'nome': session.get('nome_completo')})
    return jsonify({'autenticado': False}), 401

# ================================================================
# ROTAS DE TÉCNICOS (CRUD)
# ================================================================
@app.route('/api/tecnicos/ativos', methods=['GET'])
def get_tecnicos_ativos():
    db = get_db()
    rows = db.execute('SELECT id, nome, matricula FROM tecnicos WHERE ativo = 1 ORDER BY nome').fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/tecnicos', methods=['GET'])
@login_required
def get_tecnicos():
    db = get_db()
    rows = db.execute('SELECT id, nome, matricula, ativo FROM tecnicos ORDER BY nome').fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/tecnicos', methods=['POST'])
@login_required
def criar_tecnico():
    data = request.get_json()
    nome = data.get('nome', '').strip()
    matricula = data.get('matricula', '').strip()
    senha = data.get('senha', matricula)
    if not nome or not matricula:
        return jsonify({'sucesso': False, 'erro': 'Nome e matricula obrigatorios'}), 400
    tecnico_id = str(uuid.uuid4())
    senha_hash = generate_password_hash(senha)
    db = get_db()
    db.execute('INSERT INTO tecnicos (id, nome, matricula, ativo, senha_hash) VALUES (?, ?, ?, 1, ?)',
               (tecnico_id, nome, matricula, senha_hash))
    db.commit()
    db.close()
    return jsonify({'sucesso': True, 'id': tecnico_id, 'nome': nome, 'matricula': matricula})

@app.route('/api/tecnicos/<tecnico_id>', methods=['DELETE'])
@login_required
def desativar_tecnico(tecnico_id):
    db = get_db()
    db.execute('UPDATE tecnicos SET ativo = 0 WHERE id = ?', (tecnico_id,))
    db.commit()
    db.close()
    return jsonify({'sucesso': True, 'mensagem': 'Tecnico desativado'})

@app.route('/api/tecnicos/<tecnico_id>/reativar', methods=['PUT'])
@login_required
def reativar_tecnico(tecnico_id):
    db = get_db()
    db.execute('UPDATE tecnicos SET ativo = 1 WHERE id = ?', (tecnico_id,))
    db.commit()
    db.close()
    return jsonify({'sucesso': True, 'mensagem': 'Tecnico reativado'})

@app.route('/api/tecnicos/<tecnico_id>/senha', methods=['PUT'])
@login_required
def alterar_senha_tecnico(tecnico_id):
    try:
        data = request.get_json()
        nova_senha = data.get('senha', '').strip()
        if not nova_senha or len(nova_senha) < 4:
            return jsonify({'sucesso': False, 'erro': 'Senha deve ter pelo menos 4 caracteres'}), 400
        db = get_db()
        tecnico = db.execute('SELECT id, nome FROM tecnicos WHERE id = ?', (tecnico_id,)).fetchone()
        if not tecnico:
            db.close()
            return jsonify({'sucesso': False, 'erro': 'Técnico não encontrado'}), 404
        senha_hash = generate_password_hash(nova_senha)
        db.execute('UPDATE tecnicos SET senha_hash = ? WHERE id = ?', (senha_hash, tecnico_id))
        db.commit()
        db.close()
        logger.info(f"✅ Senha do técnico {tecnico['nome']} alterada com sucesso!")
        return jsonify({'sucesso': True, 'mensagem': f'Senha do técnico {tecnico["nome"]} alterada com sucesso!', 'nova_senha': nova_senha})
    except Exception as e:
        logger.error(f"Erro ao alterar senha: {e}", exc_info=True)
        return jsonify({'sucesso': False, 'erro': 'Erro interno'}), 500

# ================================================================
# ROTA CORRIGIDA: LISTAR TEMPLATES
# ================================================================
@app.route('/api/templates', methods=['GET'])
def list_templates():
    """
    Lista templates disponíveis para assinatura.
    Filtra templates que já foram usados em contratos finalizados.
    """
    tipo = request.args.get('tipo')
    mostrar_todos = request.args.get('todos', '0') == '1'
    db = get_db()
    
    try:
        if mostrar_todos:
            # Admin: mostra todos os templates ativos
            if tipo:
                rows = db.execute('''SELECT id, nome, descricao, filename, criado_em, txt_path, txt_original_name, tipo, 
                                           os_pdf_path, os_pdf_original_name, cliente_nome, mensagem_extra, 
                                           radiusnet_id_cliente_plano, has_retirada, radiusnet_id_os, data_agendada
                                    FROM templates WHERE ativo = 1 AND tipo = ? 
                                    ORDER BY criado_em DESC''', (tipo,)).fetchall()
            else:
                rows = db.execute('''SELECT id, nome, descricao, filename, criado_em, txt_path, txt_original_name, tipo, 
                                           os_pdf_path, os_pdf_original_name, cliente_nome, mensagem_extra,
                                           radiusnet_id_cliente_plano, has_retirada, radiusnet_id_os, data_agendada
                                    FROM templates WHERE ativo = 1 
                                    ORDER BY criado_em DESC''').fetchall()
        else:
            # CORREÇÃO: Busca templates que NÃO foram usados em contratos finalizados
            # Status que indicam que o template já foi usado e não deve ser reutilizado
            status_finalizados = (
                'complete', 'complete_remote', 'os_finalizada', 
                'completed_retirada', 'admin_finalized', 'removed_by_admin'
            )
            
            # Busca todos os template_ids que já foram usados em contratos finalizados
            templates_usados = db.execute('''
                SELECT DISTINCT template_id 
                FROM contratos 
                WHERE template_id IS NOT NULL
                AND (
                    signature_status IN (?, ?, ?, ?, ?, ?)
                    OR arquivado = 1
                    OR (tipo_instalacao = 'retirada_sistema' AND resultado_tentativa = 'sucesso')
                )
            ''', status_finalizados).fetchall()
            
            template_ids_bloqueados = [str(row['template_id']) for row in templates_usados]
            
            # Query base
            if tipo:
                query = '''SELECT id, nome, descricao, filename, criado_em, txt_path, txt_original_name, tipo, 
                               os_pdf_path, os_pdf_original_name, cliente_nome, mensagem_extra, 
                               radiusnet_id_cliente_plano, has_retirada, radiusnet_id_os, data_agendada
                        FROM templates 
                        WHERE ativo = 1 AND tipo = ?'''
                params = [tipo]
            else:
                query = '''SELECT id, nome, descricao, filename, criado_em, txt_path, txt_original_name, tipo, 
                               os_pdf_path, os_pdf_original_name, cliente_nome, mensagem_extra,
                               radiusnet_id_cliente_plano, has_retirada, radiusnet_id_os, data_agendada
                        FROM templates 
                        WHERE ativo = 1'''
                params = []
            
            # Adiciona filtro de exclusão se houver templates bloqueados
            if template_ids_bloqueados:
                placeholders = ','.join(['?'] * len(template_ids_bloqueados))
                query += f' AND id NOT IN ({placeholders})'
                params.extend(template_ids_bloqueados)
            
            query += ' ORDER BY criado_em DESC'
            
            rows = db.execute(query, params).fetchall()
        
        templates = []
        for r in rows:
            t = dict(r)
            t['has_txt'] = bool(r['txt_path'])
            t['has_os'] = bool(r['os_pdf_path'])
            t['has_retirada'] = bool(r['has_retirada'])
            t['radiusnet_id_os'] = r['radiusnet_id_os'] or ''
            t['data_agendada'] = r['data_agendada'] or ''
            templates.append(t)
        
        db.close()
        return jsonify(templates)
        
    except Exception as e:
        db.close()
        logger.error(f"❌ Erro em list_templates: {e}", exc_info=True)
        return jsonify({'erro': 'Erro ao carregar templates'}), 500

@app.route('/api/templates/<tid>/txt', methods=['GET'])
def get_template_txt(tid):
    db = get_db()
    row = db.execute('SELECT txt_path, txt_original_name FROM templates WHERE id = ? AND ativo = 1', (tid,)).fetchone()
    db.close()
    if not row or not row['txt_path'] or not os.path.exists(row['txt_path']):
        return jsonify({'erro': 'TXT nao encontrado'}), 404
    with open(row['txt_path'], 'r', encoding='utf-8') as f:
        content = f.read()
    response = app.make_response(content)
    response.headers['Content-Type'] = 'text/plain; charset=utf-8'
    return response

@app.route('/api/templates/<tid>/pdf-view', methods=['GET'])
def view_template_pdf(tid):
    db = get_db()
    row = db.execute('SELECT filename FROM templates WHERE id = ? AND ativo = 1', (tid,)).fetchone()
    db.close()
    if not row:
        return jsonify({'erro': 'Template nao encontrado'}), 404
    pdf_path = os.path.join(UPLOAD_DIR, row['filename'])
    if not os.path.exists(pdf_path):
        return jsonify({'erro': 'PDF nao encontrado'}), 404
    return send_file(pdf_path, mimetype='application/pdf')

@app.route('/api/templates/<tid>/os-pdf', methods=['GET'])
def get_linked_os_pdf(tid):
    db = get_db()
    row = db.execute('SELECT os_pdf_path FROM templates WHERE id = ? AND ativo = 1', (tid,)).fetchone()
    db.close()
    if not row or not row['os_pdf_path'] or not os.path.exists(row['os_pdf_path']):
        return jsonify({'erro': 'OS nao encontrada'}), 404
    return send_file(row['os_pdf_path'], mimetype='application/pdf')

@app.route('/api/templates/<tid>/retirada-pdf', methods=['GET'])
def get_linked_retirada_pdf(tid):
    db = get_db()
    row = db.execute('SELECT os_pdf_path FROM templates WHERE id = ? AND ativo = 1 AND has_retirada = 1', (tid,)).fetchone()
    db.close()
    if not row or not row['os_pdf_path'] or not os.path.exists(row['os_pdf_path']):
        return jsonify({'erro': 'Retirada nao encontrada'}), 404
    return send_file(row['os_pdf_path'], mimetype='application/pdf')



@app.route('/api/templates', methods=['POST'])
@login_required
def upload_template():
    try:
        tipo = request.form.get('tipo', 'instalacao')
        tid = str(uuid.uuid4())
        pdf_filename = None
        radiusnet_id_os = request.form.get('radiusnet_id_os', '').strip()
        radiusnet_id_cliente_plano = request.form.get('radiusnet_id_cliente_plano', '').strip()
        cliente_nome_extraido = request.form.get('cliente_nome', '').strip()
        data_agendada = request.form.get('data_agendada', '').strip()
        
        if not cliente_nome_extraido and radiusnet_id_os and radiusnet_client:
            try:
                tipo_os = request.form.get('tipo_os')
                if tipo_os:
                    tipo_os = int(tipo_os)
                os_data = radiusnet_client.get_os_completa_por_id(radiusnet_id_os, tipo_os)
                if os_data:
                    cliente_nome_extraido = os_data.get('cliente_nome', '') or os_data.get('cliente', '')
                    logger.info(f"✅ Nome do cliente obtido da OS: {cliente_nome_extraido}")
            except Exception as e:
                logger.warning(f"⚠️ Não foi possível obter nome do cliente da OS: {e}")
        
        if tipo == 'instalacao' and radiusnet_id_cliente_plano and radiusnet_client:
            try:
                logger.info(f"📥 Baixando termo para cliente plano: {radiusnet_id_cliente_plano}")
                pdf_bytes = radiusnet_client.get_termo_adesao_pdf(radiusnet_id_cliente_plano, tipo_termo=0, formato='pdf')
                if pdf_bytes and len(pdf_bytes) > 100:
                    pdf_filename = f'{tid}.pdf'
                    pdf_path = os.path.join(UPLOAD_DIR, pdf_filename)
                    with open(pdf_path, 'wb') as f:
                        f.write(pdf_bytes)
                    logger.info(f"✅ PDF do termo para {radiusnet_id_cliente_plano} baixado com sucesso. Tamanho: {len(pdf_bytes)} bytes")
                else:
                    logger.error(f"❌ PDF vazio ou inválido para {radiusnet_id_cliente_plano}")
                    return jsonify({'sucesso': False, 'erro': 'O PDF baixado está vazio ou corrompido'}), 500
            except Exception as e:
                logger.error(f"❌ Erro ao baixar termo: {e}", exc_info=True)
                return jsonify({'sucesso': False, 'erro': f'Erro ao baixar termo: {str(e)}'}), 500
        
        elif tipo == 'ordem_servico' and radiusnet_id_os and radiusnet_client:
            try:
                tipo_os = request.form.get('tipo_os')
                if tipo_os:
                    tipo_os = int(tipo_os)
                
                logger.info(f"📥 Gerando PDF para OS {radiusnet_id_os}, tipo: {tipo_os}")
                pdf_bytes = radiusnet_client.get_os_pdf(radiusnet_id_os, radiusnet_id_cliente_plano, tipo_os)
                
                if pdf_bytes and len(pdf_bytes) > 100:
                    pdf_filename = f'{tid}.pdf'
                    pdf_path = os.path.join(UPLOAD_DIR, pdf_filename)
                    with open(pdf_path, 'wb') as f:
                        f.write(pdf_bytes)
                    logger.info(f"✅ PDF gerado para OS {radiusnet_id_os}. Tamanho: {len(pdf_bytes)} bytes")
                else:
                    logger.error(f"❌ PDF da OS {radiusnet_id_os} vazio ou inválido")
                    return jsonify({'sucesso': False, 'erro': 'Não foi possível gerar o PDF da OS. Verifique se o ID está correto.'}), 404
                    
            except TimeoutError as e:
                logger.error(f"⏰ Timeout ao gerar PDF da OS {radiusnet_id_os}: {e}")
                return jsonify({'sucesso': False, 'erro': 'Tempo limite excedido ao gerar o PDF da OS. Tente novamente.'}), 504
            except Exception as e:
                logger.error(f"❌ Erro ao gerar PDF da OS: {e}", exc_info=True)
                return jsonify({'sucesso': False, 'erro': f'Erro ao gerar PDF da OS: {str(e)}'}), 500
        
        elif 'arquivo' in request.files:
            pdf_file = request.files['arquivo']
            if pdf_file and pdf_file.filename:
                if not pdf_file.filename.lower().endswith('.pdf'):
                    return jsonify({'sucesso': False, 'erro': 'Apenas PDF'}), 400
                header = pdf_file.stream.read(5)
                pdf_file.stream.seek(0)
                if header != b'%PDF-':
                    return jsonify({'sucesso': False, 'erro': 'Arquivo não é um PDF válido'}), 400
                pdf_filename = f'{tid}.pdf'
                pdf_path = os.path.join(UPLOAD_DIR, pdf_filename)
                pdf_file.save(pdf_path)
            else:
                return jsonify({'sucesso': False, 'erro': 'Arquivo inválido'}), 400
        else:
            return jsonify({'sucesso': False, 'erro': 'Fonte do PDF não identificada'}), 400
        
        txt_path = None
        txt_original_name = None
        if 'txtfile' in request.files:
            txt_file = request.files['txtfile']
            if txt_file and txt_file.filename and txt_file.filename.lower().endswith('.txt'):
                txt_path = os.path.join(UPLOAD_TXT_DIR, f'{tid}.txt')
                txt_file.save(txt_path)
                txt_original_name = txt_file.filename
        
        os_pdf_path = None
        os_pdf_original_name = None
        if 'osfile' in request.files:
            os_file = request.files['osfile']
            if os_file and os_file.filename and os_file.filename.lower().endswith('.pdf'):
                header = os_file.stream.read(5)
                os_file.stream.seek(0)
                if header != b'%PDF-':
                    return jsonify({'sucesso': False, 'erro': 'Arquivo OS não é um PDF válido'}), 400
                os_pdf_path = os.path.join(UPLOAD_OS_DIR, f'{tid}_os.pdf')
                os_file.save(os_pdf_path)
                os_pdf_original_name = os_file.filename
        
        retirada_pdf_path = None
        retirada_original_name = None
        has_retirada = 0
        if 'retirada_pdf' in request.files:
            retirada_file = request.files['retirada_pdf']
            if retirada_file and retirada_file.filename and retirada_file.filename.lower().endswith('.pdf'):
                header = retirada_file.stream.read(5)
                retirada_file.stream.seek(0)
                if header != b'%PDF-':
                    return jsonify({'sucesso': False, 'erro': 'Arquivo de retirada não é um PDF válido'}), 400
                retirada_pdf_path = os.path.join(UPLOAD_OS_DIR, f'{tid}_retirada.pdf')
                retirada_file.save(retirada_pdf_path)
                retirada_original_name = retirada_file.filename
                has_retirada = 1
        
        nome = request.form.get('nome', '').strip()
        mensagem_extra = request.form.get('mensagem_extra', '').strip()
        if not nome:
            if cliente_nome_extraido:
                nome = f"Termo - {cliente_nome_extraido}"
            elif radiusnet_id_cliente_plano:
                nome = f"Instalação - {radiusnet_id_cliente_plano}"
            else:
                nome = f"Template_{tid[:8]}"
        
        db = get_db()
        db.execute('''INSERT INTO templates (
            id, nome, filename, descricao, ativo, criado_em, txt_path, txt_original_name,
            tipo, os_pdf_path, os_pdf_original_name,
            radiusnet_id_cliente_plano, cliente_nome, mensagem_extra, has_retirada,
            radiusnet_id_os, data_agendada
        ) VALUES (?,?,?,?,1,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (tid, nome, pdf_filename, '', datetime.now().isoformat(),
         txt_path, txt_original_name, tipo, os_pdf_path, os_pdf_original_name,
         radiusnet_id_cliente_plano or None,
         cliente_nome_extraido or None, mensagem_extra, has_retirada,
         radiusnet_id_os or None, data_agendada or None))
        db.commit()
        db.close()
        return jsonify({'sucesso': True, 'id': tid, 'nome': nome, 'has_txt': bool(txt_path), 'has_os': bool(os_pdf_path), 'has_retirada': bool(has_retirada), 'cliente_nome': cliente_nome_extraido, 'radiusnet_id_os': radiusnet_id_os, 'data_agendada': data_agendada})
    except Exception as e:
        logger.error(f"Erro no upload_template: {e}", exc_info=True)
        return jsonify({'sucesso': False, 'erro': 'Erro interno ao processar o upload'}), 500

@app.route('/api/templates/<tid>/delete', methods=['DELETE'])
@login_required
def delete_template(tid):
    db = get_db()
    db.execute('UPDATE templates SET ativo = 0 WHERE id = ?', (tid,))
    db.commit()
    db.close()
    return jsonify({'sucesso': True})

def gerar_pdf_retirada_completo(contrato, dados, historico_tentativas, equipamentos):
    """
    Gera um PDF completo para retirada com histórico de tentativas.
    """
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20*mm, rightMargin=20*mm, 
                           topMargin=20*mm, bottomMargin=20*mm)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('CustomTitle', parent=styles['Title'], fontSize=16, 
                                 alignment=1, spaceAfter=20)
    
    if isinstance(contrato, sqlite3.Row):
        contrato = dict(contrato)
    
    cliente_nome = contrato.get('cliente_nome', 'Cliente nao informado')
    cliente_cpf = contrato.get('cliente_cpf', '')
    tecnico_nome = contrato.get('tecnico_nome', 'Tecnico nao informado')
    contrato_id = contrato.get('contrato_id', '')
    motivo_retirada = contrato.get('motivo_retirada', '')
    boleto_aberto = contrato.get('boleto_aberto', '')
    
    story = [
        Paragraph("TERMO DE RETIRADA DO SISTEMA", title_style),
        Spacer(1, 12),
        Paragraph(f"Protocolo: {contrato_id}", styles['Normal']),
        Paragraph(f"Cliente: {cliente_nome}", styles['Normal']),
        Paragraph(f"CPF: {cliente_cpf or '—'}", styles['Normal']),
        Paragraph(f"Tecnico: {tecnico_nome}", styles['Normal']),
        Paragraph(f"Data: {datetime.now().strftime('%d/%m/%Y %H:%M')}", styles['Normal']),
        Spacer(1, 12)
    ]
    
    if historico_tentativas:
        story.append(Paragraph('<b>HISTORICO DE TENTATIVAS:</b>', styles['Normal']))
        for i, tentativa in enumerate(historico_tentativas, 1):
            resultado = tentativa.get('resultado', '')
            relato = tentativa.get('texto', tentativa.get('relato', ''))
            eq = tentativa.get('equipamentos', {})
            status_texto = '✅ SUCESSO' if resultado == 'sucesso' else '❌ INSUCESSO'
            
            story.append(Paragraph(f'<b>Tentativa {i}:</b> {status_texto}', styles['Normal']))
            if relato:
                story.append(Paragraph(f'<b>Relato:</b> {relato}', styles['Normal']))
            if eq:
                eq_text = f'ONU: {eq.get("onu",0)} | Cordao: {eq.get("cordao_monofibra",0)} | PTO: {eq.get("pto",0)} | Roteador: {eq.get("roteador",0)} | Suporte: {eq.get("suporte_roteador",0)}'
                story.append(Paragraph(f'<b>Equipamentos:</b> {eq_text}', styles['Normal']))
            story.append(Spacer(1, 4))
    
    ultima_tentativa_sucesso = None
    for tentativa in reversed(historico_tentativas):
        if tentativa.get('resultado') == 'sucesso':
            ultima_tentativa_sucesso = tentativa
            break
    
    if ultima_tentativa_sucesso:
        eq = ultima_tentativa_sucesso.get('equipamentos', {})
        story.append(Spacer(1, 6))
        story.append(Paragraph('<b>EQUIPAMENTOS RETIRADOS:</b>', styles['Normal']))
        story.append(Paragraph(f'ONU: {eq.get("onu", 0)}', styles['Normal']))
        story.append(Paragraph(f'Cordao Monofibra: {eq.get("cordao_monofibra", 0)}', styles['Normal']))
        story.append(Paragraph(f'PTO: {eq.get("pto", 0)}', styles['Normal']))
        story.append(Paragraph(f'Roteador: {eq.get("roteador", 0)}', styles['Normal']))
        story.append(Paragraph(f'Suporte de Roteador: {eq.get("suporte_roteador", 0)}', styles['Normal']))
    
    if motivo_retirada:
        story.append(Spacer(1, 6))
        story.append(Paragraph(f'<b>Motivo:</b> {motivo_retirada}', styles['Normal']))
    
    if boleto_aberto:
        story.append(Paragraph(f'<b>Boleto em Aberto:</b> {boleto_aberto}', styles['Normal']))
    
    if dados.get('resolucao_admin'):
        story.append(Spacer(1, 6))
        story.append(Paragraph('<b>RESOLUCAO DO ADMINISTRADOR:</b>', styles['Normal']))
        story.append(Paragraph(f'<b>Data:</b> {dados.get("resolucao_admin_data", "")}', styles['Normal']))
        story.append(Paragraph(f'<b>Resultado Final:</b> {dados.get("resultado_final", "")}', styles['Normal']))
        story.append(Paragraph(f'<b>Descricao:</b> {dados.get("resolucao_admin", "")}', styles['Normal']))
    
    story.append(Spacer(1, 20))
    story.append(Paragraph('<i>Documento gerado eletronicamente.</i>', styles['Italic']))
    
    doc.build(story)
    buf.seek(0)
    return buf.read()
    

# ================================================================
# ROTA PRINCIPAL - SALVAR CONTRATO (CORRIGIDA)
# ================================================================
@app.route('/api/salvar-contrato', methods=['POST'])
@tecnico_auth_required
def salvar_contrato():
    db = None
    try:
        dados = request.get_json()
        signature_pending = dados.get('signature_pending', False)
        tipo_documento = dados.get('tipo_instalacao') or 'instalacao'
        tecnico_id = dados.get('tecnico_id')
        contrato_existente_id = dados.get('contrato_id_existente')
        
        # CORREÇÃO: Verifica se é retirada com insucesso
        if tipo_documento == 'retirada_sistema':
            resultado_tentativa = dados.get('resultado_tentativa', '').strip()
            if resultado_tentativa == 'insucesso':
                signature_pending = True
                logger.info(f"📌 Retirada com INSUCESSO - mantendo contrato como pending/draft")
        
        # Valida assinatura apenas se não for retirada
        if tipo_documento != 'retirada_sistema':
            if not signature_pending and (not dados.get('assinatura_base64') or len(dados['assinatura_base64']) < 50):
                return jsonify({'sucesso': False, 'erro': 'Assinatura inválida'}), 400
        
        if not tecnico_id:
            return jsonify({'sucesso': False, 'erro': 'Técnico obrigatório'}), 400
        
        db = get_db()
        
        # CORREÇÃO: Atualização de contrato de retirada existente
        if contrato_existente_id and tipo_documento == 'retirada_sistema':
            logger.info(f"🔄 Atualizando contrato de retirada existente: {contrato_existente_id}")
            
            contrato_existente = db.execute(
                'SELECT * FROM contratos WHERE id = ? AND tipo_instalacao = ?',
                (contrato_existente_id, 'retirada_sistema')
            ).fetchone()
            
            if not contrato_existente:
                db.close()
                return jsonify({'sucesso': False, 'erro': 'Contrato de retirada não encontrado'}), 404
            
            # Atualiza dados do contrato
            cliente_nome = dados.get('cliente_nome', '').strip() or contrato_existente['cliente_nome']
            cliente_cpf = dados.get('cliente_cpf', '').strip() or contrato_existente['cliente_cpf']
            motivo_retirada = dados.get('motivo_retirada', '').strip()
            boleto_aberto = dados.get('boleto_aberto', '').strip()
            relato_tentativa = (dados.get('relato_tentativa') or '').strip()
            equipamentos = dados.get('equipamentos_retirados') or {}
            historico_tentativas = dados.get('historico_tentativas', [])
            resultado_tentativa = dados.get('resultado_tentativa', '').strip()
            
            if resultado_tentativa == 'sucesso' and not signature_pending:
                # CORRIGIDO: converter contrato_existente para dict
                contrato_dict = dict(contrato_existente)
                pdf_bytes = gerar_pdf_retirada_completo(contrato_dict, dados, historico_tentativas, equipamentos)
                
                fname = f"{contrato_existente['contrato_id'].replace('-', '_')}_{contrato_existente_id[:6]}.pdf"
                pdf_path_full = os.path.join(PDF_DIR, fname)
                with open(pdf_path_full, 'wb') as fh:
                    fh.write(pdf_bytes)
                logger.info(f"📄 PDF da retirada salvo em: {pdf_path_full}")
                
                novo_status = 'completed_retirada'
                pdf_path_value = fname
            else:
                novo_status = 'pending_retirada'
                pdf_path_value = contrato_existente['pdf_path']
            
            db.execute('''UPDATE contratos SET
                cliente_nome = ?,
                cliente_cpf = ?,
                motivo_retirada = ?,
                boleto_aberto = ?,
                relato_tentativa = ?,
                qtd_onu = ?,
                qtd_cordao_monofibra = ?,
                qtd_pto = ?,
                qtd_roteador = ?,
                qtd_suporte_roteador = ?,
                historico_tentativas = ?,
                signature_status = ?,
                resultado_tentativa = ?,
                pdf_path = ?,
                retirado_sistema_ativado = ?,
                retirado_sistema_data = ?
                WHERE id = ?''',
                (cliente_nome, cliente_cpf, motivo_retirada, boleto_aberto,
                 relato_tentativa,
                 int(equipamentos.get('onu', 0) or 0),
                 int(equipamentos.get('cordao_monofibra', 0) or 0),
                 int(equipamentos.get('pto', 0) or 0),
                 int(equipamentos.get('roteador', 0) or 0),
                 int(equipamentos.get('suporte_roteador', 0) or 0),
                 json.dumps(historico_tentativas) if historico_tentativas else None,
                 novo_status,
                 resultado_tentativa,
                 pdf_path_value,
                 1 if resultado_tentativa == 'sucesso' else 0,
                 datetime.now().isoformat() if resultado_tentativa == 'sucesso' else None,
                 contrato_existente_id))
            
            db.commit()
            db.close()
            
            return jsonify({
                'sucesso': True,
                'id': contrato_existente_id,
                'contrato_id': contrato_existente['contrato_id'],
                'status': novo_status,
                'pdf_url': f'/api/pdf/{contrato_existente_id}' if pdf_path_value else None,
                'is_retirada': True
            })
        
        # CRIAÇÃO DE NOVO CONTRATO
        num = proximo_numero(db)
        contrato_id = f"ML-{datetime.now().year}{num:04d}"
        
        tec = db.execute('SELECT nome FROM tecnicos WHERE id = ? AND ativo = 1', (tecnico_id,)).fetchone()
        if not tec:
            db.close()
            return jsonify({'sucesso': False, 'erro': 'Técnico não encontrado'}), 400
        tec_nome = tec['nome']
        
        cliente_nome = dados.get('cliente_nome', '').strip() or "Cliente não informado"
        cliente_cpf = dados.get('cliente_cpf', '').strip()
        template_id = dados.get('template_id')
        
        # Busca do template com fallback
        template_path = None
        txt_path = None
        
        if template_id:
            logger.info(f"🔍 Buscando template ID: {template_id}")
            row = db.execute('SELECT filename, txt_path FROM templates WHERE id = ? AND ativo = 1', (template_id,)).fetchone()
            if row:
                # Tenta encontrar o PDF em diferentes diretórios
                possiveis_caminhos = [
                    os.path.join(UPLOAD_DIR, row['filename']),
                    os.path.join(UPLOAD_OS_DIR, row['filename']),
                    os.path.join(BASE_DIR, 'uploads', row['filename']),
                    os.path.join(BASE_DIR, 'uploads', 'os_pdfs', row['filename']),
                ]
                
                for caminho in possiveis_caminhos:
                    if os.path.exists(caminho):
                        template_path = caminho
                        logger.info(f"✅ Template encontrado em: {template_path}")
                        break
                
                if not template_path:
                    logger.warning(f"⚠️ Template não encontrado nos caminhos: {possiveis_caminhos}")
                
                if row['txt_path'] and os.path.exists(row['txt_path']):
                    txt_path = row['txt_path']
            else:
                logger.error(f"❌ Template {template_id} não encontrado no banco")
        else:
            logger.warning("⚠️ Nenhum template_id fornecido")
        
        # Busca assinatura da empresa (apenas para documentos que não são OS)
        assinatura_responsavel = None
        assinatura_responsavel_id = None
        
        if tipo_documento == 'retirada_sistema':
            assinatura_responsavel = None
            assinatura_responsavel_id = None
            logger.info(f"📌 Retirada: usando nome do técnico '{tec_nome}' em texto")
        elif tipo_documento not in ['ordem_servico']:
            ass_row = db.execute('SELECT id, assinatura_base64 FROM assinaturas_empresa WHERE ativo = 1 ORDER BY data_cadastro DESC LIMIT 1').fetchone()
            if ass_row:
                assinatura_responsavel = ass_row['assinatura_base64']
                assinatura_responsavel_id = ass_row['id']
        
        rid = str(uuid.uuid4())
        data_hora = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        ip = request.remote_addr
        
        # Campos do contrato
        campo_cliente = dados.get('campo_cliente', '').strip()
        campo_ctop = dados.get('campo_ctop', '').strip()
        campo_db = dados.get('campo_db', '').strip()
        os_descricao = dados.get('os_descricao', '').strip()
        mensagem_extra = dados.get('mensagem_extra', '').strip()
        radiusnet_id_os = dados.get('radiusnet_id_os', '').strip()
        descricao_servico = dados.get('descricao_servico', '').strip()
        ocorrencias_texto = dados.get('ocorrencias_texto', '').strip()
        dados_os_completos = dados.get('dados_os_completos', '').strip()
        
        # Dados de retirada
        motivo_retirada = dados.get('motivo_retirada', '').strip()
        numero_tentativa = dados.get('numero_tentativa')
        resultado_tentativa = dados.get('resultado_tentativa', '').strip()
        boleto_aberto = dados.get('boleto_aberto', '').strip()
        relato_tentativa = (dados.get('relato_tentativa') or '').strip()
        equipamentos = dados.get('equipamentos_retirados') or {}
        historico_tentativas = dados.get('historico_tentativas', [])
        
        qtd_onu = int(equipamentos.get('onu', 0) or 0)
        qtd_cordao = int(equipamentos.get('cordao_monofibra', 0) or 0)
        qtd_pto = int(equipamentos.get('pto', 0) or 0)
        qtd_roteador = int(equipamentos.get('roteador', 0) or 0)
        qtd_suporte = int(equipamentos.get('suporte_roteador', 0) or 0)
        
        # CORREÇÃO: Geração do PDF com fallback
        if tipo_documento == 'retirada_sistema':
            signature_status = 'pending_retirada' if resultado_tentativa == 'insucesso' else 'draft'
            fname = None
            assinatura_base64_value = None
            logger.info(f"📌 Retirada - criando contrato {signature_status}, sem PDF")
        elif signature_pending:
            signature_status = 'pending'
            fname = None
            assinatura_base64_value = None
        else:
            signature_status = 'complete'
            assinatura_base64_value = dados.get('assinatura_base64')
            
            # CORREÇÃO: Se não há template_path, usa fallback
            if template_path and os.path.exists(template_path):
                logger.info(f"📄 Usando template: {template_path}")
                try:
                    pdf_bytes = overlay_signature_on_pdf(
                        template_path, 
                        assinatura_base64_value, 
                        txt_path,
                        campo_cliente, 
                        campo_ctop, 
                        campo_db, 
                        os_descricao,
                        tipo_documento, 
                        assinatura_responsavel
                    )
                    logger.info(f"✅ PDF assinado gerado com sucesso a partir do template!")
                except Exception as e:
                    logger.error(f"❌ Erro ao gerar PDF a partir do template: {e}")
                    # Fallback para caso o template falhe
                    fallback_dados = {
                        'cliente_nome': cliente_nome,
                        'cliente_cpf': cliente_cpf,
                        'assinatura_base64': assinatura_base64_value,
                        'campo_cliente': campo_cliente,
                        'campo_ctop': campo_ctop,
                        'campo_db': campo_db,
                        'os_descricao': os_descricao,
                        'motivo_retirada': motivo_retirada,
                        'boleto_aberto': boleto_aberto,
                        'equipamentos_retirados': equipamentos,
                        'relato_tentativa': relato_tentativa if resultado_tentativa == 'insucesso' else None,
                        'historico_tentativas': historico_tentativas,
                    }
                    pdf_bytes = gerar_pdf_fallback(rid, fallback_dados, tec_nome, data_hora, ip, contrato_id, tipo_documento, assinatura_responsavel)
            else:
                logger.warning(f"⚠️ Template não encontrado. Usando fallback.")
                fallback_dados = {
                    'cliente_nome': cliente_nome,
                    'cliente_cpf': cliente_cpf,
                    'assinatura_base64': assinatura_base64_value,
                    'campo_cliente': campo_cliente,
                    'campo_ctop': campo_ctop,
                    'campo_db': campo_db,
                    'os_descricao': os_descricao,
                    'motivo_retirada': motivo_retirada,
                    'boleto_aberto': boleto_aberto,
                    'equipamentos_retirados': equipamentos,
                    'relato_tentativa': relato_tentativa if resultado_tentativa == 'insucesso' else None,
                    'historico_tentativas': historico_tentativas,
                }
                pdf_bytes = gerar_pdf_fallback(rid, fallback_dados, tec_nome, data_hora, ip, contrato_id, tipo_documento, assinatura_responsavel)
            
            fname = f"{contrato_id.replace('-', '_')}_{rid[:6]}.pdf"
            pdf_path_full = os.path.join(PDF_DIR, fname)
            with open(pdf_path_full, 'wb') as fh:
                fh.write(pdf_bytes)
            logger.info(f"📄 PDF salvo em: {pdf_path_full}")
        
        historico_json = json.dumps(historico_tentativas) if historico_tentativas else None
        
        # Inserção do contrato com todos os campos
        db.execute('''INSERT INTO contratos (
            id, template_id, contrato_id, numero_seq, tecnico_id, tecnico_nome,
            cliente_nome, cliente_cpf, assinatura_base64, data_hora, ip_dispositivo,
            pdf_path, campo_cliente, campo_ctop, campo_db, os_descricao,
            tipo_instalacao, assinatura_responsavel_id, assinatura_responsavel_data,
            signature_status, mensagem_extra, radiusnet_id_os,
            descricao_servico, ocorrencias_texto, dados_os_completos,
            motivo_retirada, numero_tentativa, resultado_tentativa, boleto_aberto,
            relato_tentativa, qtd_onu, qtd_cordao_monofibra, qtd_pto,
            qtd_roteador, qtd_suporte_roteador, arquivado, historico_tentativas
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)''',
                   (rid, template_id, contrato_id, num, tecnico_id, tec_nome,
                    cliente_nome, cliente_cpf, assinatura_base64_value, data_hora, ip,
                    fname, campo_cliente, campo_ctop, campo_db, os_descricao,
                    tipo_documento, assinatura_responsavel_id,
                    datetime.now().isoformat() if assinatura_responsavel_id else None,
                    signature_status, mensagem_extra, radiusnet_id_os,
                    descricao_servico, ocorrencias_texto, dados_os_completos,
                    motivo_retirada, numero_tentativa, resultado_tentativa, boleto_aberto,
                    relato_tentativa, qtd_onu, qtd_cordao, qtd_pto,
                    qtd_roteador, qtd_suporte, historico_json))
        db.commit()
        db.close()
        
        if signature_status == 'pending' or signature_status == 'pending_retirada':
            return jsonify({
                'sucesso': True, 
                'status': signature_status, 
                'id': rid, 
                'contrato_id': contrato_id, 
                'tipo': tipo_documento, 
                'resultado': resultado_tentativa
            })
        else:
            return jsonify({
                'sucesso': True, 
                'id': rid, 
                'contrato_id': contrato_id, 
                'pdf_url': f'/api/pdf/{rid}', 
                'data_hora': data_hora, 
                'status': 'complete'
            })
            
    except Exception as e:
        logger.error(f"❌ Erro salvar: {e}", exc_info=True)
        if db: 
            db.close()
        return jsonify({'sucesso': False, 'erro': f'Erro interno ao salvar contrato: {str(e)}'}), 500
# ================================================================
# ROTAS DE CONTRATOS - APENAS NÃO ARQUIVADOS
# ================================================================
@app.route('/api/contratos', methods=['GET'])
@tecnico_auth_required
def listar_contratos():
    db = get_db()
    try:
        rows = db.execute('''SELECT id, contrato_id, numero_seq, tecnico_nome, cliente_nome,
            data_hora, pdf_path, campo_cliente, campo_ctop, campo_db, os_descricao,
            tipo_instalacao, signature_status, signed_at, email_sent_to,
            retirado_sistema_ativado, retirado_sistema_data, mensagem_extra,
            radiusnet_id_os, descricao_servico, ocorrencias_texto,
            radiusnet_finalizado, radiusnet_finalizado_data, radiusnet_os_status,
            motivo_retirada, numero_tentativa, resultado_tentativa, boleto_aberto,
            relato_tentativa, qtd_onu, qtd_cordao_monofibra, qtd_pto,
            qtd_roteador, qtd_suporte_roteador, historico_tentativas,
            resolucao_admin, resolucao_admin_data, resultado_final_retirada
            FROM contratos 
            WHERE arquivado = 0
            ORDER BY numero_seq DESC''').fetchall()
    except Exception as e:
        logger.error(f"❌ Erro ao listar contratos: {e}", exc_info=True)
        # Fallback para caso a tabela não tenha todas as colunas
        rows = db.execute('''SELECT id, contrato_id, numero_seq, tecnico_nome, cliente_nome,
            data_hora, pdf_path, campo_cliente, campo_ctop, campo_db, os_descricao,
            tipo_instalacao, signature_status, signed_at, email_sent_to,
            retirado_sistema_ativado, retirado_sistema_data, mensagem_extra,
            radiusnet_id_os, descricao_servico, ocorrencias_texto,
            historico_tentativas,
            resolucao_admin, resolucao_admin_data, resultado_final_retirada
            FROM contratos 
            WHERE arquivado = 0
            ORDER BY numero_seq DESC''').fetchall()
        rows = [dict(row) for row in rows]
        for row in rows:
            row['radiusnet_finalizado'] = None
            row['radiusnet_finalizado_data'] = None
            row['radiusnet_os_status'] = None
            row['motivo_retirada'] = ''
            row['numero_tentativa'] = 0
            row['resultado_tentativa'] = ''
            row['boleto_aberto'] = ''
            row['relato_tentativa'] = ''
            row['qtd_onu'] = 0
            row['qtd_cordao_monofibra'] = 0
            row['qtd_pto'] = 0
            row['qtd_roteador'] = 0
            row['qtd_suporte_roteador'] = 0
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/contratos/arquivados', methods=['GET'])
@login_required
def listar_contratos_arquivados():
    db = get_db()
    try:
        rows = db.execute('''SELECT id, contrato_id, numero_seq, tecnico_nome, cliente_nome,
            data_hora, pdf_path, campo_cliente, campo_ctop, campo_db, os_descricao,
            tipo_instalacao, signature_status, signed_at, email_sent_to,
            retirado_sistema_ativado, retirado_sistema_data, mensagem_extra,
            radiusnet_id_os, descricao_servico, ocorrencias_texto,
            radiusnet_finalizado, radiusnet_finalizado_data, radiusnet_os_status,
            motivo_retirada, numero_tentativa, resultado_tentativa, boleto_aberto,
            relato_tentativa, qtd_onu, qtd_cordao_monofibra, qtd_pto,
            qtd_roteador, qtd_suporte_roteador, historico_tentativas,
            resolucao_admin, resolucao_admin_data, resultado_final_retirada
            FROM contratos 
            WHERE arquivado = 1
            ORDER BY numero_seq DESC''').fetchall()
    except Exception as e:
        logger.error(f"❌ Erro ao listar contratos arquivados: {e}", exc_info=True)
        rows = db.execute('''SELECT id, contrato_id, numero_seq, tecnico_nome, cliente_nome,
            data_hora, pdf_path, campo_cliente, campo_ctop, campo_db, os_descricao,
            tipo_instalacao, signature_status, signed_at, email_sent_to,
            retirado_sistema_ativado, retirado_sistema_data, mensagem_extra,
            radiusnet_id_os, descricao_servico, ocorrencias_texto,
            historico_tentativas,
            resolucao_admin, resolucao_admin_data, resultado_final_retirada
            FROM contratos 
            WHERE arquivado = 1
            ORDER BY numero_seq DESC''').fetchall()
        rows = [dict(row) for row in rows]
        for row in rows:
            row['radiusnet_finalizado'] = None
            row['radiusnet_finalizado_data'] = None
            row['radiusnet_os_status'] = None
            row['motivo_retirada'] = ''
            row['numero_tentativa'] = 0
            row['resultado_tentativa'] = ''
            row['boleto_aberto'] = ''
            row['relato_tentativa'] = ''
            row['qtd_onu'] = 0
            row['qtd_cordao_monofibra'] = 0
            row['qtd_pto'] = 0
            row['qtd_roteador'] = 0
            row['qtd_suporte_roteador'] = 0
    db.close()
    return jsonify([dict(r) for r in rows])

# ================================================================
# ROTAS DE RETIRADA (MULTI-ETAPAS)
# ================================================================
@app.route('/api/contratos/retirada', methods=['POST'])
@tecnico_auth_required
def criar_contrato_retirada():
    try:
        data = request.get_json()
        tecnico_id = data.get('tecnico_id')
        cliente_nome = data.get('cliente_nome', '').strip()
        cliente_cpf = data.get('cliente_cpf', '').strip()
        template_id = data.get('template_id')
        if not tecnico_id:
            return jsonify({'sucesso': False, 'erro': 'Tecnico obrigatorio'}), 400
        db = get_db()
        tec = db.execute('SELECT nome FROM tecnicos WHERE id = ? AND ativo = 1', (tecnico_id,)).fetchone()
        if not tec:
            db.close()
            return jsonify({'sucesso': False, 'erro': 'Tecnico nao encontrado'}), 400
        num = proximo_numero(db)
        contrato_id = f"ML-RET-{datetime.now().year}{num:04d}"
        rid = str(uuid.uuid4())
        data_hora = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        ip = request.remote_addr
        
        template_data = db.execute('SELECT cliente_nome, radiusnet_id_os FROM templates WHERE id = ? AND ativo = 1', (template_id,)).fetchone()
        if template_data and not cliente_nome:
            cliente_nome = template_data['cliente_nome'] or ''
        
        db.execute('''INSERT INTO contratos (
            id, template_id, contrato_id, numero_seq, tecnico_id, tecnico_nome,
            cliente_nome, cliente_cpf, data_hora, ip_dispositivo,
            tipo_instalacao, signature_status, retirado_sistema_ativado,
            tentativas, tentativa_count, arquivado, historico_tentativas,
            radiusnet_id_os, descricao_servico
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?)''',
                   (rid, template_id, contrato_id, num, tecnico_id, tec['nome'],
                    cliente_nome or '', cliente_cpf or '', data_hora, ip,
                    'retirada_sistema', 'draft', 0, json.dumps([]), 0, json.dumps([]),
                    template_data['radiusnet_id_os'] if template_data else None,
                    ''))
        db.commit()
        db.close()
        return jsonify({'sucesso': True, 'id': rid, 'contrato_id': contrato_id})
    except Exception as e:
        logger.error(f"Erro criar contrato retirada: {e}", exc_info=True)
        return jsonify({'sucesso': False, 'erro': 'Erro interno'}), 500

@app.route('/api/contrato/<contrato_id>/tentativa', methods=['POST'])
@tecnico_auth_required
def adicionar_tentativa_retirada(contrato_id):
    try:
        data = request.get_json()
        texto = data.get('texto', '').strip()
        resultado = data.get('resultado', 'insucesso')
        equipamentos = data.get('equipamentos', {})

        if not texto:
            return jsonify({'sucesso': False, 'erro': 'Texto obrigatorio'}), 400

        db = get_db()
        contrato = db.execute('SELECT * FROM contratos WHERE id = ?', (contrato_id,)).fetchone()
        if not contrato:
            db.close()
            return jsonify({'sucesso': False, 'erro': 'Contrato nao encontrado'}), 404

        historico = json.loads(contrato['historico_tentativas']) if contrato['historico_tentativas'] else []
        
        try:
            tentativa_count = int(contrato['tentativa_count']) if contrato['tentativa_count'] is not None else 0
        except (ValueError, TypeError):
            tentativa_count = 0

        if tentativa_count >= 3:
            db.close()
            return jsonify({'sucesso': False, 'erro': 'Limite de 3 tentativas'}), 400

        tentativa_count += 1
        historico.append({
            'numero': tentativa_count,
            'texto': texto,
            'resultado': resultado,
            'equipamentos': equipamentos,
            'data': datetime.now().isoformat()
        })

        if resultado == 'sucesso':
            novo_status = 'completed_retirada'
        else:
            novo_status = 'pending_retirada' if tentativa_count > 0 else 'draft'

        db.execute('''UPDATE contratos SET 
            tentativa_count = ?,
            historico_tentativas = ?,
            signature_status = ?,
            resultado_tentativa = ?,
            relato_tentativa = ?,
            qtd_onu = ?,
            qtd_cordao_monofibra = ?,
            qtd_pto = ?,
            qtd_roteador = ?,
            qtd_suporte_roteador = ?
            WHERE id = ?''',
            (tentativa_count,
             json.dumps(historico),
             novo_status,
             resultado,
             texto,
             int(equipamentos.get('onu', 0) or 0),
             int(equipamentos.get('cordao_monofibra', 0) or 0),
             int(equipamentos.get('pto', 0) or 0),
             int(equipamentos.get('roteador', 0) or 0),
             int(equipamentos.get('suporte_roteador', 0) or 0),
             contrato_id))

        # CORRIGIDO: converter contrato para dict para passar para a função
        contrato_dict = dict(contrato)
        contrato_dict['historico_tentativas'] = json.dumps(historico)
        contrato_dict['tentativa_count'] = tentativa_count
        contrato_dict['resultado_tentativa'] = resultado
        
        # CORRIGIDO: usar a função correta
        pdf_bytes = gerar_pdf_retirada_completo(contrato_dict, {}, historico, equipamentos)
        fname = f"{contrato['contrato_id'].replace('-', '_')}_{contrato_id[:6]}.pdf"
        pdf_path_full = os.path.join(PDF_DIR, fname)
        with open(pdf_path_full, 'wb') as fh:
            fh.write(pdf_bytes)
        
        db.execute('UPDATE contratos SET pdf_path = ? WHERE id = ?', (fname, contrato_id))
        db.commit()
        db.close()

        return jsonify({
            'sucesso': True,
            'mensagem': f'Tentativa {tentativa_count} registrada com {resultado}',
            'tentativa_atual': tentativa_count,
            'historico': historico,
            'pdf_url': f'/api/pdf/{contrato_id}'
        })
    except NameError as e:
        logger.error(f"❌ Erro de nome na função: {e}", exc_info=True)
        return jsonify({'sucesso': False, 'erro': 'Erro interno: função não definida'}), 500
    except Exception as e:
        logger.error(f"❌ Erro ao adicionar tentativa: {e}", exc_info=True)
        return jsonify({'sucesso': False, 'erro': 'Erro interno'}), 500
    
# ================================================================
# ROTA: BUSCAR TENTATIVAS DE RETIRADA DE UM CONTRATO
# ================================================================
@app.route('/api/contrato/<contrato_id>/tentativas', methods=['GET'])
@tecnico_auth_required
def buscar_tentativas_retirada(contrato_id):
    """
    Busca o histórico de tentativas de retirada de um contrato.
    Usado pelo admin para exibir detalhes completos da retirada.
    """
    try:
        db = get_db()
        
        # Busca o contrato com todos os campos necessários
        contrato = db.execute('''SELECT 
            id, contrato_id, template_id, cliente_nome, cliente_cpf, 
            tecnico_nome, tecnico_id,
            motivo_retirada, numero_tentativa, resultado_tentativa, 
            boleto_aberto, relato_tentativa,
            qtd_onu, qtd_cordao_monofibra, qtd_pto,
            qtd_roteador, qtd_suporte_roteador,
            tentativas, tentativa_count, historico_tentativas,
            signature_status, descricao_servico,
            retirado_sistema_ativado, retirado_sistema_data,
            pdf_path, arquivado,
            resolucao_admin, resolucao_admin_data, resultado_final_retirada,
            tipo_instalacao
            FROM contratos 
            WHERE id = ? AND tipo_instalacao = 'retirada_sistema'
            AND arquivado = 0''', (contrato_id,)).fetchone()
        
        db.close()
        
        if not contrato:
            return jsonify({'sucesso': False, 'erro': 'Contrato de retirada não encontrado'}), 404
        
        # Parse do histórico de tentativas
        historico_tentativas = []
        if contrato['historico_tentativas']:
            try:
                historico_tentativas = json.loads(contrato['historico_tentativas'])
                if not isinstance(historico_tentativas, list):
                    historico_tentativas = []
            except:
                historico_tentativas = []
        
        # Verifica se o contrato está finalizado (3 tentativas com sucesso)
        tentativas_finalizadas = False
        if len(historico_tentativas) >= 3:
            ultima = historico_tentativas[-1]
            if ultima.get('resultado') == 'sucesso':
                tentativas_finalizadas = True
        
        # Contagem de tentativas
        tentativa_count = 0
        try:
            tentativa_count = int(contrato['tentativa_count']) if contrato['tentativa_count'] is not None else len(historico_tentativas)
        except (ValueError, TypeError):
            tentativa_count = len(historico_tentativas)
        
        # Função para converter valores seguros para int
        def safe_int(val):
            try:
                return int(val) if val is not None else 0
            except (ValueError, TypeError):
                return 0
        
        # Monta resposta com todos os dados
        return jsonify({
            'sucesso': True,
            'contrato_id': contrato['id'],
            'contrato_numero': contrato['contrato_id'],
            'template_id': contrato['template_id'],
            'cliente_nome': contrato['cliente_nome'] or '',
            'cliente_cpf': contrato['cliente_cpf'] or '',
            'tecnico_nome': contrato['tecnico_nome'] or '',
            'motivo_retirada': contrato['motivo_retirada'] or '',
            'boleto_aberto': contrato['boleto_aberto'] or '',
            'relato_tentativa': contrato['relato_tentativa'] or '',
            'qtd_onu': safe_int(contrato['qtd_onu']),
            'qtd_cordao_monofibra': safe_int(contrato['qtd_cordao_monofibra']),
            'qtd_pto': safe_int(contrato['qtd_pto']),
            'qtd_roteador': safe_int(contrato['qtd_roteador']),
            'qtd_suporte_roteador': safe_int(contrato['qtd_suporte_roteador']),
            'tentativa_count': tentativa_count,
            'historico_tentativas': historico_tentativas,
            'tentativas_finalizadas': tentativas_finalizadas,
            'signature_status': contrato['signature_status'] or 'draft',
            'descricao_servico': contrato['descricao_servico'] or '',
            'retirado_sistema_ativado': bool(contrato['retirado_sistema_ativado']),
            'data_ativacao': contrato['retirado_sistema_data'] or '',
            'tem_pdf': bool(contrato['pdf_path']),
            'arquivado': bool(contrato['arquivado']),
            'resolucao_admin': contrato['resolucao_admin'] or '',
            'resolucao_admin_data': contrato['resolucao_admin_data'] or '',
            'resultado_final_retirada': contrato['resultado_final_retirada'] or ''
        })
        
    except Exception as e:
        logger.error(f"❌ Erro ao buscar tentativas de retirada: {e}", exc_info=True)
        return jsonify({'sucesso': False, 'erro': str(e)}), 500
    
    
@app.route('/api/contrato/<contrato_id>/finalizar-retirada', methods=['POST'])
@login_required
def finalizar_retirada_admin(contrato_id):
    try:
        data = request.get_json()
        resolucao_admin = data.get('resolucao', '').strip()
        resultado_final = data.get('resultado_final', '').strip()
        
        db = get_db()
        
        contrato = db.execute('''SELECT * FROM contratos 
            WHERE id = ? AND tipo_instalacao = 'retirada_sistema' 
            AND arquivado = 0''', (contrato_id,)).fetchone()
        
        if not contrato:
            db.close()
            return jsonify({'sucesso': False, 'erro': 'Contrato de retirada nao encontrado'}), 404
        
        if contrato['signature_status'] in ['complete', 'completed_retirada', 'admin_finalized', 'removed_by_admin']:
            db.close()
            return jsonify({'sucesso': False, 'erro': 'Retirada ja finalizada'}), 400
        
        template_path = None
        txt_path = None
        if contrato['template_id']:
            row = db.execute('SELECT filename, txt_path FROM templates WHERE id = ? AND ativo = 1', (contrato['template_id'],)).fetchone()
            if row:
                possiveis_caminhos = [
                    os.path.join(UPLOAD_DIR, row['filename']),
                    os.path.join(UPLOAD_OS_DIR, row['filename']),
                    os.path.join(BASE_DIR, 'uploads', row['filename']),
                    os.path.join(BASE_DIR, 'uploads', 'os_pdfs', row['filename']),
                ]
                for caminho in possiveis_caminhos:
                    if os.path.exists(caminho):
                        template_path = caminho
                        break
                if row['txt_path'] and os.path.exists(row['txt_path']):
                    txt_path = row['txt_path']
        
        historico_tentativas = json.loads(contrato['historico_tentativas']) if contrato['historico_tentativas'] else []
        
        dados_retirada_overlay = {
            'motivo_retirada_completo': (contrato['motivo_retirada'] or '') + (f" — Boleto em aberto: {contrato['boleto_aberto']}" if contrato['boleto_aberto'] else ''),
            'boleto_aberto_texto': contrato['boleto_aberto'] or '',
            'tecnico_nome': contrato['tecnico_nome'] or '',
        }
        
        for i, tentativa in enumerate(historico_tentativas, 1):
            if i <= 3:
                campo_tentativa = f'tentativa_{i}_texto'
                if tentativa.get('resultado') == 'sucesso':
                    eq = tentativa.get('equipamentos', {})
                    texto_tentativa = f"SUCESSO - ONU:{eq.get('onu',0)} Cordao:{eq.get('cordao_monofibra',0)} PTO:{eq.get('pto',0)} Roteador:{eq.get('roteador',0)} Suporte:{eq.get('suporte_roteador',0)}"
                else:
                    texto_tentativa = f"INSUCESSO - {tentativa.get('texto', 'Sem detalhes')}"
                dados_retirada_overlay[campo_tentativa] = texto_tentativa
        
        dados_retirada_overlay['resolucao_admin'] = resolucao_admin or 'Resolvido pelo administrador'
        dados_retirada_overlay['resultado_final'] = resultado_final or 'Finalizado'
        dados_retirada_overlay['resolucao_admin_data'] = datetime.now().strftime('%d/%m/%Y %H:%M')
        
        ultima_tentativa_sucesso = None
        for tentativa in reversed(historico_tentativas):
            if tentativa.get('resultado') == 'sucesso':
                ultima_tentativa_sucesso = tentativa
                break
        
        equipamentos_retirados = {}
        if ultima_tentativa_sucesso:
            equipamentos_retirados = ultima_tentativa_sucesso.get('equipamentos', {})
            dados_retirada_overlay.update({
                'qtd_onu': equipamentos_retirados.get('onu', 0),
                'qtd_cordao_monofibra': equipamentos_retirados.get('cordao_monofibra', 0),
                'qtd_pto': equipamentos_retirados.get('pto', 0),
                'qtd_roteador': equipamentos_retirados.get('roteador', 0),
                'qtd_suporte_roteador': equipamentos_retirados.get('suporte_roteador', 0),
            })
        
        if template_path and os.path.exists(template_path):
            try:
                pdf_bytes = overlay_signature_on_pdf(
                    template_path,
                    None,
                    txt_path,
                    '', '', '', '',
                    'retirada_sistema',
                    None,
                    dados_retirada=dados_retirada_overlay
                )
            except Exception as e:
                logger.error(f"Erro ao gerar PDF a partir do template: {e}")
                pdf_bytes = gerar_pdf_fallback(
                    contrato_id,
                    {
                        'cliente_nome': contrato['cliente_nome'] or 'Cliente',
                        'cliente_cpf': contrato['cliente_cpf'] or '',
                        'motivo_retirada': contrato['motivo_retirada'] or '',
                        'boleto_aberto': contrato['boleto_aberto'] or '',
                        'historico_tentativas': historico_tentativas,
                        'equipamentos_retirados': equipamentos_retirados,
                        'resolucao_admin': resolucao_admin or 'Resolvido pelo administrador',
                        'resultado_final': resultado_final or 'Finalizado',
                        'resolucao_admin_data': datetime.now().strftime('%d/%m/%Y %H:%M')
                    },
                    contrato['tecnico_nome'] or 'Admin',
                    datetime.now().isoformat(),
                    request.remote_addr,
                    contrato['contrato_id'],
                    'retirada_sistema',
                    None
                )
        else:
            fallback_dados = {
                'cliente_nome': contrato['cliente_nome'] or 'Cliente',
                'cliente_cpf': contrato['cliente_cpf'] or '',
                'motivo_retirada': contrato['motivo_retirada'] or '',
                'boleto_aberto': contrato['boleto_aberto'] or '',
                'historico_tentativas': historico_tentativas,
                'equipamentos_retirados': equipamentos_retirados,
                'resolucao_admin': resolucao_admin or 'Resolvido pelo administrador',
                'resultado_final': resultado_final or 'Finalizado',
                'resolucao_admin_data': datetime.now().strftime('%d/%m/%Y %H:%M')
            }
            pdf_bytes = gerar_pdf_fallback(
                contrato_id,
                fallback_dados,
                contrato['tecnico_nome'] or 'Admin',
                datetime.now().isoformat(),
                request.remote_addr,
                contrato['contrato_id'],
                'retirada_sistema',
                None
            )
        
        fname = f"{contrato['contrato_id'].replace('-', '_')}_{contrato_id[:6]}.pdf"
        pdf_path_full = os.path.join(PDF_DIR, fname)
        with open(pdf_path_full, 'wb') as fh:
            fh.write(pdf_bytes)
        logger.info(f"PDF da retirada salvo em: {pdf_path_full}")
        
        agora = datetime.now().isoformat()
        db.execute('''UPDATE contratos SET
            signature_status = 'admin_finalized',
            pdf_path = ?,
            retirado_sistema_ativado = 1,
            retirado_sistema_data = ?,
            arquivado = 1,
            resolucao_admin = ?,
            resolucao_admin_data = ?,
            resultado_final_retirada = ?
            WHERE id = ?''',
            (fname, agora, resolucao_admin, agora, resultado_final, contrato_id))
        
        db.commit()
        db.close()
        
        return jsonify({
            'sucesso': True,
            'mensagem': 'Retirada finalizada pelo administrador com sucesso!',
            'contrato_id': contrato['contrato_id'],
            'pdf_url': f'/api/pdf/{contrato_id}'
        })
        
    except Exception as e:
        logger.error(f"Erro ao finalizar retirada pelo admin: {e}", exc_info=True)
        return jsonify({'sucesso': False, 'erro': str(e)}), 500

@app.route('/api/contrato/<contrato_id>/regenerar-pdf', methods=['POST'])
@login_required
def regenerar_pdf_retirada(contrato_id):
    try:
        db = get_db()
        
        contrato = db.execute('''SELECT * FROM contratos 
            WHERE id = ? AND tipo_instalacao = 'retirada_sistema' 
            AND arquivado = 1''', (contrato_id,)).fetchone()
        
        if not contrato:
            db.close()
            return jsonify({'sucesso': False, 'erro': 'Contrato de retirada nao encontrado'}), 404
        
        historico_tentativas = json.loads(contrato['historico_tentativas']) if contrato['historico_tentativas'] else []
        
        ultima_tentativa_sucesso = None
        for tentativa in reversed(historico_tentativas):
            if tentativa.get('resultado') == 'sucesso':
                ultima_tentativa_sucesso = tentativa
                break
        
        equipamentos_retirados = {}
        if ultima_tentativa_sucesso:
            equipamentos_retirados = ultima_tentativa_sucesso.get('equipamentos', {})
        
        pdf_bytes = gerar_pdf_fallback(
            contrato_id,
            {
                'cliente_nome': contrato['cliente_nome'] or 'Cliente',
                'cliente_cpf': contrato['cliente_cpf'] or '',
                'motivo_retirada': contrato['motivo_retirada'] or '',
                'boleto_aberto': contrato['boleto_aberto'] or '',
                'historico_tentativas': historico_tentativas,
                'equipamentos_retirados': equipamentos_retirados,
                'resolucao_admin': contrato['resolucao_admin'] or '',
                'resultado_final': contrato['resultado_final_retirada'] or '',
                'resolucao_admin_data': contrato['resolucao_admin_data'] or ''
            },
            contrato['tecnico_nome'] or 'Admin',
            datetime.now().isoformat(),
            request.remote_addr,
            contrato['contrato_id'],
            'retirada_sistema',
            None
        )
        
        fname = f"{contrato['contrato_id'].replace('-', '_')}_{contrato_id[:6]}.pdf"
        pdf_path_full = os.path.join(PDF_DIR, fname)
        with open(pdf_path_full, 'wb') as fh:
            fh.write(pdf_bytes)
        
        db.execute('UPDATE contratos SET pdf_path = ? WHERE id = ?', (fname, contrato_id))
        db.commit()
        db.close()
        
        return jsonify({
            'sucesso': True,
            'mensagem': 'PDF regenerado com sucesso!',
            'pdf_url': f'/api/pdf/{contrato_id}'
        })
        
    except Exception as e:
        logger.error(f"Erro ao regenerar PDF da retirada: {e}", exc_info=True)
        return jsonify({'sucesso': False, 'erro': str(e)}), 500

@app.route('/api/contratos/draft-retirada/<template_id>', methods=['GET'])
@tecnico_auth_required
def buscar_draft_retirada(template_id):
    try:
        db = get_db()
        row = db.execute('''SELECT 
            id, contrato_id, cliente_nome, cliente_cpf, tecnico_nome,
            motivo_retirada, numero_tentativa, resultado_tentativa, 
            boleto_aberto, relato_tentativa,
            qtd_onu, qtd_cordao_monofibra, qtd_pto,
            qtd_roteador, qtd_suporte_roteador,
            tentativas, tentativa_count, historico_tentativas,
            signature_status, descricao_servico,
            retirado_sistema_ativado, retirado_sistema_data,
            pdf_path, arquivado
            FROM contratos 
            WHERE template_id = ? 
            AND tipo_instalacao = 'retirada_sistema' 
            AND arquivado = 0
            AND signature_status IN ('draft', 'pending', 'pending_retirada')
            ORDER BY criado_em DESC LIMIT 1''', (template_id,)).fetchone()
        db.close()
        
        if not row:
            return jsonify({'sucesso': False, 'erro': 'Nenhum draft encontrado'}), 404
        
        historico = json.loads(row['historico_tentativas']) if row['historico_tentativas'] else []
        
        def safe_int(val):
            try:
                return int(val) if val is not None else 0
            except (ValueError, TypeError):
                return 0
        
        return jsonify({
            'sucesso': True,
            'contrato_id': row['id'],
            'contrato_numero': row['contrato_id'],
            'cliente_nome': row['cliente_nome'] or '',
            'cliente_cpf': row['cliente_cpf'] or '',
            'tecnico_nome': row['tecnico_nome'] or '',
            'motivo_retirada': row['motivo_retirada'] or '',
            'numero_tentativa': safe_int(row['numero_tentativa']),
            'resultado_tentativa': row['resultado_tentativa'] or '',
            'boleto_aberto': row['boleto_aberto'] or '',
            'relato_tentativa': row['relato_tentativa'] or '',
            'qtd_onu': safe_int(row['qtd_onu']),
            'qtd_cordao_monofibra': safe_int(row['qtd_cordao_monofibra']),
            'qtd_pto': safe_int(row['qtd_pto']),
            'qtd_roteador': safe_int(row['qtd_roteador']),
            'qtd_suporte_roteador': safe_int(row['qtd_suporte_roteador']),
            'tentativas': json.loads(row['tentativas']) if row['tentativas'] else [],
            'tentativa_count': safe_int(row['tentativa_count']),
            'historico_tentativas': historico,
            'signature_status': row['signature_status'] or 'draft',
            'descricao_servico': row['descricao_servico'] or '',
            'ativado': bool(row['retirado_sistema_ativado']),
            'data_ativacao': row['retirado_sistema_data'] or '',
            'tem_pdf': bool(row['pdf_path']),
            'arquivado': bool(row['arquivado'])
        })
    except Exception as e:
        logger.error(f"Erro ao buscar draft de retirada: {e}", exc_info=True)
        return jsonify({'sucesso': False, 'erro': 'Erro interno'}), 500

@app.route('/api/enviar-link-assinatura', methods=['POST'])
@tecnico_auth_required
def enviar_link_assinatura():
    try:
        data = request.get_json()
        documento_id = data.get('documento_id')
        email_cliente = data.get('email_cliente', '').strip()
        
        if not documento_id or not email_cliente:
            return jsonify({'sucesso': False, 'erro': 'Dados incompletos'}), 400
        
        db = get_db()
        contrato = db.execute('SELECT id, contrato_id, cliente_nome, signature_status, tipo_instalacao FROM contratos WHERE id = ?', (documento_id,)).fetchone()
        
        if not contrato:
            db.close()
            return jsonify({'sucesso': False, 'erro': 'Documento nao encontrado'}), 404
        
        if contrato['tipo_instalacao'] == 'retirada_sistema':
            db.close()
            return jsonify({'sucesso': False, 'erro': 'Documento de retirada não requer assinatura'}), 400
        
        if contrato['signature_status'] != 'pending':
            db.close()
            return jsonify({'sucesso': False, 'erro': 'Documento nao esta pendente'}), 400
        
        token = str(uuid.uuid4())
        expires_at = datetime.now() + timedelta(hours=24)
        db.execute('UPDATE contratos SET signature_token = ?, token_expires_at = ?, email_sent_to = ? WHERE id = ?', (token, expires_at.isoformat(), email_cliente, documento_id))
        db.commit()
        db.close()
        
        sign_url = f"{BASE_URL}/sign.html?token={token}"
        tipo_texto = {'instalacao':'Contrato','mudanca_endereco':'Mudanca','mudanca_assinante':'Mudanca Assinante','ordem_servico':'OS'}.get(contrato['tipo_instalacao'], 'Documento')
        
        # CORRIGIDO: verifica o retorno do envio
        email_enviado = send_remote_signature_email(email_cliente, contrato['cliente_nome'], sign_url, tipo_texto, contrato['contrato_id'])
        
        if email_enviado:
            return jsonify({'sucesso': True, 'mensagem': f'Link enviado para {email_cliente}'})
        else:
            return jsonify({
                'sucesso': False, 
                'erro': 'Erro ao enviar e-mail. Verifique a configuração do servidor de e-mail.',
                'link': sign_url
            }), 500
            
    except Exception as e:
        logger.error(f"Erro ao enviar link de assinatura: {e}", exc_info=True)
        return jsonify({'sucesso': False, 'erro': str(e)}), 500
    
@app.route('/api/verificar-token/<token>', methods=['GET'])
def verificar_token_assinatura(token):
    db = get_db()
    contrato = db.execute('''SELECT id, contrato_id, cliente_nome, campo_cliente, campo_ctop, campo_db, os_descricao, tipo_instalacao, signature_status, token_expires_at 
                            FROM contratos WHERE signature_token = ?''', (token,)).fetchone()
    db.close()
    if not contrato:
        return jsonify({'valido': False, 'razao': 'Token invalido'}), 404
    if contrato['token_expires_at'] and datetime.fromisoformat(contrato['token_expires_at']) < datetime.now():
        return jsonify({'valido': False, 'razao': 'Token expirado'}), 400
    if contrato['signature_status'] != 'pending':
        return jsonify({'valido': False, 'razao': 'Documento ja assinado'}), 400
    return jsonify({'valido': True, 'cliente_nome': contrato['cliente_nome'], 'contrato_id': contrato['contrato_id'], 'tipo_documento': contrato['tipo_instalacao'], 'campo_cliente': contrato['campo_cliente'] or '', 'campo_ctop': contrato['campo_ctop'] or '', 'campo_db': contrato['campo_db'] or '', 'os_descricao': contrato['os_descricao'] or '', 'expira_em': contrato['token_expires_at']})

@app.route('/api/assinatura-remota/<token>', methods=['POST'])
def realizar_assinatura_remota(token):
    db = None
    try:
        data = request.get_json()
        assinatura_base64 = data.get('assinatura_base64', '').strip()
        if not assinatura_base64 or len(assinatura_base64)<50:
            return jsonify({'sucesso': False, 'erro': 'Assinatura invalida'}), 400
        db = get_db()
        contrato = db.execute('SELECT id, contrato_id, template_id, cliente_nome, cliente_cpf, tecnico_id, tecnico_nome, campo_cliente, campo_ctop, campo_db, os_descricao, tipo_instalacao, signature_status, token_expires_at FROM contratos WHERE signature_token = ?', (token,)).fetchone()
        if not contrato:
            return jsonify({'sucesso': False, 'erro': 'Token invalido'}), 404
        if contrato['token_expires_at'] and datetime.fromisoformat(contrato['token_expires_at']) < datetime.now():
            return jsonify({'sucesso': False, 'erro': 'Link expirado'}), 400
        if contrato['signature_status'] != 'pending':
            return jsonify({'sucesso': False, 'erro': 'Documento ja assinado'}), 400
        if contrato['tipo_instalacao'] == 'retirada_sistema':
            return jsonify({'sucesso': False, 'erro': 'Documento de retirada não requer assinatura'}), 400
        template_path = None
        txt_path = None
        if contrato['template_id']:
            row = db.execute('SELECT filename, txt_path FROM templates WHERE id = ? AND ativo = 1', (contrato['template_id'],)).fetchone()
            if row:
                template_path = os.path.join(UPLOAD_DIR, row['filename'])
                txt_path = row['txt_path']
        assinatura_responsavel = None
        assinatura_responsavel_id = None
        if contrato['tipo_instalacao'] != 'ordem_servico':
            ass_row = db.execute('SELECT id, assinatura_base64 FROM assinaturas_empresa WHERE ativo = 1 ORDER BY data_cadastro DESC LIMIT 1').fetchone()
            if ass_row:
                assinatura_responsavel = ass_row['assinatura_base64']
                assinatura_responsavel_id = ass_row['id']
        if template_path and os.path.exists(template_path):
            pdf_bytes = overlay_signature_on_pdf(template_path, assinatura_base64, txt_path, contrato['campo_cliente'] or '', contrato['campo_ctop'] or '', contrato['campo_db'] or '', contrato['os_descricao'] or '', contrato['tipo_instalacao'], assinatura_responsavel)
        else:
            dados_contrato = {'cliente_nome': contrato['cliente_nome'], 'cliente_cpf': contrato['cliente_cpf'], 'assinatura_base64': assinatura_base64, 'campo_cliente': contrato['campo_cliente'], 'campo_ctop': contrato['campo_ctop'], 'campo_db': contrato['campo_db'], 'os_descricao': contrato['os_descricao']}
            pdf_bytes = gerar_pdf_fallback(contrato['id'], dados_contrato, contrato['tecnico_nome'], datetime.now().strftime('%Y-%m-%dT%H:%M:%S'), request.remote_addr, contrato['contrato_id'], contrato['tipo_instalacao'], assinatura_responsavel)
        signed_at = datetime.now().isoformat()
        fname = f"{contrato['contrato_id'].replace('-', '_')}_{contrato['id'][:6]}.pdf"
        with open(os.path.join(PDF_DIR, fname), 'wb') as fh:
            fh.write(pdf_bytes)
        db.execute('UPDATE contratos SET assinatura_base64 = ?, signature_status = "complete_remote", signed_at = ?, pdf_path = ?, signature_token = NULL, assinatura_responsavel_id = ?, assinatura_responsavel_data = ? WHERE id = ?', (assinatura_base64, signed_at, fname, assinatura_responsavel_id, datetime.now().isoformat() if assinatura_responsavel_id else None, contrato['id']))
        db.commit()
        db.close()
        return jsonify({'sucesso': True, 'mensagem': 'Assinatura registrada!', 'contrato_id': contrato['contrato_id']})
    except Exception as e:
        logger.error(f"Erro assinatura remota: {e}", exc_info=True)
        if db: db.close()
        return jsonify({'sucesso': False, 'erro': 'Erro interno'}), 500

@app.route('/api/pdf/<rid>')
def download_pdf(rid):
    db = get_db()
    row = db.execute('SELECT pdf_path, cliente_nome, contrato_id FROM contratos WHERE id = ?', (rid,)).fetchone()
    db.close()
    if not row or not row['pdf_path']:
        return jsonify({'erro': 'PDF nao encontrado'}), 404
    path = os.path.join(PDF_DIR, row['pdf_path'])
    if not os.path.exists(path):
        return jsonify({'erro': 'PDF nao encontrado'}), 404
    nome = re.sub(r'[^a-zA-Z0-9_]', '_', row['cliente_nome'] or 'documento')
    return send_file(path, as_attachment=True, download_name=f"{row['contrato_id']}_{nome}.pdf")

@app.route('/api/assinatura-empresa/ativa', methods=['GET'])
@login_required
def get_assinatura_empresa_ativa():
    db = get_db()
    assinatura = db.execute('SELECT id, responsavel_nome, responsavel_cargo, assinatura_base64, data_cadastro FROM assinaturas_empresa WHERE ativo = 1 ORDER BY data_cadastro DESC LIMIT 1').fetchone()
    db.close()
    if not assinatura:
        return jsonify({'sucesso': False, 'mensagem': 'Nenhuma assinatura ativa'}), 404
    return jsonify({'sucesso': True, 'id': assinatura['id'], 'responsavel_nome': assinatura['responsavel_nome'], 'responsavel_cargo': assinatura['responsavel_cargo'], 'assinatura_base64': assinatura['assinatura_base64'], 'data_cadastro': assinatura['data_cadastro']})

@app.route('/api/assinatura-empresa/salvar', methods=['POST'])
@login_required
def salvar_assinatura_empresa():
    data = request.get_json()
    responsavel_nome = data.get('responsavel_nome', '').strip()
    responsavel_cargo = data.get('responsavel_cargo', '').strip()
    assinatura_base64 = data.get('assinatura_base64', '').strip()
    if not responsavel_nome or not assinatura_base64 or len(assinatura_base64)<50:
        return jsonify({'sucesso': False, 'erro': 'Dados invalidos'}), 400
    assinatura_id = str(uuid.uuid4())
    db = get_db()
    db.execute('UPDATE assinaturas_empresa SET ativo = 0 WHERE ativo = 1')
    db.execute('INSERT INTO assinaturas_empresa (id, responsavel_nome, responsavel_cargo, assinatura_base64, data_cadastro, ativo) VALUES (?,?,?,?,?,1)', (assinatura_id, responsavel_nome, responsavel_cargo, assinatura_base64, datetime.now().isoformat()))
    db.commit()
    db.close()
    return jsonify({'sucesso': True, 'mensagem': f'Assinatura de {responsavel_nome} salva!'})

@app.route('/api/enviar-email-pdf', methods=['POST'])
@tecnico_auth_required
def enviar_email_pdf():
    try:
        data = request.get_json()
        doc_id = data.get('documento_id')
        email = data.get('email_cliente', '').strip()
        
        if not doc_id or not email:
            return jsonify({'sucesso': False, 'erro': 'Dados incompletos'}), 400
        
        db = get_db()
        contrato = db.execute('SELECT id, contrato_id, cliente_nome, pdf_path, data_hora, tecnico_nome FROM contratos WHERE id = ?', (doc_id,)).fetchone()
        
        if not contrato or not contrato['pdf_path']:
            db.close()
            return jsonify({'sucesso': False, 'erro': 'Documento nao encontrado'}), 404
        
        pdf_path = os.path.join(PDF_DIR, contrato['pdf_path'])
        if not os.path.exists(pdf_path):
            db.close()
            return jsonify({'sucesso': False, 'erro': 'PDF nao encontrado'}), 404
        
        # CORRIGIDO: bloco try/except para SMTP
        try:
            msg = MIMEMultipart()
            msg['From'] = f"{EMAIL_CONFIG['email_nome']} <{EMAIL_CONFIG['email_remetente']}>"
            msg['To'] = email
            msg['Subject'] = f"Documento Assinado - Sua-empresa Internet"
            
            corpo = f"""<html><body><p>Ola <strong>{contrato['cliente_nome']}</strong>,</p><p>Segue em anexo o seu documento assinado.</p><p>Protocolo: {contrato['contrato_id']}<br>Data: {contrato['data_hora']}</p><p>Atenciosamente,<br>Sua-empresa Internet</p></body></html>"""
            msg.attach(MIMEText(corpo, 'html'))
            
            with open(pdf_path, 'rb') as f:
                anexo = MIMEApplication(f.read(), _subtype='pdf')
                anexo.add_header('Content-Disposition', 'attachment', filename=f"{contrato['contrato_id']}.pdf")
                msg.attach(anexo)
            
            server = smtplib.SMTP(EMAIL_CONFIG['smtp_server'], EMAIL_CONFIG['smtp_port'])
            server.starttls()
            server.login(EMAIL_CONFIG['email_remetente'], EMAIL_CONFIG['email_senha'])
            server.send_message(msg)
            server.quit()
            
            db.execute('UPDATE contratos SET email_sent_to = ? WHERE id = ?', (email, doc_id))
            db.commit()
            db.close()
            
            logger.info(f"✅ PDF enviado para {email}")
            return jsonify({'sucesso': True, 'mensagem': 'E-mail enviado!'})
            
        except smtplib.SMTPAuthenticationError as e:
            db.close()
            logger.error(f"❌ Erro de autenticação SMTP: {e}")
            return jsonify({'sucesso': False, 'erro': 'Erro de autenticação do servidor de e-mail. Verifique as credenciais.'}), 500
        except smtplib.SMTPException as e:
            db.close()
            logger.error(f"❌ Erro SMTP: {e}")
            return jsonify({'sucesso': False, 'erro': f'Erro ao enviar e-mail: {str(e)}'}), 500
        except Exception as e:
            db.close()
            logger.error(f"❌ Erro ao enviar email: {e}")
            return jsonify({'sucesso': False, 'erro': f'Erro ao enviar e-mail: {str(e)}'}), 500
            
    except Exception as e:
        logger.error(f"❌ Erro geral em enviar_email_pdf: {e}", exc_info=True)
        return jsonify({'sucesso': False, 'erro': 'Erro interno ao processar o envio'}), 500
        
# ================================================================
# ROTAS RADIUSNET
# ================================================================

# ================================================================
# ROTA: ARQUIVAR CONTRATO (reversível)
# ================================================================
@app.route('/api/contratos/<contrato_id>/arquivar', methods=['POST'])
@login_required
def arquivar_contrato(contrato_id):
    """
    Arquiva um contrato. O PDF NÃO é apagado do disco do servidor.
    Requer código de confirmação definido em CODIGO_ARQUIVAR.
    Pode ser restaurado depois pela aba 'Arquivados'.
    """
    try:
        data = request.get_json() or {}
        codigo = str(data.get('codigo', '')).strip()
        codigo_correto = os.getenv('CODIGO_ARQUIVAR', '1234')
        
        if not codigo:
            return jsonify({'sucesso': False, 'erro': 'Código de confirmação obrigatório'}), 400
        
        if codigo != codigo_correto:
            logger.warning(f"⚠️ Tentativa de ARQUIVAR com código inválido para contrato {contrato_id}")
            return jsonify({'sucesso': False, 'erro': 'Código de confirmação inválido'}), 403
        
        db = get_db()
        contrato = db.execute(
            'SELECT id, contrato_id, cliente_nome, arquivado FROM contratos WHERE id = ?',
            (contrato_id,)
        ).fetchone()
        
        if not contrato:
            db.close()
            return jsonify({'sucesso': False, 'erro': 'Documento não encontrado'}), 404
        
        if contrato['arquivado'] == 1:
            db.close()
            return jsonify({'sucesso': False, 'erro': 'Documento já está arquivado'}), 400
        
        db.execute('''
            UPDATE contratos 
            SET arquivado = 1
            WHERE id = ?
        ''', (contrato_id,))
        
        db.commit()
        db.close()
        
        logger.info(f"📦 Contrato {contrato['contrato_id']} ARQUIVADO pelo admin")
        
        return jsonify({
            'sucesso': True,
            'mensagem': f'Documento {contrato["contrato_id"]} movido para arquivados',
            'contrato_id': contrato['contrato_id'],
            'cliente_nome': contrato['cliente_nome'],
            'acao': 'arquivado'
        })
        
    except Exception as e:
        logger.error(f"❌ Erro ao arquivar contrato: {e}", exc_info=True)
        return jsonify({'sucesso': False, 'erro': 'Erro interno'}), 500


# ================================================================
# ROTA: APAGAR CONTRATO PERMANENTEMENTE (irreversível)
# ================================================================
@app.route('/api/contratos/<contrato_id>/apagar', methods=['DELETE'])
@login_required
def apagar_contrato(contrato_id):
    """
    APAGA PERMANENTEMENTE um contrato.
    - Remove o PDF do disco do servidor (pasta signed_pdfs/)
    - Remove o registro do banco de dados
    - Remove o template vinculado (se houver)
    ATENÇÃO: IRREVERSÍVEL. Requer código em CODIGO_APAGAR.
    """
    try:
        data = request.get_json() or {}
        codigo = str(data.get('codigo', '')).strip()
        codigo_correto = os.getenv('CODIGO_APAGAR', '9999')
        
        if not codigo:
            return jsonify({'sucesso': False, 'erro': 'Código de confirmação obrigatório'}), 400
        
        if codigo != codigo_correto:
            logger.warning(f"🚨 Tentativa de APAGAR PERMANENTEMENTE com código inválido para contrato {contrato_id}")
            return jsonify({'sucesso': False, 'erro': 'Código de confirmação inválido'}), 403
        
        db = get_db()
        contrato = db.execute(
            'SELECT id, contrato_id, cliente_nome, pdf_path, template_id FROM contratos WHERE id = ?',
            (contrato_id,)
        ).fetchone()
        
        if not contrato:
            db.close()
            return jsonify({'sucesso': False, 'erro': 'Documento não encontrado'}), 404
        
        contrato_numero = contrato['contrato_id']
        cliente_nome = contrato['cliente_nome']
        pdf_path = contrato['pdf_path']
        template_id = contrato['template_id']
        
        # 1. Apaga o PDF do disco do VPS
        pdf_apagado = False
        if pdf_path:
            pdf_full = os.path.join(PDF_DIR, pdf_path)
            if os.path.exists(pdf_full):
                try:
                    os.remove(pdf_full)
                    pdf_apagado = True
                    logger.info(f"🗑️ PDF removido do disco: {pdf_full}")
                except Exception as e:
                    logger.warning(f"⚠️ Não foi possível apagar PDF: {e}")
        
        # 2. Remove o registro do banco
        db.execute('DELETE FROM contratos WHERE id = ?', (contrato_id,))
        
        # 3. Remove o template vinculado (se houver)
        if template_id:
            try:
                template = db.execute(
                    'SELECT filename, txt_path, os_pdf_path FROM templates WHERE id = ?',
                    (template_id,)
                ).fetchone()
                
                if template:
                    # Apaga arquivos do template
                    if template['filename']:
                        t_pdf = os.path.join(UPLOAD_DIR, template['filename'])
                        if os.path.exists(t_pdf):
                            os.remove(t_pdf)
                            logger.info(f"🗑️ Template PDF removido: {t_pdf}")
                    
                    if template['txt_path'] and os.path.exists(template['txt_path']):
                        os.remove(template['txt_path'])
                    
                    if template['os_pdf_path'] and os.path.exists(template['os_pdf_path']):
                        os.remove(template['os_pdf_path'])
                    
                    # Apaga o template do banco
                    db.execute('DELETE FROM templates WHERE id = ?', (template_id,))
                    logger.info(f"🗑️ Template {template_id} removido do banco")
            except Exception as e:
                logger.warning(f"⚠️ Erro ao apagar template vinculado: {e}")
        
        db.commit()
        db.close()
        
        logger.warning(f"🚨 Contrato {contrato_numero} APAGADO PERMANENTEMENTE pelo admin")
        
        return jsonify({
            'sucesso': True,
            'mensagem': f'Documento {contrato_numero} apagado permanentemente',
            'contrato_id': contrato_numero,
            'cliente_nome': cliente_nome,
            'pdf_apagado': pdf_apagado,
            'acao': 'apagado'
        })
        
    except Exception as e:
        logger.error(f"❌ Erro ao apagar contrato: {e}", exc_info=True)
        return jsonify({'sucesso': False, 'erro': 'Erro interno ao apagar'}), 500


# ================================================================
# ROTA: RESTAURAR CONTRATO ARQUIVADO
# ================================================================
@app.route('/api/contratos/<contrato_id>/restaurar', methods=['PUT'])
@login_required
def restaurar_contrato(contrato_id):
    """
    Restaura um contrato que estava arquivado.
    """
    try:
        db = get_db()
        contrato = db.execute(
            'SELECT id, contrato_id, cliente_nome, arquivado FROM contratos WHERE id = ?',
            (contrato_id,)
        ).fetchone()
        
        if not contrato:
            db.close()
            return jsonify({'sucesso': False, 'erro': 'Documento não encontrado'}), 404
        
        if contrato['arquivado'] != 1:
            db.close()
            return jsonify({'sucesso': False, 'erro': 'Documento não está arquivado'}), 400
        
        db.execute('UPDATE contratos SET arquivado = 0 WHERE id = ?', (contrato_id,))
        db.commit()
        db.close()
        
        logger.info(f"♻️ Contrato {contrato['contrato_id']} RESTAURADO pelo admin")
        
        return jsonify({
            'sucesso': True,
            'mensagem': f'Documento {contrato["contrato_id"]} restaurado com sucesso',
            'contrato_id': contrato['contrato_id'],
            'cliente_nome': contrato['cliente_nome']
        })
        
    except Exception as e:
        logger.error(f"❌ Erro ao restaurar contrato: {e}", exc_info=True)
        return jsonify({'sucesso': False, 'erro': 'Erro interno'}), 500


@app.route('/api/radiusnet/dados-cliente/<id_cliente_plano>', methods=['GET'])
@tecnico_auth_required
def obter_dados_cliente_radiusnet(id_cliente_plano):
    if not radiusnet_client:
        return jsonify({'sucesso': False, 'erro': 'RadiusNet client nao configurado'}), 500
    try:
        # Tenta buscar dados completos
        dados = radiusnet_client.get_cliente_completo_por_plano(id_cliente_plano)
        
        if not dados:
            # Fallback: tenta dados básicos
            try:
                dados_basicos = radiusnet_client.get_dados_cliente_por_plano(id_cliente_plano)
                if dados_basicos:
                    return jsonify({'sucesso': True, **dados_basicos})
            except Exception as e:
                logger.warning(f"⚠️ Fallback get_dados_cliente_por_plano falhou: {e}")
            
            # Tenta pelo menos o nome do cliente via ID
            try:
                cliente = radiusnet_client.get_cliente_por_id(id_cliente_plano)
                if cliente:
                    return jsonify({
                        'sucesso': True,
                        'nome': cliente.get('nome_razao', 'Cliente'),
                        'cpf': cliente.get('cpf_cnpj', ''),
                        'id_cliente': id_cliente_plano
                    })
            except Exception as e:
                logger.warning(f"⚠️ Fallback get_cliente_por_id falhou: {e}")
            
            return jsonify({'sucesso': False, 'erro': 'Cliente nao encontrado'}), 404
        
        return jsonify({'sucesso': True, **dados})
        
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            logger.warning(f"⚠️ Cliente {id_cliente_plano} não encontrado na API")
            return jsonify({'sucesso': False, 'erro': 'Cliente não encontrado'}), 404
        logger.error(f"Erro HTTP ao buscar dados do cliente: {e}", exc_info=True)
        return jsonify({'sucesso': False, 'erro': 'Erro na comunicação com a API'}), 500
    except Exception as e:
        logger.error(f"Erro ao buscar dados do cliente: {e}", exc_info=True)
        return jsonify({'sucesso': False, 'erro': 'Erro interno'}), 500

@app.route('/api/radiusnet/ctos', methods=['GET'])
@tecnico_auth_required
def listar_ctos():
    if not radiusnet_client:
        return jsonify({'sucesso': False, 'erro': 'RadiusNet não configurado'}), 500
    try:
        ctos = radiusnet_client.get_ctos()
        return jsonify({'sucesso': True, 'ctos': ctos})
    except Exception as e:
        logger.error(f"Erro ao listar CTOs: {e}", exc_info=True)
        return jsonify({'sucesso': False, 'erro': 'Erro interno'}), 500

@app.route('/api/radiusnet/cto/<id_cto>', methods=['GET'])
@tecnico_auth_required
def buscar_cto_por_id(id_cto):
    if not radiusnet_client:
        return jsonify({'sucesso': False, 'erro': 'RadiusNet não configurado'}), 500
    try:
        cto = radiusnet_client.get_cto_por_id(id_cto)
        if not cto:
            return jsonify({'sucesso': False, 'erro': 'CTO não encontrada'}), 404
        return jsonify({'sucesso': True, 'cto': cto})
    except Exception as e:
        logger.error(f"Erro ao buscar CTO {id_cto}: {e}", exc_info=True)
        return jsonify({'sucesso': False, 'erro': 'Erro interno'}), 500

@app.route('/api/radiusnet/os-abertas/<id_cliente_plano>', methods=['GET'])
@tecnico_auth_required
def listar_os_abertas(id_cliente_plano):
    if not radiusnet_client:
        return jsonify({'sucesso': False, 'erro': 'RadiusNet client nao configurado'}), 500
    try:
        todas_os = radiusnet_client.get_ordens_servico(id_cliente_plano)
        abertas = [os for os in todas_os if os.get('status') != 'Finalizada']
        return jsonify({'sucesso': True, 'os': abertas})
    except Exception as e:
        logger.error(f"Erro ao listar OS: {e}", exc_info=True)
        return jsonify({'sucesso': False, 'erro': 'Erro interno'}), 500

@app.route('/api/radiusnet/tos/<int:tipo_os>', methods=['GET'])
@tecnico_auth_required
def listar_os_por_tipo(tipo_os):
    if not radiusnet_client:
        return jsonify({'sucesso': False, 'erro': 'RadiusNet não configurado'}), 500
    try:
        pagina = request.args.get('pagina', 1, type=int)
        resultado = radiusnet_client.get_ordens_servico_por_tipo(tipo_os, pagina)
        tipo_texto = {1: 'Normal', 2: 'Instalação', 3: 'Retirada'}.get(tipo_os, 'Desconhecido')
        return jsonify({'sucesso': True, 'tipo': tipo_texto, 'tipo_codigo': tipo_os, 'total': resultado.get('count', 0), 'pagina': pagina, 'os': resultado.get('rows', [])})
    except Exception as e:
        logger.error(f"Erro ao listar OS por tipo: {e}", exc_info=True)
        return jsonify({'sucesso': False, 'erro': 'Erro interno'}), 500

@app.route('/api/radiusnet/os-completa/<id_ordem_servico>', methods=['GET'])
@tecnico_auth_required
def buscar_os_completa(id_ordem_servico):
    if not radiusnet_client:
        return jsonify({'sucesso': False, 'erro': 'RadiusNet não configurado'}), 500
    try:
        tipo_os = request.args.get('tipo_os', type=int)
        os_data = radiusnet_client.get_os_completa_por_id(id_ordem_servico, tipo_os)
        if not os_data:
            return jsonify({'sucesso': False, 'erro': 'OS não encontrada'}), 404
        return jsonify({'sucesso': True, 'os': os_data})
    except Exception as e:
        logger.error(f"Erro ao buscar OS: {e}", exc_info=True)
        return jsonify({'sucesso': False, 'erro': 'Erro interno'}), 500

@app.route('/api/radiusnet/os-completa-com-descricao/<id_ordem_servico>', methods=['GET'])
@tecnico_auth_required
def buscar_os_completa_com_descricao(id_ordem_servico):
    if not radiusnet_client:
        return jsonify({'sucesso': False, 'erro': 'RadiusNet não configurado'}), 500
    try:
        tipo_os = request.args.get('tipo_os', type=int)
        os_data = radiusnet_client.get_os_completa_por_id(id_ordem_servico, tipo_os)
        if not os_data:
            return jsonify({'sucesso': False, 'erro': 'OS não encontrada'}), 404
        dados_cliente = radiusnet_client.extrair_dados_os_completos(os_data, {})
        if dados_cliente:
            return jsonify({'sucesso': True, 'dados_cliente': dados_cliente, 'os': os_data})
        else:
            return jsonify({'sucesso': False, 'erro': 'Não foi possível extrair dados do cliente'}), 404
    except Exception as e:
        logger.error(f"Erro ao buscar OS completa com descrição: {e}", exc_info=True)
        return jsonify({'sucesso': False, 'erro': 'Erro interno'}), 500

@app.route('/api/radiusnet/cliente-nome/<id_ordem_servico>', methods=['GET'])
@tecnico_auth_required
def buscar_nome_cliente_por_os(id_ordem_servico):
    if not radiusnet_client:
        return jsonify({'sucesso': False, 'erro': 'RadiusNet não configurado'}), 500
    try:
        tipo_os = request.args.get('tipo_os', type=int)
        os_data = radiusnet_client.get_os_completa_por_id(id_ordem_servico, tipo_os)
        if not os_data:
            return jsonify({'sucesso': False, 'erro': 'OS não encontrada'}), 404
        nome_cliente = os_data.get('cliente_nome', '') or os_data.get('cliente', '')
        if not nome_cliente:
            id_cliente = os_data.get('id_cliente')
            if id_cliente:
                cliente_data = radiusnet_client.get_cliente_por_id(id_cliente)
                if cliente_data:
                    nome_cliente = cliente_data.get('nome_razao', '') or cliente_data.get('nome', '')
        if nome_cliente:
            return jsonify({'sucesso': True, 'nome_cliente': nome_cliente})
        else:
            return jsonify({'sucesso': False, 'erro': 'Cliente não encontrado'}), 404
    except Exception as e:
        logger.error(f"Erro ao buscar nome do cliente: {e}", exc_info=True)
        return jsonify({'sucesso': False, 'erro': 'Erro interno'}), 500

@app.route('/api/radiusnet/cpos/<id_cliente_plano>', methods=['GET'])
@tecnico_auth_required
def listar_os_por_cliente_plano(id_cliente_plano):
    if not radiusnet_client:
        return jsonify({'sucesso': False, 'erro': 'RadiusNet não configurado'}), 500
    try:
        os_list = radiusnet_client.get_ordens_servico(id_cliente_plano)
        os_abertas = [os for os in os_list if os.get('status') != 'Finalizada']
        return jsonify({'sucesso': True, 'os': os_abertas})
    except Exception as e:
        logger.error(f"Erro ao listar OS por cliente plano: {e}", exc_info=True)
        return jsonify({'sucesso': False, 'erro': 'Erro interno'}), 500

@app.route('/api/radiusnet/os-pdf/<id_ordem_servico>', methods=['GET'])
@tecnico_auth_required
def gerar_pdf_os_radiusnet(id_ordem_servico):
    if not radiusnet_client:
        return jsonify({'sucesso': False, 'erro': 'RadiusNet não configurado'}), 500
    try:
        tipo_os = request.args.get('tipo_os', type=int)
        id_cliente_plano = request.args.get('id_cliente_plano')
        
        pdf_bytes = radiusnet_client.get_os_pdf(id_ordem_servico, id_cliente_plano, tipo_os)
        
        if pdf_bytes:
            return send_file(
                BytesIO(pdf_bytes),
                mimetype='application/pdf',
                as_attachment=True,
                download_name=f'OS_{id_ordem_servico}.pdf'
            )
        else:
            return jsonify({'sucesso': False, 'erro': 'Não foi possível gerar o PDF da OS'}), 404
    except TimeoutError as e:
        logger.error(f"⏰ Timeout ao gerar PDF da OS {id_ordem_servico}: {e}")
        return jsonify({'sucesso': False, 'erro': 'O servidor demorou muito para gerar o PDF. Tente novamente.'}), 504
    except Exception as e:
        logger.error(f"Erro ao gerar PDF da OS: {e}", exc_info=True)
        return jsonify({'sucesso': False, 'erro': str(e)}), 500

@app.route('/api/radiusnet/termo-pdf/<id_cliente_plano>', methods=['GET'])
@tecnico_auth_required
def buscar_termo_pdf(id_cliente_plano):
    if not radiusnet_client:
        return jsonify({'sucesso': False, 'erro': 'RadiusNet não configurado'}), 500
    try:
        tipo_termo = request.args.get('tipo', 0, type=int)
        formato = request.args.get('formato', 'pdf')
        pdf_bytes = radiusnet_client.get_termo_adesao_pdf(id_cliente_plano, tipo_termo, formato)
        if pdf_bytes:
            return send_file(
                BytesIO(pdf_bytes),
                mimetype='application/pdf',
                as_attachment=True,
                download_name=f'termo_{id_cliente_plano}.pdf'
            )
        else:
            return jsonify({'sucesso': False, 'erro': 'Termo não encontrado'}), 404
    except Exception as e:
        logger.error(f"Erro ao buscar termo PDF: {e}", exc_info=True)
        return jsonify({'sucesso': False, 'erro': str(e)}), 500

@app.route('/api/radiusnet/verificar-os-para-finalizar', methods=['POST'])
@login_required
def verificar_os_para_finalizar():
    try:
        data = request.get_json()
        contrato_id = data.get('contrato_id')
        os_id = data.get('os_id')
        atendimento_id = data.get('atendimento_id')
        
        if not contrato_id or not os_id:
            return jsonify({'sucesso': False, 'erro': 'Dados incompletos'}), 400
        
        db = get_db()
        contrato = db.execute('SELECT id, signature_status, cliente_nome FROM contratos WHERE id = ?', (contrato_id,)).fetchone()
        db.close()
        
        if not contrato:
            return jsonify({'sucesso': False, 'erro': 'Contrato nao encontrado'}), 404
        
        if contrato['signature_status'] not in ['complete', 'complete_remote']:
            return jsonify({'sucesso': False, 'erro': 'Documento nao assinado'}), 400
        
        return jsonify({
            'sucesso': True,
            'pode_finalizar': True,
            'cliente_nome': contrato['cliente_nome'] or 'Cliente'
        })
        
    except Exception as e:
        logger.error(f"Erro ao verificar OS para finalizar: {e}", exc_info=True)
        return jsonify({'sucesso': False, 'erro': str(e)}), 500

# ================================================================
# ROTA: FINALIZAR ATENDIMENTO - ARQUIVA TODOS OS TIPOS DE CONTRATO
# ================================================================
@app.route('/api/radiusnet/finalizar-atendimento', methods=['POST'])
@login_required
def finalizar_atendimento_radiusnet():
    if not radiusnet_client:
        return jsonify({'sucesso': False, 'erro': 'RadiusNet não configurado'}), 500
    try:
        data = request.get_json()
        contrato_id = data.get('contrato_id')
        id_atendimento = data.get('id_atendimento')
        motivo = data.get('motivo', '').strip()
        descricao = data.get('descricao', '').strip()
        adicionar_ocorrencia = data.get('adicionar_ocorrencia', True)
        id_resolucao = data.get('id_resolucao', 1)

        if not contrato_id or not id_atendimento:
            return jsonify({'sucesso': False, 'erro': 'Contrato e atendimento obrigatórios'}), 400

        db = get_db()
        contrato = db.execute('''
            SELECT id, contrato_id, tecnico_nome, ocorrencias_texto, descricao_servico, tipo_instalacao
            FROM contratos WHERE id = ?
        ''', (contrato_id,)).fetchone()

        if not contrato:
            db.close()
            return jsonify({'sucesso': False, 'erro': 'Contrato não encontrado'}), 404

        if motivo:
            motivo_final = motivo
        elif contrato['ocorrencias_texto']:
            motivo_final = contrato['ocorrencias_texto']
        elif descricao:
            motivo_final = descricao
        else:
            motivo_final = 'Serviço concluído com sucesso'

        logger.info(f"📌 Motivo da ocorrência para finalização: {motivo_final}")

        if adicionar_ocorrencia:
            try:
                ocorrencia_desc = f"OS finalizada: {motivo_final}"
                radiusnet_client.adicionar_ocorrencia(id_atendimento, ocorrencia_desc)
                logger.info(f"✅ Ocorrência adicionada ao atendimento {id_atendimento}")
            except Exception as e:
                logger.warning(f"⚠️ Erro ao adicionar ocorrência: {e}")

        try:
            resultado = radiusnet_client.finalizar_atendimento(
                id_atendimento,
                id_resolucao,
                motivo_final
            )

            # CORREÇÃO: Arquiva todos os tipos de contrato
            db.execute('''
                UPDATE contratos
                SET radiusnet_finalizado = 1,
                    radiusnet_finalizado_data = ?,
                    radiusnet_os_status = 'finalizada',
                    signature_status = 'os_finalizada',
                    arquivado = 1
                WHERE id = ?
            ''', (datetime.now().isoformat(), contrato_id))

            db.commit()
            db.close()

            logger.info(f"✅ Atendimento finalizado e contrato {contrato['contrato_id']} (tipo: {contrato['tipo_instalacao']}) arquivado!")

            return jsonify({
                'sucesso': True,
                'mensagem': 'Atendimento finalizado com sucesso!',
                'resultado': resultado,
                'motivo_utilizado': motivo_final,
                'arquivado': True,
                'contrato_id': contrato['contrato_id'],
                'tipo': contrato['tipo_instalacao']
            })

        except Exception as e:
            db.close()
            logger.error(f"❌ Erro ao finalizar atendimento: {e}")
            return jsonify({'sucesso': False, 'erro': str(e)}), 500

    except Exception as e:
        logger.error(f"❌ Erro ao finalizar atendimento: {e}", exc_info=True)
        return jsonify({'sucesso': False, 'erro': 'Erro interno'}), 500

@app.route('/api/contrato/<contrato_id>/txt', methods=['GET'])
@login_required
def baixar_txt_contrato(contrato_id):
    try:
        db = get_db()
        contrato = db.execute('SELECT template_id FROM contratos WHERE id = ?', (contrato_id,)).fetchone()
        if not contrato or not contrato['template_id']:
            db.close()
            return jsonify({'erro': 'Template nao encontrado'}), 404
        
        template = db.execute('SELECT txt_path FROM templates WHERE id = ? AND ativo = 1', (contrato['template_id'],)).fetchone()
        db.close()
        
        if not template or not template['txt_path'] or not os.path.exists(template['txt_path']):
            return jsonify({'erro': 'TXT nao encontrado'}), 404
        
        with open(template['txt_path'], 'r', encoding='utf-8') as f:
            content = f.read()
        
        response = app.make_response(content)
        response.headers['Content-Type'] = 'text/plain; charset=utf-8'
        response.headers['Content-Disposition'] = f'attachment; filename=contrato_{contrato_id}.txt'
        return response
    except Exception as e:
        logger.error(f"Erro ao baixar TXT do contrato: {e}", exc_info=True)
        return jsonify({'erro': 'Erro ao baixar TXT'}), 500

# ================================================================
# INICIALIZAÇÃO DO SERVIDOR
# ================================================================
if __name__ == '__main__':
    init_db()
    local_ip = get_local_ip()
    debug_mode = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    
    if WEASYPRINT_AVAILABLE:
        try:
            import weasyprint
            logger.info(f"✅ WeasyPrint instalado: versão {weasyprint.__version__}")
        except:
            pass
    else:
        logger.warning("⚠️ WeasyPrint não está instalado! Execute: pip install weasyprint")
        logger.warning("   O sistema usará ReportLab como fallback para geração de PDFs.")
    
    print(f'\n{"="*50}')
    print(f'🚀 SERVIDOR INICIADO COM SUCESSO!')
    print(f'{"="*50}')
    print(f'📱 Acesse no celular: http://{local_ip}:5000')
    print(f'💻 Acesse no computador: http://localhost:5000')
    print(f'👤 Login admin: admin / [variável ADMIN_SENHA_PADRAO]')
    print(f'🔧 Login técnicos: nome / matrícula (ex: renato / MAT001)')
    if not WEASYPRINT_AVAILABLE:
        print(f'⚠️  WeasyPrint NÃO INSTALADO - use ReportLab (menos qualidade)')
    print(f'{"="*50}\n')
    
    app.run(debug=debug_mode, host='0.0.0.0', port=5000, threaded=True)