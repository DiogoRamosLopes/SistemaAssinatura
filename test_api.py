import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_URL = os.getenv('RADIUSNET_API_URL')
TOKEN = os.getenv('RADIUSNET_API_TOKEN')

CPF_TESTE = "45532740860"  



try:
    url = f"{API_URL}/cp/{CPF_TESTE}/2"
    headers = {"RTOKEN": TOKEN}
    
    response = requests.get(url, headers=headers, timeout=15)
    print(f"\n Status: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        if data.get('rows'):
            cliente = data['rows'][0]
            print(f" Conexão OK!")
            print(f" Cliente: {cliente.get('nome_razao')}")
            print(f" CPF: {cliente.get('cpf_cnpj')}")
            print(f" Planos: {len(cliente.get('planos', []))}")
        else:
            print(" Cliente não encontrado. Tente outro CPF.")
    else:
        print(f" Erro: {response.text}")
        
except Exception as e:
    print(f" Falha na conexão: {e}")
