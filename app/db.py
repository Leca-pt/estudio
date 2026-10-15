from sqlalchemy import event
from sqlmodel import Session, create_engine

from app.config import settings

# Mais ligações e uma espera mais curta: um pico de pedidos atrasa, mas não esgota a app (SQLite em WAL aguenta
# muitas leituras ao mesmo tempo).
engine = create_engine(settings.database_url, connect_args={"check_same_thread": False, "timeout": 15},
                       pool_size=20, max_overflow=30, pool_timeout=20)


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
