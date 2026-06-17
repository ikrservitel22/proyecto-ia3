#!/bin/bash
set -e

echo "Iniciando SSH..."
mkdir -p /var/run/sshd
/usr/sbin/sshd

echo "Iniciando Ollama..."
ollama serve &

echo "Esperando a que Ollama arranque..."
sleep 10

if [ ! -f /app/database/database.db ]; then
    echo "Inicializando base de datos..."
    python database/setup_tablas.py
fi

echo "Iniciando aplicación..."
exec python server_llamadas.py
