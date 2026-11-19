# Image unique pour l'application ET le pipeline (Cloud Run service + Cloud Run job, ou tout autre runtime).
#   App      : docker run -p 8080:8080 -e BOURSE_DATA_DIR=/data -v $PWD/data:/data bourse
#   Pipeline : docker run -e FMP_API_KEY=... -e BOURSE_DATA_DIR=/data -v $PWD/data:/data bourse bourse run
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    BOURSE_DATA_DIR=/data

WORKDIR /srv
COPY requirements.txt pyproject.toml README.md ./
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
COPY app ./app
COPY config ./config
COPY .streamlit ./.streamlit
RUN pip install --no-cache-dir --no-deps -e .

EXPOSE 8080
CMD ["sh", "-c", "streamlit run app/streamlit_app.py --server.port=${PORT} --server.address=0.0.0.0"]
