from fastapi import FastAPI

app = FastAPI(title="CatalogAgent")


@app.get("/health")
def health():
    return {"status": "ok", "service": "catalogagent"}
