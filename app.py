from flask import Flask, request, jsonify, send_file, send_from_directory, session
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
import hashlib
import socket

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024
app.secret_key = os.urandom(24)

app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = 86400

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'database', 'contratos.db')
UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads')
PDF_DIR = os.path.join(BASE_DIR, 'signed_pdfs')
UPLOAD_TXT_DIR = os.path.join(BASE_DIR, 'uploads', 'txt')
UPLOAD_OS_DIR = os.path.join(BASE_DIR, 'uploads', 'os_pdfs')
SIGNATURE_DIR = os.path.join(BASE_DIR, 'signatures')

for d in [UPLOAD_DIR, PDF_DIR, os.path.dirname(DB_PATH), UPLOAD_TXT_DIR, UPLOAD_OS_DIR, SIGNATURE_DIR]:
    os.makedirs(d, exist_ok=True)

EMAIL_CONFIG = {
    'smtp_server': 'smtp.gmail.com',
    'smtp_port': 587,
    'email_remetente': 'melolinkerp@gmail.com',
    'email_senha': 'anms piqe epte zhyi',
    'email_nome': 'Melolink Internet'
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

BASE_URL = f"http://{get_local_ip()}:5000"

SIG_CONFIG_PADRAO = {
    'quality': 100,
    'dpi': 600,
    'width': 180,
    'height': 93
}

TEXT_FIELDS_CONFIG_PADRAO = {
    'font': 'Helvetica-Bold',
    'font_size': 13
}

DATE_FIELDS_CONFIG_PADRAO = {
    'font': 'Helvetica-Bold',
    'font_size': 13
}

SIG_CONFIG_INSTALACAO = {
    'page_index': 9,
    'x': 85,
    'y': 140,
    **SIG_CONFIG_PADRAO
}

SIG_CONFIG_RESPONSAVEL_INSTALACAO = {
    'page_index': 9,
    'x': 330,
    'y': 100,
    **SIG_CONFIG_PADRAO
}

TEXT_FIELDS_CONFIG_INSTALACAO = {
    'page_index': 9,
    **TEXT_FIELDS_CONFIG_PADRAO,
    'fields': {
        'campo_cliente': {'x': 467, 'y': 581},
        'campo_ctop': {'x': 474, 'y': 558},
        'campo_db': {'x': 433, 'y': 534},
    }
}

DATE_FIELDS_CONFIG_INSTALACAO = {
    'page_index': 9,
    **DATE_FIELDS_CONFIG_PADRAO,
    'fields': {
        'data_dia': {'x': 185, 'y': 226},
        'data_mes': {'x': 300, 'y': 226},
        'data_ano': {'x': 435, 'y': 226}
    }
}

SIG_CONFIG_MUDANCA = {
    'page_index': 10,
    'x': 85,
    'y': 125,
    **SIG_CONFIG_PADRAO
}

SIG_CONFIG_RESPONSAVEL_MUDANCA = {
    'page_index': 10,
    'x': 340,
    'y': 82,
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

SIG_CONFIG_OS = {
    'page_index': 0,
    'x': 50,
    'y': 225,
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
        'os_descricao': {'x': 48, 'y': 450},
    }
}

ASSINATURA_LABEL_CONFIG_OS = {
    'page_index': 0,
    'font': 'Helvetica-Bold',
    'font_size': 11,
    'x': 32,
    'y': 210,
}

DATE_FIELDS_CONFIG_OS = {
    'page_index': 0,
    'font': 'Helvetica',
    'font_size': 10,
    'fields': {
        'data_dia': {'x': 245, 'y': 240},
        'data_mes': {'x': 270, 'y': 240},
        'data_ano': {'x': 315, 'y': 240}
    }
}


def remove_white_background_otsu(image):
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
    raw = b64_str.split(',')[1] if ',' in b64_str else b64_str
    pil = PILImage.open(BytesIO(base64.b64decode(raw))).convert("RGBA")
    pil = remove_white_background_otsu(pil)
    pil = pil.filter(ImageFilter.GaussianBlur(radius=0.5))
    return pil

def get_current_date_parts():
    now = datetime.now()
    meses_pt = {
        1: 'janeiro', 2: 'fevereiro', 3: 'março', 4: 'abril',
        5: 'maio', 6: 'junho', 7: 'julho', 8: 'agosto',
        9: 'setembro', 10: 'outubro', 11: 'novembro', 12: 'dezembro'
    }
    return {
        'data_dia': str(now.day),
        'data_mes': meses_pt[now.month],
        'data_ano': str(now.year)
    }

def draw_multiline_text(c, text, x, y, font_name, font_size, max_width=450):
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
    conn = sqlite3.connect(DB_PATH, timeout=20.0)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS templates (
        id TEXT PRIMARY KEY, 
        nome TEXT NOT NULL, 
        filename TEXT NOT NULL,
        descricao TEXT, 
        ativo INTEGER DEFAULT 1, 
        criado_em TEXT DEFAULT CURRENT_TIMESTAMP,
        txt_path TEXT,
        txt_original_name TEXT,
        tipo TEXT DEFAULT 'instalacao',
        os_pdf_path TEXT,
        os_pdf_original_name TEXT
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS contratos (
        id TEXT PRIMARY KEY, 
        template_id TEXT, 
        contrato_id TEXT NOT NULL,
        numero_seq INTEGER, 
        tecnico_id TEXT, 
        tecnico_nome TEXT,
        cliente_nome TEXT,
        cliente_cpf TEXT,
        assinatura_base64 TEXT,
        data_hora TEXT NOT NULL,
        ip_dispositivo TEXT, 
        pdf_path TEXT,
        campo_cliente TEXT, 
        campo_ctop TEXT, 
        campo_db TEXT,
        os_descricao TEXT,
        tipo_instalacao TEXT DEFAULT 'instalacao',
        criado_em TEXT DEFAULT CURRENT_TIMESTAMP,
        assinatura_responsavel_id TEXT,
        assinatura_responsavel_data TEXT,
        signature_status TEXT DEFAULT 'complete',
        signature_token TEXT,
        token_expires_at TEXT,
        email_sent_to TEXT,
        signed_at TEXT
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS tecnicos (
        id TEXT PRIMARY KEY, 
        nome TEXT NOT NULL, 
        matricula TEXT UNIQUE NOT NULL, 
        ativo INTEGER DEFAULT 1
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS seq_counter
        (id INTEGER PRIMARY KEY CHECK(id=1), valor INTEGER DEFAULT 0)''')
    c.execute('INSERT OR IGNORE INTO seq_counter VALUES (1, 0)')
    
    c.execute('''CREATE TABLE IF NOT EXISTS emails_enviados (
        id TEXT PRIMARY KEY,
        contrato_id TEXT,
        email_destino TEXT,
        data_envio TEXT,
        status TEXT,
        FOREIGN KEY (contrato_id) REFERENCES contratos(id)
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS assinaturas_empresa (
        id TEXT PRIMARY KEY,
        responsavel_nome TEXT NOT NULL,
        responsavel_cargo TEXT,
        assinatura_base64 TEXT NOT NULL,
        data_cadastro TEXT NOT NULL,
        ativo INTEGER DEFAULT 1
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS usuarios_admin (
        id TEXT PRIMARY KEY,
        username TEXT UNIQUE NOT NULL,
        senha_hash TEXT NOT NULL,
        nome_completo TEXT NOT NULL,
        email TEXT,
        ativo INTEGER DEFAULT 1,
        criado_em TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    
    count = c.execute('SELECT COUNT(*) FROM tecnicos').fetchone()[0]
    if count == 0:
        tecnicos_padrao = [
            ('tec001', 'Renato', 'MAT001'),
            ('tec002', 'João', 'MAT002'),
            ('tec003', 'Rodrigo', 'MAT003'),
        ]
        for tid, nome, mat in tecnicos_padrao:
            c.execute('INSERT OR IGNORE INTO tecnicos (id, nome, matricula, ativo) VALUES (?,?,?,1)', (tid, nome, mat))
        logger.info(" Técnicos padrão inseridos")
    
    admin_count = c.execute('SELECT COUNT(*) FROM usuarios_admin').fetchone()[0]
    if admin_count == 0:
        admin_id = str(uuid.uuid4())
        senha_hash = hashlib.sha256("admin123".encode()).hexdigest()
        c.execute('''INSERT INTO usuarios_admin (id, username, senha_hash, nome_completo, email, ativo)
                     VALUES (?, ?, ?, ?, ?, 1)''',
                  (admin_id, "admin", senha_hash, "Administrador", "admin@melolink.com.br"))
        logger.info(" Usuário admin padrão criado")
    
    try:
        c.execute("PRAGMA table_info(contratos)")
        columns = c.fetchall()
        for col in columns:
            if col[1] == 'assinatura_base64' and col[3] == 1:
                c.execute("CREATE TABLE contratos_temp AS SELECT * FROM contratos")
                c.execute("DROP TABLE contratos")
                c.execute('''CREATE TABLE contratos (
                    id TEXT PRIMARY KEY, 
                    template_id TEXT, 
                    contrato_id TEXT NOT NULL,
                    numero_seq INTEGER, 
                    tecnico_id TEXT, 
                    tecnico_nome TEXT,
                    cliente_nome TEXT,
                    cliente_cpf TEXT,
                    assinatura_base64 TEXT,
                    data_hora TEXT NOT NULL,
                    ip_dispositivo TEXT, 
                    pdf_path TEXT,
                    campo_cliente TEXT, 
                    campo_ctop TEXT, 
                    campo_db TEXT,
                    os_descricao TEXT,
                    tipo_instalacao TEXT DEFAULT 'instalacao',
                    criado_em TEXT DEFAULT CURRENT_TIMESTAMP,
                    assinatura_responsavel_id TEXT,
                    assinatura_responsavel_data TEXT,
                    signature_status TEXT DEFAULT 'complete',
                    signature_token TEXT,
                    token_expires_at TEXT,
                    email_sent_to TEXT,
                    signed_at TEXT
                )''')
                c.execute("INSERT INTO contratos SELECT * FROM contratos_temp")
                c.execute("DROP TABLE contratos_temp")
                logger.info(" Restrição NOT NULL removida da coluna assinatura_base64")
                break
    except Exception as e:
        logger.warning(f" Erro ao verificar NOT NULL: {e}")
    
    conn.commit()
    conn.close()
    logger.info(" Banco de dados inicializado")

def proximo_numero(db):
    db.execute('UPDATE seq_counter SET valor = valor + 1 WHERE id = 1')
    return db.execute('SELECT valor FROM seq_counter WHERE id = 1').fetchone()['valor']

def parse_txt_coordinates(txt_path):
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
                    if key in ['page', 'x', 'y', 'width', 'height']:
                        sig_config[key] = int(value)
                    elif key in ['quality', 'dpi']:
                        sig_config[key] = int(value)
                elif current_section == 'RESPONSIBLE_SIGNATURE':
                    if key in ['page', 'x', 'y', 'width', 'height']:
                        sig_config_resp[key] = int(value)
                    elif key in ['quality', 'dpi']:
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
                            text_config['fields'][key] = {
                                'x': int(coords[0].strip()),
                                'y': int(coords[1].strip())
                            }
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
                            date_config['fields'][key] = {
                                'x': int(coords[0].strip()),
                                'y': int(coords[1].strip())
                            }
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
        logger.error(f" Erro ao parsear TXT: {e}")
        return None, None, None, None

def overlay_signature_on_pdf(template_path, sig_b64, txt_path=None,
                              campo_cliente='', campo_ctop='', campo_db='',
                              os_descricao='', tipo_documento='instalacao',
                              assinatura_responsavel_b64=None):
    reader = PdfReader(template_path)
    total_pages = len(reader.pages)
    writer = PdfWriter()
    sig_config = None
    sig_config_resp = None
    text_config = None
    date_config = None
    if txt_path:
        sig_config, sig_config_resp, text_config, date_config = parse_txt_coordinates(txt_path)
    if not sig_config:
        if tipo_documento == 'mudanca_endereco':
            sig_config = SIG_CONFIG_MUDANCA.copy()
            sig_config_resp = SIG_CONFIG_RESPONSAVEL_MUDANCA.copy()
            text_config = TEXT_FIELDS_CONFIG_MUDANCA.copy()
            date_config = DATE_FIELDS_CONFIG_MUDANCA.copy()
        elif tipo_documento == 'ordem_servico':
            sig_config = SIG_CONFIG_OS.copy()
            sig_config_resp = None  
            text_config = TEXT_FIELDS_CONFIG_OS.copy()
            date_config = DATE_FIELDS_CONFIG_OS.copy()
        else:
            sig_config = SIG_CONFIG_INSTALACAO.copy()
            sig_config_resp = SIG_CONFIG_RESPONSAVEL_INSTALACAO.copy()
            text_config = TEXT_FIELDS_CONFIG_INSTALACAO.copy()
            date_config = DATE_FIELDS_CONFIG_INSTALACAO.copy()
    else:
        if not sig_config_resp:
            sig_config_resp = sig_config.copy()
            sig_config_resp['x'] = sig_config_resp.get('x', 110) + 200
    for page_num in range(total_pages):
        original_page = reader.pages[page_num]
        need_overlay = False
        overlay_buffer = BytesIO()
        c = None
        if page_num == sig_config.get('page_index', 0) and sig_b64 and len(sig_b64) > 100:
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
            sig_x = sig_config.get('x', 110)
            sig_y = sig_config.get('y', 150)
            c.drawImage(ImageReader(sig_buf), sig_x, sig_y,
                        width=target_w, height=target_h,
                        preserveAspectRatio=True, mask='auto')
        if (sig_config_resp and 
            page_num == sig_config_resp.get('page_index', 0) and 
            assinatura_responsavel_b64 and 
            len(assinatura_responsavel_b64) > 50 and 
            tipo_documento != 'ordem_servico'):
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
            sig_x_resp = sig_config_resp.get('x', 350)
            sig_y_resp = sig_config_resp.get('y', 150)
            c.drawImage(ImageReader(sig_buf_resp), sig_x_resp, sig_y_resp,
                        width=target_w_resp, height=target_h_resp,
                        preserveAspectRatio=True, mask='auto')
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
                'os_descricao': os_descricao,
            }
            fields = text_config.get('fields', {})
            for field, coords in fields.items():
                valor = (valores.get(field) or '').strip()
                if valor:
                    if field == 'os_descricao':
                        c.setFont(font_name, font_size)
                        draw_multiline_text(c, valor, coords['x'], coords['y'], font_name, font_size, max_width=450)
                    else:
                        c.setFont(font_name, font_size)
                        c.drawString(coords['x'], coords['y'], valor)
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
            date_fields = date_config.get('fields', {})
            for field_name, coords in date_fields.items():
                if field_name in date_parts:
                    valor = date_parts[field_name]
                    c.drawString(coords['x'], coords['y'], valor)
        if need_overlay:
            c.save()
            overlay_buffer.seek(0)
            overlay_pdf = PdfReader(overlay_buffer)
            if len(overlay_pdf.pages) > 0:
                overlay_page = overlay_pdf.pages[0]
                original_page.merge_page(overlay_page)
        writer.add_page(original_page)
    output = BytesIO()
    writer.write(output)
    output.seek(0)
    return output.read()

def gerar_pdf_fallback(rid, dados, tec_nome, data_hora, ip, contrato_id, tipo_documento='instalacao', assinatura_responsavel_b64=None):
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image as RLImage
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=20*mm, leftMargin=20*mm, topMargin=20*mm, bottomMargin=20*mm)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('CustomTitle', parent=styles['Title'], fontSize=18, textColor='#CC0000', alignment=1, spaceAfter=20)
    titulo = 'MELOLINK INTERNET FIBRA ÓPTICA LTDA'
    if tipo_documento == 'mudanca_endereco':
        subtitulo = 'TERMO DE MUDANÇA DE ENDEREÇO/ASSINANTE'
    elif tipo_documento == 'ordem_servico':
        subtitulo = 'ORDEM DE SERVIÇO - Registro de Atendimento Técnico'
    else:
        subtitulo = 'CONTRATO DE PRESTAÇÃO DE SERVIÇOS'
    date_parts = get_current_date_parts()
    data_atual = f"{date_parts['data_dia']} de {date_parts['data_mes']} de {date_parts['data_ano']}"
    cliente_nome = dados.get('cliente_nome') or 'Cliente não informado'
    story = [
        Paragraph(titulo, title_style),
        Paragraph(subtitulo, styles['Heading2']),
        Spacer(1, 20),
        Paragraph(f'<b>Documento Nº:</b> {contrato_id}', styles['Normal']),
        Paragraph(f'<b>Data:</b> {data_atual}', styles['Normal']),
        Paragraph(f'<b>Cliente:</b> {cliente_nome}', styles['Normal']),
        Paragraph(f'<b>CPF:</b> {dados.get("cliente_cpf", "—")}', styles['Normal']),
        Paragraph(f'<b>Técnico:</b> {tec_nome}', styles['Normal']),
        Paragraph(f'<b>Data/Hora:</b> {data_hora}', styles['Normal']),
        Spacer(1, 12),
    ]
    if tipo_documento == 'ordem_servico':
        if dados.get('os_descricao'):
            story.append(Paragraph('<b>Descrição do Serviço:</b>', styles['Normal']))
            descricao_com_br = dados['os_descricao'].replace('\n', '<br/>')
            story.append(Paragraph(descricao_com_br, styles['Normal']))
            story.append(Spacer(1, 12))
    else:
        for label, key in [('Nº Cliente', 'campo_cliente'), ('Caixa CTOP', 'campo_ctop'), ('DB', 'campo_db')]:
            val = dados.get(key, '').strip()
            if val:
                story.append(Paragraph(f'<b>{label}:</b> {val}', styles['Normal']))
                story.append(Spacer(1, 6))
    if dados.get('assinatura_base64') and len(dados['assinatura_base64']) > 100:
        pil = process_signature_image(dados['assinatura_base64'])
        pil = pil.resize((180, 100), PILImage.Resampling.LANCZOS)
        enhancer = ImageEnhance.Sharpness(pil)
        pil = enhancer.enhance(1.6)
        enhancer = ImageEnhance.Contrast(pil)
        pil = enhancer.enhance(1.4)
        sig_buf = BytesIO()
        pil.save(sig_buf, 'PNG', optimize=False, compress_level=0)
        sig_buf.seek(0)
        story.extend([Spacer(1, 20), Paragraph('<b>Assinatura do Cliente:</b>', styles['Normal']), Spacer(1, 10), RLImage(sig_buf, width=180, height=100)])
    if assinatura_responsavel_b64 and len(assinatura_responsavel_b64) > 50:
        pil_resp = process_signature_image(assinatura_responsavel_b64)
        pil_resp = pil_resp.resize((180, 100), PILImage.Resampling.LANCZOS)
        enhancer = ImageEnhance.Sharpness(pil_resp)
        pil_resp = enhancer.enhance(1.6)
        enhancer = ImageEnhance.Contrast(pil_resp)
        pil_resp = enhancer.enhance(1.4)
        sig_buf_resp = BytesIO()
        pil_resp.save(sig_buf_resp, 'PNG', optimize=False, compress_level=0)
        sig_buf_resp.seek(0)
        story.extend([Spacer(1, 30), Paragraph('<b>Assinatura do Responsável:</b>', styles['Normal']), Spacer(1, 10), RLImage(sig_buf_resp, width=180, height=100)])
    story.extend([Spacer(1, 20), Paragraph('<i>Documento assinado digitalmente conforme Lei 14.063/2020</i>', styles['Italic'])])
    doc.build(story)
    buf.seek(0)
    return buf.read()

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'erro': 'Autenticação necessária'}), 401
        return f(*args, **kwargs)
    return decorated_function


def generate_signature_token():
    return str(uuid.uuid4())

def send_remote_signature_email(to_email, client_name, sign_url, documento_tipo, documento_id):
    try:
        msg = MIMEMultipart('alternative')
        msg['From'] = f"{EMAIL_CONFIG['email_nome']} <{EMAIL_CONFIG['email_remetente']}>"
        msg['To'] = to_email
        msg['Subject'] = f"Documento pendente de assinatura - {documento_tipo} - Melolink Internet"
        
        tipo_texto = {
            'instalacao': 'Contrato de Instalação',
            'mudanca_endereco': 'Termo de Mudança de Endereço',
            'ordem_servico': 'Ordem de Serviço'
        }.get(documento_tipo, 'Documento')
        
        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset="UTF-8"></head>
        <body style="font-family: Arial, sans-serif;">
            <div style="max-width: 600px; margin: 0 auto; background: #f9f9f9;">
                <div style="background: linear-gradient(135deg, #dc2626, #b91c1c); padding: 20px; text-align: center; color: white;">
                    <h2 style="margin: 0;">MELOLINK INTERNET</h2>
                    <p style="margin: 5px 0 0;">Fibra Óptica</p>
                </div>
                <div style="padding: 20px; background: white;">
                    <p>Olá, <strong>{client_name}</strong>!</p>
                    <p>Há um <strong>{tipo_texto}</strong> aguardando sua assinatura digital.</p>
                    <div style="background-color: #f3f4f6; padding: 15px; border-radius: 8px; margin: 20px 0;">
                        <p style="margin: 5px 0;"><strong> Documento:</strong> {documento_id}</p>
                        <p style="margin: 5px 0;"><strong> Data de emissão:</strong> {datetime.now().strftime('%d/%m/%Y às %H:%M')}</p>
                    </div>
                    <div style="text-align: center; margin: 30px 0;">
                        <a href="{sign_url}" style="display: inline-block; background-color: #dc2626; color: white; padding: 14px 28px; border-radius: 6px; text-decoration: none; font-weight: bold; font-size: 16px;">📝 ASSINAR DOCUMENTO</a>
                    </div>
                    <p style="font-size: 14px; color: #666;">⚠️ <strong>Importante:</strong> Este link é válido por <strong>24 horas</strong>.</p>
                    <hr style="margin: 20px 0; border-color: #e5e7eb;">
                    <p style="font-size: 11px; color: #6b7280; text-align: center;">Caso o botão não funcione, copie e cole o link abaixo no seu navegador:<br><a href="{sign_url}" style="color: #6b7280; word-break: break-all;">{sign_url}</a></p>
                    <p style="font-size: 11px; color: #6b7280; text-align: center; margin-top: 20px;">Este e-mail é automático, por favor não responda.<br>Em caso de dúvidas, entre em contato com nosso suporte.</p>
                </div>
            </div>
        </body>
        </html>
        """
        msg.attach(MIMEText(html_body, 'html'))
        server = smtplib.SMTP(EMAIL_CONFIG['smtp_server'], EMAIL_CONFIG['smtp_port'])
        server.starttls()
        server.login(EMAIL_CONFIG['email_remetente'], EMAIL_CONFIG['email_senha'])
        server.send_message(msg)
        server.quit()
        logger.info(f"✅ E-mail de assinatura remota enviado para {to_email}")
        return True
    except Exception as e:
        logger.error(f"❌ Erro ao enviar e-mail de assinatura remota: {e}")
        return False


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


@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    try:
        data = request.get_json()
        username = data.get('username') or data.get('usuario')
        senha = data.get('senha') or data.get('password')
        if not username or not senha:
            return jsonify({'sucesso': False, 'erro': 'Credenciais obrigatórias'}), 400
        db = get_db()
        user = db.execute('SELECT id, username, senha_hash, nome_completo FROM usuarios_admin WHERE username = ? AND ativo = 1', 
                         (username,)).fetchone()
        db.close()
        if not user:
            return jsonify({'sucesso': False, 'erro': 'Usuário não encontrado'}), 401
        senha_hash = hashlib.sha256(senha.encode()).hexdigest()
        if senha_hash != user['senha_hash']:
            return jsonify({'sucesso': False, 'erro': 'Senha incorreta'}), 401
        session.clear()
        session.permanent = True
        session['user_id'] = user['id']
        session['username'] = user['username']
        session['nome_completo'] = user['nome_completo']
        return jsonify({'sucesso': True, 'usuario': user['username'], 'nome': user['nome_completo']})
    except Exception as e:
        return jsonify({'sucesso': False, 'erro': str(e)}), 500

@app.route('/api/admin/logout', methods=['POST'])
def admin_logout():
    session.clear()
    return jsonify({'sucesso': True})

@app.route('/api/admin/check-auth', methods=['GET'])
def check_auth():
    if 'user_id' in session:
        return jsonify({'autenticado': True, 'usuario': session.get('username'), 'nome': session.get('nome_completo')})
    return jsonify({'autenticado': False}), 401

@app.route('/api/tecnicos/ativos', methods=['GET'])
def get_tecnicos_ativos():
    try:
        db = get_db()
        rows = db.execute('SELECT id, nome, matricula FROM tecnicos WHERE ativo = 1 ORDER BY nome').fetchall()
        db.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({'erro': str(e)}), 500

@app.route('/api/templates', methods=['GET'])
def list_templates():
    try:
        tipo = request.args.get('tipo')
        db = get_db()
        if tipo:
            rows = db.execute('SELECT id, nome, descricao, filename, criado_em, txt_path, txt_original_name, tipo, os_pdf_path, os_pdf_original_name FROM templates WHERE ativo = 1 AND tipo = ? ORDER BY criado_em DESC', (tipo,)).fetchall()
        else:
            rows = db.execute('SELECT id, nome, descricao, filename, criado_em, txt_path, txt_original_name, tipo, os_pdf_path, os_pdf_original_name FROM templates WHERE ativo = 1 ORDER BY criado_em DESC').fetchall()
        db.close()
        templates = []
        for r in rows:
            t = dict(r)
            t['has_txt'] = bool(r['txt_path'])
            t['has_os'] = bool(r['os_pdf_path'])
            templates.append(t)
        return jsonify(templates)
    except Exception as e:
        return jsonify({'erro': str(e)}), 500

@app.route('/api/templates/<tid>/txt', methods=['GET'])
def get_template_txt(tid):
    try:
        db = get_db()
        row = db.execute('SELECT txt_path, txt_original_name FROM templates WHERE id = ? AND ativo = 1', (tid,)).fetchone()
        db.close()
        if not row or not row['txt_path'] or not os.path.exists(row['txt_path']):
            return jsonify({'erro': 'Arquivo TXT não encontrado'}), 404
        with open(row['txt_path'], 'r', encoding='utf-8') as f:
            content = f.read()
        response = app.make_response(content)
        response.headers['Content-Type'] = 'text/plain; charset=utf-8'
        return response
    except Exception as e:
        return jsonify({'erro': str(e)}), 500

@app.route('/api/templates/<tid>/pdf-view', methods=['GET'])
def view_template_pdf(tid):
    try:
        db = get_db()
        row = db.execute('SELECT filename FROM templates WHERE id = ? AND ativo = 1', (tid,)).fetchone()
        db.close()
        if not row:
            return jsonify({'erro': 'Template não encontrado'}), 404
        pdf_path = os.path.join(UPLOAD_DIR, row['filename'])
        if not os.path.exists(pdf_path):
            return jsonify({'erro': 'PDF não encontrado'}), 404
        return send_file(pdf_path, mimetype='application/pdf')
    except Exception as e:
        return jsonify({'erro': str(e)}), 500

@app.route('/api/templates/<tid>/os-pdf', methods=['GET'])
def get_linked_os_pdf(tid):
    try:
        db = get_db()
        row = db.execute('SELECT os_pdf_path FROM templates WHERE id = ? AND ativo = 1', (tid,)).fetchone()
        db.close()
        if not row or not row['os_pdf_path'] or not os.path.exists(row['os_pdf_path']):
            return jsonify({'erro': 'OS não encontrada'}), 404
        return send_file(row['os_pdf_path'], mimetype='application/pdf')
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/api/salvar-contrato', methods=['POST'])
def salvar_contrato():
    db = None
    try:
        dados = request.get_json()
        signature_pending = dados.get('signature_pending', False)
        
        if not signature_pending:
            if not dados.get('assinatura_base64') or len(dados['assinatura_base64']) < 50:
                return jsonify({'sucesso': False, 'erro': 'Assinatura do cliente inválida'}), 400
        
        if not dados.get('tecnico_id'):
            return jsonify({'sucesso': False, 'erro': 'Técnico obrigatório'}), 400
        
        tipo_documento = dados.get('tipo_instalacao') or 'instalacao'
        
        db = get_db()
        num = proximo_numero(db)
        contrato_id = f"ML-{datetime.now().year}{num:04d}"
        
        tec = db.execute('SELECT nome FROM tecnicos WHERE id = ? AND ativo = 1', (dados['tecnico_id'],)).fetchone()
        if not tec:
            db.close()
            return jsonify({'sucesso': False, 'erro': 'Técnico não encontrado'}), 400
        tec_nome = tec['nome']
        
        cliente_nome = dados.get('cliente_nome', '').strip()
        if not cliente_nome:
            cliente_nome = "Cliente não informado"
        
        cliente_cpf = dados.get('cliente_cpf', '').strip()
        template_id = dados.get('template_id')
        template_path = None
        txt_path = None
        
        if template_id:
            row = db.execute('SELECT filename, txt_path FROM templates WHERE id = ? AND ativo = 1', (template_id,)).fetchone()
            if row:
                template_path = os.path.join(UPLOAD_DIR, row['filename'])
                txt_path = row['txt_path']
        
        assinatura_responsavel = None
        assinatura_responsavel_id = None
        if tipo_documento != 'ordem_servico':
            assinatura_row = db.execute('SELECT id, assinatura_base64 FROM assinaturas_empresa WHERE ativo = 1 ORDER BY data_cadastro DESC LIMIT 1').fetchone()
            if assinatura_row:
                assinatura_responsavel = assinatura_row['assinatura_base64']
                assinatura_responsavel_id = assinatura_row['id']
        
        rid = str(uuid.uuid4())
        data_hora = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        ip = request.remote_addr
        
        campo_cliente = dados.get('campo_cliente', '').strip()
        campo_ctop = dados.get('campo_ctop', '').strip()
        campo_db = dados.get('campo_db', '').strip()
        os_descricao = dados.get('os_descricao', '').strip()
        
        if signature_pending:
            signature_status = 'pending'
            fname = None
            assinatura_base64_value = None
        else:
            signature_status = 'complete'
            assinatura_base64_value = dados['assinatura_base64']
            if template_path and os.path.exists(template_path):
                pdf_bytes = overlay_signature_on_pdf(
                    template_path, assinatura_base64_value,
                    txt_path=txt_path,
                    campo_cliente=campo_cliente, campo_ctop=campo_ctop,
                    campo_db=campo_db, os_descricao=os_descricao,
                    tipo_documento=tipo_documento,
                    assinatura_responsavel_b64=assinatura_responsavel
                )
            else:
                pdf_bytes = gerar_pdf_fallback(rid, dados, tec_nome, data_hora, ip, contrato_id, 
                                              tipo_documento, assinatura_responsavel)
            fname = f"{contrato_id.replace('-', '_')}_{rid[:6]}.pdf"
            pdf_full_path = os.path.join(PDF_DIR, fname)
            with open(pdf_full_path, 'wb') as fh:
                fh.write(pdf_bytes)
        
        db.execute('''
            INSERT INTO contratos (
                id, template_id, contrato_id, numero_seq, tecnico_id, tecnico_nome,
                cliente_nome, cliente_cpf, assinatura_base64, data_hora, ip_dispositivo,
                pdf_path, campo_cliente, campo_ctop, campo_db, os_descricao, tipo_instalacao,
                assinatura_responsavel_id, assinatura_responsavel_data, signature_status
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', (
            rid, template_id, contrato_id, num, dados['tecnico_id'], tec_nome,
            cliente_nome, cliente_cpf, assinatura_base64_value,
            data_hora, ip, fname, campo_cliente, campo_ctop, campo_db, os_descricao, tipo_documento,
            assinatura_responsavel_id, datetime.now().isoformat() if assinatura_responsavel_id else None,
            signature_status
        ))
        
        db.commit()
        db.close()
        
        if signature_pending:
            return jsonify({
                'sucesso': True,
                'status': 'pending',
                'id': rid,
                'contrato_id': contrato_id
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
        logger.error(f"Erro ao salvar: {e}")
        if db:
            db.close()
        return jsonify({'sucesso': False, 'erro': str(e)}), 500


@app.route('/api/enviar-link-assinatura', methods=['POST'])
@login_required
def enviar_link_assinatura():
    db = None
    try:
        data = request.get_json()
        documento_id = data.get('documento_id')
        email_cliente = data.get('email_cliente', '').strip()
        
        if not documento_id:
            return jsonify({'sucesso': False, 'erro': 'ID do documento não informado'}), 400
        if not email_cliente:
            return jsonify({'sucesso': False, 'erro': 'E-mail do cliente é obrigatório'}), 400
        
        db = get_db()
        contrato = db.execute('SELECT id, contrato_id, cliente_nome, signature_status, tipo_instalacao FROM contratos WHERE id = ?', (documento_id,)).fetchone()
        
        if not contrato:
            db.close()
            return jsonify({'sucesso': False, 'erro': 'Documento não encontrado'}), 404
        if contrato['signature_status'] != 'pending':
            db.close()
            return jsonify({'sucesso': False, 'erro': 'Documento não está pendente'}), 400
        
        token = generate_signature_token()
        expires_at = datetime.now() + timedelta(hours=24)
        
        db.execute('UPDATE contratos SET signature_token = ?, token_expires_at = ?, email_sent_to = ? WHERE id = ?', 
                  (token, expires_at.isoformat(), email_cliente, documento_id))
        db.commit()
        db.close()
        
        
        sign_url = f"{BASE_URL}/sign.html?token={token}"
        
        tipo_texto = {
            'instalacao': 'Contrato de Instalação',
            'mudanca_endereco': 'Termo de Mudança',
            'ordem_servico': 'Ordem de Serviço'
        }.get(contrato['tipo_instalacao'], 'Documento')
        
        send_remote_signature_email(email_cliente, contrato['cliente_nome'], sign_url, tipo_texto, contrato['contrato_id'])
        
        return jsonify({'sucesso': True, 'mensagem': f'Link enviado para {email_cliente}'})
    except Exception as e:
        logger.error(f"Erro ao enviar link: {e}")
        if db:
            db.close()
        return jsonify({'sucesso': False, 'erro': str(e)}), 500


@app.route('/api/verificar-token/<token>', methods=['GET'])
def verificar_token_assinatura(token):
    db = None
    try:
        db = get_db()
        contrato = db.execute('''
            SELECT id, contrato_id, cliente_nome, campo_cliente, campo_ctop, campo_db,
                   os_descricao, tipo_instalacao, signature_status, token_expires_at
            FROM contratos WHERE signature_token = ?
        ''', (token,)).fetchone()
        
        if not contrato:
            db.close()
            return jsonify({'valido': False, 'razao': 'Token inválido'}), 404
        
        if contrato['token_expires_at']:
            expires_at = datetime.fromisoformat(contrato['token_expires_at'])
            if expires_at < datetime.now():
                db.close()
                return jsonify({'valido': False, 'razao': 'Token expirado'}), 400
        
        if contrato['signature_status'] != 'pending':
            db.close()
            return jsonify({'valido': False, 'razao': 'Documento já assinado'}), 400
        
        db.close()
        return jsonify({
            'valido': True,
            'cliente_nome': contrato['cliente_nome'],
            'contrato_id': contrato['contrato_id'],
            'tipo_documento': contrato['tipo_instalacao'],
            'campo_cliente': contrato['campo_cliente'] or '',
            'campo_ctop': contrato['campo_ctop'] or '',
            'campo_db': contrato['campo_db'] or '',
            'os_descricao': contrato['os_descricao'] or '',
            'expira_em': contrato['token_expires_at']
        })
    except Exception as e:
        logger.error(f"Erro ao verificar token: {e}")
        if db:
            db.close()
        return jsonify({'valido': False, 'razao': str(e)}), 500

@app.route('/api/assinatura-remota/<token>', methods=['POST'])
def realizar_assinatura_remota(token):
    db = None
    try:
        data = request.get_json()
        assinatura_base64 = data.get('assinatura_base64', '').strip()
        
        if not assinatura_base64 or len(assinatura_base64) < 50:
            return jsonify({'sucesso': False, 'erro': 'Assinatura inválida'}), 400
        
        db = get_db()
        contrato = db.execute('''
            SELECT id, contrato_id, template_id, cliente_nome, cliente_cpf, tecnico_id, tecnico_nome,
                   campo_cliente, campo_ctop, campo_db, os_descricao, tipo_instalacao,
                   signature_status, token_expires_at
            FROM contratos WHERE signature_token = ?
        ''', (token,)).fetchone()
        
        if not contrato:
            db.close()
            return jsonify({'sucesso': False, 'erro': 'Token inválido'}), 404
        
        if contrato['token_expires_at']:
            expires_at = datetime.fromisoformat(contrato['token_expires_at'])
            if expires_at < datetime.now():
                db.close()
                return jsonify({'sucesso': False, 'erro': 'Link expirado'}), 400
        
        if contrato['signature_status'] != 'pending':
            db.close()
            return jsonify({'sucesso': False, 'erro': 'Documento já assinado'}), 400
        
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
            assinatura_row = db.execute('SELECT id, assinatura_base64 FROM assinaturas_empresa WHERE ativo = 1 ORDER BY data_cadastro DESC LIMIT 1').fetchone()
            if assinatura_row:
                assinatura_responsavel = assinatura_row['assinatura_base64']
                assinatura_responsavel_id = assinatura_row['id']
        
        if template_path and os.path.exists(template_path):
            pdf_bytes = overlay_signature_on_pdf(
                template_path, assinatura_base64,
                txt_path=txt_path,
                campo_cliente=contrato['campo_cliente'] or '',
                campo_ctop=contrato['campo_ctop'] or '',
                campo_db=contrato['campo_db'] or '',
                os_descricao=contrato['os_descricao'] or '',
                tipo_documento=contrato['tipo_instalacao'],
                assinatura_responsavel_b64=assinatura_responsavel
            )
        else:
            dados_contrato = {
                'cliente_nome': contrato['cliente_nome'],
                'cliente_cpf': contrato['cliente_cpf'],
                'assinatura_base64': assinatura_base64,
                'campo_cliente': contrato['campo_cliente'],
                'campo_ctop': contrato['campo_ctop'],
                'campo_db': contrato['campo_db'],
                'os_descricao': contrato['os_descricao']
            }
            pdf_bytes = gerar_pdf_fallback(
                contrato['id'], dados_contrato, contrato['tecnico_nome'],
                datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
                request.remote_addr, contrato['contrato_id'],
                contrato['tipo_instalacao'], assinatura_responsavel
            )
        
        signed_at = datetime.now().isoformat()
        fname = f"{contrato['contrato_id'].replace('-', '_')}_{contrato['id'][:6]}.pdf"
        pdf_full_path = os.path.join(PDF_DIR, fname)
        with open(pdf_full_path, 'wb') as fh:
            fh.write(pdf_bytes)
        
        db.execute('''
            UPDATE contratos 
            SET assinatura_base64 = ?, 
                signature_status = 'complete_remote',
                signed_at = ?,
                pdf_path = ?,
                signature_token = NULL,
                assinatura_responsavel_id = ?,
                assinatura_responsavel_data = ?
            WHERE id = ?
        ''', (assinatura_base64, signed_at, fname, assinatura_responsavel_id, 
              datetime.now().isoformat() if assinatura_responsavel_id else None,
              contrato['id']))
        
        db.commit()
        db.close()
        
        return jsonify({'sucesso': True, 'mensagem': 'Assinatura registrada!', 'contrato_id': contrato['contrato_id']})
    except Exception as e:
        logger.error(f"Erro na assinatura remota: {e}")
        if db:
            db.close()
        return jsonify({'sucesso': False, 'erro': str(e)}), 500


@app.route('/api/contratos', methods=['GET'])
@login_required
def listar_contratos():
    try:
        db = get_db()
        rows = db.execute('''
            SELECT id, contrato_id, numero_seq, tecnico_nome, cliente_nome,
                   data_hora, pdf_path, campo_cliente, campo_ctop, campo_db,
                   os_descricao, tipo_instalacao, signature_status, signed_at, email_sent_to
            FROM contratos ORDER BY numero_seq DESC
        ''').fetchall()
        db.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({'erro': str(e)}), 500

@app.route('/api/pdf/<rid>')
def download_pdf(rid):
    try:
        db = get_db()
        row = db.execute('SELECT pdf_path, cliente_nome, contrato_id FROM contratos WHERE id = ?', (rid,)).fetchone()
        db.close()
        if not row or not row['pdf_path']:
            return jsonify({'erro': 'PDF não encontrado'}), 404
        path = os.path.join(PDF_DIR, row['pdf_path'])
        if not os.path.exists(path):
            return jsonify({'erro': 'PDF não encontrado'}), 404
        nome = re.sub(r'[^a-zA-Z0-9_]', '_', row['cliente_nome'] or 'documento')
        return send_file(path, as_attachment=True, download_name=f"{row['contrato_id']}_{nome}.pdf")
    except Exception as e:
        return jsonify({'erro': str(e)}), 500

@app.route('/api/assinatura-empresa/ativa', methods=['GET'])
@login_required
def get_assinatura_empresa_ativa():
    try:
        db = get_db()
        assinatura = db.execute('SELECT id, responsavel_nome, responsavel_cargo, assinatura_base64, data_cadastro FROM assinaturas_empresa WHERE ativo = 1 ORDER BY data_cadastro DESC LIMIT 1').fetchone()
        db.close()
        if not assinatura:
            return jsonify({'sucesso': False, 'mensagem': 'Nenhuma assinatura ativa'}), 404
        return jsonify({'sucesso': True, 'id': assinatura['id'], 'responsavel_nome': assinatura['responsavel_nome'], 'responsavel_cargo': assinatura['responsavel_cargo'], 'assinatura_base64': assinatura['assinatura_base64'], 'data_cadastro': assinatura['data_cadastro']})
    except Exception as e:
        return jsonify({'erro': str(e)}), 500

@app.route('/api/assinatura-empresa/salvar', methods=['POST'])
@login_required
def salvar_assinatura_empresa():
    try:
        data = request.get_json()
        responsavel_nome = data.get('responsavel_nome', '').strip()
        responsavel_cargo = data.get('responsavel_cargo', '').strip()
        assinatura_base64 = data.get('assinatura_base64', '').strip()
        if not responsavel_nome:
            return jsonify({'sucesso': False, 'erro': 'Nome obrigatório'}), 400
        if not assinatura_base64 or len(assinatura_base64) < 50:
            return jsonify({'sucesso': False, 'erro': 'Assinatura inválida'}), 400
        assinatura_id = str(uuid.uuid4())
        db = get_db()
        db.execute('UPDATE assinaturas_empresa SET ativo = 0 WHERE ativo = 1')
        db.execute('INSERT INTO assinaturas_empresa (id, responsavel_nome, responsavel_cargo, assinatura_base64, data_cadastro, ativo) VALUES (?, ?, ?, ?, ?, 1)', (assinatura_id, responsavel_nome, responsavel_cargo, assinatura_base64, datetime.now().isoformat()))
        db.commit()
        db.close()
        return jsonify({'sucesso': True, 'mensagem': f'Assinatura de {responsavel_nome} salva!'})
    except Exception as e:
        return jsonify({'sucesso': False, 'erro': str(e)}), 500

@app.route('/api/enviar-email-pdf', methods=['POST'])
@login_required
def enviar_email_pdf():
    try:
        data = request.get_json()
        documento_id = data.get('documento_id')
        email_cliente = data.get('email_cliente', '').strip()
        
        db = get_db()
        contrato = db.execute('SELECT id, contrato_id, cliente_nome, pdf_path, data_hora, tecnico_nome FROM contratos WHERE id = ?', (documento_id,)).fetchone()
        
        if not contrato:
            db.close()
            return jsonify({'sucesso': False, 'erro': 'Documento não encontrado'}), 404
        
        pdf_path = os.path.join(PDF_DIR, contrato['pdf_path'])
        if not os.path.exists(pdf_path):
            db.close()
            return jsonify({'sucesso': False, 'erro': 'PDF não encontrado'}), 404
        
        msg = MIMEMultipart()
        msg['From'] = f"{EMAIL_CONFIG['email_nome']} <{EMAIL_CONFIG['email_remetente']}>"
        msg['To'] = email_cliente
        msg['Subject'] = f"Documento Assinado - Melolink Internet"
        
        corpo = f"""
        <html><body>
            <p>Olá <strong>{contrato['cliente_nome']}</strong>,</p>
            <p>Segue em anexo o seu documento assinado.</p>
            <p>Protocolo: {contrato['contrato_id']}<br>Data: {contrato['data_hora']}</p>
            <p>Atenciosamente,<br>Melolink Internet</p>
        </body></html>
        """
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
        
        db.close()
        return jsonify({'sucesso': True, 'mensagem': 'E-mail enviado!'})
    except Exception as e:
        return jsonify({'sucesso': False, 'erro': str(e)}), 500

@app.route('/api/tecnicos', methods=['GET'])
@login_required
def get_tecnicos():
    try:
        db = get_db()
        rows = db.execute('SELECT id, nome, matricula, ativo FROM tecnicos ORDER BY nome').fetchall()
        db.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({'erro': str(e)}), 500

@app.route('/api/tecnicos', methods=['POST'])
@login_required
def criar_tecnico():
    try:
        data = request.get_json()
        nome = data.get('nome', '').strip()
        matricula = data.get('matricula', '').strip()
        if not nome or not matricula:
            return jsonify({'sucesso': False, 'erro': 'Nome e matrícula obrigatórios'}), 400
        tecnico_id = str(uuid.uuid4())
        db = get_db()
        db.execute('INSERT INTO tecnicos (id, nome, matricula, ativo) VALUES (?, ?, ?, 1)', (tecnico_id, nome, matricula))
        db.commit()
        db.close()
        return jsonify({'sucesso': True, 'id': tecnico_id, 'nome': nome, 'matricula': matricula})
    except sqlite3.IntegrityError:
        return jsonify({'sucesso': False, 'erro': 'Matrícula já existe'}), 409
    except Exception as e:
        return jsonify({'sucesso': False, 'erro': str(e)}), 500

@app.route('/api/tecnicos/<tecnico_id>', methods=['DELETE'])
@login_required
def desativar_tecnico(tecnico_id):
    try:
        db = get_db()
        db.execute('UPDATE tecnicos SET ativo = 0 WHERE id = ?', (tecnico_id,))
        db.commit()
        db.close()
        return jsonify({'sucesso': True, 'mensagem': 'Técnico desativado'})
    except Exception as e:
        return jsonify({'sucesso': False, 'erro': str(e)}), 500

@app.route('/api/tecnicos/<tecnico_id>/reativar', methods=['PUT'])
@login_required
def reativar_tecnico(tecnico_id):
    try:
        db = get_db()
        db.execute('UPDATE tecnicos SET ativo = 1 WHERE id = ?', (tecnico_id,))
        db.commit()
        db.close()
        return jsonify({'sucesso': True, 'mensagem': 'Técnico reativado'})
    except Exception as e:
        return jsonify({'sucesso': False, 'erro': str(e)}), 500


@app.route('/api/templates', methods=['POST'])
@login_required
def upload_template():
    try:
        if 'arquivo' not in request.files:
            return jsonify({'sucesso': False, 'erro': 'Arquivo PDF obrigatório'}), 400
        
        pdf_file = request.files['arquivo']
        if not pdf_file.filename.endswith('.pdf'):
            return jsonify({'sucesso': False, 'erro': 'Apenas PDF'}), 400
        
        tipo = request.form.get('tipo', 'instalacao')
        tid = str(uuid.uuid4())
        pdf_filename = f'{tid}.pdf'
        pdf_path = os.path.join(UPLOAD_DIR, pdf_filename)
        pdf_file.save(pdf_path)
        
        txt_path = None
        txt_original_name = None
        if 'txtfile' in request.files and request.files['txtfile'].filename:
            txt_file = request.files['txtfile']
            if txt_file.filename.endswith('.txt'):
                txt_path = os.path.join(UPLOAD_TXT_DIR, f'{tid}.txt')
                txt_file.save(txt_path)
                txt_original_name = txt_file.filename
        
        os_pdf_path = None
        os_pdf_original_name = None
        if 'osfile' in request.files and request.files['osfile'].filename:
            os_file = request.files['osfile']
            if os_file.filename.endswith('.pdf'):
                os_pdf_path = os.path.join(UPLOAD_OS_DIR, f'{tid}_os.pdf')
                os_file.save(os_pdf_path)
                os_pdf_original_name = os_file.filename
        
        nome = request.form.get('nome', pdf_file.filename).strip()
        
        db = get_db()
        db.execute('''INSERT INTO templates (id, nome, filename, ativo, criado_em, txt_path, txt_original_name, tipo, os_pdf_path, os_pdf_original_name) 
                     VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?)''',
                  (tid, nome, pdf_filename, datetime.now().isoformat(), txt_path, txt_original_name, tipo, os_pdf_path, os_pdf_original_name))
        db.commit()
        db.close()
        
        return jsonify({'sucesso': True, 'id': tid, 'nome': nome})
    except Exception as e:
        return jsonify({'sucesso': False, 'erro': str(e)}), 500

@app.route('/api/templates/<tid>/delete', methods=['DELETE'])
@login_required
def delete_template(tid):
    try:
        db = get_db()
        db.execute('UPDATE templates SET ativo = 0 WHERE id = ?', (tid,))
        db.commit()
        db.close()
        return jsonify({'sucesso': True})
    except Exception as e:
        return jsonify({'sucesso': False, 'erro': str(e)}), 500

if __name__ == '__main__':
    init_db()
    local_ip = get_local_ip()
    print(f'Local: http://localhost:5000')
    print(f'Rede:  http://{local_ip}:5000')
    print(f'Links de assinatura usarão: {BASE_URL}')
    app.run(debug=True, host='0.0.0.0', port=5000, threaded=True)