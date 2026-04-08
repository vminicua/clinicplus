#!/bin/bash

echo "============================================"
echo "    Clinic Plus - Setup inicial (Linux/Mac)"
echo "============================================"
echo ""

if [ ! -d "venv" ]; then
  echo "Criando ambiente virtual..."
  python3 -m venv venv
else
  echo "Ambiente virtual existente. Reutilizando venv."
fi

echo "Ativando ambiente virtual..."
source venv/bin/activate

echo "Instalando dependencias..."
pip install -r requirements.txt

if [ ! -f ".env" ]; then
  echo "Arquivo .env nao encontrado."
  echo "Copie .env.example para .env e preencha os dados do banco remoto antes de continuar."
  echo "Exemplo:"
  echo "  cp .env.example .env"
  exit 1
fi

echo "Executando migracoes..."
python manage.py makemigrations
python manage.py migrate

echo ""
echo "Criando superuser..."
python manage.py createsuperuser

echo ""
echo "Setup concluido."
echo "Se for usar banco remoto, copie .env.example para .env e preencha os dados reais."
echo "Exemplo generico de tunel SSH:"
echo "  ssh -L 5522:SEU_DB_HOST:3306 SEU_USUARIO@SEU_HOST -p SUA_PORTA"
echo ""
echo "Inicie a aplicacao com:"
echo "  python manage.py runserver"
