from sqlalchemy import event
from sqlmodel import Session, create_engine

from app.config import settings

# Muitas ligações e uma espera curta pela pool. Uma página pode disparar mais de cem pedidos de uma vez (miniaturas,
# custos, estado) e cada um pode precisar de uma ligação; o SQLite em WAL aguenta muitas leituras ao mesmo tempo. A
# espera pela pool acontece em código síncrono, muitas vezes no próprio ciclo de eventos: com 20 s, cada pedido à
# espera parava a app inteira 20 s (2026-11-16: 10 minutos sem resposta). Com 5 s, falha depressa e a app continua.
engine = create_engine(settings.database_url, connect_args={"check_same_thread": False, "timeout": 15},
                       pool_size=30, max_overflow=90, pool_timeout=5)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=15000")
    cur.close()


async def get_session():
    """Sessão de um pedido. É "async" de propósito: a sessão abre e fecha no ciclo de eventos, não no conjunto de
    threads do FastAPI. Com uma dependência normal, abrir e fechar precisa de uma thread livre: sob muitos pedidos ao
    mesmo tempo, as threads ficavam todas à espera de uma ligação e as ligações à espera de uma thread para fechar —
    a app parava (aconteceu a 2026-10-02). A ligação em si só é pedida na primeira consulta."""
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
