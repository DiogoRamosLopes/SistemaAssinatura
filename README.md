\# ASSINANET - Sistema de Assinatura Digital



\## Sobre o Projeto



O AssinaNet é um sistema completo de gestão de contratos e assinaturas digitais desenvolvido especialmente para a Melolink Internet Fibra Óptica. A plataforma permite a geração, assinatura e gerenciamento de documentos contratuais com validade jurídica, atendendo aos requisitos da Lei 14.063/2020.



\## Funcionalidades



Gerenciamento de Documentos

\- Contratos de Instalação

\- Termos de Mudança de Endereço

\- Ordens de Serviço Técnico



Assinatura Digital

\- Captura de assinatura via touch ou mouse

\- Modo tela cheia para dispositivos móveis

\- Assinatura remota por link enviado ao cliente

\- Validação jurídica conforme legislação brasileira



Administração

\- Painel administrativo completo

\- Gestão de técnicos e permissões

\- Cadastro de templates de documentos

\- Visualização e filtro de contratos assinados



Comunicação

\- Envio de documentos por e-mail com PDF anexo

\- Compartilhamento por WhatsApp

\- Notificações automáticas de documentos pendentes



\## Arquitetura



Backend

\- Python 3.10+

\- Framework Flask

\- SQLite3 para persistência de dados

\- PyPDF2 para manipulação de PDF

\- ReportLab para geração de documentos



Frontend

\- HTML5 Semântico

\- CSS3 com design responsivo

\- JavaScript (React via CDN)

\- Canvas API para captura de assinaturas



Infraestrutura

\- Servidor embutido Flask (desenvolvimento)

\- Suporte a deploy em redes locais

\- Configuração via variáveis de ambiente



\## Pré-requisitos



\- Python 3.10 ou superior

\- Pip (Python package manager)

\- Conexão com internet para carregamento de CDNs

\- Navegador moderno com suporte a Canvas



\## Instalação



Clone o repositório



```bash

git clone https://github.com/DiogoRamosLopes/SistemaAssinatura.git

cd SistemaAssinatura

