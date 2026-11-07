# AWS Lambda Python 3.12 runtime (Amazon Linux 2023 base image)
FROM public.ecr.aws/lambda/python:3.12

# Dependencies - pip Linux wheels install karta hai
# (Windows .venv kabhi copy nahi hota - sab image ke andar banta hai)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code + chat UI. src/ ke andar hi views/ (HTML) aur assets/ (CSS/JS) hain,
# yani sirf do COPY chahiye. main.py sirf entrypoint hai (COPY pura src/ le aata hai).
COPY main.py ${LAMBDA_TASK_ROOT}/
COPY src/ ${LAMBDA_TASK_ROOT}/src/

# Entry point: main.py ka `handler` (Mangum + FastAPI)
CMD ["main.handler"]
