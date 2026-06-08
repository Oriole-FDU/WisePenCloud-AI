FROM sagemath/sagemath:latest

WORKDIR /app

USER root

RUN sage -pip install fastapi uvicorn pydantic

COPY app.py models.py tasks.py /app/

EXPOSE 8000

CMD ["sage", "-python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]