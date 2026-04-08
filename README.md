# Установить python3-full если ещё не установлен
sudo apt install python3-full

# Создать виртуальное окружение
python3 -m venv myenv

# Активировать окружение
source myenv/bin/activate

# Теперь установить пакеты
pip install rnsh lxmf rns python-telegram-bot


python3 script_name.py
