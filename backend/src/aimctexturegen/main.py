from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="AIMCTextureGen API", version="0.1.0")

    @app.get("/api/health")
    def health() -> dict[str, object]:
        return {"status": "ok", "schema_version": 1}

    return app


app = create_app()
