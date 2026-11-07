"""AWS Lambda entry point - Docker CMD ["main.handler"] isi ko call karta hai."""

import uvicorn
from mangum import Mangum

from src.app import app

handler = Mangum(app, lifespan="off")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=3000)
