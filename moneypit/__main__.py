import os
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "moneypit.main:app",
        host=os.environ.get("MONEYPIT_HOST", "127.0.0.1"),
        port=int(os.environ.get("MONEYPIT_PORT", "8000")),
        reload=False,
    )
