#!/usr/bin/env bash
# Instala o ambiente do projeto numa sessão do Colab. Cada parte confere o que já existe e só faz o que falta.
#
#   bash ambiente/instalar_colab.sh bancos   [pasta_de_cache]   PostgreSQL 16 + Java 17 + Neo4j 5.26 Community
#   bash ambiente/instalar_colab.sh servidor                    bibliotecas do servidor MCP em servidor_libs/
#   bash ambiente/instalar_colab.sh ollama   [modelo]           Ollama + o modelo (padrão qwen3:8b)
#
# pasta_de_cache: uma pasta do Drive onde o .tar.gz do Neo4j fica guardado entre sessões (evita baixar de novo).
set -euo pipefail

NEO4J_VERSAO="5.26.0"
NEO4J_HOME="/opt/neo4j-server"          # fora de /content: uma pasta "neo4j" ali esconderia a biblioteca Python
NEO4J_SENHA="${YELP_NEO4J_SENHA:-yelp-santa-barbara}"
PG_SENHA_ADMIN="postgres"
export JAVA_HOME="/usr/lib/jvm/java-17-openjdk-amd64"
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"

espera_porta() {   # espera_porta <porta> <segundos> <nome>
  for _ in $(seq "$2"); do
    if (echo > "/dev/tcp/127.0.0.1/$1") 2>/dev/null; then echo "✅ $3 ouvindo na porta $1"; return 0; fi
    sleep 1
  done
  echo "❌ $3 não abriu a porta $1 em $2 s"; return 1
}

parte_bancos() {
  local cache="${1:-}"
  local faltam=""
  command -v psql > /dev/null || faltam="$faltam postgresql"
  [ -d "$JAVA_HOME" ] || faltam="$faltam openjdk-17-jre-headless"
  if [ -n "$faltam" ]; then
    echo "⏳ apt:$faltam (alguns minutos)"
    apt-get -qq update > /dev/null
    DEBIAN_FRONTEND=noninteractive apt-get -qq install -y $faltam > /dev/null
  fi
  service postgresql start > /dev/null
  espera_porta 5432 30 "PostgreSQL"
  (cd /tmp && runuser -u postgres -- psql -q -c "ALTER USER postgres PASSWORD '$PG_SENHA_ADMIN'")

  if [ ! -x "$NEO4J_HOME/bin/neo4j" ]; then
    local tar="neo4j-community-$NEO4J_VERSAO-unix.tar.gz"
    if [ -n "$cache" ] && [ -f "$cache/$tar" ]; then
      echo "📦 Neo4j do cache: $cache/$tar"
      cp "$cache/$tar" "/tmp/$tar"
    else
      echo "⏳ baixando o Neo4j $NEO4J_VERSAO"
      wget -q "https://dist.neo4j.org/$tar" -O "/tmp/$tar"
      if [ -n "$cache" ]; then mkdir -p "$cache" && cp "/tmp/$tar" "$cache/$tar" && echo "📦 guardado no cache para a próxima sessão"; fi
    fi
    mkdir -p "$NEO4J_HOME"
    tar -xzf "/tmp/$tar" -C "$NEO4J_HOME" --strip-components=1
    "$NEO4J_HOME/bin/neo4j-admin" dbms set-initial-password "$NEO4J_SENHA" > /dev/null
  fi
  if ! (echo > /dev/tcp/127.0.0.1/7687) 2>/dev/null; then
    "$NEO4J_HOME/bin/neo4j" start > /dev/null
  fi
  espera_porta 7687 180 "Neo4j" || { tail -n 30 "$NEO4J_HOME/logs/neo4j.log"; exit 1; }
}

parte_servidor() {
  if [ -d "$RAIZ/servidor_libs/fastmcp" ]; then
    echo "✅ bibliotecas do servidor já estão em servidor_libs/"
  else
    echo "⏳ instalando as bibliotecas do servidor em servidor_libs/"
    pip -q install --disable-pip-version-check --target "$RAIZ/servidor_libs" -r "$RAIZ/servidor/requirements.txt" > /dev/null 2>&1
    [ -d "$RAIZ/servidor_libs/fastmcp" ] || { echo "❌ a instalação falhou"; exit 1; }
    echo "✅ bibliotecas do servidor instaladas"
  fi
}

parte_ollama() {
  local modelo="${1:-qwen3:8b}"
  if ! command -v ollama > /dev/null; then
    echo "⏳ instalando o Ollama"
    apt-get -qq install -y zstd > /dev/null 2>&1 || true
    curl -fsSL https://ollama.com/install.sh | sh > /dev/null 2>&1
    command -v ollama > /dev/null || { echo "❌ a instalação do Ollama falhou"; exit 1; }
  fi
  if ! (echo > /dev/tcp/127.0.0.1/11434) 2>/dev/null; then
    nohup ollama serve > /content/ollama.log 2>&1 &
  fi
  espera_porta 11434 30 "Ollama" || { tail -n 30 /content/ollama.log; exit 1; }
  if ! ollama list | grep -q "^$modelo"; then
    echo "⏳ baixando $modelo (~5 GB, alguns minutos sem mostrar progresso)"
    ollama pull "$modelo" > /dev/null 2>&1 || { echo "❌ não consegui baixar $modelo"; exit 1; }
  fi
  echo "✅ modelo $modelo pronto"
}

case "${1:-}" in
  bancos)   parte_bancos "${2:-}" ;;
  servidor) parte_servidor ;;
  ollama)   parte_ollama "${2:-}" ;;
  *) echo "uso: bash ambiente/instalar_colab.sh <bancos|servidor|ollama> [argumento]"; exit 2 ;;
esac
