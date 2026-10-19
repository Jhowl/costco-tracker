import os


class Settings:
    def __init__(self) -> None:
        self.db_path = os.getenv("DB_PATH", "data/costco.db")
        self.ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
        self.ollama_model = os.getenv("OLLAMA_MODEL", "gemma4:26b")
        self.ollama_timeout = float(os.getenv("OLLAMA_TIMEOUT_S", "600"))
        # products sent to the model per request when categorising
        self.ai_batch_size = int(os.getenv("AI_BATCH_SIZE", "20"))
        # people created on first start; more can be added in the UI
        self.people = [p.strip() for p in os.getenv("PEOPLE", "Me,Partner").split(",") if p.strip()]
        # long side in px of receipt photos sent to the model
        self.max_image_side = int(os.getenv("LLM_MAX_IMAGE_SIDE", "3000"))


settings = Settings()
