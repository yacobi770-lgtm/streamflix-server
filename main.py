from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"message": "Server is running"}

@app.get("/api/anime/stream/{episode_id}")
async def get_stream(episode_id: str):
    return {"stream_url": "example_url"}

if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=8000)
