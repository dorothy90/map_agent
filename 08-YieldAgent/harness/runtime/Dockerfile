FROM python:3.11-slim
RUN pip install --no-cache-dir pandas==2.3.3 numpy==2.2.6 scipy==1.16.1 plotly==6.3.0 statsmodels==0.14.5
ENV PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
COPY worker.py /worker.py
USER 1000:1000
ENTRYPOINT ["python", "/worker.py"]
