#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -z "${JAVA_HOME:-}" || ! -x "${JAVA_HOME}/bin/java" ]]; then
  for candidate in \
    /opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home \
    /usr/local/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home
  do
    if [[ -x "$candidate/bin/java" ]]; then
      export JAVA_HOME="$candidate"
      break
    fi
  done
fi
if [[ -z "${JAVA_HOME:-}" || ! -x "${JAVA_HOME}/bin/java" ]]; then
  echo "Нужен JDK 21. Установите его и задайте JAVA_HOME." >&2
  exit 1
fi
export PATH="$JAVA_HOME/bin:$PATH"

if ! command -v npm >/dev/null 2>&1; then
  echo "Нужен Node.js, чтобы собрать интерфейс." >&2
  exit 1
fi
(cd frontend && npm install && npm run build)

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python ml/generate_sources.py
exec mvn spring-boot:run
