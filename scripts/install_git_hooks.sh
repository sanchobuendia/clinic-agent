#!/usr/bin/env sh

set -eu

git config core.hooksPath .githooks
chmod +x .githooks/pre-commit .githooks/commit-msg .githooks/pre-push

echo "Hooks instalados com sucesso."
echo "Mensagens de commit devem seguir o formato: feature: descricao"

