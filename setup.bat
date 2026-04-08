@echo off
echo ============================================
echo    Clinic Plus - Setup inicial
echo ============================================
echo.

if not exist venv (
    echo Criando ambiente virtual...
    python -m venv venv
) else (
    echo Ambiente virtual existente. Reutilizando venv.
)

echo Ativando ambiente virtual...
call venv\Scripts\activate

echo Instalando dependencias...
pip install -r requirements.txt

echo Executando migracoes...
python manage.py makemigrations
python manage.py migrate

echo.
echo Criando superuser...
python manage.py createsuperuser

echo.
echo Setup concluido.
echo Se for usar banco remoto, copie .env.example para .env e preencha os dados reais.
echo Exemplo generico de tunel SSH:
echo    ssh -L 5522:SEU_DB_HOST:3306 SEU_USUARIO@SEU_HOST -p SUA_PORTA
echo.
echo Inicie a aplicacao com:
echo    python manage.py runserver
echo.
pause
