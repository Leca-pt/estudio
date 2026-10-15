#!/bin/sh
# Deploy seguro: só reinicia a app com a fila de geração vazia (um reinício volta a lançar os pedidos
# que estavam a correr e, no Artlist, isso pode gastar créditos duas vezes).
# A imagem constrói-se primeiro (demora); a fila verifica-se imediatamente antes de reiniciar.
set -e
cd "$(dirname "$0")/.."
docker compose build -q app
busy=$(docker exec director-app python -c "
from app.db import engine
from sqlmodel import Session, select
from app.models import ImageJob
with Session(engine) as s: print(len(s.exec(select(ImageJob).where(ImageJob.status.in_(['queued','running']))).all()))")
if [ "$busy" != "0" ]; then
  echo "Fila ocupada ($busy pedido(s) em curso). Imagem construída, reinício adiado." >&2
  exit 1
fi
docker compose up -d app
