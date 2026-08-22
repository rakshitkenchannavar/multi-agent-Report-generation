from sqlmodel import SQLModel, create_engine, Session
import os

# Use DATABASE_PATH env variable if set (Render production),
# otherwise fall back to local file path (local development)
DB_PATH = os.getenv(
    "DATABASE_PATH",
    os.path.join(os.path.dirname(__file__), "report_app.db")
)

DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False})

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session