FROM python:3.13-slim

# System dependencies: Tesseract for OCR, plus tools needed to add
# Microsoft's package repo for the ODBC driver below.
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    curl \
    gnupg2 \
    unixodbc \
    unixodbc-dev \
    && rm -rf /var/lib/apt/lists/*

# Microsoft ODBC Driver 18 for SQL Server - not pip-installable, same
# driver you installed manually on Windows for pyodbc to work.
RUN curl -sSL https://packages.microsoft.com/keys/microsoft.asc \
        | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && curl -sSL https://packages.microsoft.com/config/debian/12/prod.list \
        | tee /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y msodbcsql18 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps before copying the rest of the code, so Docker can
# cache this layer and skip reinstalling on every code change.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# spaCy's model isn't in requirements.txt (it's downloaded via a CLI
# command), so it needs its own explicit step.
RUN python -m spacy download en_core_web_sm

COPY . .

RUN useradd -m appuser && chown -R appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]