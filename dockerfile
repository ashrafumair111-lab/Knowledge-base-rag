# AWS Lambda Python 3.12 runtime (Amazon Linux 2023 base image)
FROM public.ecr.aws/lambda/python:3.12

# Dependencies - pip Linux wheels install karta hai
# (Windows .venv kabhi copy nahi hota - sab image ke andar banta hai)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code + chat UI (templates/ aur static/ read-only hain - wahi chahiye Lambda ko)
COPY main.py ${LAMBDA_TASK_ROOT}/
COPY templates/ ${LAMBDA_TASK_ROOT}/templates/
COPY static/ ${LAMBDA_TASK_ROOT}/static/

# Entry point: main.py ka `handler` (Mangum + FastAPI)
CMD ["main.handler"]