# check=skip=InvalidDefaultArgInFrom
ARG PYTHON_VERSION
FROM python:${PYTHON_VERSION}-alpine
WORKDIR /app
COPY scripts/worker_release.py scripts/worker_updater.py ./
CMD ["python", "worker_updater.py"]
