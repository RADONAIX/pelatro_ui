# RA Backend

Minimal FastAPI backend for the RA UI application. No authentication.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run (localhost:8001)

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
```

or

```bash
python run.py
```

## Endpoints

| Method | Path       | Response            |
| ------ | ---------- | ------------------- |
| GET    | `/say_hi`  | `{"message": "hi"}` |
| GET    | `/health`  | `{"message": "ok"}` |
| GET    | `/docs`    | Swagger UI          |

## Example

```bash
curl http://localhost:8001/say_hi
```

```json
{ "message": "hi" }
```

From a browser: open <http://localhost:8001/say_hi>

From a frontend:

```js
const res = await fetch("http://localhost:8001/say_hi");
const data = await res.json(); // { message: "hi" }
```

## Configuration

Optional `.env` file in the project root:

```
PORT=8001
CORS_ORIGINS=["http://localhost:3000","http://localhost:5173"]
```

CORS defaults to `*` for local development — restrict `CORS_ORIGINS` before deploying.
