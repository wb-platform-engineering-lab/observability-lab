FROM python:3.12-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim
RUN groupadd -r lumio && useradd -r -g lumio lumio
WORKDIR /app
COPY --from=builder /install /usr/local
COPY app.py .
USER lumio
EXPOSE 8000
CMD ["python", "app.py"]
