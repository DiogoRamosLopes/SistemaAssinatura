# AssinaNet — Melolink Internet Fibra Óptica

Sistema PWA de assinatura digital para contratos de adesão, usado por técnicos de instalação no celular.

## Instalação

```bash
pip install -r requirements.txt
python app.py
```

Acesse: http://localhost:5000

## Fluxo

1. Técnico seleciona seu nome e informa o nº do contrato
2. Cliente lê o termo na tela e confirma concordância
3. Cliente preenche nome e CPF
4. Cliente assina com o dedo no canvas
5. Backend sobrepõe a assinatura no PDF original do ERP
6. PDF assinado disponível para download

## Configuração da assinatura

Em `app.py`, ajuste `SIG_CONFIG` se necessário:

```python
SIG_CONFIG = {
    'page_index': 9,   # página 10 (0-based) = Termo de Adesão
    'x':      50,      # pts da margem esquerda
    'y':      168,     # pts da margem inferior
    'width':  220,     # largura em pts (~77mm)
    'height':  46,     # altura em pts (~16mm)
}
```

## Admin

Acesse `/admin` para:
- Fazer upload do PDF template do ERP
- Ver todos os contratos assinados
- Baixar PDFs individuais

## Deploy (Render.com — grátis)

1. Suba o projeto para um repositório GitHub
2. Crie um novo Web Service no render.com
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn -w 2 app:app`
5. Pronto — HTTPS automático, PWA instalável
