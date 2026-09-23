import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_URL = os.getenv('RADIUSNET_API_URL')
TOKEN = os.getenv('RADIUSNET_API_TOKEN')

print(f"🔗 URL: {API_URL}")
print(f"🔑 Token: {TOKEN[:10]}...")

# Teste com um ID_CLIENTE_PLANO real (substitua pelo seu)
ID_CLIENTE_PLANO = "9583"  # Use um ID real do seu sistema
TIPO_TERMO = 0  # 0=Adesão, 1=Cancelamento

try:
    url = f"{API_URL}/termo/{ID_CLIENTE_PLANO}/{TIPO_TERMO}/pdf"
    headers = {"RTOKEN": TOKEN}
    
    print(f"\n📡 GET {url}")
    response = requests.get(url, headers=headers, timeout=30)
    print(f"📊 Status: {response.status_code}")
    print(f"📝 Resposta: {response.text}")
    
    if response.status_code == 200:
        data = response.json()
        link_pdf = data.get('rows')
        if link_pdf:
            print(f"✅ Link do PDF: {link_pdf}")
            
            # Tenta baixar o PDF
            pdf_response = requests.get(link_pdf, timeout=30)
            if pdf_response.status_code == 200:
                print(f"✅ PDF baixado com sucesso! Tamanho: {len(pdf_response.content)} bytes")
            else:
                print(f"❌ Erro ao baixar PDF: {pdf_response.status_code}")
        else:
            print("❌ Link do PDF não encontrado na resposta")
    else:
        print(f"❌ Erro: {response.text}")
        
except Exception as e:
    print(f"❌ Falha: {e}")
    import traceback
    traceback.print_exc()